"""OneBot 连接使用公开启动配置；保存与当前已生效配置分别展示。"""
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from fastapi import Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

CLIENTS = {'napcat': 'NapCat', 'lagrange': 'Lagrange', 'onebot': '其他 OneBot v11 客户端'}


class Connections:
    def __init__(self, data_dir, token=''):
        self.path = Path(data_dir) / 'connections.json'
        self.values = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {
            'client': 'napcat', 'mode': 'reverse_ws', 'ws_url': '', 'access_token': token}
        self.active = dict(self.values)

    def startup(self):
        # v11 专用配置项优先于通用环境值，使保存后的连接令牌真正生效。
        return {'onebot_v11_access_token': self.values['access_token'] or None,
                'onebot_v11_ws_urls': [self.values['ws_url']] if self.values['mode'] == 'forward_ws' else []}

    def save(self, body):
        if not isinstance(body, dict) or set(body) - {'client', 'mode', 'ws_url', 'access_token'}:
            raise ValueError('未知连接设置')
        values = {**self.values, **body}
        if values['client'] not in CLIENTS or values['mode'] not in ('reverse_ws', 'forward_ws'):
            raise ValueError('请选择支持的客户端和连接方式')
        if not isinstance(values['access_token'], str) or not isinstance(values['ws_url'], str):
            raise ValueError('连接地址和令牌需要填写文字')
        # 空白密码输入保留已存令牌；协议端和角色平台必须使用相同令牌。
        values['access_token'] = body.get('access_token') or self.values['access_token']
        values['ws_url'] = values['ws_url'].strip()
        if values['mode'] == 'forward_ws':
            url = urlsplit(values['ws_url'])
            if url.scheme not in ('ws', 'wss') or not url.hostname or url.username or url.password or url.fragment or url.port == 0:
                raise ValueError('正向连接填写 ws:// 或 wss:// 地址，不在地址中填写账号密码')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temporary, self.path)
        self.values = values

    def view(self, host):
        return {'settings': {k: v for k, v in self.values.items() if k != 'access_token'},
                'has_token': bool(self.values['access_token']),
                'restart_required': self.values != self.active,
                'active': {k: v for k, v in self.active.items() if k != 'access_token'},
                'bots': [p.self_id for p in host.platforms.values()],
                'clients': CLIENTS, 'reverse_path': '/onebot/v11/ws',
                'container_url': 'ws://nonebot:8080/onebot/v11/ws'}


def install(app, host, authorize):
    dependencies = [Depends(authorize)]

    @app.get('/assets/connections.js')
    async def script():
        return FileResponse(Path(__file__).parent / 'assets/connections.js', media_type='application/javascript')

    @app.get('/api/connections', dependencies=dependencies)
    async def view():
        return host.connections.view(host)

    @app.get('/api/connections/token', dependencies=dependencies)
    async def token():
        return JSONResponse({'access_token': host.connections.values['access_token']}, headers={'Cache-Control': 'no-store'})

    @app.post('/api/connections', dependencies=dependencies)
    async def save(request: Request):
        try: host.connections.save(await request.json())
        except ValueError as error: raise HTTPException(422, str(error))
        return {'ok': True, **host.connections.view(host)}
