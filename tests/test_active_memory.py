"""真实宿主的主动工具与数据回放；模型、嵌入及QQ发送采用离线替身。"""
import asyncio,json,sys,tempfile,time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from role_system.host import Host
from role_system.service import RoleSystem
from role_system.ports import Event,ToolContext,ModelRequest,MessageChain,NativeSegment
from role_system.provider import ModelResponse,EmbeddingProvider
from role_system.active_memory.core import Context,Cues,Memory,candidates,select,normalize_time
from role_system.active_memory.api import install
from role_system.spectrum_module.spectrum import freeze
from role_system.prism_module.prism.scene import Moment,build_scene
from role_system.prism_module.prism.affect import Mood
from fastapi import FastAPI,Request
from test_http import ASGIClient


async def run():
    now=datetime(2026,10,14,20,tzinfo=ZoneInfo('Asia/Shanghai')).timestamp()
    ctx=Context('qq-9','role','qq-9:FriendMessage:2',now)
    start,end,latest,_=normalize_time({'hint':'昨天晚上'},ctx)
    assert start==datetime(2026,10,13,18,tzinfo=ZoneInfo('Asia/Shanghai')).timestamp()
    assert end==datetime(2026,10,14,tzinfo=ZoneInfo('Asia/Shanghai')).timestamp() and not latest
    memory=Memory('one','qq-9','role','fixture','qq-9:FriendMessage:1','甲派人送奶茶',now,start+1,people=('qq-9:1',))
    rows=[memory,replace(memory,key='foreign-role',role='other'),replace(memory,key='foreign-account',account='qq-8'),
          replace(memory,key='new-summary',occurred_at=None,text='今天整理昨天送奶茶的摘要')]
    assert [x[0].key for x in candidates(rows,ctx,Cues('奶茶',start=start,end=end))]==['one']
    assert not candidates(rows,ctx,Cues('火箭发射',people=('qq-9:1',)))
    assert not candidates(rows,ctx,Cues(people=('qq-9:1',),emotion='喜悦'))
    assert select(candidates(rows,ctx,Cues('奶茶')),ctx,Cues('奶茶',latest=True))[0].key=='one'
    with tempfile.TemporaryDirectory() as folder:
        host=Host(folder);system=RoleSystem(host);active=system.active_memory
        assert not active.active
        store=system.spectrum.store
        for uid,name in [('1','甲'),('2','乙')]:
            store.upsert_person('qq-9:'+uid,'qq-9',name);store.add_person_alias('qq-9:'+uid,name)
        first=store.add_entry('qq-9:FriendMessage:1','received','我派人送奶茶给你了',ts=start+1,meta={'sender':'甲','sender_id':'1'})
        store.add_entry('qq-9:FriendMessage:2','received','今天整理昨天的奶茶记录',ts=now,meta={'sender':'乙','sender_id':'2'})
        store.add_entry('qq-8:FriendMessage:1','received','奶茶是另一个账号的',ts=start+2,meta={'sender_id':'1'})
        promise_source=store.add_entry('qq-9:FriendMessage:1','delivered','不再吃米线的约定已经取消',ts=start+3)
        store.record_shared_life_event({'kind':'commitment','subject':'qq-9:1','text':'不再吃米线的约定已经取消','state':'resolved','basis':'reply','validUntil':start+4},
                                      source_umo='qq-9:FriendMessage:1',source_entry_id=promise_source,life_id='fixture',ts=start+3)
        active.active=True
        event=Event(host,'qq-9:FriendMessage:2','2','乙','奶茶是谁送的')
        wrap=ToolContext(host,event)
        before=host.history(event.unified_msg_origin)
        result=json.loads(await active.call(wrap,query='奶茶',about='甲',time={'hint':'昨天晚上'}))
        # 工具以实时日期解析相对时间；固定回放用明确日期再次检索。
        result=json.loads(await active.call(wrap,query='奶茶',about='甲',time={'start':'2026-10-13','end':'2026-10-13'}))
        assert result['记忆'] and all('qq-9:1' in row['涉及人物'] for row in result['记忆'])
        assert all(row['归属角色']==system.personality.config.role_id for row in result['记忆'])
        assert host.history(event.unified_msg_origin)==before
        promise=json.loads(await active.call(wrap,query='米线',about='甲'))
        assert any(row['状态'].startswith('resolved') and '有效期已过' in row['状态'] for row in promise['记忆'])
        scene=build_scene([Moment(start+1,'qq-9:1','我派人送奶茶给你了',mood=Mood(.4,.2,.1),emotions=(('喜悦',.8),),
                                 umo='qq-9:FriendMessage:1',topic='奶茶',memory_role_id=host.memory_role_id())],'fixture')
        system.prism.memory.scenes['fixture']=scene
        emotional=json.loads(await active.call(wrap,about='甲',feel={'emotion':'开心'}))
        assert any(row['情绪主体']=='我' for row in emotional['记忆'])
        store.add_person_alias('qq-9:2','甲')
        assert not json.loads(await active.call(wrap,query='奶茶',about='甲'))['记忆']
        # 模型只在自行发出工具调用时拿到记忆；已有上下文和系统前缀不被重写。
        class Model:
            def __init__(self,recall=False):self.calls=[];self.recall=recall
            async def text_chat(self,**kwargs):
                self.calls.append(kwargs)
                if self.recall and len(self.calls)==1:
                    return ModelResponse('',tool_calls=[{'id':'memory-call','function':{'name':'recall_memory','arguments':json.dumps({'query':'奶茶'})}}],
                                         input_messages=[{'role':'system','content':'固定前缀'},{'role':'user','content':'奶茶'}])
                return ModelResponse('正常回应')
        request=ModelRequest('你好','固定前缀',[],host.tools)
        plain=Model();await system._generate(event,request,provider=plain)
        assert len(plain.calls)==1 and plain.calls[0]['contexts']==[]
        request=ModelRequest('奶茶','固定前缀',[],host.tools)
        model=Model(True);await system._generate(event,request,provider=model)
        assert len(model.calls)==2 and model.calls[1]['system_prompt']=='固定前缀'
        assert any(row['role']=='tool' for row in model.calls[1]['contexts'])
        # 真实嵌入入口的可选失败不能累计冻结；必需调用仍登记原有异常。
        class Broken(EmbeddingProvider):
            async def _get_embeddings(self,texts):raise RuntimeError('fixture unavailable')
        broken=Broken({'base_url':'https://fixture.invalid','model':'fixture','enabled':True})
        with patch.object(freeze,'record_failure') as failures:
            try:await broken.get_embedding('奶茶',optional=True)
            except RuntimeError:pass
            else:raise AssertionError('可选错误被吞掉')
            assert failures.call_count==0
            try:await broken.get_embedding('奶茶')
            except RuntimeError:pass
            assert failures.call_count==1
        await broken.close()
        class Vector:
            config={'model':'fixture','category':'embedding'};ready=True
            async def get_embedding(self,text,optional=False):assert optional;return [1.,0.]
        host.providers['embed-fixture']=Vector()
        host.config['active_memory'].update(vector_enabled=True,embedding_provider_id='embed-fixture',vector_threshold=.5)
        records=active.adapter.records(active.context(event.unified_msg_origin),active.settings)
        pool=candidates(records,active.context(event.unified_msg_origin),Cues('奶茶'))
        space=active.space(host.providers['embed-fixture'])
        for index,(m,_,_) in enumerate(pool):active.vectors[active.vector_key(active.context(event.unified_msg_origin),m,space)]=[1.,0.] if index==0 else [-1.,0.]
        filtered,_=await active.semantic_filter(pool,active.context(event.unified_msg_origin),Cues('奶茶'))
        assert len(filtered)==1
        # 当前角色切换后新回忆不能读走上一角色的原文。
        old_role=host.memory_role_id();system.personality.new_role()
        isolated=json.loads(await active.call(wrap,query='奶茶'))
        assert not isolated['记忆'] and host.memory_role_id()!=old_role
        new_role=host.memory_role_id()
        system.personality.switch_role(old_role)
        store.add_factual_memory(event.unified_msg_origin,'邮票归属回放')
        store.add_person_memory('qq-9:2','邮票人物回放')
        store.upsert_factual_memory(event.unified_msg_origin,'邮票重复回放')
        observation=store.upsert_observed('邮票放在桌上',umo=event.unified_msg_origin)
        assert json.loads(await active.call(wrap,query='邮票'))['记忆']
        system.personality.switch_role(new_role)
        assert not json.loads(await active.call(wrap,query='邮票'))['记忆']
        # 同文去重只刷新本角色的记录，不覆盖另一角色；无来源人物记忆也有账号归属。
        assert store.upsert_factual_memory(event.unified_msg_origin,'邮票重复回放')=='new'
        assert store.add_person_memory('qq-9:2','邮票人物回放')>0
        assert store.upsert_observed('邮票放在桌上',umo=event.unified_msg_origin)!=observation
        assert json.loads(await active.call(wrap,query='邮票'))['记忆']
        # 只确认真实送达后保存媒体事实，签名和凭据不进入历史。
        async def transport(session,chain):return {'message_id':77}
        host.transport=transport
        payload={'app':'com.tencent.music.lua','meta':{'music':{'title':'歌曲','desc':'歌手','key':'PRIVATE-FIXTURE'}}}
        await host.send_message(event.unified_msg_origin,MessageChain([NativeSegment('json',{'data':payload})]))
        history=host.history(event.unified_msg_origin)
        assert '歌曲' in history[-1]['content'] and 'PRIVATE-FIXTURE' not in str(history)
        media=json.loads(await active.call(wrap,query='歌曲'))
        assert any(row['性质']=='我的媒体送达记录' for row in media['记忆'])
        app=FastAPI()
        async def auth(request:Request):
            from fastapi import HTTPException
            if request.cookies.get('fixture')!='yes':raise HTTPException(401)
        install(app,host,system,auth)
        async with ASGIClient(app) as client:
            assert (await client.get('/api/active-memory')).status_code==401
            client.cookie='fixture=yes'
            assert (await client.get('/api/active-memory')).status_code==200
            assert (await client.post('/api/active-memory/settings',json={'enabled':'yes'})).status_code==422
            settings={**active.settings,'enabled':False,'vector_enabled':False,'embedding_provider_id':''}
            assert (await client.post('/api/active-memory/settings',json=settings)).status_code==200
            assert not active.active and '关闭' in await active.call(wrap,query='歌曲')
            assert (await client.post('/api/active-memory/index-clear',json={})).status_code==422
            assert (await client.post('/api/active-memory/index-clear',json={'confirm':True})).status_code==200
        await active.close();system.personality.store.close();await host.close()
    print('PASS active memory: optional tool, scope, provenance, time, feel, vectors, freeze policy, media receipts and settings')

if __name__=='__main__':asyncio.run(run())
