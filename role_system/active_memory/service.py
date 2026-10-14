"""可选主动工具和语义过滤，所有检索由显式调用启动。"""
import asyncio
import hashlib
import json
import math
import time as clock
from collections import deque
from .adapter import HostAdapter
from .core import Context, Cues, EMOTIONS, candidates, normalize_time, render, select
from ..ports import ToolUsageError, logger
from ..spectrum_module.spectrum import freeze

DEFAULTS={'enabled':False,'prism':True,'personality':False,'max_results':3,'char_budget':1800,
          'vector_enabled':False,'embedding_provider_id':'','vector_threshold':.25,'candidate_limit':80}


def validate(values):
    if not isinstance(values,dict) or set(values)-set(DEFAULTS):raise ValueError('主动回忆设置包含未知选项')
    result={**DEFAULTS,**values}
    for key in ('enabled','prism','personality','vector_enabled'):
        if type(result[key]) is not bool:raise ValueError(key+'使用开关值')
    for key,low,high in [('max_results',1,8),('char_budget',300,5000),('candidate_limit',10,200)]:
        if type(result[key]) is not int or not low<=result[key]<=high:raise ValueError(key+'超出允许范围')
    threshold=result['vector_threshold']
    if type(threshold) not in (int,float) or not math.isfinite(threshold) or not -1<=threshold<=1:raise ValueError('语义过滤阈值使用-1至1的数值')
    if not isinstance(result['embedding_provider_id'],str):raise ValueError('请选择已经配置的嵌入模型')
    return result


class ActiveMemory:
    name='recall_memory'
    description='我想主动回忆过去的人物、事情、时间或感受时，可以使用这个工具。我可以只给出记得的线索；我保留结果的归属与来源，是否提起由我决定。'
    parameters={'type':'object','properties':{
        'query':{'type':'string','description':'我想回忆的事情、话题或片段'},
        'about':{'type':'string','description':'我想起的相关人物称呼或身份，省略时按其他线索查找'},
        'time':{'type':'object','description':'我记得的时间线索','properties':{
            'start':{'type':'string','description':'起始ISO日期或时间'},'end':{'type':'string','description':'结束ISO日期或时间'},
            'hint':{'type':'string','description':'昨天、昨天晚上、上周、上次等线索'}}},
        'feel':{'type':'object','description':'我自己当时的感受，不代表交谈者的情绪','properties':{
            'emotion':{'type':'string','description':'开心、委屈、紧张等感受'},
            'intensity':{'type':'number','description':'我记得的情绪强度，0至1，可省略'}}}}}

    def __init__(self,system):
        self.system=system
        self.host=system.host
        self.host.config.setdefault('active_memory',dict(DEFAULTS))
        self.host.config.save_config()
        self.adapter=HostAdapter(system)
        self.recent=deque(maxlen=30)
        self.path=self.host.data_dir/'active_memory_vectors.json'
        self.vectors=json.loads(self.path.read_text()) if self.path.exists() else {}
        self.query_cache={}
        self.task=None
        self.index_status={'state':'idle','encoded':0,'total':0}

    @property
    def settings(self):return validate(self.host.config.get('active_memory',{}))

    @property
    def active(self):return self.settings['enabled']

    @active.setter
    def active(self,value):
        self.host.config['active_memory']={**self.settings,'enabled':bool(value)}
        self.host.config.save_config()

    def context(self,session):
        return Context(session.split(':',1)[0],self.system.personality.config.role_id,session,clock.time())

    def provider(self):
        id=self.settings['embedding_provider_id']
        if not id:id=self.host.config.get('embedding_provider_id','')
        provider=self.host.providers.get(id) if id else self.host.embedding
        return provider if provider and provider.ready and provider.config.get('category','embedding')=='embedding' else None

    def space(self,provider):
        config=provider.config
        values={k:config.get(k) for k in ('model','base_url','protocol','parameters','source_parameters')}
        return hashlib.sha256(json.dumps(values,sort_keys=True).encode()).hexdigest()

    def vector_key(self,ctx,memory,space):
        return hashlib.sha256((ctx.account+ctx.role+space+memory.key+memory.text).encode()).hexdigest()

    def save_vectors(self):
        temp=self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(self.vectors))
        temp.replace(self.path)

    async def semantic_filter(self,pool,ctx,cues):
        settings=self.settings
        if not settings['vector_enabled']:return pool,'关闭'
        if not cues.query:return pool,'本次没有需要语义过滤的文字线索'
        provider=self.provider()
        if provider is None:return pool,'嵌入模型未配置，使用基础筛选'
        space=self.space(provider)
        cached={m.key:self.vectors.get(self.vector_key(ctx,m,space)) for m,_,_ in pool}
        if not any(cached.values()):return pool,'候选尚未建立语义索引，使用基础筛选'
        query_key=hashlib.sha256((space+cues.query).encode()).hexdigest()
        try:
            qvec=self.query_cache.get(query_key)
            if qvec is None:
                qvec=await asyncio.wait_for(provider.get_embedding(cues.query,optional=True),timeout=10)
                if len(self.query_cache)>=64:self.query_cache.pop(next(iter(self.query_cache)))
                self.query_cache[query_key]=qvec
        except freeze.FrozenSkipped:raise
        except Exception as error:
            logger.warning('[主动回忆] 可选语义过滤未完成：%s；保留基础筛选',type(error).__name__)
            return pool,'语义过滤未完成，使用基础筛选'
        if ctx.role!=self.system.personality.config.role_id or space!=self.space(provider):
            raise ToolUsageError('回忆期间角色或嵌入配置发生变化，本次结果未使用')
        result=[];covered=0
        for item in pool:
            vector=cached[item[0].key]
            if vector is None or len(vector)!=len(qvec):result.append(item);continue
            norm=math.sqrt(sum(x*x for x in vector)*sum(x*x for x in qvec))
            if not norm:result.append(item);continue
            covered+=1
            score=sum(a*b for a,b in zip(vector,qvec))/norm
            if score>=settings['vector_threshold']:result.append(item)
        return result,f'已过滤{covered}个有索引候选；无索引候选保留基础判断'

    async def call(self,wrapper,query='',about='',time=None,feel=None):
        if not self.active:return '我当前的主动回忆功能已关闭。'
        for text in (query,about):
            if not isinstance(text,str) or len(text)>300:raise ToolUsageError('query/about使用最多300字的文字')
        event=wrapper.context.event
        ctx=self.context(event.unified_msg_origin)
        epoch=freeze.checkpoint()
        started=clock.monotonic()
        try:
            start,end,latest,notes=normalize_time(time,ctx)
            people=()
            if about.strip() not in ('','我','自己'):
                target=ctx.account+':'+about.strip() if about.strip().isdigit() else about.strip()
                people=tuple(sorted({pid for alias,pid in self.adapter.aliases(ctx.account) if alias==target}))
                if len(people)!=1:return json.dumps({'说明':'我没有确定这个称呼对应的唯一人物。','记忆':[]},ensure_ascii=False)
            feel=feel or {}
            if not isinstance(feel,dict) or set(feel)-{'emotion','intensity'}:raise ValueError('feel使用emotion/intensity组成的对象')
            emotion=feel.get('emotion','')
            if not isinstance(emotion,str):raise ValueError('情绪名称使用文字')
            intensity=feel.get('intensity')
            if intensity is not None and (type(intensity) not in (int,float) or not math.isfinite(intensity) or not 0<=intensity<=1):raise ValueError('情绪强度使用0至1的数值')
        except (ValueError,TypeError,AttributeError) as error:raise ToolUsageError(str(error)) from error
        if not (query.strip() or people or start is not None or end is not None or emotion.strip()):
            return json.dumps({'说明':'我需要至少一个事情、人物、时间或感受线索。','记忆':[]},ensure_ascii=False)
        cues=Cues(query.strip(),people,start,end,latest,EMOTIONS.get(emotion,emotion),intensity,notes)
        records=self.adapter.records(ctx,self.settings)
        pool=candidates(records,ctx,cues,self.settings['candidate_limit'])
        count=len(pool)
        pool,semantic=await self.semantic_filter(pool,ctx,cues) if pool else (pool,'没有候选')
        if not freeze.may_commit(epoch):raise freeze.FrozenSkipped('回忆结果跨过系统暂停')
        if ctx.role!=self.system.personality.config.role_id:raise ToolUsageError('回忆期间角色已切换，本次结果未使用')
        found=select(pool,ctx,cues,self.settings['max_results'])
        result=render(found,ctx,notes,self.settings['char_budget'])
        result['语义过滤']=semantic
        diagnostic={'at':ctx.now,'session':ctx.session,'role':ctx.role,'candidates':count,'searchable':len(pool),
                    'results':[m.key for m in found],'semantic':semantic,'elapsed_ms':round((clock.monotonic()-started)*1000)}
        self.recent.appendleft(diagnostic)
        logger.info('[主动回忆] %s | 候选%d / 可搜索%d / 返回%d | %s',ctx.session,count,len(pool),len(found),semantic)
        return json.dumps(result,ensure_ascii=False)

    def start_index(self):
        if self.task and not self.task.done():raise ValueError('已有一项语义索引任务')
        if not self.settings['vector_enabled'] or self.provider() is None:raise ValueError('请开启语义过滤并选择可用的嵌入模型')
        self.task=asyncio.create_task(self.prepare_index())

    async def prepare_index(self):
        provider=self.provider();space=self.space(provider);role=self.system.personality.config.role_id
        epoch=freeze.checkpoint()
        self.index_status={'state':'running','encoded':0,'total':0}
        try:
            for account in list(self.host.platforms):
                ctx=self.context(account+':FriendMessage:index')
                async with self.system.lock:records=self.adapter.records(ctx,self.settings)
                pending=[m for m in records if self.vector_key(ctx,m,space) not in self.vectors]
                self.index_status['total']+=len(pending)
                for offset in range(0,len(pending),32):
                    if not self.settings['vector_enabled'] or not freeze.may_commit(epoch) or role!=self.system.personality.config.role_id:
                        self.index_status['state']='paused';return
                    batch=pending[offset:offset+32]
                    vectors=await asyncio.wait_for(provider.get_embeddings([m.text[:1200] for m in batch],optional=True),timeout=30)
                    if not freeze.may_commit(epoch) or role!=self.system.personality.config.role_id or space!=self.space(provider):
                        self.index_status['state']='paused';return
                    for m,vector in zip(batch,vectors):self.vectors[self.vector_key(ctx,m,space)]=vector
                    self.index_status['encoded']+=len(batch)
                    self.save_vectors()
            self.index_status['state']='complete'
        except asyncio.CancelledError:
            self.index_status['state']='paused';raise
        except Exception as error:
            self.index_status['state']='unavailable'
            logger.warning('[主动回忆] 可选索引准备未完成：%s',type(error).__name__)

    async def close(self):
        if self.task and not self.task.done():
            self.task.cancel()
            try:await self.task
            except asyncio.CancelledError:pass
