"""显式确认维护范围，在旧进程退出后清理，再由正常启动建立新状态。"""
import asyncio
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import sqlite3
import time
import zipfile
from pathlib import Path

from fastapi import Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

PENDING = "maintenance-pending.json"
RESULT = "maintenance-result.json"
DATABASES = {
    "conversation": ("聊天记录", "conversation.db", {
        "messages": ("全部会话的聊天消息", ("messages",)),
    }),
    "spectrum": ("光谱数据", "spectrum/spectrum.db", {
        "experience": ("经历、人物关系与运行状态", (
            "entries", "memories", "persons", "relationship_events", "person_aliases",
            "person_memories", "person_sessions", "relation_history", "lives", "state",
            "intents", "pending", "pending_life_events", "pending_communication",
            "shared_life_events", "director_fires", "drama_threads")),
        "worldbook": ("世界书与激活记录", ("worldbook", "worldbook_state", "worldbook_books", "worldbook_selection")),
        "storylets": ("剧情脚本", ("storylets",)),
        "tools": ("工具使用说明", ("tool_notes",)),
    }),
    "prism": ("棱镜数据", "prism/prism.db", {
        "experience": ("场景、记忆痕迹、联想、人物与心情", (
            "scenes", "traces", "nodes", "edges", "persons", "meta")),
    }),
    "personality": ("人格数据", "personality/personality.db", {
        "profiles": ("角色档案、底稿与底稿版本", ("roles", "core_versions")),
        "learning": ("经历认识、认识版本与学习记录", (
            "insights", "insight_versions", "requests", "learning_queue")),
    }),
    "guard": ("投递哨兵", "guard/guard.db", {
        "state": ("会话送达状态", ("state",)),
        "outbox": ("待补发消息", ("outbox",)),
        "inbox": ("缺席期间的收件记录", ("inbox",)),
        "usage": ("发送用量记录", ("usage",)),
    }),
}


def database_definitions(directory):
    result = dict(DATABASES)
    root = Path(directory).resolve()
    registry = read_json(root / "extensions/registry.json", {"plugins": {}})
    from .extensions import plugin_id
    for pid, item in registry["plugins"].items():
        plugin_id(pid)
        for path in (root / "extensions" / pid / "data").rglob("*"):
            if not path.is_file() or path.is_symlink() or path.suffix not in (".db", ".sqlite", ".sqlite3"):
                continue
            if not path.resolve().is_relative_to(root):
                continue
            relative = path.relative_to(root).as_posix()
            key = "extension-" + hashlib.sha256(relative.encode()).hexdigest()
            result[key] = (f'扩展 · {item.get("name", pid)} · {path.name}', relative,
                           {"all": ("此扩展数据库的全部记录", ("*",))})
    return result


def read_json(path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def catalog(directory):
    """维护面板只公开类别与数量，不公开聊天内容和凭据。"""
    root = Path(directory)
    result = []
    for db_id, (title, relative, groups) in database_definitions(root).items():
        rows = []
        path = root / relative
        counts = {}
        if path.exists():
            with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
                tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                for _, names in groups.values():
                    for name in names:
                        if name == "*":
                            counts[name] = sum(db.execute('SELECT COUNT(*) FROM "' + table[1].replace('"', '""') + '"').fetchone()[0]
                                               for table in db.execute("PRAGMA table_list").fetchall()
                                               if table[0] == "main" and table[2] == "table" and not table[1].startswith("sqlite_"))
                            continue
                        if name in tables and name not in counts:
                            counts[name] = db.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
        for group_id, (label, names) in groups.items():
            rows.append({"id": group_id, "label": label, "count": sum(counts.get(n, 0) for n in names)})
        result.append({"id": db_id, "title": title, "groups": rows})
    return result


def plan(directory, action, selected=None):
    if action not in ("settings", "databases", "global"):
        raise ValueError("未知初始化操作")
    selected = selected or {}
    if not isinstance(selected, dict):
        raise ValueError("请选择要清理的数据类别")
    choices = {}
    definitions = database_definitions(directory)
    for db_id, groups in selected.items():
        if db_id not in definitions or not isinstance(groups, list) or any(g not in definitions[db_id][2] for g in groups):
            raise ValueError("包含未知数据类别")
        if groups:
            choices[db_id] = sorted(set(groups))
    if action != "databases" and choices:
        raise ValueError("此操作不使用数据库选择范围")
    if action == "databases" and not choices:
        raise ValueError("请先选择要清理的数据")
    notes = []
    if "experience" in choices.get("spectrum", []):
        choices["personality"] = sorted(set(choices.get("personality", [])) | {"learning"})
        choices["guard"] = sorted(set(choices.get("guard", [])) | {"outbox"})
        notes.append("光谱经历清理会同步删除人格学习结果与待补发消息，避免旧经历重新注入或再次发送；人格底稿保留。")
    if "profiles" in choices.get("personality", []):
        choices["personality"] = ["learning", "profiles"]
        notes.append("删除角色档案会同时清理其经历认识，重启后建立一个空白档案。")
    titles = {"settings": "恢复默认设置", "databases": "清空所选数据", "global": "全局初始化"}
    if action == "databases" and all(set(choices.get(db_id, [])) == set(groups)
                                      for db_id, (_, _, groups) in definitions.items()):
        titles["databases"] = "清空全部数据库"
    if action == "settings":
        notes.extend([
            "恢复系统开关、模型与备用模型、模块、人格学习、正则开关、连接及扩展插件设置；模型接口与密钥需要重新配置。",
            "保留所有数据库、人设原文、人格底稿、世界书、剧情脚本、工具说明和正则规则正文；正则规则全部停用。",
            "已安装扩展插件保留并停用。网页登录密钥保留。",
        ])
    elif action == "global":
        notes.extend([
            "删除全部角色数据、设置、模型与密钥、人设、世界书、自定义正则、日志和已安装的额外插件，回到首次安装状态。",
            "网页登录密钥与操作前备份保留；QQ 客户端账号和其他机器人不受影响。",
        ])
    else:
        notes.append("清理所选类别的全部记录，包括所有会话与未展示的数据；设置、人设和未选类别保留。")
    if action in ("settings", "global"):
        notes.append("连接恢复默认反向 WebSocket；使用自定义正向连接时，需要重新配置。")
    notes.append("确认后先生成私有备份，再自动重启以同步内存。备份含密钥，请勿公开分享。")
    labels = []
    for row in catalog(directory):
        for group in row["groups"]:
            if group["id"] in choices.get(row["id"], []):
                labels.append(f'{row["title"]} · {group["label"]}（当前 {group["count"]} 条）')
    return {"action": action, "title": titles[action], "selected": choices, "scope": labels, "notes": notes}


def remove_path(root, path):
    """所有删除目标都由实例根目录下的固定路径或目录枚举产生。"""
    if path.is_symlink():
        path.unlink()
        return
    if not path.resolve().is_relative_to(root.resolve()) or path.resolve() == root.resolve():
        raise ValueError("维护目标超出当前实例")
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def reset_settings(root):
    system = read_json(root / "system.json", {})
    write_json(root / "system.json", {"persona": system.get("persona", "")})
    for name in ("spectrum", "prism", "guard"):
        (root / name / "config.json").unlink(missing_ok=True)
    personality = read_json(root / "personality/config.json", {})
    write_json(root / "personality/config.json", {"role_id": personality.get("role_id", "")})
    rules = read_json(root / "output_rules.json")
    if rules:
        rules["enabled"] = False
        for rule in rules["rules"]:
            rule["enabled"] = False
        write_json(root / "output_rules.json", rules)
    (root / "connections.json").unlink(missing_ok=True)
    registry = read_json(root / "extensions/registry.json", {"plugins": {}})
    for pid, item in registry["plugins"].items():
        from .extensions import plugin_id
        plugin_id(pid)
        item["enabled"] = False
        item["revision"] = item.get("revision", 0) + 1
        write_json(root / "extensions" / pid / "config.json", {
            key: field["default"] for key, field in item.get("schema", {}).items() if "default" in field})
    write_json(root / "extensions/registry.json", registry)


def delete_database(root, relative):
    path = root / relative
    for suffix in ("", "-wal", "-shm", "-journal"):
        remove_path(root, path.with_name(path.name + suffix))
    for sidecar in path.parent.glob(path.name + ".vectors_*"):
        remove_path(root, sidecar)


def clear_databases(root, selected):
    if set(selected) & {'spectrum','prism','personality'}:
        (root/'active_memory_vectors.json').unlink(missing_ok=True)
    definitions = database_definitions(root)
    for db_id in definitions:
        if db_id not in selected:
            continue
        group_ids = selected[db_id]
        _, relative, groups = definitions[db_id]
        if set(group_ids) == set(groups):
            delete_database(root, relative)
        else:
            path = root / relative
            names = {name for group_id in group_ids for name in groups[group_id][1]}
            if path.exists():
                with sqlite3.connect(path) as db:
                    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    for name in sorted(names & tables):
                        db.execute(f'DELETE FROM "{name}"')
                    if "sqlite_sequence" in tables:
                        for name in names:
                            db.execute("DELETE FROM sqlite_sequence WHERE name=?", (name,))
                    if db_id == "personality" and "learning" in group_ids:
                        entry_db = root / DATABASES["spectrum"][1]
                        watermark = 0
                        if entry_db.exists():
                            with sqlite3.connect(entry_db) as source:
                                watermark = source.execute("SELECT COALESCE(MAX(id),0) FROM entries").fetchone()[0]
                        db.execute("UPDATE roles SET watermark=?, scanned=?", (watermark, watermark))
                for name in names:
                    (path.parent / f"{path.name}.vectors_{name}.json").unlink(missing_ok=True)
        if db_id == "personality" and "profiles" in group_ids:
            values = read_json(root / "personality/config.json", {})
            values["role_id"] = ""
            write_json(root / "personality/config.json", values)


def offline_backup(root, job_id):
    directory = root / "backups"
    directory.mkdir(exist_ok=True, mode=0o700)
    destination = directory / f"before-maintenance-{job_id}.zip"
    if destination.exists():
        return destination
    temporary = destination.with_suffix(".tmp")
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in root.rglob("*"):
            relative = path.relative_to(root)
            if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(root) and not any(
                part in ("backups", "__pycache__", "dependencies") for part in relative.parts
            ) and path.name not in (PENDING, RESULT):
                archive.write(path, str(relative))
    os.chmod(temporary, 0o600)
    temporary.replace(destination)
    return destination


def apply_pending(directory):
    """在 Connections、Host 和任何数据库连接创建前调用；重复启动可继续未完成清理。"""
    root = Path(directory).resolve()
    marker = root / PENDING
    if not marker.exists():
        return
    job = read_json(marker)
    if not isinstance(job.get("id"), str) or not re.fullmatch(r"[a-f0-9]{24}", job["id"]):
        raise ValueError("维护任务标识不正确")
    # 重新验证固定类别，磁盘标记不允许指定任意路径或 SQL。
    operation = plan(root, job["action"], job["selected"])
    backup = offline_backup(root, job["id"])
    if operation["action"] == "settings":
        reset_settings(root)
    elif operation["action"] == "databases":
        clear_databases(root, operation["selected"])
    else:
        for path in root.iterdir():
            if path.name not in ("panel-access.json", "backups", PENDING, RESULT):
                remove_path(root, path)
    write_json(root / RESULT, {"id": job["id"], "ok": True, "title": operation["title"],
                             "backup": backup.name, "completed_at": time.time()})
    marker.unlink()


def schedule_restart():
    asyncio.get_running_loop().call_later(1, os.kill, os.getpid(), signal.SIGTERM)


def install(app, host, system, authorize):
    dependencies = [Depends(authorize)]
    confirmations = {}
    host.maintenance_pending = False

    @app.get("/assets/maintenance.js")
    async def script():
        return FileResponse(Path(__file__).parent / "assets/maintenance.js", media_type="application/javascript")

    @app.get("/api/maintenance", dependencies=dependencies)
    async def state():
        return {"databases": catalog(host.data_dir), "pending": host.maintenance_pending,
                "last_result": read_json(host.data_dir / RESULT)}

    @app.post("/api/maintenance/prepare", dependencies=dependencies)
    async def prepare(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(422, "操作需要 JSON 对象")
        try:
            operation = plan(host.data_dir, body.get("action"), body.get("selected"))
        except (ValueError, TypeError) as error:
            raise HTTPException(422, str(error)) from None
        now = time.monotonic()
        for key in list(confirmations):
            if confirmations[key][0] < now:
                del confirmations[key]
        token = secrets.token_urlsafe(24)
        confirmations[token] = (now + 300, operation)
        return {**operation, "token": token, "expires_in": 300}

    @app.post("/api/maintenance/commit", dependencies=dependencies)
    async def commit(request: Request):
        body = await request.json()
        if not isinstance(body, dict) or not isinstance(body.get("token", ""), str):
            raise HTTPException(422, "确认格式不正确")
        async with host.extensions.busy, system.lock:
            if host.maintenance_pending:
                raise HTTPException(409, "已有维护操作等待重启，请勿重复提交")
            confirmation = confirmations.get(body.get("token", ""))
            if not confirmation or confirmation[0] < time.monotonic():
                raise HTTPException(409, "确认已过期，请重新查看清理范围")
            if body.get("confirmation") != confirmation[1]["title"]:
                raise HTTPException(422, "请按确认窗口填写操作名称")
            job_id = secrets.token_hex(12)
            operation = confirmation[1]
            try:
                plan(host.data_dir, operation["action"], operation["selected"])
            except (ValueError, TypeError) as error:
                raise HTTPException(409, "范围已改变，请重新确认：" + str(error)) from None
            write_json(host.data_dir / PENDING, {"id": job_id, "action": operation["action"],
                                                "selected": operation["selected"]})
            host.maintenance_pending = True
            confirmations.clear()
            schedule_restart()
        return {"ok": True, "id": job_id, "message": "已确认，平台正在重启并执行维护"}

    @app.middleware("http")
    async def maintenance_gate(request, call_next):
        if host.maintenance_pending and request.method not in ("GET", "HEAD") and request.url.path not in (
            "/api/maintenance/commit", "/api/logout"
        ):
            return JSONResponse({"detail": "维护已确认，重启完成前暂停修改"}, status_code=409)
        return await call_next(request)
