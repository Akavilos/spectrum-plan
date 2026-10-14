"""插件、正则与数据交换的认证管理 API。"""
import asyncio
import json
import os
import signal
import zipfile
from pathlib import Path
from fastapi import Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from .extensions import plugin_id, check_settings
from .output_rules import OutputRules, presets, normalize_rule
from .data_exchange import export_data, validate_import, import_data, snapshot


def install_management(app, host, system, authorize):
    dependencies = [Depends(authorize)]

    def download(value, filename):
        return Response(json.dumps(value, ensure_ascii=False, indent=2), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    def error(exception):
        return HTTPException(422, str(exception))

    @app.get("/assets/management.js")
    async def script():
        return FileResponse(Path(__file__).parent / "assets/management.js", media_type="application/javascript")

    @app.get("/assets/personality.js")
    async def personality_script():
        return FileResponse(Path(__file__).parent / "assets/personality.js", media_type="application/javascript")

    @app.get("/api/extensions", dependencies=dependencies)
    async def plugins():
        return {"plugins": host.extensions.rows()}

    @app.get("/api/extensions/example", dependencies=dependencies)
    async def example():
        from io import BytesIO
        output = BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps({"id": "greeting_example", "name": "问候示例", "kind": "module", "entry": "plugin",
                "description": "注册 /问候示例 指令，演示独立配置与发送接口。", "requirements": [],
                "schema": {"greeting": {"type": "string", "title": "问候内容", "default": "你好！"}}}, ensure_ascii=False))
            archive.writestr("plugin.py", 'from role_system.ports import MessageChain, Plain\n\ndef setup(api):\n    async def greet(event, arguments):\n        await api.send(event.unified_msg_origin, MessageChain([Plain(api.config["greeting"])]))\n    api.command("问候示例", greet)\n')
        return Response(output.getvalue(), media_type="application/zip", headers={"Content-Disposition": 'attachment; filename="greeting-example.zip"'})

    @app.post("/api/extensions/install", dependencies=dependencies)
    async def install(request: Request):
        try:
            async with host.extensions.busy:
                if "application/zip" in request.headers.get("content-type", ""):
                    content = await request.body()
                    if len(content) > 16 * 1024 * 1024: raise ValueError("上传包不能超过 16 MB")
                    await host.extensions.install_zip(content)
                else:
                    await host.extensions.install_package(await request.json())
        except (ValueError, KeyError, zipfile.BadZipFile) as exc: raise error(exc)
        return {"ok": True, "message": "已安装，默认关闭；配置后启用并重启"}

    @app.post("/api/extensions/{pid}", dependencies=dependencies)
    async def plugin_update(pid: str, request: Request):
        body = await request.json()
        try:
            async with host.extensions.busy:
                item = host.extensions.registry["plugins"][plugin_id(pid)]
                action = body.get("action", "save")
                if action == "remove":
                    host.extensions.remove(pid)
                elif action == "save":
                    if not isinstance(body.get("enabled", False), bool): raise ValueError("开关格式错误")
                    values = body.get("settings", {})
                    check_settings(item.get("schema", {}), values)
                    config = host.extensions.settings(pid)
                    config.clear(); config.update(values); config.save_config()
                    item["enabled"] = body.get("enabled", False)
                    item["revision"] = item.get("revision", 0) + 1
                    host.extensions.registry.save_config()
                else: raise ValueError("未知插件操作")
        except (ValueError, KeyError) as exc: raise error(exc)
        return {"ok": True, "message": "已保存；加载状态将在重启后更新"}

    @app.post("/api/restart", dependencies=dependencies)
    async def restart():
        asyncio.get_running_loop().call_later(1, os.kill, os.getpid(), signal.SIGTERM)
        return {"ok": True, "message": "平台即将重启，数秒后刷新页面"}

    @app.get("/api/regex", dependencies=dependencies)
    async def rules():
        return {**host.output_rules.data, "presets": presets()}

    @app.post("/api/regex", dependencies=dependencies)
    async def save_rules(request: Request):
        try:
            value = OutputRules.normalize(await request.json())
            async with system.lock: host.output_rules.save(value)
        except (ValueError, KeyError) as exc: raise error(exc)
        return {"ok": True}

    @app.post("/api/regex/preview", dependencies=dependencies)
    async def preview(request: Request):
        body = await request.json()
        if not isinstance(body.get("text"), str): raise HTTPException(422, "预览文字格式错误")
        try:
            staged = OutputRules(host.data_dir)
            staged.data = OutputRules.normalize({"enabled": True, "rules": body.get("rules", host.output_rules.data["rules"])})
            from .output_rules import compile_rule
            staged.compiled = [(r, compile_rule(r)) for r in staged.data["rules"]]
            text, hits = staged.apply(body["text"], preview=True)
        except ValueError as exc: raise error(exc)
        return {"text": text, "hits": hits}

    @app.get("/api/regex/export", dependencies=dependencies)
    async def export_rules(tavern: bool = False):
        try:
            return download(host.output_rules.tavern_export() if tavern else host.output_rules.data, "output-regex.json")
        except ValueError as exc: raise error(exc)

    @app.post("/api/regex/import", dependencies=dependencies)
    async def import_rules(request: Request):
        body = await request.json()
        try:
            data = body.get("data")
            rows = data if isinstance(data, list) else data.get("rules", data.get("regex_scripts", [data]))
            imported = [normalize_rule(row) for row in rows]
            from uuid import uuid4
            for row in imported: row["id"] = uuid4().hex
            async with system.lock:
                existing = [] if body.get("replace", False) else host.output_rules.data["rules"]
                host.output_rules.save({"enabled": host.output_rules.data["enabled"], "rules": existing + imported})
        except (ValueError, TypeError, AttributeError) as exc: raise error(exc)
        return {"ok": True, "count": len(imported)}

    @app.get("/api/data/export", dependencies=dependencies)
    async def export(section: str = "all"):
        try:
            async with system.lock: value = export_data(host, system, section)
        except ValueError as exc: raise error(exc)
        return download(value, "role-system-data.json")

    @app.post("/api/data/import", dependencies=dependencies)
    async def import_settings(request: Request):
        body = await request.json()
        try:
            sections = validate_import(host, system, body.get("data"))
            async with host.extensions.busy, system.lock:
                backup_dir = host.data_dir / "backups"
                backup_dir.mkdir(exist_ok=True)
                import time
                (backup_dir / f"before-import-{time.time_ns()}.json").write_text(json.dumps(export_data(host, system), ensure_ascii=False), encoding="utf-8")
                changed = import_data(host, system, sections)
        except (ValueError, TypeError, KeyError) as exc: raise error(exc)
        return {"ok": True, "changed": changed, "restart_required": bool(set(changed) & {"settings", "plugins"})}

    @app.get("/api/data/history", dependencies=dependencies)
    async def history():
        async with system.lock:
            host.history_db.row_factory = __import__("sqlite3").Row
            rows = host.history_db.execute("SELECT * FROM messages ORDER BY id").fetchall()
            host.history_db.row_factory = None
            text = "\n".join(json.dumps(dict(row), ensure_ascii=False) for row in rows)
        return Response(text, media_type="application/x-ndjson", headers={"Content-Disposition": 'attachment; filename="conversation.jsonl"'})

    @app.get("/api/data/backup", dependencies=dependencies)
    async def backup(include_secrets: bool = False):
        async with host.extensions.busy, system.lock:
            value = snapshot(host, include_secrets)
        return Response(value, media_type="application/zip", headers={"Content-Disposition": 'attachment; filename="role-system-backup.zip"'})
