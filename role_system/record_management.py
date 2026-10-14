"""按明确记录ID管理内置数据；批量选择与整库初始化使用不同入口。"""
import json
from dataclasses import dataclass
from contextlib import nullcontext

from fastapi import Depends, HTTPException, Request


@dataclass(frozen=True)
class Resource:
    database: str
    table: str
    columns: tuple
    title: str
    impact: str = '只删除所选记录。'


RESOURCES = {
    'spectrum.entries': Resource('spectrum','entries',('id',),'往来记录'),
    'spectrum.lives': Resource('spectrum','lives',('life_id',),'生活段','同时删除所选生活段中的往来；当前生活段被删除时解除其外出锁。'),
    'spectrum.persons': Resource('spectrum','persons',('person_id',),'人物档案','同时删除这些人物的别名、认知、会话关联和关系记录；原始聊天保留。'),
    'spectrum.person_memories': Resource('spectrum','person_memories',('id',),'人物认知'),
    'spectrum.memories': Resource('spectrum','memories',('id',),'长期认知'),
    'spectrum.intents': Resource('spectrum','intents',('id',),'任务与承诺','所选任务将停止执行并从列表移除。'),
    'spectrum.pending': Resource('spectrum','pending',('id',),'待发消息','未发送的所选消息停止投递，关联的待执行生活变化一并移除。'),
    'spectrum.worldbook': Resource('spectrum','worldbook',('id',),'世界书条目','只删除所选条目，其他世界书及其启用选择保留。'),
    'spectrum.worldbook_books': Resource('spectrum','worldbook_books',('id',),'世界书','同时删除所选书中的条目；被删除的书会取消启用选择。'),
    'spectrum.storylets': Resource('spectrum','storylets',('sid',),'剧情片段'),
    'spectrum.director_fires': Resource('spectrum','director_fires',('id',),'剧情触发记录'),
    'spectrum.drama_threads': Resource('spectrum','drama_threads',('tid',),'伏笔线索'),
    'spectrum.tool_notes': Resource('spectrum','tool_notes',('tool_name',),'工具说明','只清除自定义说明，不卸载或禁用工具。'),
    'prism.scenes': Resource('prism','scenes',('scene_id',),'场景回忆','同时删除对应记忆痕迹，并同步联想图和派生归纳。'),
    'prism.traces': Resource('prism','traces',('key',),'记忆痕迹','同时删除对应场景及其原始往来，并同步联想图。'),
    'prism.persons': Resource('prism','persons',('person_id',),'人物感受','只清除棱镜核心对这些人的既有感受，聊天与光谱核心关系保留。'),
    'prism.generalizations': Resource('prism','',('id',),'熟悉经历','移除当前归纳，来源场景保留；新经历可能形成新的归纳。'),
    'conversation.messages': Resource('conversation','messages',('id',),'聊天记录','删除平台聊天上下文中的所选消息；其他模块的经历记录保留。'),
    'personality.insights': Resource('personality','insights',('id',),'人格认识','同时删除所选认识的修订历史；底稿与原始聊天保留。'),
    'personality.roles': Resource('personality','roles',('id',),'角色档案','同时删除所选非当前档案的底稿版本、认识与学习记录。'),
    'guard.outbox': Resource('guard','outbox',('id',),'待补发消息','所选内容将停止补发。'),
    'guard.inbox': Resource('guard','inbox',('id',),'缺席来信'),
    'guard.state': Resource('guard','state',('umo','key'),'会话送达状态'),
    'guard.usage': Resource('guard','usage',('day','umo'),'发送用量记录'),
}


def record_key(row, columns):
    return str(row[columns[0]]) if len(columns)==1 else json.dumps([row[c] for c in columns],ensure_ascii=False,separators=(',',':'))


class Records:
    def __init__(self, host, system):
        self.host,self.system=host,system
        saved=system.prism.store._db.execute("SELECT value FROM meta WHERE key='suppressed_generalizations'").fetchone()
        system.prism.memory.suppressed_generalizations=set(json.loads(saved[0])) if saved else set()

    def resource(self, key):
        if key not in RESOURCES:raise ValueError('没有这种可管理的数据')
        return RESOURCES[key]

    def db(self, resource):
        if resource.database=='conversation':return self.host.history_db
        if resource.database=='personality':return self.system.personality.store.db
        return getattr(self.system,resource.database).store._db

    def rows(self, key, filters=None):
        resource=self.resource(key); filters=filters or {}
        if key=='prism.generalizations':
            from .prism_module.prism.retrieval import familiar_experiences
            rows=familiar_experiences(self.system.prism.memory)
        else:
            db=self.db(resource)
            cursor=db.execute(f'SELECT * FROM {resource.table}')
            names=[c[0] for c in cursor.description]
            rows=[dict(zip(names,row)) for row in cursor]
        result=[]
        for row in rows:
            if any(filters.get(k) not in (None,'') and str(row.get(k,''))!=str(filters[k])
                   for k in ('umo','life_id','person_id','book_id')):continue
            if key=='spectrum.intents' and not filters.get('history') and row['status']!='waiting':continue
            if key=='spectrum.pending' and not filters.get('history') and row['status'] not in ('waiting','sending','uncertain'):continue
            if key=='spectrum.entries' and not filters.get('history') and row['kind'] not in ('received','delivered','delivered_media'):continue
            if key=='personality.insights':
                if row['role_id']!=self.system.personality.config.role_id:continue
                if filters.get('status') and row['status']!=filters['status']:continue
            if key=='personality.roles' and row['id']==self.system.personality.config.role_id:continue
            if key=='spectrum.persons':
                row['aliases']=[r['alias'] for r in self.system.spectrum.store.list_person_aliases(row['person_id'])]
            search=str(filters.get('search') or '').strip().casefold()
            if search and search not in json.dumps(row,ensure_ascii=False).casefold():continue
            row['_key']=record_key(row,resource.columns)
            result.append(row)
        return result

    def description(self,key):
        r=self.resource(key)
        return {'resource':key,'title':r.title,'impact':r.impact}

    def delete(self,key,ids):
        r=self.resource(key)
        ids=list(dict.fromkeys(str(i) for i in ids))
        if not ids:raise ValueError('请先选择记录')
        current={row['_key']:row for row in self.rows(key,{'history':True})}
        selected=[current[i] for i in ids if i in current]
        s=self.system.spectrum.store
        if key=='spectrum.lives':
            for row in selected:self.system.spectrum.l2.life.delete_life(row['life_id'])
        elif key=='spectrum.persons':
            for row in selected:
                pid=row['person_id'];s.delete_person(pid)
                with s._lock,s._db:
                    s._db.execute('DELETE FROM person_aliases WHERE person_id=?',(pid,))
        elif key=='spectrum.storylets':
            for row in selected:s.delete_storylet(row['sid'])
            self.system.spectrum.l2.director.refresh_library()
        elif key=='spectrum.memories':
            for row in selected:s.delete_memory(row['id'])
        elif key=='spectrum.person_memories':
            for row in selected:s.delete_person_memory(row['id'])
        elif key=='spectrum.worldbook':
            for row in selected:s.delete_worldbook(row['id'])
        elif key=='spectrum.worldbook_books':
            for row in selected:s.worldbooks.delete(row['id'])
        elif key.startswith('prism.'):
            p=self.system.prism
            if key=='prism.generalizations':
                p.memory.suppressed_generalizations.update(row['id'] for row in selected)
                with p.store._db:
                    p.store._db.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('suppressed_generalizations',?)",
                        (json.dumps(sorted(p.memory.suppressed_generalizations)),))
            elif key=='prism.persons':
                for row in selected:
                    pid=row['person_id'];p.interperson._state.pop(pid,None);p.relations.pop(pid,None)
                self.delete_sql(r,selected)
            else:
                scenes=[row['scene_id'] if key=='prism.scenes' else row['key'].removeprefix('scene:') for row in selected]
                with p.store._db:
                    for sid in scenes:
                        p.memory.scenes.pop('scene:'+sid,None);p.memory.traces.pop('scene:'+sid,None)
                        p.store._db.execute('DELETE FROM scenes WHERE scene_id=?',(sid,))
                        p.store._db.execute('DELETE FROM traces WHERE key=?',('scene:'+sid,))
                        for umo,opened in list(p.scene_book.open.items()):
                            if opened.scene_id==sid:p.scene_book.open.pop(umo)
                p.graph.rebuild(p.memory.scenes.values());p.store.persist_from(p.graph,p.memory)
        elif key.startswith('personality.'):
            learner=self.system.personality
            if learner.busy:raise ValueError('人格整理正在进行，请完成后再删除')
            learner.epoch+=1
            db=learner.store.db
            with db:
                for row in selected:
                    if key=='personality.insights':
                        db.execute('DELETE FROM insight_versions WHERE insight_id=?',(row['id'],))
                    else:
                        db.execute('DELETE FROM insight_versions WHERE insight_id IN (SELECT id FROM insights WHERE role_id=?)',(row['id'],))
                        for table in ('core_versions','insights','requests','learning_queue'):
                            db.execute(f'DELETE FROM {table} WHERE role_id=?',(row['id'],))
            self.delete_sql(r,selected)
        else:
            if key=='spectrum.pending':
                if any(row['status']=='sending' for row in selected):raise ValueError('选择中有正在发送的消息，请等发送结束后刷新')
                with s._lock,s._db:
                    for row in selected:
                        for table in ('pending_life_events','pending_communication'):
                            s._db.execute(f'DELETE FROM {table} WHERE pending_id=?',(row['id'],))
            self.delete_sql(r,selected)
            if key=='spectrum.entries':s._drop_vectors('entries',[row['id'] for row in selected])
        return {'deleted':len(selected),'missing':len(ids)-len(selected),**self.description(key)}

    def delete_sql(self,r,rows):
        db=self.db(r)
        where=' AND '.join(c+'=?' for c in r.columns)
        lock=getattr(self.system,r.database).store._lock if r.database in ('spectrum','guard') else nullcontext()
        with lock,db:db.executemany(f'DELETE FROM {r.table} WHERE {where}',[tuple(row[c] for c in r.columns) for row in rows])


def install_records(app,host,system,authorize):
    records=Records(host,system)
    dependencies=[Depends(authorize)]
    @app.get('/api/records/resources',dependencies=dependencies)
    async def resources():return [records.description(k) for k in RESOURCES]

    @app.post('/api/records/query',dependencies=dependencies)
    async def query(request:Request):
        body=await request.json()
        try:
            async with system.lock:
                rows=records.rows(body['resource'],body.get('filters'))
                description=records.description(body['resource'])
            if body.get('select_all'):
                ids=[r['_key'] for r in rows if not(body['resource']=='spectrum.pending' and r.get('status')=='sending')]
                return {'ids':ids,'total':len(ids),**description}
            offset=max(0,int(body.get('offset',0)));limit=max(1,min(200,int(body.get('limit',50))))
            return {'items':rows[offset:offset+limit],'total':len(rows),**description}
        except (ValueError,KeyError,TypeError) as error:raise HTTPException(422,str(error)) from error

    @app.post('/api/records/delete',dependencies=dependencies)
    async def delete(request:Request):
        body=await request.json()
        if body.get('confirmed') is not True:raise HTTPException(409,'需要确认删除的记录与范围')
        if not isinstance(body.get('ids'),list):raise HTTPException(422,'请提交明确的记录编号列表')
        try:
            async with system.lock:return records.delete(body['resource'],body['ids'])
        except (ValueError,KeyError,TypeError) as error:raise HTTPException(422,str(error)) from error
    return records
