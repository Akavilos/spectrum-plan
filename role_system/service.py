"""统一角色回合：送达前不登记已说的话，客观状态与情绪背景分别处理。"""
import asyncio
import inspect
import json
import time
from .ports import ModelRequest, Plain, ToolContext, ToolUsageError, call_tool, logger
from .spectrum_module.main import SpectrumCorePlugin
from .prism_module.main import PrismPlugin
from .guard_module.main import DeliveryGuardPlugin
from .spectrum_module.spectrum import freeze, limiter, prism_bridge
from .spectrum_module.spectrum import settings as spectrum_settings
from .prism_module.prism import settings as prism_settings
from .guard_module.guard import settings as guard_settings


class RoleSystem:
    def __init__(self, host):
        self.host = host
        host.system = self
        self.configs = {name: host.module_config(name) for name in ("spectrum", "prism", "guard")}
        self.spectrum = SpectrumCorePlugin(host, self.configs["spectrum"])
        for provider in (host.provider, *host.providers.values()):
            provider.role_config = self.spectrum._cfg
        self.prism = PrismPlugin(host, self.configs["prism"])
        self.guard = DeliveryGuardPlugin(host, self.configs["guard"])
        prism_bridge.bind(self.prism)
        self.lock = asyncio.Lock()
        from .private_messages import PrivateMessages
        self.private_messages = PrivateMessages(self.lock, self._receive, self._store_inputs, self.spectrum._cfg)
        from .personality import Personality
        self.personality = Personality(self)
        self.spectrum.store.bind_memory_role(self.host.memory_role_id)
        from .active_memory.service import ActiveMemory
        self.active_memory = ActiveMemory(self)
        self.host.tools.register(self.active_memory)
        from .speech import Speech
        self.speech=Speech(self)
        self.host.tools.register(self.speech)
        self.commands = {"光谱核心": self.spectrum.spectrum_cmd, "叙述事实": self.spectrum.observe_cmd,
                         "叙述查看": self.spectrum.observed_list_cmd, "叙述删除": self.spectrum.observed_delete_cmd,
                         "棱镜": self.prism.prism_cmd, "哨兵": self.guard.guard_cmd}
        self.started = False

    async def start(self):
        for module in (self.spectrum, self.prism, self.guard):
            await module.initialize()
        self.started = True
        self.personality.start()
        await self.host.extensions.emit("startup")

    async def close(self):
        await self.private_messages.close()
        await self.active_memory.close()
        await self.personality.close()
        await self.host.extensions.emit("shutdown")
        if self.started:
            for module in (self.guard, self.prism, self.spectrum):
                await module.terminate()
        prism_bridge.bind(None)
        await self.host.close()

    async def receive(self, event):
        private = ':FriendMessage:' in event.unified_msg_origin
        key = (event.unified_msg_origin, event.user_id)
        ready = self.host.config['enabled'] and not getattr(self.host, 'maintenance_pending', False)
        if private and not event.message_str.strip().startswith('/') and (
                (ready and self.spectrum._cfg('private_message_merge_enabled', True)) or key in self.private_messages.pending):
            if self.spectrum._active(event.unified_msg_origin):
                self.spectrum.store.cancel_pending_for(event.unified_msg_origin)
            return await self.private_messages.receive(event)
        async with self.lock:
            return await self._receive(event)

    def _store_inputs(self, event):
        originals = event.get_extra('_private_message_batch', (event,))
        for original in originals:
            self.host.append_history(event.unified_msg_origin, 'user', original.message_str.strip(),
                                     sender_id=original.user_id, sender_name=original.name,
                                     ts=original.get_extra('_private_received_at'))
        if event.get_extra('_private_message_batch'):
            self.spectrum.record_private_batch(event)

    async def _receive(self, event):
        if getattr(self.host, "maintenance_pending", False):
            if event.get_extra('_private_message_batch'):
                self._store_inputs(event)
            return "maintenance"
        text = event.message_str.strip()
        command, _, arguments = text.removeprefix("/").partition(" ")
        if text.startswith("/") and command in self.commands:
            return await self._command(event, command, arguments)
        if text.startswith("/"):
            if await self.host.extensions.command(command, event, arguments):
                return "extension_command"
            return "unhandled_command"
        if not self.host.config["enabled"]:
            if event.get_extra('_private_message_batch'):
                self._store_inputs(event)
            return "disabled"
        if self.host.provider_for() is None:
            if event.get_extra('_private_message_batch'):
                self._store_inputs(event)
            selected = self.host.config.get('chat_provider_id') or 'role'
            provider = self.host.provider if selected in ('role', 'default') else self.host.providers[selected]
            logger.warning('[回合未执行] %s | 主对话模型 %s 不可用：%s',
                           event.unified_msg_origin, selected, provider.unavailable_reason)
            return "model_unconfigured"
        event.set_extra("_spectrum_failure_scope", freeze.begin_failure_scope())
        # The guard decides before any model observes an undeliverable turn.
        req = ModelRequest(text, self.host.persona_prompt(), self.host.history(event.unified_msg_origin), self.host.tools)
        if freeze.is_frozen():
            self._store_inputs(event)
            await self.spectrum.on_llm_request(event, req)
            return "frozen"
        await self.guard.on_llm_request(event, req)
        if event.stopped:
            return "delivery_blocked"
        self._store_inputs(event)
        self.spectrum.l2.sync_sleep_setting()
        group = ":GroupMessage:" in event.unified_msg_origin
        if group:
            await self.spectrum.on_message_eavesdrop(event)
            if not event.is_at_or_wake_command:
                return "observed"
        # Let Spectrum record received messages during freeze/sleep without spending a Prism call.
        if freeze.is_frozen() or (self.spectrum._active(event.unified_msg_origin) and self.spectrum.store.is_asleep() and not self.spectrum.store.sleep_due()):
            await self.spectrum.on_llm_request(event, req)
            return "frozen" if freeze.is_frozen() else "sleeping"
        from .image_input import receive_images
        await receive_images(event,req)
        await self.prism.on_llm_request(event, req)
        await self.spectrum.on_llm_request(event, req)
        if event.stopped or event.get_extra("_spectrum_sleep_muted", False):
            return "paused"
        await self.guard.inject_brief(event, req)
        from .spectrum_module.spectrum.prompt_flow import temporary_context
        req.extra_user_content_parts.extend(temporary_context(
            f"【我本轮正在交流的对象】\n我正在会话 {event.unified_msg_origin} 接到 {event.name or '对方'}（发送者身份 {event.user_id}）的来信。"
            "其他会话的摘录是过去的背景，其中的人物和经历保留原有归属；我不把旧记录中的发言或要求算到当前发言人身上。"))
        response = await self._generate(event, req)
        await self.spectrum._on_llm_response(event, response)
        event.result.chain = [Plain(response.completion_text)] if response.completion_text else []
        await self.spectrum.on_decorating_result(event)
        if event.stopped or not event.result.chain:
            return "silent"
        await event.send(event.result)
        await self.spectrum.on_after_message_sent(event)
        # A reply becomes a Prism experience only after actual transmission.
        await self.prism.on_decorating_result(event)
        return "sent"

    async def _generate(self, event, req, *, model=None, provider=None):
        from .spectrum_module.spectrum.person import make_person_id, is_private_umo
        from .spectrum_module.spectrum.prompt_flow import temporary_context
        umo = event.unified_msg_origin
        sender_id = event.user_id or (umo.rsplit(':', 1)[-1] if is_private_umo(umo) else '')
        personality = self.personality.context(umo, make_person_id(umo.split(':', 1)[0], sender_id), req.prompt)
        if personality: req.extra_user_content_parts.extend(temporary_context(personality))
        await self.host.extensions.emit("before_request", event, req)
        from .ports import logger
        logger.info("[模型请求] %s | 模式 %s | %s %s 字 | 历史 %s 条",
                    event.unified_msg_origin, req.role_mode,
                    "后台事件" if req.turn_origin == "background" else "来信", len(req.prompt), len(req.contexts))
        kwargs = dict(prompt=req.prompt, system_prompt=req.system_prompt, contexts=list(req.contexts),
                      extra_user_content_parts=req.extra_user_content_parts, func_tool=req.func_tool, role_mode=req.role_mode, model=model, media=req.media, turn_origin=req.turn_origin)
        provider = provider or self.host.provider_for()
        def bind_response(response):
            nonlocal provider
            actual = getattr(response, 'used_provider', None)
            if actual is not None:
                if actual is not provider: kwargs['model'] = None
                provider = actual
                req.role_mode = response.role_mode
                request_data = dict(event.get_extra('_spectrum_request', {}))
                request_data.update(prefill=req.role_mode == 'deepseek', role_mode=req.role_mode, provider=actual)
                event.set_extra('_spectrum_request', request_data)
                kwargs['role_mode'] = req.role_mode
        response = await freeze.call_model(provider, **kwargs)
        bind_response(response)
        if not response.tool_calls:
            return response
        if len(response.tool_calls) > 4:
            raise ValueError("单轮工具调用超过四次")
        # 沿用实际发出的消息序列；临时背景仅在本轮工具续请求中保留。
        history = list(response.input_messages)
        if history and history[0]["role"] == "system":
            history.pop(0)
        history.append(response.native_message or {"role": "assistant", "content": response.completion_text or None, "tool_calls": response.tool_calls})
        for call in response.tool_calls:
            epoch = event.get_extra("_spectrum_freeze_epoch", freeze.checkpoint())
            if not freeze.may_commit(epoch):
                raise freeze.FrozenSkipped("工具执行前系统暂停")
            function = call["function"]
            tool = self.host.tools.get_tool(function["name"])
            try:
                arguments = json.loads(function["arguments"])
                outcome = await call_tool(tool, ToolContext(self.host, event), arguments)
            except (ToolUsageError, json.JSONDecodeError) as error:
                outcome = "我这次工具调用未执行：" + str(error)
            history.append({"role": "tool", "tool_call_id": call["id"], "content": str(outcome)})
        kwargs["prompt"] = ""
        kwargs["contexts"] = history
        kwargs["extra_user_content_parts"] = []
        kwargs["media"] = []
        kwargs["continue_tools"] = True
        response = await freeze.call_model(provider, **kwargs)
        bind_response(response)
        return response

    async def _command(self, event, command, arguments):
        # Narrative commands are participant observations; system controls belong to configured operators.
        operators = {str(uid) for uid in self.host.config["operators"]}
        if command in ("光谱核心", "棱镜", "哨兵") and event.user_id not in operators:
            await event.send(event.plain_result("此管理指令需要在控制台登记操作员 QQ 号。"))
            return "permission_required"
        event.set_extra("_management_reply", True)
        handler = self.commands[command]
        if command in ("光谱核心", "哨兵"):
            event.message_str = command + (" " + arguments if arguments else "")
            result = handler(event)
        elif command == "叙述事实":
            result = handler(event, arguments)
        else:
            result = handler(event)
        if inspect.isasyncgen(result):
            async for chain in result:
                await event.send(chain)
        else:
            chain = await result
            if chain is not None:
                await event.send(chain)
        return "command"

    async def generate_background(self, umo, **kwargs):
        from .ports import Event
        event = Event(self.host, umo, "", "", kwargs.get("prompt", ""))
        event.set_extra("_spectrum_freeze_epoch", freeze.checkpoint())
        provider = self.host.provider_for(kwargs.get("provider_id", ""))
        selected = provider.role_mode(self.spectrum.store.get_state(umo, "reply_mode"))
        req = ModelRequest(kwargs.get("prompt", ""), kwargs.get("system_prompt", ""), kwargs.get("contexts", []), self.host.tools,
                           kwargs.get("extra_user_content_parts", []), selected)
        req.turn_origin = "background"
        event.set_extra("_spectrum_request", {"tools": self.host.tools, "prefill": selected == "deepseek"})
        response = await self._generate(event, req, model=kwargs.get("model"), provider=provider)
        if req.role_mode == "deepseek":
            from .spectrum_module.spectrum.parse import extract_tool_calls
            calls = extract_tool_calls(response.completion_text)[1]
            if calls:
                await self.spectrum._run_tool_calls(event, umo, calls)
        response.delivery_receipts = dict(event.get_extra("_spectrum_delivery_receipts", {}))
        return response

    def status(self):
        selected = self.host.config.get('chat_provider_id') or 'role'
        provider = self.host.provider if selected in ('role', 'default') else self.host.providers[selected]
        return {"enabled": self.host.config["enabled"], "model_ready": self.host.provider_for() is not None,
                "model_unavailable_reason": "" if provider.ready else provider.unavailable_reason,
                "modules": {name: bool(settings.value(self.configs[name], "enabled", name == "prism"))
                            for name, settings in (("spectrum", spectrum_settings), ("prism", prism_settings), ("guard", guard_settings))},
                "frozen": freeze.is_frozen(), "freeze": freeze.status(),
                "sleep": self.spectrum.store.get_sleep(), "persons": self.spectrum.store.count_persons(),
                "sessions": self.spectrum.store.list_umos(), "role_system": "integrated"}
