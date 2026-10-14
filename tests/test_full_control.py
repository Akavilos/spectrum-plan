"""完整请求接线验收；HTTP 全部由进程内替身拦截，不请求模型服务器。"""
import asyncio, io, json, sys, tempfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from role_system.host import Host
from role_system.service import RoleSystem
from role_system.provider import Provider, EmbeddingProvider
from role_system.ports import Event, MessageChain, NativeSegment
from role_system.spectrum_module.spectrum import settings, vectors, freeze

BODY='<life>我接着聊。</life><reply>{"seen":true,"reply":{"mode":"immediate","content":"吃过了，你呢？"},"sharedEvents":[]}</reply>'
class Wire:
    def __init__(self): self.calls=[]
    def __call__(self, request, **kwargs):
        body=json.loads(request.data); self.calls.append((request.full_url,body))
        if request.full_url.endswith('/embeddings'):
            result={'data':[{'index':i,'embedding':[float(i+1),1.0]} for i in reversed(range(len(body['input'])))]}
        else:
            content=('我随口接着聊。\n</thinking>\n' if body['messages'][-1].get('prefix') else '<think>我接着聊。</think>')+BODY
            result={'choices':[{'message':{'role':'assistant','content':content,'reasoning_content':'fixture-native-reasoning'},'finish_reason':'stop'}]}
        return io.BytesIO(json.dumps(result).encode())

async def run():
    wire=Wire()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory, patch('urllib.request.urlopen',wire):
        host=Host(directory);host.config['enabled']=True
        host.config['model'].update(base_url='https://fixture.invalid/beta',model='deepseek-fixture',api_key='fixture-secret',family='deepseek',parameters={'thinking':{'type':'disabled'},'max_completion_tokens':512})
        cfg=host.module_config('spectrum');settings.migrate(cfg);cfg['basic']['enabled']=True
        cfg[settings.FIELD_GROUP['possession_enabled']].update(possession_enabled=True,prefill_possession=True,prefill_gemini_closed=True)
        cfg['conversation']['isolate_history']=True;cfg.save_config()
        system=RoleSystem(host);await system.start()
        sent=[]
        async def send(umo,chain):sent.append((umo,chain));return {'message_id':len(sent)}
        host.transport=send
        umo='qq-90001:FriendMessage:10001'
        host.append_history(umo,'assistant','这条原始历史由隔离开关排除')
        result=await system.receive(Event(host,umo,'10001','甲','吃饭了吗'))
        assert result=='sent',result
        request=wire.calls[-1][1]
        assert request['messages'][-1]['role']=='assistant' and request['messages'][-1]['prefix']
        assert request['thinking']=={'type':'disabled'} and request['max_completion_tokens']==512
        assert not any(m.get('content')=='这条原始历史由隔离开关排除' for m in request['messages'])
        assert host.history(umo)[-1]['content']=='吃过了，你呢？'
        system.spectrum.store.set_state(umo,'reply_mode','contract')
        await system.receive(Event(host,umo,'10001','甲','晚安'))
        assert not wire.calls[-1][1]['messages'][-1].get('prefix')
        assert any('吃过了，你呢？' in str(m.get('content', '')) for m in wire.calls[-1][1]['messages']), 'isolated context lost the previous real exchange'
        host.provider.config['family']='gemini';host.provider.config['model']='gemini-fixture'
        system.spectrum.store.set_state(umo,'reply_mode','prefill')
        await system.receive(Event(host,umo,'10001','甲','早'))
        assert wire.calls[-1][1]['messages'][-1]['role']=='user'
        assert '<thinking></thinking>' in wire.calls[-1][1]['messages'][-1]['content']
        system.configs['spectrum'][settings.FIELD_GROUP['possession_enabled']]['possession_enabled']=False
        await system.receive(Event(host,umo,'10001','甲','你好'))
        assert '<thinking></thinking>' not in wire.calls[-1][1]['messages'][-1]['content']
        before=len(wire.calls);assert host.get_all_embedding_providers()==[];assert len(wire.calls)==before
        freeze.manual('freeze')
        try:
            await host.provider.text_chat(prompt='直接请求也受冻结控制')
            raise AssertionError('frozen direct call reached model')
        except freeze.FrozenSkipped:
            pass
        assert len(wire.calls)==before
        freeze.manual('thaw')
        host.config['embedding'].update(enabled=True,base_url='https://fixture.invalid/v1',model='embedding-fixture')
        provider=host.get_all_embedding_providers()[0]
        host.config['enabled']=False
        assert await system.spectrum._embed_query('暂停时不请求嵌入') is None
        assert system.spectrum._vector_tables()==()
        host.config['enabled']=True
        cfg=system.configs['spectrum']
        cfg[settings.FIELD_GROUP['worldbook_enabled']]['worldbook_enabled']=True
        cfg[settings.FIELD_GROUP['worldbook_vector_enabled']]['worldbook_vector_enabled']=True
        assert 'worldbook' in system.spectrum._vector_tables()
        assert await provider.get_embeddings(['甲','乙'])==[[1.0,1.0],[2.0,1.0]]
        sc=vectors.VecSidecar(Path(directory)/'fixture.db','memories')
        assert sc.add([(1,[1,0]),(2,[0,1])])==2
        assert sc.search([1,0],1)==[1]
        assert vectors.VecSidecar(Path(directory)/'fixture.db','memories').search([0,1],1)==[2]
        assert sc.prune({2})==1;sc.clear();assert sc.search([1,0],2)==[]
        host.config['providers']['prism-fixture']=dict(host.config['model']);host.providers['prism-fixture']=Provider(host.config['providers']['prism-fixture'])
        assert host.get_provider_by_id('prism-fixture').ready
        await host.send_message(umo,MessageChain([NativeSegment('image',{'file':'https://fixture.invalid/image.png'})]))
        assert sent[-1][1].delivery_payload['segments'][0]['type']=='image'
        await system.close()
    print('PASS: prefix wire, Gemini strategy, master disable, per-session mode, history isolation, request parameters, embeddings, index persistence, auxiliary model and media transport')

if __name__=='__main__':asyncio.run(run())
