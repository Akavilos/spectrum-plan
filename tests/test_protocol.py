"""OneBot reverse-WebSocket simulation using the actual native entry; no QQ/LLM network."""
import asyncio
import json
import os
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path
import websockets

KEY='fixture-protocol-key'


def request(path):
    req=urllib.request.Request('http://127.0.0.1:8080'+path,headers={'Authorization':'Bearer '+KEY})
    with urllib.request.urlopen(req,timeout=2) as response:return json.load(response)


async def verify():
    with tempfile.TemporaryDirectory() as directory:
        env={**os.environ,'PANEL_KEY':KEY,'ONEBOT_ACCESS_TOKEN':KEY,'ROLE_DATA_DIR':directory+'/data'}
        log_path=Path(directory)/'native.log'
        with log_path.open('w') as log:
            process=subprocess.Popen(['python','-c',"from run import run; raise SystemExit(run(['python', 'tests/protocol_boot.py']))"],env=env,stdout=log,stderr=subprocess.STDOUT)
            try:
                for _ in range(80):
                    if process.poll() is not None:raise RuntimeError(log_path.read_text())
                    try:
                        request('/health');break
                    except (OSError,TimeoutError):await asyncio.sleep(.1)
                else:raise AssertionError('native startup timeout')
                async with websockets.connect('ws://127.0.0.1:8080/onebot/v11/ws',additional_headers={
                    'Authorization':'Bearer '+KEY,'X-Self-ID':'999999999','X-Client-Role':'Universal'}) as websocket:
                    await websocket.send(json.dumps({'time':int(time.time()),'self_id':999999999,'post_type':'meta_event',
                        'meta_event_type':'heartbeat','status':{'online':True,'good':True},'interval':30000}))
                    for group in (None,20001):
                        message=[{'type':'text','data':{'text':'晚饭吃了吗'}}]
                        if group:message.insert(0,{'type':'at','data':{'qq':'999999999'}})
                        event={'time':int(time.time()),'self_id':999999999,'post_type':'message',
                            'message_type':'group' if group else 'private','sub_type':'normal' if group else 'friend',
                            'user_id':10001,'message_id':2 if group else 1,'message':message,'raw_message':'晚饭吃了吗',
                            'font':0,'sender':{'user_id':10001,'nickname':'模拟联系人','card':'','role':'member'}}
                        if group:event['group_id']=group
                        await websocket.send(json.dumps(event,ensure_ascii=False))
                        for _ in range(8):
                            action=json.loads(await asyncio.wait_for(websocket.recv(),8))
                            api=action['action'];params=action['params']
                            if api=='get_group_member_info':data={'user_id':999999999,'role':'member','shut_up_timestamp':0}
                            elif api=='get_group_info':data={'group_id':20001,'group_all_shut':False}
                            elif api=='get_login_info':data={'user_id':999999999,'nickname':'模拟机器人'}
                            elif api in ('send_private_msg','send_group_msg'):
                                assert api==('send_group_msg' if group else 'send_private_msg')
                                assert params['group_id' if group else 'user_id']==(group or 10001)
                                assert ''.join(part['data']['text'] for part in params['message'])=='吃过了，你呢？'
                                data={'message_id':100}
                            else:raise AssertionError('unexpected native API '+api)
                            await websocket.send(json.dumps({'status':'ok','retcode':0,'data':data,'echo':action['echo']}))
                            if api.startswith('send_'):break
                        else:raise AssertionError('no native reply')
                        await asyncio.sleep(.1)
                    status=request('/api/status')
                    assert status['bots']==['999999999'] and status['persons']==1
                    assert len(status['sessions'])==2
                    assert request('/api/logs?source=console')['entries'], 'process output is not available in the panel'
                    assert request('/api/logs?source=all')['entries']
                print('PASS: real native startup, reverse WebSocket, private/group events, guarded QQ send and receipts')
            finally:
                process.terminate()
                process.wait(timeout=8)


if __name__=='__main__':asyncio.run(verify())
