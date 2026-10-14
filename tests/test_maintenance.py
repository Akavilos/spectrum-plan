"""临时实例验证确认、范围隔离、备份与退出后的重新初始化；不连接模型或 QQ。"""
import asyncio
import hashlib
import json
import sqlite3
import sys
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI, HTTPException, Request
from role_system.host import Host
from role_system.service import RoleSystem
from role_system.web import install_panel
from role_system.maintenance import DATABASES, PENDING, RESULT, apply_pending, catalog, plan, write_json
from role_system.ports import Event, MessageChain, Plain
from role_system.spectrum_module.spectrum import freeze
from test_http import ASGIClient


def checksum(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def count(root, relative, table):
    with sqlite3.connect(root / relative) as db:
        return db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]


async def instance(root):
    app = FastAPI()
    def authorize(request: Request):
        if request.cookies.get('role_panel') != 'fixture-panel-key':
            raise HTTPException(401)
    host = Host(root, app, authorize)
    system = RoleSystem(host)
    install_panel(app, host, system, authorize, 'fixture-panel-key')
    await system.start()
    return app, host, system


def seed(host, system):
    host.panel_access.save('fixture-panel-key')
    host.config['persona'] = '我喜欢热闹，也有自己的主意。'
    host.config['enabled'] = True
    host.config['operators'] = ['10001']
    host.config.save_config()
    host.append_history('fixture:FriendMessage:10001', 'user', '今晚吃面吗？')
    system.spectrum.store.add_entry('fixture:FriendMessage:10001', 'received', '今晚吃面吗？')
    system.spectrum.store.upsert_worldbook(title='世界书保留测试', text='我住在测试小镇。')
    system.spectrum.store.add_factual_memory('fixture:FriendMessage:10001', '我知道他喜欢面。')
    host.output_rules.save({'enabled': True, 'rules': [{'id': 'custom', 'name': '自定义',
        'pattern': '废话', 'replacement': '', 'enabled': True}]})
    system.guard.store.push_outbox('fixture:FriendMessage:10001', '待补发测试', 'fixture')
    config = system.personality.config
    system.personality.store.db.execute("UPDATE roles SET base='[\"我喜欢热闹。\"]'")
    system.personality.store.db.commit()
    config.save({**config.values, 'enabled': True})
    host.connections.save({'client': 'napcat', 'mode': 'forward_ws', 'ws_url': 'ws://fixture.invalid:3001', 'access_token': 'fixture-token'})


def queue(root, action, choices=None, suffix='a'):
    operation = plan(root, action, choices)
    write_json(root / PENDING, {'id': suffix * 24, 'action': action, 'selected': operation['selected']})


@patch('role_system.maintenance.schedule_restart')
async def run(restart):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        app, host, system = await instance(root)
        seed(host, system)
        async with ASGIClient(app) as client:
            assert (await client.get('/api/maintenance')).status_code == 401
            await client.post('/api/login', {'key': 'fixture-panel-key'})
            assert len((await client.get('/api/maintenance')).json()['databases']) == 5
            assert (await client.post('/api/maintenance/prepare', {'action': 'databases', 'selected': {}})).status_code == 422
            assert (await client.post('/api/maintenance/prepare', {'action': 'databases', 'selected': {'../../other': ['all']}})).status_code == 422
            assert (await client.post('/api/maintenance/commit', {'token': 'invented', 'confirmation': '全局初始化'})).status_code == 409
            prepared = (await client.post('/api/maintenance/prepare', {'action': 'settings'})).json()
            assert not (root / PENDING).exists()  # 打开或取消确认窗口没有写操作。
            assert (await client.post('/api/maintenance/commit', {'token': prepared['token'], 'confirmation': '错误范围'})).status_code == 422
            with patch('role_system.maintenance.time.monotonic', return_value=10**15):
                assert (await client.post('/api/maintenance/commit', {'token': prepared['token'], 'confirmation': '恢复默认设置'})).status_code == 409
            prepared = (await client.post('/api/maintenance/prepare', {'action': 'settings'})).json()
            response = await client.post('/api/maintenance/commit', {'token': prepared['token'], 'confirmation': '恢复默认设置'})
            assert response.status_code == 200, response.text
            restart.assert_called_once()
            assert (await client.post('/api/config/spectrum', {'basic': {'enabled': False}})).status_code == 409
            assert (await client.post('/api/maintenance/commit', {'token': prepared['token'], 'confirmation': '恢复默认设置'})).status_code == 409
            assert await system.receive(Event(host, 'fixture:FriendMessage:10001', '10001', '测试人', '你好')) == 'maintenance'
            try:
                await host.send_message('fixture:FriendMessage:10001', MessageChain([Plain('不可发送')]))
            except freeze.FrozenSkipped:
                pass
            else:
                raise AssertionError('维护期间发送没有被阻止')
        await system.close()
        db_hashes = {relative: checksum(root / relative) for _, relative, _ in DATABASES.values()}
        persona = json.loads((root / 'system.json').read_text())['persona']
        apply_pending(root)
        assert db_hashes == {relative: checksum(root / relative) for relative in db_hashes}, '恢复设置修改了数据库字节'
        assert json.loads((root / 'system.json').read_text()) == {'persona': persona}
        assert json.loads((root / 'output_rules.json').read_text())['rules'][0]['pattern'] == '废话'
        assert not json.loads((root / 'output_rules.json').read_text())['rules'][0]['enabled']
        assert (root / 'panel-access.json').exists()
        app, host, system = await instance(root)
        assert not host.config['enabled'] and not host.provider.ready
        assert not system.personality.config.values['enabled']
        assert system.personality.role()['base'] == ['我喜欢热闹。']
        assert count(root, 'conversation.db', 'messages') == 1
        assert len(system.spectrum.store.worldbook_all()) == 1
        await system.close()

        # 世界书单类清理不影响往来、人格、聊天与设置；检索触发器同步清理。
        before = checksum(root / 'system.json')
        queue(root, 'databases', {'spectrum': ['worldbook']}, 'b')
        apply_pending(root)
        assert checksum(root / 'system.json') == before
        assert count(root, 'spectrum/spectrum.db', 'worldbook') == 0
        assert count(root, 'spectrum/spectrum.db', 'entries') == 1
        assert count(root, 'spectrum/spectrum.db', 'memories') == 1
        with sqlite3.connect(root / 'spectrum/spectrum.db') as db:
            assert not db.execute("SELECT rowid FROM worldbook_fts WHERE worldbook_fts MATCH '测试'").fetchall()

        # 来源清理按固定依赖顺序处理，旧学习结果和待发内容不会回流。
        queue(root, 'databases', {'personality': ['learning'], 'spectrum': ['experience']}, 'c')
        apply_pending(root)
        assert count(root, 'spectrum/spectrum.db', 'entries') == 0
        assert count(root, 'spectrum/spectrum.db', 'memories') == 0
        assert count(root, 'guard/guard.db', 'outbox') == 0
        assert count(root, 'personality/personality.db', 'roles') == 1
        with sqlite3.connect(root / 'personality/personality.db') as db:
            assert db.execute('SELECT watermark,scanned FROM roles').fetchone() == (0, 0)

        # 扩展数据库进入统一目录，清空只涉及数据，扩展代码与配置保留。
        extension = root / 'extensions/fixture/data'
        extension.mkdir(parents=True)
        write_json(root / 'extensions/registry.json', {'plugins': {'fixture': {'name': '测试扩展', 'enabled': False}}})
        with sqlite3.connect(extension / 'extra.db') as db:
            db.execute('CREATE TABLE evidence(id INTEGER)'); db.execute('INSERT INTO evidence VALUES(1)')
        rows = catalog(root)
        extra = next(row for row in rows if row['id'].startswith('extension-'))
        assert extra['groups'][0]['count'] == 1

        all_selected = {row['id']: [group['id'] for group in row['groups']] for row in rows}
        queue(root, 'databases', all_selected, 'd')
        apply_pending(root)
        assert not (extension / 'extra.db').exists()
        assert (root / 'extensions/registry.json').exists()
        app, host, system = await instance(root)
        assert count(root, 'conversation.db', 'messages') == 0
        assert count(root, 'spectrum/spectrum.db', 'entries') == 0
        assert count(root, 'prism/prism.db', 'scenes') == 0
        assert system.personality.role()['base'] == []
        assert host.config['persona'] == persona
        await system.close()

        # 全局初始化删除作者内容、额外插件与日志，独立的登录密钥和备份保留。
        outside = root.parent / (root.name + '-outside.txt')
        outside.write_text('other instance untouched')
        try:
            key_hash = checksum(root / 'panel-access.json')
            queue(root, 'global', suffix='e')
            apply_pending(root)
            assert checksum(root / 'panel-access.json') == key_hash
            assert set(p.name for p in root.iterdir()) == {'panel-access.json', 'backups', RESULT}
            assert outside.read_text() == 'other instance untouched'
            archive_path = root / 'backups' / json.loads((root / RESULT).read_text())['backup']
            with zipfile.ZipFile(archive_path) as archive:
                assert 'system.json' in archive.namelist() and 'conversation.db' in archive.namelist()
            apply_pending(root)  # 完成的操作不会再次执行。
            app, host, system = await instance(root)
            assert host.config['persona'] == '' and not host.config['enabled']
            assert host.config['providers'] == {} and host.config['fallback_models'] == {}
            assert system.spectrum.store.worldbook_all() == []
            assert system.personality.role()['base'] == []
            assert host.extensions.registry['plugins'] == {}
            assert not host.output_rules.data['enabled']
            await system.close()
        finally:
            outside.unlink()
    print('PASS maintenance: confirmation, settings/data separation, dependencies, backups, restart, fresh initialization')


asyncio.run(run())
