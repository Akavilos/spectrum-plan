"""组织稳定合约、结构化历史和本轮背景，不改写外部框架。"""

from collections import Counter
import re

from ...ports import TextPart


def temporary_context(*blocks: str) -> list[TextPart]:
    """合并已筛选的背景，标记为本轮有效，不写入框架聊天记录。"""
    text = "\n\n".join(block for block in blocks if block)
    return [TextPart(text=text).mark_as_temp()] if text else []


def inject_context(req, contract: str, *blocks: str) -> None:
    """保留原系统前缀、历史、用户输入、媒体和工具，只追加稳定合约和临时背景。"""
    req.system_prompt = system_context(req.system_prompt, contract)
    req.extra_user_content_parts.extend(temporary_context(*blocks))


def system_context(persona: str, contract: str) -> str:
    """普通聊天和后台生活共用相同系统前缀；动态资料不进入此处。"""
    return "\n\n".join(part for part in (persona, contract) if part)


def exchange_context(rows, current_text: str, parse_meta, *, is_group: bool, current_ids=None) -> list[dict]:
    """光谱历史模式保留消息角色和顺序；最新来信由本轮 prompt 提供。"""
    current_id = next((row["id"] for row in rows
                       if row["kind"] == "received" and row["text"].strip() == current_text.strip()), None) if current_ids is None else None
    messages = []
    for row in reversed(rows):
        if row["id"] == current_id or (current_ids is not None and row['id'] in current_ids):
            continue
        role = "user" if row["kind"] == "received" else "assistant"
        text = row["text"]
        if is_group and role == "user":
            meta = parse_meta(row["meta"])
            text = f"[来信者：{meta.get('sender') or '群友'} · {meta.get('sender_id') or ''}]\n{text}"
        messages.append({"role": role, "content": text})
    return messages


def _plain(content) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "\n".join(p.get("text", "") for p in content
                         if isinstance(p, dict) and p.get("type") == "text").strip()
    return ""


def missing_exchange(rows, contexts, current_text: str, parse_meta, *, is_group: bool, current_ids=None) -> str:
    """补充框架历史未收录的真实往来；按次数去重，保留重复说话的顺序。"""
    known = Counter()
    for message in contexts or []:
        role = message.get("role")
        text = _plain(message.get("content"))
        if role == "user" and text.startswith("[来信者："):
            text = text.partition("\n")[2]
        if role == "assistant":
            reply = re.search(r"<reply>(.*?)</reply>", text, re.S)
            if reply:
                text = reply.group(1).strip()
        if role in ("user", "assistant") and text:
            known[(role, text)] += 1

    # 当前来信已经在 req.prompt 中；只排除最新的一条同文记录。
    current_id = next((r["id"] for r in rows
                       if r["kind"] == "received" and r["text"].strip() == current_text.strip()), None) if current_ids is None else None
    lines = []
    for row in reversed(rows):
        if row["id"] == current_id or (current_ids is not None and row['id'] in current_ids):
            continue
        role = "user" if row["kind"] == "received" else "assistant"
        text = row["text"].strip()
        key = (role, text)
        if known[key]:
            known[key] -= 1
            continue
        sender = str(parse_meta(row["meta"]).get("sender") or "").strip()
        label = (sender if sender not in ("", "某人", "对方", "群友")
                 else "群友" if is_group else "对方") if role == "user" else "我"
        lines.append(f"{label}：{text}")
    return "\n".join(lines)
