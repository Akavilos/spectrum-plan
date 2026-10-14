"""提供商请求共用的传输、密钥解析与可读错误；代理只作用于该接口。"""
import asyncio
import json
import os
import re
import socket
import urllib.error
import urllib.request
from urllib.parse import urlsplit, urlunsplit


class ModelError(RuntimeError):
    def __init__(self, message, *, status=None, detail='', endpoint=''):
        super().__init__(message)
        self.status, self.detail, self.endpoint = status, detail, endpoint

    def as_dict(self):
        return {'ok': False, 'message': str(self), 'status': self.status,
                'detail': self.detail, 'endpoint': self.endpoint}


def resolve_key(value):
    if value.startswith('$'):
        name = value[1:]
        if not os.environ.get(name):
            raise ModelError('密钥环境变量未设置：' + name)
        return os.environ[name]
    return value


def safe_text(text, secrets=()):
    for secret in secrets:
        if secret:
            text = text.replace(secret, '[密钥已隐藏]')
    return re.sub(r'(?i)(bearer\s+|api[-_ ]?key[=: ]+)[^\s"\']+', r'\1[密钥已隐藏]', text)


def safe_url(url):
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.hostname + (':' + str(parts.port) if parts.port else ''),
                       parts.path, '', ''))


class ModelHTTP:
    def __init__(self):
        self.key_index = 0

    async def json(self, config, url, body=None, *, protocol='openai', stream=False, raw_data=None,
                   content_type='application/json', binary=False):
        keys = [resolve_key(k) for k in config.get('api_keys', [])]
        if not keys and config.get('api_key'):
            keys = [resolve_key(config['api_key'])]
        if not keys:
            keys = ['']
        index = self.key_index
        self.key_index += 1
        retries = int(config.get('retry_count', 0))
        for attempt in range(retries + 1):
            key = keys[(index + attempt) % len(keys)]
            headers = {'Content-Type': content_type, 'Accept': '*/*' if binary else 'application/json'}
            if key:
                if protocol == 'anthropic':
                    headers.update({'x-api-key': key, 'anthropic-version': '2023-06-01'})
                elif protocol == 'gemini':
                    headers['x-goog-api-key'] = key
                elif protocol == 'azure':
                    headers['api-key'] = key
                else:
                    headers['Authorization'] = 'Bearer ' + key
            headers.update({name: resolve_key(value) if value.startswith('$') else value
                            for name, value in config.get('custom_headers', {}).items()})
            secrets = keys + [value for name, value in headers.items()
                              if any(word in name.lower() for word in ('key', 'authorization', 'token'))]

            def send():
                request = urllib.request.Request(url, data=raw_data if raw_data is not None else json.dumps(body, ensure_ascii=False).encode('utf-8')
                                                 if body is not None else None, headers=headers)
                proxy = config.get('proxy', '')
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({'http': proxy, 'https': proxy})) if proxy else None
                try:
                    response = opener.open(request, timeout=config.get('timeout', 120)) if opener else urllib.request.urlopen(request, timeout=config.get('timeout', 120))
                    with response:
                        if binary:
                            return response.read()
                        if stream:
                            from .model_protocols import read_stream
                            return read_stream(response, protocol)
                        data = json.load(response)
                        if isinstance(data, dict) and data.get('error'):
                            detail = data['error']
                            detail = detail.get('message', str(detail)) if isinstance(detail, dict) else str(detail)
                            raise ModelError(safe_text(detail, secrets), status=200, endpoint=safe_url(url))
                        return data
                except urllib.error.HTTPError as error:
                    raw = safe_text(error.read().decode('utf-8', 'replace'), secrets)
                    try:
                        payload = json.loads(raw)
                        detail = payload.get('error', payload)
                        detail = detail.get('message', json.dumps(detail, ensure_ascii=False)) if isinstance(detail, dict) else str(detail)
                    except json.JSONDecodeError:
                        detail = raw
                    labels = {400: '请求格式或参数不被接口接受', 401: '密钥无效或已过期',
                              403: '密钥没有访问权限', 404: '接口路径或模型不存在',
                              410: '接口或模型已下线', 429: '额度不足或请求过于频繁'}
                    title = labels.get(error.code, '模型服务返回错误')
                    raise ModelError(f'{title}（HTTP {error.code}）：{detail[:1200]}',
                                     status=error.code, detail=detail[:6000], endpoint=safe_url(url)) from None
                except (TimeoutError, socket.timeout):
                    raise ModelError('请求超时，请检查接口、代理或提高超时设置', endpoint=safe_url(url)) from None
                except urllib.error.URLError as error:
                    raise ModelError('无法连接模型接口：' + safe_text(str(error.reason), secrets),
                                     endpoint=safe_url(url)) from None
                except json.JSONDecodeError:
                    raise ModelError('接口没有返回有效 JSON，请检查接口类型与地址', status=200, endpoint=safe_url(url)) from None
                except ModelError as error:
                    raise ModelError(safe_text(str(error), secrets), status=error.status or 200,
                                     detail=safe_text(error.detail, secrets), endpoint=error.endpoint or safe_url(url)) from None
            try:
                return await asyncio.to_thread(send)
            except ModelError as error:
                if attempt == retries or (error.status is not None and error.status not in (401, 429) and error.status < 500):
                    raise
                await asyncio.sleep(min(2 ** attempt, 5))
