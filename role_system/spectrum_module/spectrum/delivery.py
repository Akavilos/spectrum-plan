"""追踪本轮实际成功发送的内容与完整程度。
支持分段、转发、语音原文及已登记的文字渲染结果；发送异常继续向调用方传播。"""

from dataclasses import dataclass, field
from typing import Callable
from types import SimpleNamespace


_rendered_text: dict[str, str] = {}




def rendered_content(message, extract_text: Callable) -> str:
    """提取发送组件中的文本来源，不为未登记的任意图片推断文字。"""
    chunks = []
    for component in message.chain or []:
        kind = type(component).__name__
        if kind == "Record":
            chunks.append(str(getattr(component, "text", "") or ""))
        elif kind == "Node":
            node = SimpleNamespace(chain=component.content)
            chunks.append(rendered_content(node, extract_text))
        elif kind == "Nodes":
            node = SimpleNamespace(chain=component.nodes)
            chunks.append(rendered_content(node, extract_text))
        elif kind == "Image":
            file = str(getattr(component, "file", "") or "")
            url = str(getattr(component, "url", "") or "")
            resource = file or url
            token = resource.rsplit("/api/file/", 1)[-1] if "/api/file/" in resource else ""
            chunks.append(_rendered_text.get(file, _rendered_text.get(url, _rendered_text.get(token, ""))))
        else:
            node = SimpleNamespace(chain=[component])
            chunks.append(extract_text(node))
    return "".join(chunks)


@dataclass
class ReplyDelivery:
    decision: object
    receipts: dict
    extract_text: Callable = field(repr=False)
    chunks: list[str] = field(default_factory=list)
    failed: bool = False
    recorded: bool = False

    def acknowledge(self, message) -> None:
        """发送接口确认成功后累计文字；失败内容不进入回执。"""
        text = rendered_content(message, self.extract_text)
        if text:
            self.chunks.append(text)

    @property
    def text(self) -> str:
        return "".join(self.chunks)

    @property
    def complete(self) -> bool:
        """忽略分段分隔符后核对内容覆盖；任一发送失败仍判为不完整。"""
        actual = "".join(c for c in self.text if not c.isspace() and c not in "。？！~…")
        expected = "".join(c for c in self.decision.content if not c.isspace() and c not in "。？！~…")
        return bool(expected) and expected in actual and not self.failed


def track_reply(event, decision, receipts: dict, extract_text: Callable) -> ReplyDelivery:
    """登记本轮回执，由自有 Event.send 明确调用，不替换发送方法。"""
    delivery = ReplyDelivery(decision, dict(receipts), extract_text=extract_text)
    event.set_extra("_spectrum_reply_delivery", delivery)
    return delivery


def register_rendered_text(resource: str, text: str) -> None:
    """渲染工具显式登记图片文字来源，发送回执无需改写渲染器。"""
    _rendered_text[resource] = text
    if len(_rendered_text) > 256:
        _rendered_text.pop(next(iter(_rendered_text)))
