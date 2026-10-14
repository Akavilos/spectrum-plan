"""Exercise native HTTP APIs against an isolated ASGI application; no network/model calls."""
import asyncio
import tempfile
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI, Depends, Request, HTTPException
from role_system.host import Host
from role_system.service import RoleSystem
from role_system.web import install_panel


class Response:
    def __init__(self,status,body):
        self.status_code=status
        self.text=body.decode('utf-8')
    def json(self):
        import json
        return json.loads(self.text)


class ASGIClient:
    """Minimal in-process ASGI transport; test dependencies match the deployed image."""
    def __init__(self,app):
        self.app=app
        self.cookie=''
    async def __aenter__(self):return self
    async def __aexit__(self,*args):pass
    async def request(self,method,path,json=None):
        import json as serializer
        from http.cookies import SimpleCookie
        data=serializer.dumps(json).encode() if json is not None else b''
        headers=[(b'content-type',b'application/json')]
        if self.cookie:headers.append((b'cookie',self.cookie.encode()))
        scope={'type':'http','asgi':{'version':'3.0'},'method':method,'path':path,'raw_path':path.encode(),
               'query_string':b'','root_path':'','scheme':'http','http_version':'1.1',
               'headers':headers,'server':('fixture',80),'client':('127.0.0.1',1)}
        output=[]
        async def receive():return {'type':'http.request','body':data,'more_body':False}
        async def send(message):output.append(message)
        await self.app(scope,receive,send)
        start=next(x for x in output if x['type']=='http.response.start')
        for key,value in start['headers']:
            if key.lower()==b'set-cookie':
                parsed=SimpleCookie();parsed.load(value.decode())
                self.cookie='; '.join(k+'='+v.value for k,v in parsed.items() if v['max-age']!='0')
        body=b''.join(x.get('body',b'') for x in output if x['type']=='http.response.body')
        return Response(start['status'],body)
    async def get(self,path):return await self.request('GET',path)
    async def post(self,path,json):return await self.request('POST',path,json)


async def run():
    with tempfile.TemporaryDirectory() as directory:
        app=FastAPI()
        def authorize(request: Request):
            if request.cookies.get('role_panel')!='fixture-panel-key':raise HTTPException(401)
        host=Host(directory,app,authorize)
        system=RoleSystem(host)
        install_panel(app,host,system,authorize,'fixture-panel-key')
        await system.start()
        async with ASGIClient(app) as client:
            assert (await client.get('/api/config')).status_code==401
            assert (await client.post('/api/login',json={'key':'wrong'})).status_code==401
            assert (await client.post('/api/login',json={'key':'fixture-panel-key'})).status_code==200
            config=(await client.get('/api/config')).json()
            assert config['system']['model']['api_key']==''
            assert (await client.post('/api/config/system',json={'enabled':False,'persona':'我是一名新角色。',
                'operators':['10001'],'model':{'base_url':'https://fixture.invalid/v1','model':'fixture','api_key':'fixture-secret','temperature':.8}})).status_code==200
            config=(await client.get('/api/config')).json()
            assert config['system']['model']['api_key']=='' and config['system']['model']['has_key']
            assert (await client.post('/api/embedding',json={'enabled':False,'base_url':'https://fixture.invalid/v1','model':'embed-fixture','api_key':'embed-secret'})).status_code==200
            assert (await client.post('/api/providers/prism-fixture',json={'base_url':'https://fixture.invalid/v1','model':'fixture','api_key':'aux-secret'})).status_code==200
            config=(await client.get('/api/config')).json()
            assert config['system']['embedding']['api_key']=='' and config['system']['embedding']['has_key']
            assert config['system']['providers']['prism-fixture']['api_key']==''
            assert (await client.post('/api/embedding',json={'enabled':True,'base_url':'invalid'})).status_code==422
            assert (await client.post('/api/config/spectrum' ,json={'basic':{'enabled':True}})).status_code==200
            assert (await client.post('/api/config/prism',json={'unknown':True})).status_code==422
            for path,(handler,methods) in host.routes.items():
                if 'GET' in methods:
                    response=await client.get(path)
                    assert response.status_code==200,(path,response.text)
            for page in ('spectrum','prism'):
                response=await client.get('/modules/'+page)
                assert response.status_code==200 and 'window.RoleModulePage' in response.text
                prefix='/astrbot_plugin_spectrum_core/' if page=='spectrum' else '/astrbot_plugin_prism/'
                assert 'const moduleApiRoot="'+prefix+'"' in response.text
                assert (await client.get(prefix+'page/overview')).status_code==200
            assert (await client.get('/assets/vue.global.prod.js')).status_code==200
            for name in ('workspace.css','spectrum.js','prism.js'):
                assert (await client.get('/assets/data/'+name)).status_code==200
            assert (await client.get('/assets/data/system.json')).status_code==404
            assert (await client.get('/assets/models.js')).status_code==200
            styles=await client.get('/assets/model-workspace.css')
            assert styles.status_code==200 and '.ms-workbench' in styles.text
            assert (await client.get('/api/guard')).status_code==200
            assert (await client.post('/api/control/freeze',json={})).status_code==200
            assert (await client.post('/api/control/thaw',json={})).status_code==200
            assert (await client.post('/api/logout',json={})).status_code==200
            assert (await client.get('/api/config')).status_code==401
        await system.close()
        host.runtime_logs.close()
        print('PASS: native authentication, configuration, all retained read endpoints, both data panels, assets, pause/resume')


if __name__=='__main__':asyncio.run(run())
