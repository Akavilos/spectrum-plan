"""提供商与模型的配置边界；旧模型 ID 保持不变，来源配置可复用。"""
import copy
from urllib.parse import urlsplit

PROTOCOLS = {'openai': 'OpenAI 兼容 / 反代', 'responses': 'OpenAI Responses',
             'anthropic': 'Claude 原生', 'gemini': 'Gemini 原生', 'ollama': 'Ollama', 'azure': 'Azure OpenAI'}
CATEGORIES = {'chat': '对话', 'embedding': '嵌入向量', 'stt': '语音识别', 'tts': '语音合成', 'rerank': '重排序'}
TEMPLATES = [
    {'id': 'openai', 'name': 'OpenAI / 兼容反代', 'protocol': 'openai', 'base_url': 'https://api.openai.com/v1'},
    {'id': 'responses', 'name': 'OpenAI Responses', 'protocol': 'responses', 'base_url': 'https://api.openai.com/v1'},
    {'id': 'claude', 'name': 'Anthropic Claude', 'protocol': 'anthropic', 'base_url': 'https://api.anthropic.com'},
    {'id': 'gemini', 'name': 'Google Gemini', 'protocol': 'gemini', 'base_url': 'https://generativelanguage.googleapis.com'},
    {'id': 'ark', 'name': '火山方舟', 'protocol': 'openai', 'base_url': 'https://ark.cn-beijing.volces.com/api/v3'},
    {'id': 'ark-coding', 'name': '火山方舟 Coding Plan', 'protocol': 'openai', 'base_url': 'https://ark.cn-beijing.volces.com/api/coding/v3'},
    {'id': 'deepseek', 'name': 'DeepSeek', 'protocol': 'openai', 'base_url': 'https://api.deepseek.com'},
    {'id': 'siliconflow', 'name': '硅基流动', 'protocol': 'openai', 'base_url': 'https://api.siliconflow.cn/v1'},
    {'id': 'dashscope', 'name': '阿里云百炼 / 通义', 'protocol': 'openai', 'base_url': 'https://dashscope.aliyuncs.com/compatible-mode/v1'},
    {'id': 'moonshot', 'name': 'Moonshot / Kimi', 'protocol': 'openai', 'base_url': 'https://api.moonshot.cn/v1'},
    {'id': 'zhipu', 'name': '智谱 GLM', 'protocol': 'openai', 'base_url': 'https://open.bigmodel.cn/api/paas/v4'},
    {'id': 'minimax', 'name': 'MiniMax', 'protocol': 'openai', 'base_url': 'https://api.minimax.io/v1'},
    {'id': 'openrouter', 'name': 'OpenRouter', 'protocol': 'openai', 'base_url': 'https://openrouter.ai/api/v1'},
    {'id': 'groq', 'name': 'Groq', 'protocol': 'openai', 'base_url': 'https://api.groq.com/openai/v1'},
    {'id': 'mistral', 'name': 'Mistral', 'protocol': 'openai', 'base_url': 'https://api.mistral.ai/v1'},
    {'id': 'together', 'name': 'Together AI', 'protocol': 'openai', 'base_url': 'https://api.together.xyz/v1'},
    {'id': 'xai', 'name': 'xAI / Grok', 'protocol': 'openai', 'base_url': 'https://api.x.ai/v1'},
    {'id': 'ollama', 'name': 'Ollama 本地服务', 'protocol': 'ollama', 'base_url': 'http://127.0.0.1:11434', 'allow_no_key': True},
    {'id': 'local', 'name': 'LM Studio / vLLM / 自建兼容服务', 'protocol': 'openai', 'base_url': 'http://127.0.0.1:1234/v1', 'allow_no_key': True},
    {'id': 'azure', 'name': 'Azure OpenAI', 'protocol': 'azure', 'base_url': ''},
    {'id': 'custom', 'name': '自定义 / 中转反代', 'protocol': 'openai', 'base_url': ''},
]
SOURCE_FIELDS = {'name', 'protocol', 'base_url', 'api_key', 'api_keys', 'enabled', 'allow_no_key',
                 'timeout', 'proxy', 'custom_headers', 'retry_count', 'source_parameters', 'api_version'}
MODEL_FIELDS = {'source_id', 'model', 'name', 'enabled', 'category', 'temperature', 'max_tokens',
                'context_window', 'modalities', 'parameters', 'family', 'stream', 'prompt_cache'}


def validate_source(value):
    if not isinstance(value, dict) or set(value) - SOURCE_FIELDS:
        raise ValueError('提供商包含未知设置')
    for key in ('name', 'base_url', 'api_key', 'proxy', 'api_version'):
        if key in value and not isinstance(value[key], str): raise ValueError(key + ' 必须为文字')
    if value.get('protocol', 'openai') not in PROTOCOLS: raise ValueError('请选择支持的接口格式')
    for key in ('enabled', 'allow_no_key'):
        if key in value and not isinstance(value[key], bool): raise ValueError('开关必须为布尔值')
    for key in ('base_url', 'proxy'):
        if value.get(key):
            p = urlsplit(value[key])
            if p.scheme not in ('http', 'https') or not p.hostname: raise ValueError('接口和代理地址使用完整 http 或 https URL')
            if p.username or p.password: raise ValueError('认证请填写密钥或请求头，不放在地址中')
    keys = value.get('api_keys', [])
    if not isinstance(keys, list) or any(not isinstance(k, str) or not k.strip() for k in keys): raise ValueError('每行填写一枚密钥，或 $环境变量名')
    if not isinstance(value.get('timeout', 120), (int, float)) or isinstance(value.get('timeout'), bool) or not 1 <= value.get('timeout', 120) <= 3600: raise ValueError('超时设置为 1 到 3600 秒')
    if type(value.get('retry_count', 0)) is not int or not 0 <= value.get('retry_count', 0) <= 5: raise ValueError('重试次数设置为 0 到 5')
    headers = value.get('custom_headers', {})
    if not isinstance(headers, dict) or any(not isinstance(k, str) or not isinstance(v, str) or '\n' in k + v or '\r' in k + v for k, v in headers.items()): raise ValueError('请求头填写字符串 JSON 对象，不能包含换行')
    if not isinstance(value.get('source_parameters', {}), dict): raise ValueError('附加请求参数填写 JSON 对象')


def validate_model(value):
    if not isinstance(value, dict) or set(value) - (MODEL_FIELDS | SOURCE_FIELDS): raise ValueError('模型包含未知设置')
    validate_source({k: v for k, v in value.items() if k in SOURCE_FIELDS})
    for k in ('model', 'source_id', 'name'):
        if k in value and not isinstance(value[k], str): raise ValueError('模型名称和来源必须为文字')
    if value.get('category', 'chat') not in CATEGORIES: raise ValueError('模型用途不正确')
    if value.get('family', 'auto') not in ('auto', 'deepseek', 'gemini', 'other'): raise ValueError('角色模式类型不正确')
    if not isinstance(value.get('parameters', {}), dict): raise ValueError('请求参数需为 JSON 对象')
    t = value.get('temperature', .8)
    if t is not None and (type(t) not in (int, float) or not 0 <= t <= 2): raise ValueError('温度设置为 0 到 2；留空由接口决定')
    for k in ('max_tokens', 'context_window'):
        if value.get(k) is not None and (type(value[k]) is not int or value[k] < 1): raise ValueError(k + ' 必须为正整数或留空')
    for k in ('enabled', 'stream', 'prompt_cache'):
        if k in value and not isinstance(value[k], bool): raise ValueError('开关必须为布尔值')
    if not isinstance(value.get('modalities', []), list) or any(v not in ('image', 'audio', 'tool_use', 'reasoning') for v in value.get('modalities', [])): raise ValueError('模型能力标签不正确')


def model_entries(config):
    return {'role': config['model'], **config.get('providers', {})}


def update_legacy_model(config, target, values):
    """旧保存接口继续更新原来源；避免留下遮盖来源密钥的第二份接口配置。"""
    values = dict(values)
    if not values.get('api_key'): values.pop('api_key', None)
    source = config.get('provider_sources', {}).get(target.get('source_id'))
    if source is not None:
        for key in ('base_url', 'api_key', 'api_keys', 'protocol', 'timeout', 'proxy', 'custom_headers', 'retry_count', 'source_parameters', 'api_version'):
            if key in values:
                source[key] = values.pop(key)
                if key == 'api_key': source.pop('api_keys', None)
    target.update(values)


def migrate(config):
    changed = False
    sources = config.setdefault('provider_sources', {})
    config.setdefault('embedding_provider_id', '')
    # Only populated legacy records are split; empty installs stay empty.
    for id, model in model_entries(config).items():
        if model.get('source_id') or not model.get('base_url'): continue
        source_id = 'legacy-' + id.replace('/', '-')
        if source_id in sources: continue
        label, enabled = model.get('name', ''), model.get('enabled', True)
        source = {k: copy.deepcopy(v) for k, v in model.items() if k in SOURCE_FIELDS}
        source.update(name='原有接口 · ' + id, protocol=source.get('protocol', 'openai'), enabled=enabled)
        sources[source_id] = source
        changed = True
        for k in SOURCE_FIELDS: model.pop(k, None)
        model.update(source_id=source_id, enabled=enabled, category=model.get('category', 'chat'))
        if label: model['name'] = label
    embedding = config.get('embedding', {})
    if embedding.get('base_url') and embedding.get('model') and not embedding.get('catalog_migrated'):
        source_id = 'legacy-embedding-source'
        sources[source_id] = {k: copy.deepcopy(v) for k, v in embedding.items() if k in SOURCE_FIELDS}
        sources[source_id].update(name='原有嵌入接口', protocol=embedding.get('protocol', 'openai'), enabled=True, allow_no_key=not bool(embedding.get('api_key')))
        config['providers']['legacy-embedding'] = {'source_id': source_id, 'category': 'embedding',
            'model': embedding['model'], 'name': '原有嵌入模型', 'enabled': embedding.get('enabled', False)}
        if embedding.get('enabled') and not config.get('embedding_provider_id'):
            config['embedding_provider_id'] = 'legacy-embedding'
        embedding.update(enabled=False, catalog_migrated=True)
        changed = True
    return changed


def redacted_source(source):
    from .data_exchange import public_config
    view = public_config(source)
    view.update(api_key='', api_keys=[], has_key=bool(source.get('api_key') or source.get('api_keys')),
                key_count=len(source.get('api_keys') or ([source['api_key']] if source.get('api_key') else [])))
    # Header names remain editable; values with credentials are never exposed in list views.
    view['custom_headers'] = {k: ('' if k not in public_config({k: v}) else v) for k, v in source.get('custom_headers', {}).items()}
    return view
