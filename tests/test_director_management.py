"""隔离验收剧情增删改查、条件实际判断和原有数据保留，不调用真实模型。"""
import asyncio,json,sys,tempfile
from datetime import datetime,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI,Request,HTTPException
from role_system.host import Host
from role_system.service import RoleSystem
from role_system.web import install_panel
from role_system.spectrum_module.spectrum.storylet import StoryletEngine,EvalContext
from test_http import ASGIClient

async def run():
    with tempfile.TemporaryDirectory() as directory:
        app=FastAPI()
        def authorize(request:Request):
            if request.cookies.get('role_panel')!='fixture-key':raise HTTPException(401)
        host=Host(directory,app,authorize);system=RoleSystem(host)
        install_panel(app,host,system,authorize,'fixture-key')
        await system.start()
        store=system.spectrum.store
        store.upsert_storylet('original',narrative=json.dumps(['原来的剧情']),meta=json.dumps({'target':'unchanged','repeatable':True}),eval_annot='{"keep":true}')
        old=dict(store.get_storylet('original'))
        base='/astrbot_plugin_spectrum_core/page/director/storylets'
        condition={'all':[{'tier':1,'pred':'window','var':'local_time','from':'18:00','to':'23:00'},
            {'tier':1,'pred':'threshold','var':'owner_idle_minutes','op':'>=','value':40},
            {'tier':1,'pred':'flag','var':'asleep','equals':False}]}
        payload={'sid':'new-rain','create_only':True,'enabled':False,'narrative':['我听见雨声。\n我把窗关了。','我继续手头的事。'],
            'condition':condition,'effects':{},'meta':{'title':'晚间雨声','repeatable':False,'k_minutes':30}}
        try:
            async with ASGIClient(app) as client:
                assert (await client.post(base+'/upsert',payload)).status_code==401
                await client.post('/api/login',{'key':'fixture-key'})
                assert (await client.post(base+'/upsert',payload)).json()['status']=='ok'
                row=dict(store.get_storylet('new-rain'));assert not row['enabled']
                assert 'new-rain' in system.spectrum.l2.director.engine._lib
                assert json.loads(row['narrative'])==payload['narrative']
                assert (await client.post(base+'/upsert',{**payload,'narrative':['覆盖旧内容']})).json()['status']=='error'
                assert dict(store.get_storylet('new-rain'))==row
                assert (await client.post(base+'/upsert',{'sid':'new-rain','condition':{'all':'错误格式'}})).json()['status']=='error'
                assert dict(store.get_storylet('new-rain'))==row
                assert (await client.post(base+'/upsert',{'sid':'new-rain','condition':{'all':[{'pred':'unknown'}]}})).json()['status']=='error'
                assert (await client.post(base+'/upsert',{'sid':'new-rain','meta':{**payload['meta'],'title':'晚间雨声修订'}})).json()['status']=='ok'
                assert json.loads(store.get_storylet('new-rain')['narrative'])==payload['narrative']
                await client.post(base+'/toggle',{'sid':'new-rain','enabled':True})
                engine=StoryletEngine(store,lambda key,default=None:default);assert engine.load()=={}
                now=datetime(2026,10,13,11,tzinfo=timezone.utc).timestamp()
                ctx=EvalContext(now=now,story={},owner_session='friend-a',idle_minutes={'friend-a':45},sessions=['friend-a'])
                assert engine.eval_condition(condition,ctx)
                ctx.asleep=True;assert not engine.eval_condition(condition,ctx)
                ctx.asleep=False;ctx.idle_minutes['friend-a']=10;assert not engine.eval_condition(condition,ctx)
                ctx.idle_minutes['friend-a']=45;ctx.now-=8*3600;assert not engine.eval_condition(condition,ctx)
                overnight={'all':[{'pred':'window','var':'local_time','from':'23:00','to':'02:00'}]}
                ctx.now=datetime(2026,10,13,16,tzinfo=timezone.utc).timestamp();assert engine.eval_condition(overnight,ctx)
                ctx.now=now;assert not engine.eval_condition(overnight,ctx)
                listed=(await client.get(base)).json()['data']['storylets'];assert {s['sid'] for s in listed}=={'original','new-rain'}
                query=(await client.post('/api/records/query',{'resource':'spectrum.storylets','filters':{'search':'雨声'},'select_all':True})).json()
                assert query['ids']==['new-rain']
                assert (await client.post('/api/records/delete',{'resource':'spectrum.storylets','ids':query['ids']})).status_code==409
                deleted=(await client.post('/api/records/delete',{'resource':'spectrum.storylets','ids':query['ids'],'confirmed':True})).json()
                assert deleted['deleted']==1 and store.get_storylet('new-rain') is None
                assert 'new-rain' not in system.spectrum.l2.director.engine._lib
                assert dict(store.get_storylet('original'))==old
                page=(await client.get('/modules/spectrum')).text
                assert '新建剧情片段' in page and '自定义 JSON' in page and 'saveStorylet' in page
            print('PASS director CRUD, authentication, duplicate protection, custom conditions, midnight window, filtered bulk deletion and existing data')
        finally:await system.close()

if __name__=='__main__':asyncio.run(run())
