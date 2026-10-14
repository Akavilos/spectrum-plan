"""自有接口类型。业务模块通过这些类型连接运行环境。"""
import logging
import inspect
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol
from types import SimpleNamespace

logger = logging.getLogger("role_system")


class ToolUsageError(ValueError):
    """工具参数或使用方式不符合约定，操作尚未执行。"""


async def call_tool(tool, wrapper, arguments):
    """两种模型工具协议共用参数入口，执行中的真实错误仍向上传递。"""
    if tool is None:
        raise ToolUsageError("工具未注册或未启用")
    if not isinstance(arguments, dict):
        raise ToolUsageError("工具参数需要是对象")
    try:
        inspect.signature(tool.call).bind(wrapper, **arguments)
    except TypeError as error:
        raise ToolUsageError(str(error)) from error
    return await tool.call(wrapper, **arguments)


@dataclass
class Plain:
    text: str


@dataclass
class NativeSegment:
    type: str
    data: dict
    metadata: dict = field(default_factory=dict)


def serialize_segment(part):
    if isinstance(part,Plain):return {'type':'text','data':{'text':part.text}}
    result={'type':part.type,'data':part.data}
    if part.metadata:result['metadata']=part.metadata
    return result


def deserialize_segment(part):
    return Plain(part['data']['text']) if part['type']=='text' else NativeSegment(part['type'],part['data'],part.get('metadata',{}))

@dataclass
class TextPart:
    text: str
    temporary: bool = False

    def mark_as_temp(self):
        self.temporary = True
        return self


@dataclass
class MessageChain:
    chain: list = field(default_factory=list)

    def get_plain_text(self):
        return "".join(part.text for part in self.chain if isinstance(part, Plain))


class MessageType(str, Enum):
    GROUP_MESSAGE = "GroupMessage"
    FRIEND_MESSAGE = "FriendMessage"


class Module:
    def __init__(self, context):
        self.context = context


@dataclass
class ToolContext:
    context: object
    event: object

    def __post_init__(self):
        self.context = SimpleNamespace(context=self.context, event=self.event)


@dataclass
class ModelRequest:
    prompt: str
    system_prompt: str
    contexts: list
    func_tool: object
    extra_user_content_parts: list = field(default_factory=list)
    role_mode: str = ""
    media: list = field(default_factory=list)
    turn_origin: str = "incoming"


class Event:
    def __init__(self, host, umo, user_id, name, text, *, addressed=True, message_id="", group_name="", parts=None):
        self.host = host
        self.unified_msg_origin = umo
        self.message_str = text
        self.user_id = str(user_id)
        self.name = name
        self.is_at_or_wake_command = addressed
        self.message_obj = SimpleNamespace(message_id=message_id, group=SimpleNamespace(group_name=group_name))
        self.parts = parts or [Plain(text)]
        self.extras = {}
        self.stopped = False
        self.result = MessageChain()

    def get_sender_id(self):
        return self.user_id

    def get_sender_name(self):
        return self.name

    def get_messages(self):
        return self.parts

    def get_message_type(self):
        return MessageType.GROUP_MESSAGE if ":GroupMessage:" in self.unified_msg_origin else MessageType.FRIEND_MESSAGE

    def is_at_me(self):
        return self.is_at_or_wake_command

    def get_extra(self, key, default=None):
        return self.extras.get(key, default)

    def set_extra(self, key, value):
        self.extras[key] = value

    def stop_event(self):
        self.stopped = True

    def get_result(self):
        return self.result

    def plain_result(self, text):
        return MessageChain([Plain(text)])

    async def send(self, chain):
        from .spectrum_module.spectrum import freeze
        delivery = self.get_extra("_spectrum_reply_delivery")
        if delivery is not None:
            l2 = self.host.system.spectrum.l2
            chain.delivery_payload = {"communication": l2.communication_payload(delivery.decision),
                                      "events": delivery.decision.shared_events,
                                      "output_prepared": delivery.decision.output_prepared}
        try:
            if delivery is not None and not freeze.may_commit(self.get_extra("_spectrum_freeze_epoch", freeze.checkpoint())):
                raise freeze.FrozenSkipped("回复所属的运行阶段已结束")
            result = await self.host.send_message(self.unified_msg_origin, chain, management=self.get_extra("_management_reply", False))
        except Exception as error:
            if delivery is not None:
                delivery.failed = True
                freeze.record_failure("delivery", error)
            raise
        if delivery is not None and result is not False:
            delivery.acknowledge(chain)
        return result


request_scope = ContextVar("panel_request")


class RequestView:
    @property
    def args(self):
        return request_scope.get().query_params

    async def get_json(self, silent=False):
        return await request_scope.get().json()


request = RequestView()
class Host(Protocol):
    """业务模块依赖的宿主边界，实现位于 host.py。"""
    config: dict
    system: object

    def module_dir(self, name): ...
    def register_web_api(self, path, handler, methods, description): ...
    def persona_prompt(self, umo=""): ...
    def get_provider_by_id(self, provider_id): ...
    def provider_for(self, provider_id=""): ...
    def refresh_providers(self): ...
    async def get_using_provider_async(self, umo=""): ...
    async def send_message(self, umo, chain, **kwargs): ...

