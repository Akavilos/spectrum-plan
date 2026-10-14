"""验证多本书选择、交换、触发顺序、旧数据迁移及请求前缀。"""
import asyncio
import io
import json
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI, Request, HTTPException
from role_system.host import Host
from role_system.service import RoleSystem
from role_system.ports import Event, ModelRequest
from role_system.web import install_panel
from role_system.data_exchange import export_data,validate_import,import_data
from role_system.spectrum_module.spectrum import settings,worldbook
from role_system.spectrum_module.spectrum.store import Store,_SCHEMA
from role_system.spectrum_module.spectrum.worldbook_exchange import external_export


async def api(app,path,body=None,authorized=True):
    address=urlsplit(path)
    headers=[(b'content-type',b'application/json')]
    if authorized:headers.append((b'authorization',b'Bearer fixture'))
    scope={'type':'http','asgi':{'version':'3.0'},'method':'POST' if body is not None else 'GET',
           'path':address.path,'raw_path':address.path.encode(),'query_string':address.query.encode(),
           'root_path':'','scheme':'http','http_version':'1.1','headers':headers,'server':('fixture',80),'client':('127.0.0.1',1)}
    output=[]
    async def receive():return {'type':'http.request','body':json.dumps(body).encode() if body is not None else b'','more_body':False}
    async def send(message):output.append(message)
    await app(scope,receive,send)
    status=next(row['status'] for row in output if row['type']=='http.response.start')
    raw=b''.join(row.get('body',b'') for row in output)
    return status,json.loads(raw)


async def run():
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory)
        legacy=root/'legacy.db'
        with sqlite3.connect(legacy) as db:
            db.executescript(_SCHEMA)
            db.execute("INSERT INTO worldbook(id,title,text,keys) VALUES(7,'旧标题','旧正文','雨')")
            for kind in ('received','delivered','script','think'):
                db.execute('INSERT INTO entries(umo,kind,text,ts) VALUES(?,?,?,?)',('legacy',kind,'旧记录',1))
            db.execute('INSERT INTO worldbook_state VALUES(?,?,?,?,?)',('legacy',7,4,7,9))
        migrated=Store(legacy)
        assert migrated.get_worldbook(7)['text']=='旧正文'
        assert migrated.worldbooks.books()[0]['name']=='原有世界书'
        assert migrated.worldbooks.selected_ids()==[migrated.get_worldbook(7)['book_id']]
        state=migrated.worldbook_states('legacy')[7]
        assert state['sticky_until']-migrated.worldbook_seq('legacy')==3
        assert state['cooldown_until']-migrated.worldbook_seq('legacy')==5
        migrated._db.close()

        app=FastAPI()
        def authorize(request:Request):
            if request.headers.get('authorization')!='Bearer fixture':raise HTTPException(401)
        host=Host(root/'instance',app,authorize);system=RoleSystem(host)
        install_panel(app,host,system,authorize,'fixture')
        system.spectrum.page_api.register_routes()
        store=system.spectrum.store;library=store.worldbooks
        def cfg(key,value):system.configs['spectrum'][settings.FIELD_GROUP[key]][key]=value
        cfg('enabled',True);cfg('worldbook_enabled',True);cfg('possession_enabled',False)
        assert library.books()==[] and library.selected_ids()==[]
        global_id=library.save('共同世界');current_id=library.save('当前场景');unused_id=library.save('未使用的世界')
        g=store.upsert_worldbook(book_id=global_id,text='共同设定',constant=1,position='core')
        c=store.upsert_worldbook(book_id=current_id,text='雨天场景',keys='雨',position='turn')
        u=store.upsert_worldbook(book_id=unused_id,text='不应注入',constant=1)
        library.select(global_id,current_id)
        assert {r['id'] for r in store.worldbook_hits('下雨了')}=={g,c}
        library.select(global_id,global_id)
        assert [r['id'] for r in store.worldbook_hits('下雨了')]==[g]
        library.select(global_id,current_id,'current_first')
        assert len(library.selected_ids())==2
        cfg('worldbook_min_activations',1);cfg('worldbook_max_scan_depth',2);cfg('worldbook_scan_depth',0)
        old=store.upsert_worldbook(book_id=current_id,text='古老世界书条目',keys='古老线索')
        store.upsert_worldbook(book_id=unused_id,text='未启用的大扫描窗口',keys='其他',scan_depth=120)
        for text in ('古老线索','上一条','最新一条'):store.add_entry('cap','received',text)
        library.select(None,current_id)
        rows,trace=store.worldbook_hits('新的输入',umo='cap',commit=False,return_trace=True)
        assert old not in {row['id'] for row in rows} and trace['scan_depth']==2
        cfg('worldbook_min_activations',0);cfg('worldbook_scan_depth',2)
        library.select(global_id,current_id,'current_first')
        try: library.select(global_id,999)
        except ValueError: pass
        else:raise AssertionError('不存在的书可以被启用')

        # 只有实际入选正文参与递归，0% 和预算外条目均不能带出后续内容。
        cfg('worldbook_recursion',True)
        library.select(None,current_id)
        store.set_worldbook_enabled(c,False)
        denied=store.upsert_worldbook(book_id=current_id,text='秘密钥匙',keys='起点',probability=0,order_value=200)
        dependent=store.upsert_worldbook(book_id=current_id,text='不能幽灵触发',keys='秘密钥匙',order_value=100)
        assert not store.worldbook_hits('起点')
        store.upsert_worldbook(denied,probability=100,text='秘密钥匙'+'长内容'*20)
        assert not store.worldbook_hits('起点',token_budget=1)
        store.upsert_worldbook(denied,text='秘密钥匙')
        assert {r['id'] for r in store.worldbook_hits('起点')}=={denied,dependent}

        # 多互斥组、持续中的概率豁免、编辑后清理时序。
        for row in store.worldbook_all(book_id=current_id):store.set_worldbook_enabled(row['id'],False)
        a=store.upsert_worldbook(book_id=current_id,text='A',keys='选择',group_name='甲,乙',prioritize=1,order_value=200)
        b=store.upsert_worldbook(book_id=current_id,text='B',keys='选择',group_name='甲',order_value=100)
        d=store.upsert_worldbook(book_id=current_id,text='D',keys='选择',group_name='乙',order_value=100)
        assert [r['id'] for r in store.worldbook_hits('选择')]==[a]
        store.upsert_worldbook(a,sticky=3,group_name='')
        umo='fixture:FriendMessage:1'
        store.add_entry(umo,'received','选择')
        store.worldbook_hits('选择',umo=umo)
        state=store.worldbook_states(umo)[a]
        store.add_entry(umo,'script','后台日记')
        assert store.worldbook_seq(umo)==1
        for _ in range(2):store.add_entry(umo,'think','想法')
        before=[dict(r) for r in store._db.execute('SELECT * FROM worldbook_state')]
        store.worldbook_hits('不含触发词',umo=umo,commit=False)
        assert before==[dict(r) for r in store._db.execute('SELECT * FROM worldbook_state')]
        store.upsert_worldbook(a,text='修改后的A')
        assert a not in store.worldbook_states(umo)

        assert worldbook.split_terms('/a,b/i,天气')==['/a,b/i','天气']
        assert worldbook.split_terms('天气, /a,b/i, 下雨')==['天气','/a,b/i','下雨']
        assert worldbook.term_hits('/a,b/i','A,B')
        assert worldbook.keyword_activate({'keys':'雨','secondary':'风','selective':1,'selective_logic':'not_any'},'有雨没有别的')

        external={'name':'通用导入','entries':{'0':{'key':['/雨,风/i','春天'],'comment':'条目备注','content':'我记得这段背景。',
                  'position':4,'depth':3,'role':2,'constant':False,'scanDepth':0,'sticky':2,'customUnknown':{'keep':True}}}}
        result=library.import_book(external)
        assert result['count']==1 and result['warnings']
        entry=store.worldbook_all(book_id=result['id'])[0]
        assert entry['position']=='turn' and entry['scan_depth']==-1
        exported=external_export(library.export_book(result['id']))
        assert exported['entries']['0']['customUnknown']=={'keep':True}
        assert exported['entries']['0']['depth']==3 and exported['entries']['0']['role']==2
        snapshot=library.export_library()
        assert len(snapshot['books'])==4
        package=export_data(host,system,'worldbook')
        parsed=validate_import(host,system,package)
        selected_before=library.selection()
        books_before=library.books()
        import_data(host,system,parsed)
        assert len(library.books())==8 and library.selection()==selected_before
        assert library.books()[:len(books_before)]==books_before
        before=library.export_library()
        bad={'format':'spectrum-worldbook-v1','entries':[{'text':'可用'},{'text':12}]}
        try:library.import_book(bad)
        except ValueError:pass
        else:raise AssertionError('不完整的导入成功')
        assert before==library.export_library()
        for malformed in ({'format':'spectrum-worldbook-v1','entries':['错误条目']},
                          {'entries':{'0':{'content':None}}},
                          {'entries':{'0':{'content':'正文','extensions':[]}}}):
            try: library.import_book(malformed)
            except ValueError: pass
            else: raise AssertionError('无效文件未被拒绝')
        assert before==library.export_library()

        prefix='/astrbot_plugin_spectrum_core/page/worldbook'
        assert (await api(app,prefix+'/list',authorized=False))[0]==401
        code,data=await api(app,prefix+'/list');assert code==200 and len(data['data']['books'])==8
        code,data=await api(app,prefix+'/books/save',{'name':'API世界'})
        book_id=data['data']['id']
        assert (await api(app,prefix+'/books/select',{'global_book':book_id,'current_book':None}))[1]['status']=='ok'
        assert (await api(app,prefix+'/upsert',{'book_id':book_id,'title':'固定背景','text':'我的世界一直是同一个。','constant':1,'position':'turn'}))[1]['status']=='ok'
        before=[dict(r) for r in store._db.execute('SELECT * FROM worldbook_state')]
        code,data=await api(app,prefix+'/preview',{'text':'你好','umo':umo})
        assert code==200 and data['data']['blocks'][0]['position']=='本轮补充'
        assert before==[dict(r) for r in store._db.execute('SELECT * FROM worldbook_state')]
        assert (await api(app,prefix+f'/books/export?id={book_id}&format=tavern'))[1]['data']['entries']

        # 真实请求编码：人设、合约和历史前缀在条目命中变化时保持一致。
        host.config['enabled']=True;host.config['persona']='我有自己的生活。'
        host.config['model'].update(base_url='https://fixture.invalid/v1',model='fixture',api_key='fixture')
        store.upsert_worldbook(book_id=book_id,title='雨天',text='雨天动态背景',keys='雨',position='core')
        requests=[]
        def wire(request,**kwargs):
            requests.append(json.loads(request.data))
            return io.BytesIO(json.dumps({'choices':[{'message':{'role':'assistant','content':'fixture'}}]}).encode())
        history=[{'role':'user','content':'之前的来信'},{'role':'assistant','content':'之前的回复'}]
        with patch('urllib.request.urlopen',wire):
            for text in ('有雨','没有这个关键词'):
                event=Event(host,umo,'1','名字',text)
                req=ModelRequest(text,host.persona_prompt(),list(history),host.tools)
                await system.spectrum.on_llm_request(event,req)
                await host.provider.text_chat(prompt=req.prompt,system_prompt=req.system_prompt,contexts=req.contexts,
                    extra_user_content_parts=req.extra_user_content_parts,role_mode=req.role_mode)
        assert requests[0]['messages'][:3]==requests[1]['messages'][:3]
        assert requests[0]['messages'][1:3]==history
        assert '雨天动态背景' not in requests[0]['messages'][0]['content']
        assert '雨天动态背景' in json.dumps(requests[0],ensure_ascii=False)
        # 第二轮扫描包含近期消息，关闭回看后确认只改变临时背景。
        cfg('worldbook_scan_depth',0)
        req=ModelRequest('晴天',host.persona_prompt(),list(history),host.tools)
        await system.spectrum.on_llm_request(Event(host,umo,'1','名字','晴天'),req)
        assert '雨天动态背景' not in '\n'.join(part.text for part in req.extra_user_content_parts)
        assert req.system_prompt==requests[0]['messages'][0]['content']
        assert req.contexts==history
        await system.close()
    print('PASS worldbook library: clean install, migration, two slots, inactive isolation, recursion, groups, timing, exchanges, atomic validation, APIs and stable encoded prefix')


asyncio.run(run())
