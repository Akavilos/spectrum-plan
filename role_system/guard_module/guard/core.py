"""解析会话并检查群禁言，跟踪实际发送结果和不可送达状态。
私聊没有禁言查询结果，故障判断依赖发送失败与恢复试探。"""


from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from ...ports import logger


class DeliveryBlocked(Exception):
    _spectrum_expected_skip = True


def extract_text(chain: Any) -> str:
    parts: list[str] = []
    try:
        for comp in getattr(chain, "chain", None) or []:
            text = getattr(comp, "text", None)
            if text and type(comp).__name__ == "Plain":
                parts.append(text)
    except Exception:
        pass
    text = "".join(parts).strip()
    return text or ("[媒体消息]" if getattr(chain, "chain", []) else "")


@dataclass
class Target:
    platform: Any
    bot: Any
    is_group: bool
    group_id: str
    user_id: str
    self_id: str


def parse_umo(umo: str) -> tuple[str, str, str]:
    parts = str(umo).split(":")
    if len(parts) < 3:
        return "", "", ""
    return parts[0], parts[1], ":".join(parts[2:])


class GuardCore:
    def __init__(self, context, store, cfg) -> None:
        self.context = context
        self.store = store
        self._cfg = cfg
        self._real_context = context

        self._cache: dict[str, tuple[float, bool]] = {}


    def resolve(self, umo: str) -> Target | None:
        platform = self.context.platform_for(umo)
        if platform is None:
            return None
        _, mtype, sid = parse_umo(umo)
        group = mtype == "GroupMessage"
        return Target(platform, platform.bot, group, sid if group else "", "" if group else sid, platform.self_id)

    def _diag(self, umo: str, msg: str) -> None:
        logger.warning("[guard·DIAG] %s | %s", umo, msg)

    def diagnostics(self, umo: str) -> dict:
        t = self.resolve(umo)
        bot = getattr(t, "bot", None) if t else None
        return {
            "umo": umo,
            "status": self.store.get_status(umo),
            "reason": self.store.get_state(umo, "reason", ""),
            "mute_until": self.store.get_mute_until(umo),
            "in_scope": self.in_scope(umo),
            "platform": getattr(t, "platform", None) is not None,
            "is_group": getattr(t, "is_group", None),
            "group_id": getattr(t, "group_id", ""),
            "self_id": getattr(t, "self_id", ""),
            "bot_own_id": t.self_id if t else "",
            "api_clients": None,
            "connection_registered": t is not None,
            "sent_today": self.store.sent_today(umo),
        }

    def in_scope(self, umo: str) -> bool:
        scope = str(self._cfg("scope", "all"))
        if scope == "all":
            return True
        is_group = "GroupMessage" in umo
        return (scope == "group") == is_group


    async def probe(self, umo: str) -> bool | None:
        """群聊查询禁言信息并返回真、假或未知；私聊没有该查询路径。"""
        t = self.resolve(umo)
        if t is None:
            self._diag(umo, "解析不到平台实例，跳过探测")
            return None
        if not t.is_group or t.bot is None:
            return None
        if not self._cfg("probe_groups", True):
            return None
        if not t.group_id.isdigit():
            self._diag(umo, f"群号不合法: {t.group_id!r}")
            return None
        if not t.self_id.isdigit():

            self._diag(umo, f"拿不到机器人 self_id: {t.self_id!r}")
            return None

        try:
            info = await t.bot.call_action(
                "get_group_member_info",
                group_id=int(t.group_id),
                user_id=int(t.self_id),
                no_cache=True,
            )
        except Exception as e:


            logger.warning(
                "[guard] 探测失败 %s（群 %s / QQ %s）：%s: %s",
                umo, t.group_id, t.self_id, type(e).__name__, str(e)[:160],
            )
            return None
        if not isinstance(info, dict):
            self._diag(umo, f"探测返回非字典: {type(info).__name__}")
            return None
        try:
            until = float(info.get("shut_up_timestamp", 0) or 0)
        except (TypeError, ValueError):
            until = 0.0
        now = time.time()

        self.store.set_state(umo, "probe_raw_until", repr(until))
        if until > now:

            was = self.store.get_status(umo)
            self.set_blocked(umo, "群禁言", mute_until=until)

            if was == "ok":
                logger.warning(
                    "[guard] %s 探测到群禁言，解禁于 %s",
                    umo,
                    time.strftime("%m-%d %H:%M:%S", time.localtime(until)),
                )
            return False


        if self._cfg("probe_all_shut", True):
            role = str(info.get("role", "") or "")
            if role not in ("owner", "admin"):
                try:
                    ginfo = await t.bot.call_action(
                        "get_group_info", group_id=int(t.group_id), no_cache=True
                    )
                except Exception:
                    ginfo = None
                if isinstance(ginfo, dict) and ginfo.get("group_all_shut"):

                    self.set_blocked(umo, "群全员禁言", mute_until=0.0)
                    logger.warning("[guard] %s 探测到全员禁言（机器人非管理员）", umo)
                    return False

        return True


    _CACHE_OK = 0.4
    _CACHE_BLOCK = 5.0

    def should_block(self, umo: str) -> bool:
        now = time.time()
        hit = self._cache.get(umo)
        if hit is not None and hit[0] > now:
            return hit[1]
        blocked = self._should_block_slow(umo, now)
        ttl = self._CACHE_BLOCK if blocked else self._CACHE_OK
        self._cache[umo] = (now + ttl, blocked)
        return blocked

    def _should_block_slow(self, umo: str, now: float) -> bool:
        if not self.in_scope(umo):
            return False
        status = self.store.get_status(umo)
        if status == "ok":
            return False
        if status == "muted":
            until = self.store.get_mute_until(umo)

            if until and now < until:
                return True
            return False


        # 未确认恢复的会话继续拦截；试探由后台补发流程执行。
        return True

    async def on_send_failed(self, umo: str, chain: Any, err: BaseException) -> None:
        """先核查群禁言；无探测结果时累计失败，达到门槛才标记不可达。"""
        self._cache.pop(umo, None)
        text = extract_text(chain)
        status = await self.probe(umo)
        if status is False:

            if text:
                oid = self.store.push_outbox(umo, text, "群禁言", payload=getattr(chain, "delivery_payload", {}))
                logger.warning("[guard] %s 发送失败且探测确认被拦，已暂存 #%d：%.40s",
                               umo, oid, text)
            return
        if status is not False:
            streak = self._bump_fail(umo)
            if text:
                self.store.push_outbox(umo, text, "发送失败", payload=getattr(chain, "delivery_payload", {}))
            if streak >= int(self._cfg("max_fail_streak", 3)):
                self.set_blocked(umo, f"发送连续失败 {streak} 次")
                logger.warning("[guard] %s 连续 %d 次发送失败，判定不可达", umo, streak)

    def _bump_fail(self, umo: str) -> int:
        n = int(self.store.get_state(umo, "fail_streak", "0") or 0) + 1
        self.store.set_state(umo, "fail_streak", str(n))
        return n

    def note_success(self, umo: str) -> None:
        """真实发送成功即确认通路；查询恢复另用连续确认次数。"""
        self.store.set_state(umo, "fail_streak", "0")
        if self.store.get_status(umo) != "ok":
            self.set_ok(umo)
        self.store.bump_sent(umo)

    def on_send_failed_sync(self, umo: str) -> None:
        self._invalidate(umo)
        streak = self._bump_fail(umo)
        if streak >= int(self._cfg("max_fail_streak", 3)):
            self.set_blocked(umo, f"补发连续失败 {streak} 次")


    def _invalidate(self, umo: str) -> None:
        self._cache.pop(umo, None)

    def set_blocked(self, umo: str, reason: str, mute_until: float = 0.0) -> None:
        self.store.mark_blocked(umo, reason, mute_until)
        self._invalidate(umo)

    def set_ok(self, umo: str) -> None:
        self.store.mark_ok(umo)
        self._invalidate(umo)

        self.store.set_state(umo, "last_canary", "0")







    def record_incoming(self, umo: str, sender: str, text: str) -> None:
        if not text or not text.strip():
            return
        self.store.push_inbox(umo, sender, text)
        if self._cfg("debug", False):
            logger.info("[guard] %s 缺席期收件 <- %s：%.40s", umo, sender, text)

    def build_brief(self, umo: str) -> str:
        """读取恢复背景并消费对应收件标记，避免后续请求重复注入。"""
        if not self._cfg("brief_on_recovery", True):
            return ""
        if self.store.get_state(umo, "need_brief") != "1":
            return ""
        limit = int(self._cfg("max_brief_items", 8))
        rows = self.store.peek_inbox(umo, limit)
        self.store.mark_inbox_delivered(umo)
        self.store.set_state(umo, "need_brief", "0")
        if not rows:
            return ""
        lines = ["【我的通信恢复背景】前面一段时间我的消息未能送达，现在恢复了。",
                 "以下是我尚未回应的来信；原文属于各自的说话人与时间："]
        for r in rows:
            who = (r["sender"] or "对方").strip()
            lines.append(f"· [{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(r['ts']))}] {who}：{r['text']}")
        lines.append("我刚补看的是先前的来信，不是此刻又收到相同消息。我结合后续往来与约定决定如何回应，不必逐条补答。")
        logger.info("[guard] %s 注入缺席期背景 %d 条", umo, len(rows))
        return "\n".join(lines)
