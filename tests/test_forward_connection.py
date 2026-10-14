"""真实启动入口的正向 WebSocket 模拟：不连接 QQ，不请求真实模型。"""
import asyncio
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
import websockets
from test_protocol import request, KEY


async def verify():
    completed = asyncio.Event()
    failures = []
    token = 'fixture-forward-token'
    async def client(websocket):
        try:
            assert websocket.request.headers['Authorization'] == 'Bearer ' + token, 'forward token mismatch'
            # 正向协议端首先上报 connect 生命周期事件，框架据此登记账号。
            await websocket.send(json.dumps({'time': int(time.time()), 'self_id': 999999999, 'post_type': 'meta_event', 'meta_event_type': 'lifecycle', 'sub_type': 'connect'}))
            await asyncio.sleep(.1)
            event = {'time': int(time.time()), 'self_id': 999999999, 'post_type': 'message', 'message_type': 'private', 'sub_type': 'friend',
                     'user_id': 10001, 'message_id': 1, 'message': [{'type': 'text', 'data': {'text': '晚饭吃了吗'}}], 'raw_message': '晚饭吃了吗',
                     'font': 0, 'sender': {'user_id': 10001, 'nickname': '模拟联系人'}}
            await websocket.send(json.dumps(event, ensure_ascii=False))
            action = json.loads(await asyncio.wait_for(websocket.recv(), 8))
            assert action['action'] == 'send_private_msg', action
            assert action['params']['user_id'] == 10001
            assert ''.join(part['data']['text'] for part in action['params']['message']) == '吃过了，你呢？'
            await websocket.send(json.dumps({'status': 'ok', 'retcode': 0, 'data': {'message_id': 100}, 'echo': action['echo']}))
            await asyncio.sleep(.1)
            assert request('/api/status')['bots'] == ['999999999']
            completed.set()
            await websocket.wait_closed()
        except Exception as error:
            failures.append(repr(error)); completed.set()

    with tempfile.TemporaryDirectory() as directory:
        data = Path(directory) / 'data'; data.mkdir()
        async with websockets.serve(client, '127.0.0.1', 0) as server:
            port = server.sockets[0].getsockname()[1]
            (data / 'connections.json').write_text(json.dumps({'client': 'napcat', 'mode': 'forward_ws', 'ws_url': f'ws://127.0.0.1:{port}', 'access_token': token}))
            env = {**os.environ, 'PANEL_KEY': KEY, 'ONEBOT_ACCESS_TOKEN': 'different-old-token', 'ROLE_DATA_DIR': str(data)}
            log_path = Path(directory) / 'native.log'
            with log_path.open('w') as log:
                process = subprocess.Popen(['python', 'tests/protocol_boot.py'], env=env, stdout=log, stderr=subprocess.STDOUT)
                try:
                    await asyncio.wait_for(completed.wait(), 12)
                    assert not failures, failures
                    print('PASS: native forward WebSocket, saved token overrides environment, private message and delivery receipt')
                except Exception:
                    print(log_path.read_text()); raise
                finally:
                    process.terminate(); await asyncio.to_thread(process.wait, timeout=8)


if __name__ == '__main__': asyncio.run(verify())
