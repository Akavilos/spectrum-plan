"""验证模型用途接线、配置交换与连接持久化；模型 HTTP 使用替身。"""
import asyncio
import io
import json
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI, Request, HTTPException
from role_system.host import Host
from role_system.service import RoleSystem
from role_system.web import install_panel
from role_system.data_exchange import export_data, validate_import, import_data
from role_system.connections import Connections
from role_system.ports import Event
from role_system.spectrum_module.spectrum import settings, freeze
from role_system.personality.learning import DRAFT_PROMPT
from role_system.prism_module.prism.provider_pick import pick
from test_http import ASGIClient


class Wire:
    def __init__(self): self.calls = []
    def __call__(self, request, **kwargs):
        body = json.loads(request.data)
        self.calls.append((request.full_url, request.get_header('Authorization'), body))
        system = body['messages'][0].get('content', '')
        if system == DRAFT_PROMPT: content = json.dumps({'base': ['我喜欢读书。']}, ensure_ascii=False)
        elif body['model'] == 'title': content = json.dumps({'title': '闲聊', 'summary': '我与朋友聊天。'}, ensure_ascii=False)
        elif body['model'] == 'compression': content = json.dumps({'state': '', 'facts': [], 'views': [], 'refs': []})
        else: content = '<life>我接着聊。</life><reply>{"seen":true,"reply":{"mode":"immediate","content":"吃过了，你呢？"}}</reply>'
        return io.BytesIO(json.dumps({'choices': [{'message': {'role': 'assistant', 'content': content}}]}).encode())


async def run():
    with tempfile.TemporaryDirectory() as directory, patch('urllib.request.urlopen', Wire()) as wire:
        app = FastAPI()
        def authorize(request: Request):
            if request.cookies.get('role_panel') != 'fixture-panel-key': raise HTTPException(401)
        host = Host(directory, app, authorize); system = RoleSystem(host)
        install_panel(app, host, system, authorize, 'fixture-panel-key')
        # 不启动后台任务，让每一次用途调用可精确追踪。
        async with ASGIClient(app) as client:
            assert (await client.get('/api/connections/token')).status_code == 401
            await client.post('/api/login', {'key': 'fixture-panel-key'})
            for name in ['chat', 'prism', 'compression', 'title', 'advance', 'director', 'personality']:
                result = await client.post('/api/providers/' + name, {'base_url': f'https://{name}.invalid/v1', 'model': name, 'api_key': name + '-secret', 'family': 'other'})
                assert result.status_code == 200, result.text
            assert (await client.post('/api/config/system', {'chat_provider_id': 'chat', 'enabled': True, 'persona': '我喜欢读书。'})).status_code == 200
            assert host.provider_for() is host.providers['chat'] and (await host.get_using_provider_async()) is host.providers['chat']
            assert (await client.post('/api/config/system', {'chat_provider_id': 'missing'})).status_code == 422
            assert (await client.post('/api/providers/chat/delete', {})).status_code == 409
            assert (await client.post('/api/config/prism', {'basic': {'provider_id': 'prism', 'enabled': False}})).status_code == 200
            assert await pick(host, 'fixture', 'prism') is host.providers['prism']
            body = {}
            for key, value in [('enabled', True), ('compression_provider_id', 'compression'), ('life_summary_provider_id', 'title'), ('advance_provider_id', 'advance'), ('director_provider_id', 'director'), ('advance_model_override', 'legacy-name')]:
                body.setdefault(settings.FIELD_GROUP[key], {})[key] = value
            assert (await client.post('/api/config/spectrum', body)).status_code == 200
            assert (await client.post('/api/config/spectrum', {'memory': {'compression_provider_id': 'missing'}})).status_code == 422
            umo = 'qq-90001:FriendMessage:10001'
            sent = []
            async def send(umo, chain): sent.append(chain); return {'message_id': len(sent)}
            host.transport = send
            assert await system.receive(Event(host, umo, '10001', '甲', '吃饭了吗')) == 'sent'
            assert wire.calls[-1][2]['model'] == 'chat'
            await system.generate_background(umo, provider_id='advance', prompt='继续生活', system_prompt='<content_output>')
            assert wire.calls[-1][2]['model'] == 'advance'
            await system.spectrum.l2._compress_continuity(umo, [], [], time.time())
            assert wire.calls[-1][2]['model'] == 'compression'
            lid = system.spectrum.l2.life.ensure_life()
            for n in range(3): system.spectrum.store.add_entry(umo, 'received', '你好', life_id=lid)
            assert await system.spectrum.l2._fill_life_title(lid, now=time.time())
            assert wire.calls[-1][2]['model'] == 'title'
            await system.spectrum.l2.director._call_model('整理剧情')
            assert wire.calls[-1][2]['model'] == 'director'
            system.personality.save({'settings': {**system.personality.config.values, 'provider_id': 'personality'}, 'base': [], 'locked': False}, confirm=True)
            assert await system.personality.draft() == ['我喜欢读书。']
            assert wire.calls[-1][2]['model'] == 'personality'
            for url, token, body in wire.calls:
                name = body['model']; assert url == f'https://{name}.invalid/v1/chat/completions' and token == 'Bearer ' + name + '-secret'
            exported = export_data(host, system, 'settings')
            assert 'secret' not in json.dumps(exported)
            import_data(host, system, validate_import(host, system, exported))
            assert host.provider_for().config['api_key'] == 'chat-secret'
            assert host.provider_for('compression').config['api_key'] == 'compression-secret'
            # 模型与用途可以在同一份文件中导入，不能先验证后出现半写入。
            exported['sections']['settings']['system']['providers']['imported'] = {'base_url': 'https://imported.invalid/v1', 'model': 'imported'}
            exported['sections']['settings']['modules']['spectrum']['memory']['compression_provider_id'] = 'imported'
            import_data(host, system, validate_import(host, system, exported))
            assert system.spectrum._cfg('compression_provider_id', '') == 'imported'
            assert (await client.post('/api/providers/imported/delete', {})).status_code == 409
            assert (await client.post('/api/providers/unused', {'base_url': 'https://unused.invalid/v1', 'model': 'unused'})).status_code == 200
            assert (await client.post('/api/providers/unused/delete', {})).status_code == 200
            assert (await client.post('/api/connections', {'client': 'napcat', 'mode': 'forward_ws', 'ws_url': 'http://invalid', 'access_token': 'connection-secret'})).status_code == 422
            assert (await client.post('/api/connections', {'client': 'napcat', 'mode': 'forward_ws', 'ws_url': 'ws://napcat:3001', 'access_token': 'connection-secret'})).status_code == 200
            view = (await client.get('/api/connections')).json()
            assert view['restart_required'] and 'connection-secret' not in json.dumps(view)
            restarted = Connections(directory)
            assert restarted.startup() == {'onebot_v11_access_token': 'connection-secret', 'onebot_v11_ws_urls': ['ws://napcat:3001']}
            restarted.save({'mode': 'reverse_ws', 'access_token': ''})
            assert restarted.values['access_token'] == 'connection-secret' and not restarted.startup()['onebot_v11_ws_urls']
            assert (await client.get('/api/connections/token')).json()['access_token'] == 'connection-secret'
            for asset in ['models', 'connections']: assert (await client.get('/assets/' + asset + '.js')).status_code == 200
        await system.close()
        print('PASS: selected chat/background/compression/title/director/personality interfaces and keys, registry import/delete, connection persistence and auth')


if __name__ == '__main__': asyncio.run(run())
