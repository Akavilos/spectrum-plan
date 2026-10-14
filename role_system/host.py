"""角色宿主：配置和历史归属实例，所有发送走同一哨兵与回执接口。"""
import json
import asyncio
import os
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace
from dataclasses import dataclass
from fastapi import Request, Depends
from .ports import request_scope, Plain, MessageChain, NativeSegment, logger, serialize_segment, deserialize_segment
from .provider import Provider, EmbeddingProvider

MODULES = {"spectrum": "spectrum_module", "prism": "prism_module", "guard": "guard_module"}


class Config(dict):
    def __init__(self, path):
        self.path = path
        super().__init__(json.loads(path.read_text(encoding="utf-8")) if path.exists() else {})

    def save_config(self):
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)


class Platform:
    def __init__(self, bot):
        self.native = bot
        self.self_id = str(bot.self_id)
        self.bot = self

    def meta(self):
        return SimpleNamespace(id="qq-" + self.self_id)

    async def call_action(self, action, **kwargs):
        return await self.native.call_api(action, **kwargs)

    async def image_info(self, file):
        from nonebot.exception import ActionFailed
        try:
            return await self.call_action('get_image',file=file)
        except ActionFailed as error:
            raise ValueError('聊天平台未能提供图片下载地址') from error


@dataclass
class SendTool:
    host: object
    name: str = "send_message_to_user"
    description: str = "我向已经认识的其他会话发送消息；当前对话由正常回复发送。"
    active: bool = True
    parameters: object = None

    def __post_init__(self):
        self.parameters = {"type": "object", "properties": {
            "session": {"type": "string"}, "messages": {"type": "array", "items": {
                "type": "object", "properties": {"type": {"type": "string", "enum": ["plain", "image", "record", "video", "at", "file", "node", "json", "music"]}, "text": {"type": "string"}, "url": {"type": "string"}, "data": {"type": "object"}},
                "required": ["type"]}}}, "required": ["session", "messages"]}

    async def call(self, wrapper, session, messages):
        from .ports import ToolUsageError
        from .spectrum_module.spectrum import freeze
        from .spectrum_module.spectrum.contract import _TOOL_SEND_NAME
        event = wrapper.context.event
        freeze_epoch = event.get_extra("_spectrum_freeze_epoch", freeze.checkpoint())
        if not freeze.may_commit(freeze_epoch):
            raise freeze.FrozenSkipped("发送前系统暂停")
        if session == event.unified_msg_origin:
            raise ToolUsageError("当前会话使用正常回复")
        known = self.host.system.spectrum.store.list_umos()
        if session not in known:
            raise ToolUsageError("目标会话尚未认识")
        if any(p.get("type") not in ("plain", "image", "record", "video", "at", "file", "node", "json", "music") for p in messages):
            raise ToolUsageError("消息必须包含已支持的文字或协议组件")
        text = "".join(p.get("text", "") if p.get("type") == "plain" else "[媒体消息]" for p in messages)
        if not text.strip():
            raise ToolUsageError("消息内容为空")
        chain = MessageChain([Plain(p["text"]) if p["type"] == "plain" else NativeSegment(p["type"], p.get("data") or {"file": p["url"]}) for p in messages])
        if await self.host.send_message(session, chain) is False:
            raise ToolUsageError("整理后消息为空，未发送")
        text = "".join(p.text if isinstance(p, Plain) else "[媒体消息]" for p in chain.chain)
        spec = self.host.system.spectrum
        entry = spec.store.add_entry(session, "delivered", text, ts=time.time(),
            life_id=spec.l2.life.ensure_life(), meta={"cross_send_from": event.unified_msg_origin})
        receipts = dict(event.get_extra("_spectrum_delivery_receipts", {}))
        receipts[session] = entry
        event.set_extra("_spectrum_delivery_receipts", receipts)
        return "Message sent to session " + session


class ToolSet:
    def __init__(self, host):
        from .spectrum_module.spectrum.contract import _TOOL_SEND_NAME
        self.tools = [SendTool(host, name=_TOOL_SEND_NAME)]

    def get_tool(self, name):
        return next((t for t in self.tools if t.name == name and t.active), None)

    def get_full_tool_set(self):
        return self

    def iter_builtin_tools(self):
        return iter(self.tools)

    def is_builtin_tool(self, name):
        return self.get_tool(name) is not None

    def register(self, tool):
        if self.get_tool(tool.name):
            raise ValueError("工具名称已注册")
        self.tools.append(tool)


class Host:
    def __init__(self, data_dir, app=None, authorize=None):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.app = app
        self.authorize = authorize
        self.system = None
        self.platforms = {}
        from .connections import Connections
        self.connections = Connections(self.data_dir, os.environ.get("ONEBOT_ACCESS_TOKEN", ""))
        self.platform_manager = SimpleNamespace(get_insts=lambda: list(self.platforms.values()))
        self.config = Config(self.data_dir / "system.json")
        self.config.setdefault("enabled", False)
        self.config.setdefault("persona", "")
        self.config.setdefault("model", {"base_url": "", "model": "", "api_key": "", "temperature": 0.8})
        self.config.setdefault("embedding", {"enabled": False, "base_url": "", "model": "", "api_key": ""})
        self.config.setdefault("providers", {})
        self.config.setdefault("chat_provider_id", "")
        self.config.setdefault('fallback_models', {})
        self.config.setdefault("operators", [])
        from .model_catalog import migrate
        migrate(self.config)
        self.config.save_config()
        self.provider = Provider(self.config["model"], self.config['provider_sources'], fallback_selector=self.backup_for)
        self.embedding = EmbeddingProvider(self.config["embedding"], fallback_selector=self.backup_for)
        self.providers = {key: self._make_provider(value) for key, value in self.config["providers"].items()}
        self.tools = ToolSet(self)
        self.history_db = sqlite3.connect(self.data_dir / "conversation.db")
        self.history_db.execute("CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY, umo TEXT, role TEXT, content TEXT, ts REAL, sender_id TEXT DEFAULT '', sender_name TEXT DEFAULT '')")
        self.history_db.execute("CREATE INDEX IF NOT EXISTS messages_session_id ON messages(umo,id)")
        self.history_db.commit()
        self.routes = {}
        self.transport = self._onebot_send
        from .output_rules import OutputRules
        from .extensions import Extensions
        self.output_rules = OutputRules(self.data_dir)
        self.extensions = Extensions(self)

    def module_dir(self, name):
        short = name.replace("astrbot_plugin_", "").replace("spectrum_core", "spectrum").replace("delivery_guard", "guard")
        path = self.data_dir / short
        path.mkdir(exist_ok=True)
        return path

    def module_config(self, short):
        return Config(self.module_dir(short) / "config.json")

    def get_config(self):
        return {"wake_prefix": ["/"]}

    def persona_prompt(self, umo=""):
        return self.config["persona"]

    def memory_role_id(self):
        return self.system.personality.config.role_id if self.system else ''

    def get_provider_by_id(self, provider_id):
        provider = self.provider if provider_id in ("", "default", "role") else self.providers.get(provider_id)
        if provider is None:
            raise ValueError("没有该模型配置：" + provider_id)
        return provider if provider.ready else None

    async def get_using_provider_async(self, umo=""):
        return self.provider_for()

    def provider_for(self, provider_id=""):
        """用途留空时使用当前对话模型；显式选择保留对应接口和密钥。"""
        return self.get_provider_by_id(provider_id or self.config.get("chat_provider_id") or "role")

    def refresh_providers(self):
        self.provider.config = self.config["model"]
        self.provider.sources = self.config['provider_sources']
        self.providers = {key: self._make_provider(value) for key, value in self.config["providers"].items()}
        self.embedding.config = self.config['embedding']
        if self.system is not None:
            for provider in (self.provider, *self.providers.values()):
                provider.role_config = self.system.spectrum._cfg

    def get_all_embedding_providers(self):
        selected = self.config.get('embedding_provider_id', '')
        if selected:
            provider = self.providers.get(selected)
            return [provider] if isinstance(provider, EmbeddingProvider) and provider.ready else []
        return [self.embedding] if self.embedding.ready else []

    def _make_provider(self, config):
        if config.get('category', 'chat') == 'embedding':
            factory = EmbeddingProvider
        elif config.get('category', 'chat') == 'chat':
            factory = Provider
        else:
            from .model_utilities import UtilityProvider
            factory = UtilityProvider
        return factory(config, self.config['provider_sources'], fallback_selector=self.backup_for)

    def backup_for(self, primary):
        category = primary.config.get('category', 'embedding' if isinstance(primary, EmbeddingProvider) else 'chat')
        id = self.config['fallback_models'].get(category)
        if not id:
            return None
        backup = self.provider if id == 'role' else self.providers[id]
        if backup is primary or backup._config is primary._config:
            return None
        if category == 'embedding':
            from .model_fallback import embedding_identity
            if embedding_identity(primary.config) != embedding_identity(backup.config):
                raise ValueError('嵌入备用与主模型的向量空间设置不一致')
        return backup

    def get_llm_tool_manager(self):
        return self.tools

    def platform_for(self, umo):
        return self.platforms.get(umo.split(":", 1)[0])

    def get_platform_inst(self, platform_id):
        return self.platforms.get(platform_id)

    def history(self, umo, limit=30):
        """保留连续原始消息，每十条批量移动窗口，避免每轮改变历史开头。"""
        if limit <= 0:
            return []
        count = self.history_db.execute("SELECT COUNT(*) FROM messages WHERE umo=?", (umo,)).fetchone()[0]
        window = limit + max(0, count - limit) % 10
        rows = self.history_db.execute("SELECT role,content,sender_id,sender_name,ts FROM messages WHERE umo=? ORDER BY id DESC LIMIT ?", (umo, window)).fetchall()
        from .background_turn import delivery_context
        messages = []
        for role, text, sender_id, sender_name, ts in reversed(rows):
            if role == 'assistant' and self.system is not None:
                text = delivery_context(self.system.spectrum.store, umo, text, ts)
            elif ':GroupMessage:' in umo:
                text = f'[来信者：{sender_name or sender_id} · {sender_id}]\n' + text
            messages.append({'role': role, 'content': text})
        return messages

    def append_history(self, umo, role, content, *, sender_id="", sender_name="", ts=None):
        self.history_db.execute("INSERT INTO messages(umo,role,content,ts,sender_id,sender_name) VALUES(?,?,?,?,?,?)", (umo,role,content,time.time() if ts is None else ts,sender_id,sender_name))
        self.history_db.commit()

    async def _onebot_send(self, umo, chain):
        platform = self.platform_for(umo)
        if platform is None:
            raise RuntimeError("QQ 连接尚未建立：" + umo.split(":", 1)[0])
        _, kind, target = umo.split(":", 2)
        message = [{"type": "text", "data": {"text": p.text}} if isinstance(p, Plain) else {"type": p.type, "data": p.data} for p in chain.chain]
        if len(message) != len(chain.chain):
            raise ValueError("发送组件尚未注册对应的 OneBot 编码器")
        if kind == "GroupMessage":
            return await platform.call_action("send_group_msg", group_id=int(target), message=message)
        if kind == "FriendMessage":
            return await platform.call_action("send_private_msg", user_id=int(target), message=message)
        raise ValueError("未知会话类型")

    async def send_message(self, umo, chain, *, management=False, replay=False, delivery_payload=None):
        from .guard_module.guard.core import DeliveryBlocked, extract_text
        from .spectrum_module.spectrum import freeze
        if getattr(self, "maintenance_pending", False):
            raise freeze.FrozenSkipped("正在初始化，暂停发送")
        if replay and delivery_payload and delivery_payload.get("segments"):
            chain.chain = [deserialize_segment(p) for p in delivery_payload["segments"]]
        payload = delivery_payload if replay else getattr(chain, "delivery_payload", {})
        if not management and not getattr(chain, "output_prepared", False) and not (payload or {}).get("output_prepared"):
            from .spectrum_module.spectrum.parse import Decision
            decision = Decision(ok=True, content=chain.get_plain_text(), mode="immediate",
                                shared_events=(payload or {}).get("events", []), **(payload or {}).get("communication", {}))
            self.output_rules.prepare_decision(decision)
            if all(isinstance(p, Plain) for p in chain.chain):
                chain.chain = [Plain(decision.content)] if decision.content else []
            else:
                for part in chain.chain:
                    if isinstance(part, Plain): part.text = self.output_rules.apply(part.text)[0]
                chain.chain = [p for p in chain.chain if not isinstance(p, Plain) or p.text.strip()]
            if payload is not None:
                payload.update(communication=self.system.spectrum.l2.communication_payload(decision), events=decision.shared_events, output_prepared=True)
        if not chain.chain:
            return False
        chain.output_prepared = True
        chain.delivery_payload = {**(payload or {}), "output_prepared": True}
        if any(not isinstance(p, Plain) for p in chain.chain):
            chain.delivery_payload = {**getattr(chain, "delivery_payload", {}), "segments": [serialize_segment(p) for p in chain.chain]}
        guard = self.system.guard
        protected = guard._enabled() and guard.core.in_scope(umo)
        tracking = protected and guard._cfg("track_outcome", True)
        if freeze.is_frozen() and not management:
            raise freeze.FrozenSkipped("系统冻结，停止发送")
        if protected and not replay and guard.core.should_block(umo):
            guard.store.push_outbox(umo, extract_text(chain), "会话不可送达", payload=getattr(chain, "delivery_payload", {}))
            raise DeliveryBlocked("会话不可送达")
        from .message_segments import message_batches
        full_text = chain.get_plain_text()
        get_cfg = self.system.spectrum._cfg
        batches = [chain] if management else message_batches(
            chain, get_cfg("message_segments_enabled", True), get_cfg("message_segment_chars", 55))
        if not batches:
            return False
        interval = float(get_cfg("message_segment_interval", 1.2))
        sent = []
        try:
            for index, batch in enumerate(batches):
                if index:
                    await asyncio.sleep(interval)
                if getattr(self, "maintenance_pending", False):
                    raise freeze.FrozenSkipped("正在初始化，暂停发送")
                if freeze.is_frozen() and not management:
                    raise freeze.FrozenSkipped("系统冻结，停止发送")
                receipt = await self.transport(umo, batch)
                if receipt is False:
                    raise RuntimeError("发送端报告未送达")
                logger.info("[消息已发送] %s | 第 %s/%s 段 | %s", umo, index + 1, len(batches), batch.get_plain_text() or "[媒体消息]")
                sent.append(batch.get_plain_text())
                if not management and sent[-1]:
                    self.append_history(umo, "assistant", sent[-1])
                if not management:
                    from .active_memory.media import descriptions
                    for note in descriptions(batch):
                        self.append_history(umo,'assistant','【我的媒体送达记录】'+note)
                        self.system.spectrum.store.add_entry(umo,'delivered_media',note,
                            meta={'message_id':receipt.get('message_id') if isinstance(receipt,dict) else None})
        except Exception as error:
            if sent:
                from .spectrum_module.spectrum.parse import Decision
                self.system.spectrum.l2.confirm_reply(umo, Decision(ok=True), "".join(sent), complete=False)
            remaining = MessageChain([p for batch in batches[len(sent):] for p in batch.chain])
            remaining.delivery_payload = {**(delivery_payload or getattr(chain, "delivery_payload", {})),
                                          "full_reply": (delivery_payload or {}).get("full_reply", full_text),
                                          "segments": [serialize_segment(p) for p in remaining.chain]}
            if replay and delivery_payload is not None and sent:
                delivery_payload.update(remaining.delivery_payload)
                delivery_payload["segments"] = [serialize_segment(p) for p in remaining.chain]
            if tracking and not replay:
                await guard.core.on_send_failed(umo, remaining, error)
            freeze.record_failure("delivery", error)
            raise
        if tracking or replay:
            guard.core.note_success(umo)
        text = chain.get_plain_text()
        if replay and text:
            from .spectrum_module.spectrum.parse import Decision
            payload = delivery_payload or {}
            communication = payload.get("communication", {})
            decision = Decision(ok=True, content=text, mode="immediate", shared_events=payload.get("events", []), **communication)
            self.system.spectrum.l2.confirm_reply(umo, decision, payload.get("full_reply", text))
        if not management:
            await self.extensions.emit("after_send", umo, chain, receipt)
        return receipt

    def register_web_api(self, path, handler, methods, description):
        self.routes[path] = (handler, methods)
        if self.app is None:
            return
        async def endpoint(request: Request):
            token = request_scope.set(request)
            try:
                if "POST" in methods:
                    async with self.system.lock:
                        return await handler()
                return await handler()
            finally:
                request_scope.reset(token)
        self.app.add_api_route(path, endpoint, methods=methods,
                               dependencies=[Depends(self.authorize)], name=path, description=description)

    async def close(self):
        await self.provider.close()
        self.history_db.close()
