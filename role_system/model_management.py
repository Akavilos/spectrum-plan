"""模型管理公开接口：提供商、列表、模型、用途和显式连接测试。"""
import asyncio
import copy
import json
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from fastapi import Depends, HTTPException, Request, Query
from fastapi.responses import Response
from .model_catalog import PROTOCOLS, CATEGORIES, TEMPLATES, SOURCE_FIELDS, MODEL_FIELDS, model_entries, migrate, redacted_source, validate_source, validate_model
from .model_http import ModelHTTP, ModelError, safe_text
from .model_protocols import endpoint
from .data_exchange import public_config, merge_preserving_secrets


def usages(host, system, id):
    from .web import provider_choices
    default = host.config.get('chat_provider_id') or 'role'
    labels = []
    if id == default: labels.append('主对话')
    if id == host.config.get('embedding_provider_id'): labels.append('默认嵌入')
    for category, backup in host.config['fallback_models'].items():
        if id == backup: labels.append(CATEGORIES[category] + '备用')
    for label, values in {**system.configs, '人格整理': system.personality.config.values, '主动回忆语义过滤':system.active_memory.settings, '语音表达':system.speech.settings}.items():
        if id in provider_choices(values): labels.append(label)
    return labels


async def list_models(source):
    protocol = source.get('protocol', 'openai')
    url = endpoint(source['base_url'], protocol, listing=True, api_version=source.get('api_version', '2024-10-21'))
    http, rows, pages = ModelHTTP(), [], set()
    while url not in pages:
        pages.add(url)
        data = await http.json(source, url, protocol=protocol)
        if data.get('error'): raise ModelError(str(data['error']))
        items = data.get('models', []) if protocol in ('gemini', 'ollama') else data.get('data', [])
        for item in items:
            id = item.get('id') or item.get('name', '').removeprefix('models/')
            if not id: continue
            methods = item.get('supportedGenerationMethods', [])
            categories = ['embedding'] if ('embedContent' in methods or 'embedding' in id.lower() or 'embed-' in id.lower()) else ['chat']
            if 'rerank' in id.lower(): categories = ['rerank']
            if 'whisper' in id.lower() or 'transcrib' in id.lower(): categories = ['stt']
            if 'tts' in id.lower(): categories = ['tts']
            rows.append({'model': id, 'name': item.get('displayName') or id,
                         'context_window': item.get('inputTokenLimit') or item.get('context_window'),
                         'max_tokens': item.get('outputTokenLimit'), 'categories': categories,
                         'metadata': public_config(item)})
        cursor = data.get('nextPageToken') if protocol == 'gemini' else data.get('last_id') if data.get('has_more') else None
        if not cursor: break
        p = urlsplit(url); q = dict(parse_qsl(p.query)); q['pageToken' if protocol == 'gemini' else 'after_id'] = cursor
        url = urlunsplit((p.scheme, p.netloc, p.path, urlencode(q), ''))
    if not rows: raise ModelError('接口未返回模型列表；可手动添加模型。部分 Coding Plan 和反代不提供列表接口。')
    return rows


def install(app, host, system, authorize):
    dependencies = [Depends(authorize)]
    lists, tests = {}, {}

    def source(id):
        if id not in host.config['provider_sources']: raise HTTPException(404, '提供商不存在')
        return host.config['provider_sources'][id]

    def model(id):
        entries = model_entries(host.config)
        if id not in entries: raise HTTPException(404, '模型不存在')
        return entries[id]

    def commit():
        before = host.get_all_embedding_providers()
        old = dict(before[0].config) if before else {}
        host.config.save_config(); host.refresh_providers()
        after = host.get_all_embedding_providers()
        new = dict(after[0].config) if after else {}
        if any(old.get(k) != new.get(k) for k in ('base_url', 'model')):
            for sidecar in system.spectrum.store._vec_sidecars.values(): sidecar.clear()
            from .web import restart_vectors
            restart_vectors(system)

    def verify(value, validator):
        try: validator(value)
        except ValueError as e: raise HTTPException(422, str(e)) from None

    @app.get('/api/model-management', dependencies=dependencies)
    async def overview():
        # Old API clients can still create flat records; expose them through the same source panel.
        if migrate(host.config): commit()
        rows = []
        for id, value in model_entries(host.config).items():
            if not value.get('model'): continue
            p = host.provider if id == 'role' else host.providers[id]
            rows.append({'id': id, **public_config(value), 'ready': p.ready, 'unavailable_reason': p.unavailable_reason, 'uses': usages(host, system, id), 'test': tests.get(id)})
        return {'sources': {id: redacted_source(v) for id, v in host.config['provider_sources'].items()},
                'models': rows, 'templates': TEMPLATES, 'protocols': PROTOCOLS, 'categories': CATEGORIES,
                'chat_provider_id': host.config.get('chat_provider_id') or 'role',
                'embedding_provider_id': host.config.get('embedding_provider_id', ''), 'lists': lists,
                'fallback_models': host.config['fallback_models'],
                'freeze_enabled': bool(system.spectrum._cfg('freeze_enabled', False)),
                'freeze_threshold': system.spectrum._cfg('freeze_fail_streak', 3)}

    @app.post('/api/models/fallback', dependencies=dependencies)
    async def save_fallback(request: Request):
        from .model_fallback import validate_choices
        body = await request.json()
        choices = body.get('fallback_models', {})
        verify(choices, lambda values: validate_choices(host.config, values))
        if 'freeze_enabled' in body and not isinstance(body['freeze_enabled'], bool):
            raise HTTPException(422, '异常冻结开关必须为布尔值')
        async with system.lock:
            for id in choices.values():
                if id:
                    p = host.provider if id == 'role' else host.providers[id]
                    if not p.ready: raise HTTPException(422, '备用模型不可用：' + p.unavailable_reason)
            host.config['fallback_models'] = {k: v for k, v in choices.items() if v}
            if 'freeze_enabled' in body:
                cfg = system.configs['spectrum']
                cfg['stability']['freeze_enabled'] = body['freeze_enabled']
                cfg.save_config()
            host.config.save_config()
        return {'ok': True}

    @app.post('/api/model-sources/save', dependencies=dependencies)
    async def save_source(request: Request):
        body = await request.json(); id, value = body.get('id', ''), body.get('source', {})
        if not isinstance(id, str) or not id.strip() or '/' in id or len(id) > 80: raise HTTPException(422, '提供商 ID 使用 1 到 80 个字符，不能包含斜线')
        verify(value, validate_source)
        async with system.lock:
            previous = host.config['provider_sources'].get(id, {})
            address_changed = previous.get('base_url') != value.get('base_url', previous.get('base_url'))
            merged = {**previous, **value}
            if not value.get('api_keys') and not value.get('api_key') and not body.get('clear_keys'):
                merged['api_keys'] = previous.get('api_keys', [])
                merged['api_key'] = previous.get('api_key', '')
            elif 'api_keys' in value:
                merged.pop('api_key', None)
            # Empty masked header fields preserve saved values; delete a header by removing its entry.
            merged['custom_headers'] = {k: v or previous.get('custom_headers', {}).get(k, '') for k, v in value.get('custom_headers', previous.get('custom_headers', {})).items()}
            if body.get('clear_keys'): merged.update(api_key='', api_keys=[])
            verify(merged, validate_source)
            host.config['provider_sources'][id] = merged; lists.pop(id, None); commit()
            selected = host.config.get('embedding_provider_id')
            if address_changed and selected and host.config['providers'].get(selected, {}).get('source_id') == id:
                for sidecar in system.spectrum.store._vec_sidecars.values(): sidecar.clear()
                from .web import restart_vectors
                restart_vectors(system)
        return {'ok': True}

    @app.post('/api/model-sources/keys', dependencies=dependencies)
    async def source_keys(request: Request):
        s = source((await request.json()).get('id'))
        return Response(json.dumps({'api_keys': s.get('api_keys') or ([s['api_key']] if s.get('api_key') else [])}), media_type='application/json', headers={'Cache-Control': 'no-store'})

    @app.post('/api/model-sources/list', dependencies=dependencies)
    async def fetch_models(request: Request):
        id = (await request.json()).get('id'); s = copy.deepcopy(source(id))
        try:
            rows = await list_models(s); lists[id] = rows
            return {'ok': True, 'models': rows}
        except ModelError as e: return e.as_dict()

    @app.post('/api/model-sources/delete', dependencies=dependencies)
    async def delete_source(request: Request):
        id = (await request.json()).get('id'); source(id)
        async with system.lock:
            ids = [key for key, value in model_entries(host.config).items() if value.get('source_id') == id]
            used = {key: usages(host, system, key) for key in ids if usages(host, system, key)}
            if used: raise HTTPException(409, '请先更换正在使用的模型：' + json.dumps(used, ensure_ascii=False))
            for key in ids:
                if key == 'role': host.config['model'] = {'base_url': '', 'model': '', 'api_key': ''}
                else: del host.config['providers'][key]
                tests.pop(key, None)
            del host.config['provider_sources'][id]; lists.pop(id, None); commit()
        return {'ok': True, 'deleted_models': ids}

    @app.post('/api/models/save', dependencies=dependencies)
    async def save_model(request: Request):
        body = await request.json(); value = body.get('model', {}); id = body.get('id', '')
        if not isinstance(id, str) or not id or id == 'default' or len(id) > 256: raise HTTPException(422, '请填写模型配置 ID')
        verify(value, validate_model)
        source(value.get('source_id'))
        if set(value) - MODEL_FIELDS: raise HTTPException(422, '接口与密钥请在提供商设置中填写')
        if not value.get('model', '').strip(): raise HTTPException(422, '请填写服务商提供的模型名称')
        category = value.get('category', 'chat')
        protocol = source(value['source_id']).get('protocol', 'openai')
        if category == 'embedding' and protocol == 'anthropic':
            raise HTTPException(422, 'Claude 原生不提供嵌入，请选择兼容嵌入、Gemini、Ollama 或 Azure 接口')
        if category in ('tts', 'stt', 'rerank') and protocol not in ('openai', 'responses'):
            raise HTTPException(422, '此类模型目前使用 OpenAI 兼容语音或 /rerank 接口，请选择对应提供商')
        async with system.lock:
            previous = model_entries(host.config).get(id, {})
            if usages(host, system, id) and previous.get('category', 'chat') != value.get('category', 'chat'): raise HTTPException(409, '模型正在使用，请先更换用途选择再改变模型类别')
            if id == 'role': host.config['model'] = value
            else: host.config['providers'][id] = value
            tests.pop(id, None); commit()
        return {'ok': True}

    @app.post('/api/models/delete', dependencies=dependencies)
    async def delete_model(request: Request):
        id = (await request.json()).get('id'); model(id)
        async with system.lock:
            used = usages(host, system, id)
            if used: raise HTTPException(409, '请先更换模型选择：' + '、'.join(used))
            if id == 'role': host.config['model'] = {'base_url': '', 'model': '', 'api_key': ''}
            else: del host.config['providers'][id]
            tests.pop(id, None); commit()
        return {'ok': True}

    @app.post('/api/models/default', dependencies=dependencies)
    async def set_default(request: Request):
        body = await request.json(); id = body.get('id', ''); category = body.get('category', 'chat')
        if category not in ('chat', 'embedding'): raise HTTPException(422, '默认用途仅支持对话和嵌入；其他模型供扩展插件选择调用')
        if id:
            m = model(id)
            if m.get('category', 'chat') != category: raise HTTPException(422, '模型类别与用途不匹配')
            p = host.provider if id == 'role' else host.providers[id]
            if not p.ready: raise HTTPException(422, '无法设为默认模型：' + p.unavailable_reason)
            if host.config['fallback_models'].get(category) == id:
                raise HTTPException(422, '请先更换备用模型，主模型与备用模型不能相同')
        elif category == 'chat': raise HTTPException(422, '请选择对话模型')
        async with system.lock:
            before = host.get_all_embedding_providers()
            before_config = dict(before[0].config) if before else {}
            host.config['chat_provider_id' if category == 'chat' else 'embedding_provider_id'] = id
            if category == 'embedding' and not id:
                host.config['embedding']['enabled'] = False
            commit()
            if category == 'embedding':
                after = host.get_all_embedding_providers()
                after_config = after[0].config if after else {}
                if any(before_config.get(k) != after_config.get(k) for k in ('model', 'base_url')):
                    for sidecar in system.spectrum.store._vec_sidecars.values(): sidecar.clear()
                from .web import restart_vectors
                restart_vectors(system)
        return {'ok': True}

    @app.post('/api/models/test', dependencies=dependencies)
    async def test_model(request: Request):
        body = await request.json(); id = body.get('id'); m = model(id)
        p = host.provider if id == 'role' else host.providers[id]
        if not p.ready: raise HTTPException(422, '无法测试模型：' + p.unavailable_reason)
        start = time.monotonic(); category = m.get('category', 'chat')
        try:
            if category == 'embedding':
                vectors = await p._get_embeddings(['我正在确认嵌入接口。'])
                result = {'ok': True, 'message': '嵌入接口可用', 'dimension': len(vectors[0])}
            elif category == 'tts':
                data = await p._text_to_speech(body.get('text') or '你好。')
                if not data: raise ModelError('语音接口返回空音频')
                import base64
                result = {'ok': True, 'message': '语音合成可用', 'audio_bytes': len(data),
                          'audio': base64.b64encode(data).decode(), 'audio_format': p.config.get('parameters', {}).get('response_format', 'mp3')}
            elif category == 'stt':
                import base64
                if not body.get('audio'): raise HTTPException(422, '语音识别测试请先选择一份音频文件')
                text = await p._speech_to_text(base64.b64decode(body['audio'], validate=True), body.get('filename', 'audio.wav'))
                result = {'ok': True, 'message': '语音识别可用', 'preview': text}
            elif category == 'rerank':
                rows = await p._rerank('我想吃什么？', ['我想吃米饭。', '今天下雨。'])
                if not rows: raise ModelError('重排序接口返回空结果')
                result = {'ok': True, 'message': '重排序接口可用', 'results': rows}
            else:
                response = await p._text_chat(prompt='我正在确认连接，只回复 OK。', max_tokens=1024)
                if not response.completion_text.strip(): raise ModelError('请求成功但没有返回回复文本；请检查模型的思考预算或输出上限')
                result = {'ok': True, 'message': '模型可用', 'preview': response.completion_text[:500], 'usage': response.usage}
        except ModelError as e: result = e.as_dict()
        except (KeyError, IndexError, TypeError, ValueError) as e:
            result = {'ok': False, 'message': '接口响应不符合所选格式：' + safe_text(str(e))}
        result['latency_ms'] = round((time.monotonic() - start) * 1000); tests[id] = result
        return result

    @app.get('/api/models/export', dependencies=dependencies)
    async def export_models():
        return {'format': 'spectrum-models-v1', 'sources': public_config(host.config['provider_sources']),
                'models': public_config(model_entries(host.config)), 'defaults': {'chat': host.config.get('chat_provider_id') or 'role', 'embedding': host.config.get('embedding_provider_id', '')},
                'fallback_models': host.config['fallback_models']}

    @app.post('/api/models/import', dependencies=dependencies)
    async def import_models(request: Request):
        body = await request.json()
        if body.get('format') != 'spectrum-models-v1' or not isinstance(body.get('sources'), dict) or not isinstance(body.get('models'), dict): raise HTTPException(422, '请导入模型页导出的 JSON 文件')
        for id, value in body['sources'].items():
            if not isinstance(id, str) or not id.strip() or '/' in id or len(id) > 80: raise HTTPException(422, '提供商 ID 无效')
            verify(value, validate_source)
        sources = merge_preserving_secrets(host.config['provider_sources'], body['sources'])
        for id, value in body['models'].items():
            verify(value, validate_model)
            if not isinstance(id, str) or not id or id == 'default' or len(id) > 256: raise HTTPException(422, '模型 ID 无效')
            if value.get('source_id') and value['source_id'] not in sources: raise HTTPException(422, '模型来源不存在')
            current = model_entries(host.config).get(id, {})
            if usages(host, system, id) and current.get('category', 'chat') != value.get('category', 'chat'): raise HTTPException(409, '导入将改变正在使用的模型类别，请先更换用途选择')
        models = {**model_entries(host.config), **body['models']}
        defaults = body.get('defaults', {})
        for category, id in defaults.items():
            if category not in ('chat', 'embedding') or (id and (id not in models or models[id].get('category', 'chat') != category)): raise HTTPException(422, '默认模型与用途不匹配')
        from .model_fallback import validate_choices
        proposed = {**host.config, 'provider_sources': sources, 'model': models.get('role', {}), 'providers': {k: v for k, v in models.items() if k != 'role'}}
        for category, id in defaults.items(): proposed['chat_provider_id' if category == 'chat' else 'embedding_provider_id'] = id
        choices = body.get('fallback_models', host.config['fallback_models'])
        verify(choices, lambda value: validate_choices(proposed, value))
        async with system.lock:
            host.config['provider_sources'] = sources
            host.config['model'] = merge_preserving_secrets(host.config['model'], models.pop('role'))
            host.config['providers'] = merge_preserving_secrets(host.config['providers'], models)
            for category, id in defaults.items(): host.config['chat_provider_id' if category == 'chat' else 'embedding_provider_id'] = id
            host.config['fallback_models'] = {k: v for k, v in choices.items() if v}
            commit()
        return {'ok': True, 'message': '模型设置已导入；导出文件不含密钥，新接口请补填密钥'}
