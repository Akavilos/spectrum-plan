"""优先选取明确配置的模型提供商，再尝试当前会话的框架提供商。"""


from __future__ import annotations

from ...ports import logger


async def pick(context, umo: str, provider_id: str = ""):
    pid = (provider_id or "").strip()

    try:
        return context.get_provider_by_id(pid) if pid else await context.get_using_provider_async(umo)
    except ValueError:
        logger.exception("[prism] 选择的模型配置不存在，请在模型管理中重新选择")
        return None
