"""公开模型协议适配；业务消息只在这里转换，不改写框架或供应商内部方法。"""
import json
from dataclasses import dataclass, field
from urllib.parse import urlparse
import asyncio
import urllib.request
from .model_http import ModelError


@dataclass
class ModelResponse:
    completion_text: str
    role: str = "assistant"
    finish_reason: str = "stop"
    tool_calls: list = field(default_factory=list)
    native_message: dict = field(default_factory=dict)
    reasoning_content: str = ""
    input_messages: list = field(default_factory=list, repr=False)
    usage: dict = field(default_factory=dict)
    used_provider: object = field(default=None, repr=False)
    role_mode: str = ''


class Provider:
    managed = True
    def __init__(self, config, sources=None, *, fallback_selector=None):
        self._config = config
        self.sources = sources if sources is not None else {}
        from .model_http import ModelHTTP
        self.http = ModelHTTP()
        self.role_config = lambda key, default=None: default
        self.fallback_selector = fallback_selector

    @property
    def config(self):
        source_id = self._config.get('source_id')
        if not source_id:
            return self._config
        source = self.sources.get(source_id, {})
        return {**source, **self._config, 'enabled': source.get('enabled', True) and self._config.get('enabled', True)}

    @config.setter
    def config(self, value):
        self._config = value

    @property
    def ready(self):
        c = self.config
        return bool(c.get('enabled', True) and c.get("base_url") and c.get("model") and
                    (c.get("api_key") or c.get('api_keys') or c.get('allow_no_key') or c.get('protocol') == 'ollama'))

    @property
    def unavailable_reason(self):
        if self.ready:
            return ""
        source_id = self._config.get('source_id')
        if source_id and source_id not in self.sources:
            return "提供商连接不存在"
        if source_id and not self.sources[source_id].get('enabled', True):
            return "提供商连接已关闭"
        if not self._config.get('enabled', True):
            return "模型已关闭"
        config = self.config
        if not config.get('base_url'):
            return "未填写 API 地址"
        if not config.get('model'):
            return "未填写模型名称"
        return "未配置 API 密钥"

    def role_mode(self, override=""):
        from .possession import mode
        if self.config.get('protocol', 'openai') not in ('openai', 'azure'):
            return 'contract'
        return mode(self.config, self.role_config, override)

    async def text_chat(self, **kwargs):
        from .model_fallback import invoke
        return await invoke(self, '_text_chat', kwargs)

    async def _text_chat(self, *, prompt, system_prompt="", contexts=None,
                        extra_user_content_parts=None, func_tool=None, **kwargs):
        if not self.ready:
            raise ValueError("请先在管理页面配置模型地址、名称和密钥")
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.extend(dict(m) for m in (contexts or []))
        background = "\n\n".join(p.text for p in extra_user_content_parts or [])
        origin = kwargs.get('turn_origin', 'utility')
        if kwargs.get('continue_tools', False):
            current = '\n\n'.join(x for x in (background, prompt) if x)
        elif origin == 'background':
            if background:
                messages.append({'role': 'assistant', 'content': '【我本轮读取的背景资料，不是对方新发来的消息】\n' + background})
            current = '【我这一轮的后台运行事件，不是聊天来信】\n' + prompt
        elif origin == 'incoming':
            current = '\n\n'.join(x for x in (
                '【我本轮读取的背景资料】\n' + background if background else '',
                '【我刚收到的实际消息】\n' + prompt) if x)
        else:
            current = "\n\n".join(x for x in (background, prompt) if x)
        if current:
            messages.append({"role": "user", "content": ([{"type": "text", "text": current}] + kwargs.get("media", [])) if kwargs.get("media") else current})
        selected = kwargs.get("role_mode") or self.role_mode()
        role_turn = "<content_output>" in system_prompt
        if kwargs.get("media"):
            selected = "contract"
        if role_turn and not kwargs.get("continue_tools", False):
            from .possession import apply
            apply(messages, selected)
        config = self.config
        protocol = config.get('protocol', 'openai')
        body = {"model": kwargs.get("model") or config["model"], "messages": messages}
        temperature = kwargs.get('temperature', config.get('temperature', .8))
        if temperature is not None:
            body['temperature'] = temperature
        maximum = kwargs.get('max_tokens', config.get('max_tokens'))
        if maximum is not None:
            body['max_tokens'] = maximum
        if func_tool and func_tool.tools:
            body["tools"] = [{"type": "function", "function": {
                "name": t.name, "description": t.description, "parameters": t.parameters}}
                for t in func_tool.tools if t.active]
        if role_turn and selected == "deepseek":
            # Text tools preserve the assistant-prefix turn instead of mixing
            # supplier function calls with prefix completion.
            body.pop("tools", None)
            body.pop("tool_choice", None)
        if kwargs.get("continue_tools", False) and body.get("tools"):
            # 保留原工具定义以复用前缀；公开协议明确要求本次完成回复、不再调用工具。
            body["tool_choice"] = "none"
        from .model_protocols import endpoint, encode, decode
        parameters = {**config.get('source_parameters', {}), **config.get('parameters', {}), **kwargs.get('extra_body', {})}
        stream = bool(parameters.get('stream', config.get('stream', False)))
        if stream:
            parameters['stream'] = True
        url = endpoint(config['base_url'], protocol, body['model'], stream=stream,
                       api_version=config.get('api_version', '2024-10-21'))
        if urlparse(url).scheme not in ("https", "http"):
            raise ValueError("模型地址必须使用 http 或 https")
        wire_body = await asyncio.to_thread(encode, protocol, body, parameters, config)
        image_count=sum(part.get('type')=='image_url' for message in messages
                        for part in (message.get('content') if isinstance(message.get('content'),list) else []))
        if image_count:
            from .ports import logger
            logger.info('[模型图片输入] %s | %s | %s 张',body['model'],protocol,image_count)
        result = await self.http.json(config, url, wire_body, protocol=protocol, stream=stream)
        message, finish, usage = decode(protocol, result)
        from .ports import logger
        cached = usage.get("prompt_cache_hit_tokens", (usage.get("prompt_tokens_details") or {}).get("cached_tokens"))
        logger.info("[模型用量] 输入 %s | 输出 %s | 缓存命中 %s | 缓存未命中 %s",
                    usage.get("prompt_tokens", "未提供"), usage.get("completion_tokens", "未提供"),
                    cached if cached is not None else "未提供", usage.get("prompt_cache_miss_tokens", "未提供"))
        reasoning_tokens = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
        logger.info("[模型响应] %s | 结束原因 %s | 思考 token %s | 请求输出上限 %s",
                    body['model'], finish, reasoning_tokens if reasoning_tokens is not None else "未提供",
                    body.get('max_tokens', '沿用模型服务默认'))
        content = message.get("content") or ""
        if isinstance(content, list):
            content = ''.join(part.get('text', '') for part in content if isinstance(part, dict))
        if not content.strip() and not message.get('tool_calls'):
            reasoning_chars = len(message.get('reasoning_content') or '')
            detail = {'finish_reason': finish, 'reasoning_tokens': reasoning_tokens,
                      'reasoning_chars': reasoning_chars, 'requested_max_tokens': body.get('max_tokens')}
            title = '模型输出达到长度上限，但尚未生成回复正文' if finish == 'length' else '模型没有返回回复文本或工具调用'
            raise ModelError(title + f'（结束原因 {finish}，思考 token {reasoning_tokens if reasoning_tokens is not None else "未提供"}）',
                             detail=json.dumps(detail, ensure_ascii=False))
        if role_turn and selected == "deepseek" and "</thinking>" in content:
            content = "<think>" + content.replace("</thinking>", "</think>", 1)
        return ModelResponse(content, native_message=message,
                             reasoning_content=message.get("reasoning_content") or "", finish_reason=finish,
                             tool_calls=message.get("tool_calls") or [], input_messages=messages, usage=usage,
                             used_provider=self, role_mode=selected)

    async def close(self):
        pass


class EmbeddingProvider(Provider):
    """可选 OpenAI 兼容嵌入接口；未启用时宿主不提供此实例。"""
    @property
    def ready(self):
        return bool(self.config.get("enabled", True) and self.config.get("base_url") and self.config.get("model"))

    async def get_embedding(self, text, *, optional=False):
        return (await self.get_embeddings([text], optional=optional))[0]

    async def get_embeddings(self, texts, *, optional=False):
        if not self.ready:
            raise ValueError("嵌入模型尚未配置或启用")
        from .spectrum_module.spectrum import freeze, limiter
        epoch = freeze.checkpoint()
        if not freeze.may_commit(epoch):
            raise freeze.FrozenSkipped("系统暂停，未请求嵌入")
        snapshot = dict(self.config)
        from .model_fallback import invoke
        result = await invoke(self, '_get_embeddings', {'texts': texts}, failure_policy='optional' if optional else 'required')
        if not freeze.may_commit(epoch):
            raise freeze.FrozenSkipped("嵌入结果跨过系统暂停")
        if any(snapshot.get(k) != self.config.get(k) for k in ("base_url", "model")):
            raise ValueError("嵌入配置已改变，本批结果作废")
        return result

    async def _get_embeddings(self, texts):
        from .model_protocols import endpoint
        from .model_http import ModelError
        c = self.config
        protocol = c.get('protocol', 'openai')
        base = endpoint(c['base_url'], protocol, listing=True).split('?')[0]
        params = {**c.get('source_parameters', {}), **c.get('parameters', {})}
        if protocol == 'gemini':
            url = base + '/' + c['model'].removeprefix('models/') + ':batchEmbedContents'
            body = {'requests': [{**params, 'model': 'models/' + c['model'].removeprefix('models/'), 'content': {'parts': [{'text': t}]}} for t in texts]}
            data = await self.http.json(c, url, body, protocol=protocol)
            result = [item['values'] for item in data.get('embeddings', [])]
        elif protocol == 'ollama':
            url = base.removesuffix('/api/tags') + '/api/embed'
            data = await self.http.json(c, url, {**params, 'model': c['model'], 'input': texts}, protocol=protocol)
            result = data.get('embeddings', [])
        else:
            url = (endpoint(c['base_url'], 'azure', c['model'], api_version=c.get('api_version', '2024-10-21')).replace('/chat/completions', '/embeddings')
                   if protocol == 'azure' else base.removesuffix('/models') + '/embeddings')
            data = await self.http.json(c, url, {**params, 'model': c['model'], 'input': texts}, protocol=protocol)
            ordered = sorted(data.get('data', []), key=lambda item: item['index'])
            if [item['index'] for item in ordered] != list(range(len(texts))):
                raise ModelError('嵌入结果数量或编号不匹配')
            result = [item['embedding'] for item in ordered]
        if len(result) != len(texts) or not result or any(not v or len(v) != len(result[0]) for v in result):
            raise ModelError('嵌入结果数量或维度不匹配')
        return result
