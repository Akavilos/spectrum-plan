"""从真实进程入口启动全局初始化，验证先清理后打开日志与数据库。"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from role_system.maintenance import PENDING, write_json

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory) / 'data'
    root.mkdir()
    write_json(root / 'system.json', {'persona': '我是一名旧角色。', 'enabled': False})
    write_json(root / 'panel-access.json', {'key': 'fixture-maintenance-key'})
    with sqlite3.connect(root / 'conversation.db') as db:
        db.execute('CREATE TABLE old_records(text TEXT)')
        db.execute("INSERT INTO old_records VALUES('旧数据')")
    write_json(root / PENDING, {'id': 'f' * 24, 'action': 'global', 'selected': {}})
    env = {**os.environ, 'ROLE_DATA_DIR': str(root), 'PANEL_KEY': 'fixture-initial-key', 'ONEBOT_ACCESS_TOKEN': ''}
    with (Path(directory) / 'boot.log').open('w') as output:
        process = subprocess.Popen([sys.executable, 'run.py'], env=env, stdout=output, stderr=subprocess.STDOUT)
        def api(path):
            request = urllib.request.Request('http://127.0.0.1:8080' + path,
                    headers={'Authorization': 'Bearer fixture-maintenance-key'})
            with urllib.request.urlopen(request, timeout=2) as response:
                return json.load(response)
        try:
            for _ in range(100):
                if process.poll() is not None:
                    raise AssertionError((Path(directory) / 'boot.log').read_text())
                try:
                    state = api('/api/maintenance')
                    break
                except OSError:
                    time.sleep(.1)
            else:
                raise AssertionError('真实入口启动超时')
            assert state['last_result']['ok'] and state['last_result']['id'] == 'f' * 24
            config = api('/api/config')['system']
            assert config['persona'] == '' and not config['enabled']
            assert not (root / PENDING).exists()
            assert (root / 'logs/console.log').exists() and (root / 'logs/runtime.log').exists()
            assert (root / 'backups' / state['last_result']['backup']).exists()
            with sqlite3.connect(root / 'conversation.db') as db:
                assert db.execute('SELECT COUNT(*) FROM messages').fetchone()[0] == 0
            # 初始化后的控制台输出仍可进入日志页。
            for _ in range(30):
                if api('/api/logs?source=console')['entries']:
                    break
                time.sleep(.1)
            else:
                raise AssertionError('初始化后的日志捕获未恢复')
        finally:
            process.terminate()
            process.wait(timeout=25)
    assert not (root / PENDING).exists()
print('PASS maintenance boot: real process, preserved access, new databases, console logs, graceful stop')
