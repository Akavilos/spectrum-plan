"""一次业务调用最多使用主模型和一个备用模型；冻结只观察最终结果。"""
import copy
import json
from types import SimpleNamespace
from .model_http import ModelError
from .ports import logger


class ModelFallbackError(ModelError):
    def __init__(self, primary, backup):
        self.primary_error, self.backup_error = primary, backup
        super().__init__('主模型与备用模型均调用失败；主模型：' + str(primary) + '；备用模型：' + str(backup),
                         status=getattr(backup, 'status', None))


def canonical_history(messages):
    """换模型时仅携带原文与工具结果，不转交原供应商的签名或思考报文。"""
    result = []
    for message in messages or []:
        role = message['role']
        content = copy.deepcopy(message.get('content') or '')
        if role == 'tool':
            result.append({'role': 'user', 'content': '【我这轮收到的工具返回结果】\n' + str(content)})
            continue
        calls = message.get('tool_calls') or []
        if calls:
            record = '【我这轮已有的工具调用记录】\n' + json.dumps([c['function'] for c in calls], ensure_ascii=False)
            content = content + '\n' + record if isinstance(content, str) else content + [{'type': 'text', 'text': record}]
        result.append({'role': role, 'content': content})
    return result


async def invoke(provider, operation, kwargs, *, failure_policy='required'):
    from .spectrum_module.spectrum import freeze, limiter

    async def attempts(**arguments):
        epoch = freeze.checkpoint()
        async def request(target, values):
            if not target.ready:
                raise ModelError('模型不可用：' + target.unavailable_reason)
            response = await getattr(target, operation)(**values)
            if getattr(response, 'role', '') == 'err':
                raise ModelError('供应商返回模型错误结果')
            return response
        try:
            return await request(provider, arguments)
        except (freeze.FrozenSkipped, limiter.LowFreqSkipped):
            raise
        except Exception as primary_error:
            backup = provider.fallback_selector(provider) if provider.fallback_selector else None
            if backup is None:
                raise
            if not freeze.may_commit(epoch):
                raise freeze.FrozenSkipped('系统暂停，不切换备用模型') from None
            logger.warning('[模型回退] %s → %s | %s', provider.config.get('model'), backup.config.get('model'), primary_error)
            await limiter.acquire()
            if not freeze.may_commit(epoch):
                raise freeze.FrozenSkipped('排队期间系统暂停，不请求备用模型')
            retry = dict(arguments)
            if operation == '_text_chat':
                retry.pop('model', None)
                retry['role_mode'] = 'contract'
                retry['contexts'] = canonical_history(retry.get('contexts'))
                if retry.get('continue_tools'):
                    retry['func_tool'] = None
            try:
                response = await request(backup, retry)
            except (freeze.FrozenSkipped, limiter.LowFreqSkipped):
                raise
            except Exception as backup_error:
                raise ModelFallbackError(primary_error, backup_error) from backup_error
            logger.info('[模型回退成功] %s', backup.config.get('model'))
            return response

    return await freeze.call_model(SimpleNamespace(text_chat=attempts), failure_policy=failure_policy, **kwargs)


def validate_choices(config, choices):
    from .model_catalog import CATEGORIES, model_entries
    if not isinstance(choices, dict) or set(choices) - set(CATEGORIES):
        raise ValueError('备用设置按模型用途填写')
    models = model_entries(config)
    for category, id in choices.items():
        if not isinstance(id, str): raise ValueError('备用模型使用配置 ID')
        if not id: continue
        if id not in models or models[id].get('category', 'chat') != category:
            raise ValueError('备用模型与用途不匹配：' + category)
        if category == 'chat' and id == (config.get('chat_provider_id') or 'role'):
            raise ValueError('对话备用模型不能与主对话模型相同')
        if category == 'embedding':
            if id == config.get('embedding_provider_id'):
                raise ValueError('嵌入备用模型不能与主嵌入模型相同')
            primary = models.get(config.get('embedding_provider_id'))
            if primary is None: raise ValueError('请先选择主嵌入模型')
            effective = lambda item: {**config.get('provider_sources', {}).get(item.get('source_id'), {}), **item}
            if embedding_identity(effective(primary)) != embedding_identity(effective(models[id])):
                raise ValueError('嵌入备用须使用相同模型名称与参数，不能混用向量空间')


def embedding_identity(config):
    return config.get('model'), {**config.get('source_parameters', {}), **config.get('parameters', {})}
