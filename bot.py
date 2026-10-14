"""NoneBot 原生入口与三个角色模块的统一管理页面。"""
import os
import secrets
from pathlib import Path
import nonebot
from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from nonebot.adapters.onebot.v11 import Adapter, Bot, MessageEvent
from role_system.host import Host, Platform
from role_system.ports import Event, Plain, NativeSegment
from role_system.service import RoleSystem
from role_system.web import install_panel
from role_system.connections import Connections

panel_key = os.environ["PANEL_KEY"]
data_dir = os.environ.get("ROLE_DATA_DIR", "data/role-system")
from role_system.maintenance import apply_pending
apply_pending(data_dir)
connections = Connections(data_dir, os.environ.get("ONEBOT_ACCESS_TOKEN", ""))
nonebot.init(driver="~fastapi+~websockets", host="0.0.0.0", port=8080,
             **connections.startup(), log_level="INFO")
driver = nonebot.get_driver()
driver.register_adapter(Adapter)
app = driver.server_app
auth = HTTPBearer(auto_error=False)


def authorize(request: Request, credentials: HTTPAuthorizationCredentials = Depends(auth)):
    key = credentials.credentials if credentials else request.cookies.get("role_panel", "")
    if not host.panel_access.verify(key):
        raise HTTPException(401, "管理密钥不正确")


host = Host(data_dir, app, authorize)
host.connections = connections
system = RoleSystem(host)
install_panel(app, host, system, authorize, panel_key)
from nonebot.log import logger as platform_logger
host.runtime_logs.bind_platform(platform_logger)
host.extensions.load(driver)


@app.get("/health")
async def health():
    return {"status": "ready", "platform": "nonebot", "version": "2.5.0", "role_system": "integrated", "role_system_name": "光谱计划", "role_system_version": "0.4.30"}


@app.get("/api/status", dependencies=[Depends(authorize)])
async def status():
    return {"bots": list(nonebot.get_bots()), **system.status(),
            "reverse_ws": "ws://nonebot:8080/onebot/v11/ws"}


driver.on_startup(system.start)
driver.on_startup(host.runtime_logs.start)
driver.on_shutdown(system.close)
driver.on_shutdown(host.runtime_logs.close)


@driver.on_bot_connect
async def connected(bot: Bot):
    host.platforms["qq-" + str(bot.self_id)] = Platform(bot)


@driver.on_bot_disconnect
async def disconnected(bot: Bot):
    host.platforms.pop("qq-" + str(bot.self_id), None)


matcher = nonebot.on_message(priority=10, block=False)


@matcher.handle()
async def receive(bot: Bot, event: MessageEvent):
    if str(event.user_id) == str(bot.self_id):
        return
    group = getattr(event, "group_id", None)
    kind = "GroupMessage" if group is not None else "FriendMessage"
    target = str(group if group is not None else event.user_id)
    sender = event.sender
    name = getattr(sender, "card", "") or getattr(sender, "nickname", "") or str(event.user_id)
    segments=[]
    for seg in event.message:
        if seg.type == "text":
            segments.append(seg.data["text"])
        elif seg.type == "at" and str(seg.data["qq"]) != str(bot.self_id):
            uid=str(seg.data["qq"])
            mentioned_name=host.system.spectrum.store.get_person_name("qq-" + str(bot.self_id) + ":" + uid) or uid
            segments.append("@"+mentioned_name+"("+uid+")")
        elif seg.type in ("image", "record", "video", "file"):
            segments.append("["+{"image":"图片", "record":"语音", "video":"视频", "file":"文件"}[seg.type]+"]")
    text = "".join(segments)
    own = Event(host, "qq-" + str(bot.self_id) + ":" + kind + ":" + target,
                str(event.user_id), name, text, addressed=group is None or event.is_tome(),
                message_id=str(event.message_id))
    # Retain explicit mentions as identity references, without interpreting display names as IDs.
    own.parts = [Plain(text)] + [type("At", (), {"qq": str(seg.data["qq"])})()
                                for seg in event.message if seg.type == "at"] + [NativeSegment(seg.type, dict(seg.data)) for seg in event.message if seg.type in ("image", "record", "video", "file")]
    from role_system.ports import logger
    logger.info("[收到消息] %s | %s(%s): %s", own.unified_msg_origin, name, event.user_id, text)
    outcome = await system.receive(own)
    logger.info("[回合结束] %s | %s", own.unified_msg_origin, outcome)


if __name__ == "__main__":
    nonebot.run()
