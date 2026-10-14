"""管理密钥轮换、登录保持、旧凭证失效和持久化的一次验收。"""
import asyncio
import json
import tempfile
import zipfile
from io import BytesIO
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI, Request, HTTPException
from role_system.host import Host
from role_system.service import RoleSystem
from role_system.web import install_panel
from role_system.panel_access import PanelAccess
from role_system.data_exchange import snapshot
from test_http import ASGIClient


async def run():
    with tempfile.TemporaryDirectory() as directory:
        app = FastAPI()
        def authorize(request: Request):
            key = request.headers.get('authorization', '').removeprefix('Bearer ') or request.cookies.get('role_panel', '')
            if not host.panel_access.verify(key): raise HTTPException(401)
        host = Host(directory, app, authorize); system = RoleSystem(host)
        install_panel(app,host,system,authorize,'initial-fixture-key')
        client = ASGIClient(app)
        assert (await client.post('/api/access/key',{'new_key':'fixture-new-1234','confirm_key':'fixture-new-1234'})).status_code == 401
        assert (await client.post('/api/login',{'key':'initial-fixture-key'})).status_code == 200
        old_cookie = client.cookie
        assert (await client.post('/api/access/key',{'new_key':'fixture-new-1234','confirm_key':'different'})).status_code == 422
        assert (await client.post('/api/access/key',{'new_key':'short','confirm_key':'short'})).status_code == 422
        assert host.panel_access.verify('initial-fixture-key')
        assert (await client.post('/api/access/key',{'new_key':'fixture-new-1234','confirm_key':'fixture-new-1234'})).status_code == 200
        assert (await client.get('/api/config')).status_code == 200
        client.cookie = old_cookie
        assert (await client.get('/api/config')).status_code == 401
        assert (await client.post('/api/login',{'key':'initial-fixture-key'})).status_code == 401
        assert (await client.post('/api/login',{'key':'fixture-new-1234'})).status_code == 200
        assert PanelAccess(Path(directory),'initial-fixture-key').verify('fixture-new-1234')
        assert host.runtime_logs.redact('fixture-new-1234') == '[已隐藏密钥]'
        with zipfile.ZipFile(BytesIO(snapshot(host))) as archive:
            assert 'panel-access.json' not in archive.namelist()
        with zipfile.ZipFile(BytesIO(snapshot(host,True))) as archive:
            assert json.loads(archive.read('panel-access.json'))['key']=='fixture-new-1234'
        await host.close();host.runtime_logs.close()
    print('PASS panel access: current login retained; old cookie/key rejected; custom key survives reload; logs and shared backups exclude it')


if __name__=='__main__':asyncio.run(run())
