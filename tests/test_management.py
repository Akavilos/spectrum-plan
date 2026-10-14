"""扩展真实加载、输出送达一致性和数据交换的一轮离线验收。"""
import asyncio
import json
import sqlite3
import sys
import tempfile
import time
import zipfile
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI, Request, HTTPException
from role_system.host import Host, SendTool
from role_system.ports import Event, Plain, MessageChain, NativeSegment, ToolContext
from role_system.service import RoleSystem
from role_system.web import install_panel
from role_system.output_rules import normalize_rule
from role_system.provider import ModelResponse
from role_system.spectrum_module.spectrum.parse import Decision
from test_http import ASGIClient, Response
from test_integration import enable


class Client(ASGIClient):
    async def request(self, method, url, json=None, raw=None, content_type='application/json'):
        target = urlsplit(url)
        data = raw if raw is not None else __import__('json').dumps(json).encode() if json is not None else b''
        headers = [(b'content-type', content_type.encode())]
        if self.cookie: headers.append((b'cookie', self.cookie.encode()))
        scope = {'type':'http','asgi':{'version':'3.0'},'method':method,'path':target.path,'raw_path':target.path.encode(),
            'query_string':target.query.encode(),'root_path':'','scheme':'http','http_version':'1.1','headers':headers,
            'server':('fixture',80),'client':('127.0.0.1',1)}
        messages = []
        async def receive(): return {'type':'http.request','body':data,'more_body':False}
        async def send(message): messages.append(message)
        await self.app(scope,receive,send)
        start = next(m for m in messages if m['type']=='http.response.start')
        body = b''.join(m.get('body',b'') for m in messages if m['type']=='http.response.body')
        result = type('Result', (), {'status_code':start['status'],'body':body,'json':lambda self:__import__('json').loads(self.body)})()
        return result


class Model:
    ready = True
    def role_mode(self, override=''): return 'contract'
    async def close(self): pass
    async def text_chat(self, **kwargs):
        return ModelResponse('<life>我继续聊天。</life><reply>'+json.dumps({'seen':True,'reply':{'mode':'immediate','content':'总的来说，吃过了，你呢？'}},ensure_ascii=False)+'</reply>')


async def run():
    import nonebot
    nonebot.init(driver='~fastapi', log_level='ERROR')
    with tempfile.TemporaryDirectory() as directory:
        app = FastAPI()
        def authorize(request: Request):
            if request.cookies.get('role_panel') != 'test-key': raise HTTPException(401)
        host = Host(directory,app,authorize); system = RoleSystem(host)
        install_panel(app,host,system,authorize,'test-key')
        client = Client(app)
        assert (await client.get('/api/extensions')).status_code == 401
        assert (await client.get('/api/regex')).status_code == 401
        assert (await client.get('/api/data/export')).status_code == 401
        client.cookie='role_panel=test-key'
        example=(await client.get('/api/extensions/example')).body
        assert (await client.request('POST','/api/extensions/install',raw=example,content_type='application/zip')).status_code == 200
        saved=await client.post('/api/extensions/greeting_example',{'enabled':True,'settings':{'greeting':'你好呀'}})
        assert saved.status_code == 200
        # 验证加载器能读取页面保存的配置，不访问包仓库。
        stream=BytesIO()
        with zipfile.ZipFile(stream,'w') as archive:
            archive.writestr('manifest.json',json.dumps({'id':'native_fixture','kind':'nonebot','entry':'nonebot_plugin_native_fixture'}))
            archive.writestr('nonebot_plugin_native_fixture/__init__.py','from nonebot import get_driver\nVALUE = get_driver().config.fixture_number\n')
        await host.extensions.install_zip(stream.getvalue())
        await client.post('/api/extensions/native_fixture',{'enabled':True,'settings':{'fixture_number':42}})
        host.extensions.load(nonebot.get_driver())
        assert not host.extensions.errors, host.extensions.errors
        assert sys.modules['nonebot_plugin_native_fixture'].VALUE == 42
        rows=(await client.get('/api/extensions')).json()['plugins']
        assert all(r['loaded'] and not r['pending_restart'] for r in rows)
        calls=[]
        api=host.extensions.apis['greeting_example']
        api.on('before_request',lambda event,req:calls.append(event.user_id))
        api.on('after_send',lambda session,chain,receipt:calls.append(chain.get_plain_text()))
        class Tool:
            name='fixture_tool'; active=True; description='fixture'; parameters={'type':'object','properties':{}}
            async def call(self,wrapper): return 'ok'
        api.tool(Tool()); assert host.tools.get_tool('fixture_tool')
        sent=[]
        async def transport(session,chain):
            sent.append((session,chain.get_plain_text(),[getattr(p,'type','text') for p in chain.chain]));return {'message_id':len(sent)}
        host.transport=transport;host.provider=Model();host.config['enabled']=True
        enable(system.spectrum,'enabled',True);enable(system.prism,'enabled',False)
        await system.start()
        umo='qq-90001:FriendMessage:10001'
        assert await system.receive(Event(host,umo,'10001','甲','/问候示例'))=='extension_command'
        assert sent[-1][1]=='你好呀'
        rules=(await client.get('/api/regex')).json();assert not rules['enabled']
        rule=rules['rules'][0];rule['enabled']=True
        result=await client.post('/api/regex',{'enabled':True,'rules':[rule]});assert result.status_code==200,result.body
        assert await system.receive(Event(host,umo,'10001','甲','吃了吗'))=='sent'
        assert sent[-1][1]=='吃过了，你呢？' and calls[-1]=='吃过了，你呢？'
        assert '10001' in calls
        assert host.history(umo)[-1]['content']=='吃过了，你呢？'
        delivery=system.spectrum.store.recent_exchange(umo,2)
        assert any(r['kind']=='delivered' and r['text']=='吃过了，你呢？' for r in delivery)
        # 非幂等替换一次，随后补发不再改写。
        host.output_rules.save({'enabled':True,'rules':[{'name':'一次','pattern':'猫','replacement':'猫猫','flags':'g','enabled':True}]})
        chain=MessageChain([Plain('猫')]);await host.send_message(umo,chain);assert chain.get_plain_text()=='猫猫'
        payload=chain.delivery_payload
        await host.send_message(umo,MessageChain([Plain('猫猫')]),replay=True,delivery_payload=payload)
        assert sent[-1][1]=='猫猫'
        media=MessageChain([Plain('猫'),NativeSegment('image',{'file':'file:///fixture.png'})]);await host.send_message(umo,media)
        assert sent[-1][1]=='猫猫' and sent[-1][2]==['text','image']
        management=MessageChain([Plain('猫')]);await host.send_message(umo,management,management=True);assert sent[-1][1]=='猫'
        promise=Decision(ok=True,mode='immediate',content='猫',follow_up={'summary':'猫'})
        host.output_rules.save({'enabled':True,'rules':[{'name':'全部删','pattern':'.+','replacement':'','enabled':True}]})
        host.output_rules.prepare_decision(promise);assert not promise.content and not promise.follow_up
        before=len(sent);assert await host.send_message(umo,MessageChain([Plain('猫')])) is False;assert len(sent)==before
        st={'scriptName':'捕获','findRegex':'/(猫)/g','replaceString':'$1!','trimStrings':[],'placement':[2],'disabled':False}
        assert (await client.post('/api/regex/import',{'data':st,'replace':True})).status_code==200
        result=(await client.post('/api/regex/preview',{'text':'猫猫'})).json();assert result['text']=='猫!猫!'
        exported=(await client.get('/api/regex/export?tavern=true')).json()
        assert (await client.post('/api/regex/import',{'data':exported,'replace':True})).status_code==200
        original=host.output_rules.data.copy()
        assert (await client.post('/api/regex/import',{'data':[st,{**st,'findRegex':'/(/g'}]})).status_code==422
        assert host.output_rules.data==original
        assert (await client.post('/api/regex/import',{'data':{**st,'markdownOnly':True}})).status_code==422
        host.config['model']['api_key']='keep-secret';host.config.save_config()
        book=system.spectrum.store.upsert_worldbook(text='我住在海边。',keys='家',title='家')
        export=(await client.get('/api/data/export')).json();assert 'keep-secret' not in json.dumps(export)
        assert (await client.post('/api/data/import',{'data':export})).status_code==200
        assert host.config['model']['api_key']=='keep-secret' and len(system.spectrum.store.worldbook_all())==2
        assert system.spectrum.store.get_worldbook(book)['text']=='我住在海边。'
        bad={'format':'role-system-data-v1','sections':{'persona':'不该保存','worldbook':[{'text':12}]}}
        persona=host.config['persona'];assert (await client.post('/api/data/import',{'data':bad})).status_code==422
        assert host.config['persona']==persona
        backup=(await client.get('/api/data/backup')).body
        with zipfile.ZipFile(BytesIO(backup)) as archive:
            assert b'keep-secret' not in archive.read('system.json')
            assert 'conversation.db' in archive.namelist()
            db=Path(directory)/'snapshot.db';db.write_bytes(archive.read('conversation.db'))
            with sqlite3.connect(db) as connection: assert connection.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert (await client.get('/api/data/history')).body
        await system.close()
    print('PASS management: real plugin loading/config/hooks/tools; regex delivery/media/replay/history; Tavern and data exchange; backup and authentication')


if __name__=='__main__':asyncio.run(run())
