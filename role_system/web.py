"""管理端复用全部配置分组与业务面板，认证由宿主统一处理。"""
import json
import secrets
from pathlib import Path
from fastapi import Depends, HTTPException, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, PlainTextResponse
from .host import MODULES

ROOT = Path(__file__).parent


def schemas():
    return {name: json.loads((ROOT / directory / "_conf_schema.json").read_text(encoding="utf-8"))
            for name, directory in MODULES.items()}


def provider_choices(values):
    for key, value in values.items():
        if isinstance(value, dict):
            yield from provider_choices(value)
        elif key == "provider_id" or key.endswith("_provider_id"):
            yield value


def validate_provider_choices(host, values, providers=None):
    providers = host.config["providers"] if providers is None else providers
    for provider_id in provider_choices(values):
        if not isinstance(provider_id, str) or (provider_id not in ("", "role", "default") and provider_id not in providers):
            raise HTTPException(422, "请从已保存的模型配置中选择")
    for key, value in values.items():
        if isinstance(value, dict): validate_provider_choices(host, value, providers)
        elif value and (key == 'provider_id' or key.endswith('_provider_id')):
            model = host.config['model'] if value in ('role', 'default') else providers[value]
            expected = 'embedding' if key == 'embedding_provider_id' else 'chat'
            if model.get('category', 'chat') != expected:
                raise HTTPException(422, '请选择此用途对应类别的模型')


def validate_config(schema, values):
    # Only declared fields can change through the configuration interface.
    for group, value in values.items():
        if group == "_native" or group == "settings_layout_version":
            continue
        definition = schema.get(group)
        if definition is None:
            raise HTTPException(422, "未知配置项：" + group)
        if definition["type"] == "object":
            if not isinstance(value, dict):
                raise HTTPException(422, "配置分组必须为对象")
            validate_config(definition["items"], value)
            continue
        expected = {"bool": bool, "int": int, "float": (float, int), "string": str, "text": str,
                    "list": list}.get(definition["type"])
        if expected is None or not isinstance(value, expected) or (definition["type"] in ("int", "float") and isinstance(value, bool)):
            raise HTTPException(422, "配置类型不正确：" + group)
        if definition.get("options") and value not in definition["options"]:
            raise HTTPException(422, "配置值不在可选范围：" + group)
        if group == 'cares':
            from .prism_module.prism.settings import validate_cares
            try:
                validate_cares(value)
            except ValueError as error:
                raise HTTPException(422, str(error)) from error
        if 'min' in definition or 'max' in definition:
            import math
            if not math.isfinite(value) or value < definition.get('min', float('-inf')) or value > definition.get('max', float('inf')):
                raise HTTPException(422, (definition.get('description') or group) + '超出允许范围')


def install_panel(app, host, system, authorize, panel_key):
    from .panel_access import PanelAccess
    host.panel_access = PanelAccess(host.data_dir, panel_key)
    dependencies = [Depends(authorize)]
    from .runtime_logs import install_logs
    host.runtime_logs = install_logs(host, panel_key)
    from .management import install_management
    install_management(app, host, system, authorize)
    from .record_management import install_records
    install_records(app, host, system, authorize)
    from .maintenance import install as install_maintenance
    install_maintenance(app, host, system, authorize)
    from .personality.api import install as install_personality
    install_personality(app, system, authorize)
    from .active_memory.api import install as install_active_memory
    install_active_memory(app,host,system,authorize)
    from .speech import install as install_speech
    install_speech(app,host,system,authorize)

    @app.get('/assets/active-memory.js')
    async def active_memory_script():
        return FileResponse(ROOT/'assets/active-memory.js',media_type='application/javascript')
    from .connections import install as install_connections
    install_connections(app, host, authorize)
    from .model_management import install as install_models
    install_models(app, host, system, authorize)

    @app.get("/assets/models.js")
    async def model_script():
        return FileResponse(ROOT / "assets/models.js", media_type="application/javascript")

    @app.get("/assets/model-workspace.css")
    async def model_styles():
        return FileResponse(ROOT / "assets/model-workspace.css", media_type="text/css")

    @app.get("/api/logs", dependencies=dependencies)
    async def runtime_logs(limit: int = Query(300, ge=1, le=1000),
                           level: str = Query("DEBUG", pattern="^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$"),
                           query: str = Query("", max_length=200),
                           source: str = Query("runtime", pattern="^(runtime|console|all)$")):
        return {"entries": host.runtime_logs.read(limit=limit, level=level, query=query, source=source)}

    @app.get("/api/logs/download", dependencies=dependencies)
    async def download_logs():
        rows = host.runtime_logs.read(limit=5000, source="all")
        text = "\n".join(f"{r['time']} [{r['level']}] {r['source']}: {r['message']}" for r in rows)
        return PlainTextResponse(text, headers={"Content-Disposition": 'attachment; filename="role-system.log"'})

    @app.get("/", response_class=HTMLResponse)
    async def panel():
        return Path("panel.html").read_text(encoding="utf-8")

    @app.post("/api/login")
    async def login(request: Request):
        body = await request.json()
        if not host.panel_access.verify(body.get("key", "")):
            raise HTTPException(401, "管理密钥不正确")
        response = JSONResponse({"ok": True})
        response.set_cookie("role_panel", host.panel_access.key, httponly=True, samesite="strict", max_age=28800)
        return response

    @app.post("/api/access/key", dependencies=dependencies)
    async def change_key(request: Request):
        body = await request.json()
        if body.get("new_key") != body.get("confirm_key"):
            raise HTTPException(422, "两次输入的管理密钥不一致")
        try:
            host.panel_access.save(body.get("new_key"))
        except ValueError as error:
            raise HTTPException(422, str(error))
        response = JSONResponse({"ok": True, "message": "管理密钥已更新，当前页面保持登录；其他页面需用新密钥重新登录"})
        response.set_cookie("role_panel", host.panel_access.key, httponly=True, samesite="strict", max_age=28800)
        return response

    @app.post("/api/logout", dependencies=dependencies)
    async def logout():
        response = JSONResponse({"ok": True})
        response.delete_cookie("role_panel")
        return response

    @app.get("/api/config", dependencies=dependencies)
    async def get_config():
        from .data_exchange import public_config
        settings = dict(host.config)
        settings["model"] = {**public_config(host.config["model"]), "api_key": "", "has_key": bool(host.provider.config.get("api_key") or host.provider.config.get('api_keys'))}
        settings["embedding"] = {**public_config(host.config["embedding"]), "api_key": "", "has_key": bool(host.config["embedding"].get("api_key"))}
        settings["providers"] = {key: {**public_config(value), "api_key": "", "has_key": bool(host.providers[key].config.get("api_key") or host.providers[key].config.get('api_keys'))} for key, value in host.config["providers"].items()}
        from .model_catalog import redacted_source
        settings['provider_sources'] = {id: redacted_source(value) for id, value in host.config['provider_sources'].items()}
        return {"system": settings, "modules": system.configs, "schemas": schemas()}

    @app.post("/api/config/system", dependencies=dependencies)
    async def save_system(request: Request):
        body = await request.json()
        model = body.get("model", {})
        if not isinstance(body.get("enabled", False), bool) or not isinstance(body.get("persona", ""), str):
            raise HTTPException(422, "启用状态或人设格式不正确")
        if not isinstance(body.get("operators", []), list) or any(not str(x).isdigit() for x in body.get("operators", [])):
            raise HTTPException(422, "操作员填写 QQ 号列表")
        allowed_model = {"base_url", "model", "api_key", "temperature", "family", "parameters", "timeout"}
        if set(model) - allowed_model:
            raise HTTPException(422, "未知模型设置")
        for key in ("base_url", "model", "api_key"):
            if key in model and not isinstance(model[key], str):
                raise HTTPException(422, "模型配置必须是文字")
        temperature = model.get("temperature", host.config["model"].get("temperature", 0.8))
        if isinstance(temperature, bool) or not isinstance(temperature, (int, float)) or not 0 <= temperature <= 2:
            raise HTTPException(422, "温度需在 0 到 2 之间")
        if model.get("base_url") and not model["base_url"].startswith(("http://", "https://")):
            raise HTTPException(422, "模型地址需要 http 或 https 开头")
        validate_model(model)
        async with system.lock:
            if "chat_provider_id" in body:
                validate_provider_choices(host, {"chat_provider_id": body["chat_provider_id"]})
                selected = 'role' if body['chat_provider_id'] in ('', 'role', 'default') else body['chat_provider_id']
                if selected == host.config['fallback_models'].get('chat'):
                    raise HTTPException(422, '请先更换备用模型，主模型与备用模型不能相同')
                if selected != (host.config.get('chat_provider_id') or 'role'):
                    provider = host.provider if selected == 'role' else host.providers[selected]
                    if not provider.ready:
                        raise HTTPException(422, '无法切换主对话模型：' + provider.unavailable_reason)
            host.config.update({key: body[key] for key in ("enabled", "persona", "operators", "chat_provider_id") if key in body})
            from .model_catalog import update_legacy_model
            update_legacy_model(host.config, host.config['model'], model)
            host.provider.config = host.config["model"]
            host.config.save_config()
            restart_vectors(system)
        return {"ok": True, "model_ready": host.provider_for() is not None}

    @app.post("/api/embedding", dependencies=dependencies)
    async def save_embedding(request: Request):
        body = await request.json()
        if set(body) - {"enabled", "base_url", "model", "api_key"} or not isinstance(body.get("enabled", False), bool):
            raise HTTPException(422, "嵌入配置格式不正确")
        for key in ("base_url", "model", "api_key"):
            if key in body and not isinstance(body[key], str):
                raise HTTPException(422, "嵌入配置必须为文字")
        if body.get("base_url") and not body["base_url"].startswith(("http://", "https://")):
            raise HTTPException(422, "嵌入地址需要 http 或 https 开头")
        async with system.lock:
            before = tuple(host.config["embedding"].get(k) for k in ("base_url", "model"))
            if not body.get("api_key"):
                body.pop("api_key", None)
            host.config["embedding"].update(body)
            host.config['embedding_provider_id'] = ''
            host.embedding.config = host.config["embedding"]
            after = tuple(host.config["embedding"].get(k) for k in ("base_url", "model"))
            if before != after:
                for sidecar in system.spectrum.store._vec_sidecars.values():
                    sidecar.clear()
            host.config.save_config()
            restart_vectors(system)
        return {"ok": True, "ready": host.embedding.ready}

    @app.post("/api/providers/{provider_id}", dependencies=dependencies)
    async def save_provider(provider_id: str, request: Request):
        if not provider_id or provider_id in ("role", "default") or "/" in provider_id or len(provider_id) > 64:
            raise HTTPException(422, "请给独立模型填写其他配置名称")
        body = await request.json()
        validate_model(body)
        async with system.lock:
            target = host.config["providers"].setdefault(provider_id, {})
            from .model_catalog import update_legacy_model
            update_legacy_model(host.config, target, body)
            provider = host._make_provider(target)
            provider.role_config = system.spectrum._cfg
            host.providers[provider_id] = provider
            host.config.save_config()
        return {"ok": True}

    @app.post("/api/providers/{provider_id}/delete", dependencies=dependencies)
    async def delete_provider(provider_id: str):
        async with system.lock:
            if provider_id not in host.providers:
                raise HTTPException(404, "没有这套模型配置")
            choices = {"对话": {"chat_provider_id": host.config.get("chat_provider_id", "")},
                       **system.configs, "人格整理": system.personality.config.values}
            used = [label for label, values in choices.items()
                    if provider_id in provider_choices(values)]
            if provider_id in host.config['fallback_models'].values(): used.append('备用模型')
            if used:
                raise HTTPException(409, "这套模型仍被使用，请先更换选择：" + "、".join(used))
            del host.config["providers"][provider_id]
            del host.providers[provider_id]
            host.config.save_config()
        return {"ok": True}

    @app.post("/api/config/{module}", dependencies=dependencies)
    async def save_module(module: str, request: Request):
        if module not in MODULES:
            raise HTTPException(404, "不存在该模块")
        body = await request.json()
        validate_config(schemas()[module], body)
        for group in body.values():
            if isinstance(group, dict) and "mood_half_life_hours" in group and group["mood_half_life_hours"] <= 0:
                raise HTTPException(422, "心情回落半衰期需大于 0")
        async with system.lock:
            validate_provider_choices(host, body)
            config = system.configs[module]
            for key, value in body.items():
                if isinstance(value, dict) and isinstance(config.get(key), dict):
                    config[key].update(value)
                else:
                    config[key] = value
            config.save_config()
            if module == "spectrum":
                system.spectrum.l2.sync_sleep_setting()
                restart_vectors(system)
            if module == "prism":
                system.prism.personality = system.prism._load_personality()
                system.prism._apply_half_life()
        return {"ok": True}

    @app.post("/api/control/{action}", dependencies=dependencies)
    async def control(action: str):
        from .spectrum_module.spectrum import freeze
        if action not in ("freeze", "thaw"):
            raise HTTPException(404, "未知操作")
        # Freeze interrupts a current model request through its epoch; do not wait for the turn lock.
        return {"ok": True, "message": freeze.manual(action)}

    @app.get("/modules/{module}", dependencies=dependencies, response_class=HTMLResponse)
    async def module_page(module: str):
        if module not in ("spectrum", "prism"):
            raise HTTPException(404, "该模块没有独立数据面板")
        html = (ROOT / MODULES[module] / "pages/dashboard/index.html").read_text(encoding="utf-8")
        bridge = '''<script>const moduleApiRoot="__MODULE_API_ROOT__";const moduleApiUrl=p=>new URL(p,location.origin+moduleApiRoot).pathname;window.RoleModulePage={ready:()=>Promise.resolve(),apiGet:async(p,q={})=>{
          const r=await fetch(moduleApiUrl(p)+'?'+new URLSearchParams(q));if(!r.ok)throw Error('请求失败 '+r.status);return r.json()},
          apiPost:async(p,b)=>{const r=await fetch(moduleApiUrl(p),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});
          if(!r.ok)throw Error('请求失败 '+r.status);return r.json()}};</script>'''
        api_root = '/astrbot_plugin_spectrum_core/' if module == 'spectrum' else '/astrbot_plugin_prism/'
        bridge = bridge.replace('__MODULE_API_ROOT__', api_root)
        html = html.replace('window.AstrBotPluginPage','window.RoleModulePage').replace('AstrBotPluginPage','RoleModulePage')
        html = html.replace('./vendor/vue.global.js', '/assets/vue.global.prod.js')
        return html.replace('<head>', '<head>' + bridge)

    @app.get("/assets/vue.global.prod.js")
    async def vue():
        return FileResponse(ROOT / "assets/vue.global.prod.js", media_type="application/javascript")

    @app.get("/assets/data/{filename}")
    async def data_asset(filename: str):
        types = {"workspace.css": "text/css", "selection.css": "text/css", "selection.js": "application/javascript", "spectrum.js": "application/javascript", "prism.js": "application/javascript"}
        if filename not in types:
            raise HTTPException(404, "资源不存在")
        return FileResponse(ROOT / "assets/data" / filename, media_type=types[filename])

    @app.get("/assets/record-workbench.js")
    async def record_workbench_asset():
        return FileResponse(ROOT / "assets/record-workbench.js", media_type="application/javascript")

    @app.get("/api/guard", dependencies=dependencies)
    async def guard_state():
        store = system.guard.store
        rows = []
        for umo in store.list_umos():
            sid = umo.split(":", 2)[-1]
            label = ("群聊 " + sid if ":GroupMessage:" in umo else
                     system.spectrum.store.last_sender_of(umo) or "联系人 " + sid)
            rows.append({"session": umo, "label": label, "status": store.get_status(umo),
                         "reason": store.get_state(umo,"reason",""), "queued": store.count_outbox(umo),
                         "unread": store.count_inbox(umo), "sent_today": store.sent_today(umo)})
        return {"sessions": rows}

    @app.post("/api/guard/action", dependencies=dependencies)
    async def guard_action(request: Request):
        body = await request.json()
        umo = str(body.get("session", ""))
        if umo not in system.guard.store.list_umos():
            raise HTTPException(404, "未知会话")
        async with system.lock:
            action = body.get("action")
            if action == "probe":
                await system.guard.core.probe(umo)
            elif action == "flush":
                await system.guard.flusher._flush(umo, probe_run=True)
            elif action == "clear":
                system.guard.store.mark_inbox_delivered(umo)
            else:
                raise HTTPException(422, "未知操作")
        return {"ok": True}

    @app.post("/api/selftest", dependencies=dependencies)
    async def selftest():
        import asyncio
        import sys
        process = await asyncio.create_subprocess_exec(sys.executable, "tests/test_integration.py",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            raise HTTPException(500, "离线自检失败，请查看测试实例日志")
        return {"ok": True, "message": "离线自检通过：身份、跨会话来源与日期、承诺、世界书、开关、睡眠、冻结、投递和持久化。未访问真实模型或 QQ。"}


def validate_model(model):
    from .model_catalog import validate_model as validate
    try:
        validate(model)
    except ValueError as error:
        raise HTTPException(422, str(error)) from None


def restart_vectors(system):
    import asyncio
    task = system.spectrum._vector_task
    if task is not None and not task.done():
        task.cancel()
    system.spectrum._vector_task = asyncio.create_task(system.spectrum._vector_backfill())
