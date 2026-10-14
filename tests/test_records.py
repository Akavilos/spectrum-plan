"""批量管理验收：确认、筛选快照、关联删除和导入保留；使用隔离数据。"""
import asyncio,json,sys,tempfile,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI,Request,HTTPException
from role_system.host import Host
from role_system.service import RoleSystem
from role_system.web import install_panel
from role_system.record_management import Records,RESOURCES
from role_system.prism_module.prism.scene import Moment,build_scene
from role_system.prism_module.prism.retrieval import familiar_experiences
from test_http import ASGIClient

async def run():
    with tempfile.TemporaryDirectory() as directory:
        app=FastAPI()
        def authorize(request:Request):
            if request.cookies.get('role_panel')!='fixture-key':raise HTTPException(401)
        host=Host(directory,app,authorize);system=RoleSystem(host)
        install_panel(app,host,system,authorize,'fixture-key')
        await system.start()
        try:
            records=Records(host,system)
            for key,r in RESOURCES.items():
                if not r.table:continue
                columns={row[1] for row in records.db(r).execute(f'PRAGMA table_info({r.table})')}
                assert set(r.columns)<=columns,(key,r.columns,columns)
                records.rows(key)
            async with ASGIClient(app) as client:
                assert (await client.post('/api/records/query',{'resource':'conversation.messages'})).status_code==401
                assert (await client.post('/api/login',{'key':'fixture-key'})).status_code==200
                for i in range(55):host.append_history('scope-a','user',f'需要清理{i}')
                host.append_history('scope-b','user','其他会话保留')
                result=(await client.post('/api/records/query',{'resource':'conversation.messages','filters':{'umo':'scope-a'},'select_all':True})).json()
                assert len(result['ids'])==55
                host.append_history('scope-a','user','选择之后的新消息')
                body={'resource':'conversation.messages','ids':result['ids']}
                assert (await client.post('/api/records/delete',body)).status_code==409
                assert host.history_db.execute("SELECT COUNT(*) FROM messages WHERE umo='scope-a'").fetchone()[0]==56
                body['confirmed']=True
                deleted=(await client.post('/api/records/delete',body)).json()
                assert deleted['deleted']==55
                assert any('新消息' in r['content'] for r in host.history('scope-a'))
                assert host.history('scope-b')
                assert (await client.post('/api/records/delete',{'resource':'bad;DROP TABLE messages','ids':['1'],'confirmed':True})).status_code==422
                # 人物认知只选择当前人物，删除一个人物不留下可召回的旧别名。
                store=system.spectrum.store
                pid=system.spectrum.persons.observe('qq-fixture:FriendMessage:1','1','甲','qq-fixture')
                other=system.spectrum.persons.observe('qq-fixture:FriendMessage:2','2','乙','qq-fixture')
                mid=store.add_person_memory(pid,'甲的认知','scope-a')
                store.add_person_memory(other,'乙的认知','scope-b')
                rows=records.rows('spectrum.person_memories',{'person_id':pid})
                assert [row['id'] for row in rows]==[mid]
                records.delete('spectrum.person_memories',[str(mid)])
                assert store.get_person_memories(other)
                records.delete('spectrum.persons',[pid])
                assert not store.list_person_aliases(pid) and store.get_person(other)
                pending=store.add_pending('scope-a',time.time()+100,'尚未投递')
                with store._db:
                    store._db.execute('INSERT INTO pending_communication VALUES(?,?)',(pending,'{}'))
                records.delete('spectrum.pending',[str(pending)])
                assert not store._db.execute('SELECT * FROM pending_communication WHERE pending_id=?',(pending,)).fetchone()
                sending=store.add_pending('scope-a',time.time()+100,'正在发送')
                with store._db:store._db.execute("UPDATE pending SET status='sending' WHERE id=?",(sending,))
                chosen=(await client.post('/api/records/query',{'resource':'spectrum.pending','select_all':True})).json()
                assert str(sending) not in chosen['ids']
                assert (await client.post('/api/records/delete',{'resource':'spectrum.pending','ids':[str(sending)],'confirmed':True})).status_code==422
                # 世界书单条删除不波及其他书；整本删除只清除其启用槽。
                lib=store.worldbooks
                a=lib.save('原全局');b=lib.save('原当前')
                lib.select(global_book=a,current_book=b)
                entry=store.upsert_worldbook(title='保留',text='原背景',book_id=a)
                imported={'format':'spectrum-worldbook-library-v1','books':[],'selection':{'global_book':999,'current_book':888}}
                snapshot=lib.export_library();selection=lib.selection()
                lib.import_library(snapshot)
                assert lib.selection()==selection and len(lib.books())==4 and store.worldbook_all(book_id=a)
                records.delete('spectrum.worldbook',[str(entry)])
                assert lib.selection()==selection and len(lib.books())==4
                records.delete('spectrum.worldbook_books',[str(b)])
                assert lib.selection()['global_book']==a and lib.selection()['current_book'] is None
                # 棱镜派生归纳可移除而不销毁原场景；场景与痕迹删除同步图。
                p=system.prism
                for i in range(3):
                    sc=build_scene([Moment(time.time(),'qq-fixture:2','聊早餐',umo='scope-b',topic='早餐')],f'daily-{i}')
                    sc.narrative='我记得聊过早餐';p.memory.add(sc)
                p.graph.rebuild(p.memory.scenes.values());p.store.persist_from(p.graph,p.memory)
                summary=familiar_experiences(p.memory)[0]
                records.delete('prism.generalizations',[summary['id']])
                assert not familiar_experiences(p.memory) and len(p.memory.scenes)==3
                records.delete('prism.traces',['scene:daily-0','scene:daily-1'])
                assert set(p.memory.scenes)=={'scene:daily-2'} and 'scene:daily-0' not in p.graph.nodes
                assert p.store.count()['scenes']==1
                # 投递与人格复合键、当前角色保护。
                db=system.guard.store._db
                with db:db.execute("INSERT INTO state VALUES('scope-b','delivery','blocked')")
                row=records.rows('guard.state')[0];records.delete('guard.state',[row['_key']])
                assert not db.execute('SELECT * FROM state').fetchall()
                learner=system.personality
                current=learner.config.role_id
                records.delete('personality.roles',[current])
                assert learner.store.role(current)
                inactive=learner.store.new_role('inactive',0,time.time())
                records.delete('personality.roles',[inactive])
                assert not learner.store.role(inactive) and not learner.store.core_history(inactive)
                assert learner.store.role(current)
                html=(await client.get('/')).text
                assert 'worldbookReplace' not in html and '导入世界书时替换已有全部条目' not in html
                assert all(name in html for name in ('conversationRecords','guardRecords','personalityRecords','maintenanceDatabases','maintenanceClearAll'))
                for path in ('/assets/data/selection.js','/assets/data/selection.css','/assets/data/spectrum.js','/assets/data/prism.js','/assets/record-workbench.js'):
                    response=await client.get(path)
                    assert response.status_code==200 and len(response.text)>800,path
                assert 'deleteSelected' in (await client.get('/assets/data/spectrum.js')).text
                assert '全选筛选结果' in (await client.get('/modules/spectrum')).text
        finally:await system.close()
    print('PASS records: auth/confirmation, all matching >page, immutable ID snapshot, actor filtering, aliases, worldbook scope, derived/source consistency, composite keys, current role and maintenance preservation')
if __name__=='__main__':asyncio.run(run())
