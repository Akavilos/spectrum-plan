"""棱镜闭环的集中离线验证：不连接模型服务或QQ。"""
import asyncio
import json
import sys
import tempfile
import time
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from role_system.ports import Event, request_scope
from role_system.prism_module.main import PrismPlugin
from role_system.prism_module.prism import settings
from role_system.prism_module.prism.scene import Moment, build_scene
from role_system.prism_module.prism.open_scene import OpenScene
from role_system.prism_module.prism.understanding import Understanding
from role_system.prism_module.prism.experience import correction_targets, apply_correction, rehearse
from role_system.prism_module.prism.retrieval import familiar_experiences
from role_system.prism_module.prism.reading_model import to_read_result
from role_system.prism_module.prism.personality import Personality
from role_system.prism_module.prism.affect import Mood
from role_system.prism_module.prism.state import snapshot
from role_system.prism_module.prism.store import PrismStore
from role_system.prism_module.prism.batch import to_segments
from role_system.spectrum_module.spectrum import freeze, limiter

A, B = 'qq-fixture:10001', 'qq-fixture:20002'
PRIVATE = 'qq-fixture:FriendMessage:10001'
GROUP = 'qq-fixture:GroupMessage:30003'


class Model:
    config = {'max_tokens':8192}
    def __init__(self): self.calls = []
    async def text_chat(self, **kw):
        self.calls.append(kw)
        if '"same"' in kw['system_prompt']:
            result = {'same':True,'confidence':.9}
        elif '"segments"' in kw['system_prompt']:
            result = {'segments':[{'start_index':3,'end_index':4,'topic':'奶茶'}]}
        else:
            result = {'belonging':.1,'confidence':.8,'topic':'奶茶','tone':'轻松',
                      'interpretation':'我收到一条关于奶茶的来信','corrections':[]}
        return SimpleNamespace(completion_text=json.dumps(result,ensure_ascii=False))


class Host:
    config = {'enabled':True}
    def __init__(self, path): self.path=Path(path); self.model=Model()
    def module_dir(self, name): return self.path/name
    def get_provider_by_id(self, id): return self.model
    async def get_using_provider_async(self, umo): return self.model


def set_option(p, key, value): p.config[settings.FIELD_GROUP[key]][key]=value


def add(p, name, text, topic, person=A, umo=PRIVATE, salience=0):
    now=time.time()
    sc=build_scene([Moment(now-20,person,text,umo=umo,topic=topic,name='甲' if person==A else '乙',salience=salience)],name)
    sc.narrative=text
    p.memory.add(sc,now=now-10)
    p.graph.rebuild(p.memory.scenes.values())
    p.store.persist_from(p.graph,p.memory)
    return sc


async def api(p, method, body):
    async def payload(): return body
    token=request_scope.set(SimpleNamespace(json=payload,query_params={}))
    try:return await getattr(p.page_api,method)()
    finally:request_scope.reset(token)


async def run():
    freeze.reset_for_test()
    limiter.set_cfg_getter(lambda key,default:False if key=='low_freq_mode' else default)
    with tempfile.TemporaryDirectory() as path, ExitStack() as cleanup:
        host=Host(path)
        old=settings.FIELD_GROUP['share_mood']
        p=PrismPlugin(host,{'settings_layout_version':1,old:{'share_mood':False}})
        cleanup.callback(p.store.close)
        for key in ('correction_enabled','reconsolidation_enabled','generalization_enabled','recall_by_topic'):
            assert p._cfg(key,None) is True
        for key in ('batch_reading_enabled','rehearsal_enabled','share_expression','share_scene_details'):
            assert p._cfg(key,None) is False
        assert p._cfg('share_mood',True) is False
        # 快照不因话题或时间间隔丢失后面的消息。
        opened=OpenScene(PRIVATE,'snapshot',[Moment(1,A,'前半',topic='一'),Moment(2000,A,'后半',topic='二')])
        assert [m.text for m in opened.seal().moments]==['前半','后半']
        saved=opened.seal(); saved.segments=[{'start':0,'end':1,'topic':'二'}]
        saved.moments[0].understanding=Understanding('前半','当时理解')
        restored=PrismStore.json_to_scene(PrismStore.scene_to_json(saved))
        assert restored.moments[0].understanding.read_as=='当时理解' and restored.segments==saved.segments
        legacy=json.loads(PrismStore.scene_to_json(saved)); legacy.pop('topic'); legacy.pop('segments')
        for m in legacy['moments']: m.pop('understanding'); m.pop('name')
        assert PrismStore.json_to_scene(json.dumps(legacy)).topic=='二'
        milk=add(p,'milk','昨天甲送来了奶茶','奶茶')
        unrelated=add(p,'other','乙聊起了完全无关的飞机','飞机',B,'qq-fixture:FriendMessage:20002')
        text=p.read_context(umo=GROUP,is_group=True,person=B,query='昨天那杯奶茶呢',mentioned_people=[A])
        assert '奶茶' in text and A in text and PRIVATE in text and '飞机' not in text
        assert '私下' in text and p.memory.traces['scene:milk'].rehearsals==0
        milk.noticed=['我留意到杯子']; milk.not_noticed=['NOT_PERCEIVED_SECRET']; milk.doing='休息'
        set_option(p,'share_scene_details',True)
        text=p.read_context(umo=GROUP,is_group=True,person=B,query='奶茶')
        assert '我留意到杯子' in text and 'NOT_PERCEIVED_SECRET' not in text
        set_option(p,'share_scene_details',False)
        p.interperson._state[B]={'n':5,'mood':[.3,.1,.1],'updated':time.time(),'last':'喜悦'}
        assert '既有感受' in p.read_context(umo=GROUP,is_group=True,person=B,peer_name='乙')
        set_option(p,'share_interperson',False)
        assert '既有感受' not in p.read_context(umo=GROUP,is_group=True,person=B)
        # 明确纠正保留原话、旧理解和新版本；其他人物不提供可纠正目标。
        wrong=add(p,'wrong','我记得奶茶是塔克送的','奶茶')
        wrong.moments[0].is_hers=True
        wrong.moments[0].understanding=Understanding(wrong.moments[0].text,'我以为是塔克送的')
        incoming='你记错了，奶茶是我昨天送的，不是塔克送的。'
        targets=correction_targets(p,PRIVATE,A)
        index=next(i for i,(_,m) in enumerate(targets) if m is wrong.moments[0])
        read=to_read_result(incoming,{'confidence':.9,'corrections':[{'target':str(index),'previous_quote':'奶茶是塔克送的','evidence':'奶茶是我昨天送的'}]})
        original=wrong.moments[0].text
        assert apply_correction(p,read,incoming,targets)==['scene:wrong']
        assert wrong.moments[0].text==original and wrong.moments[0].understanding.was_wrong
        assert len(wrong.revisions)==2 and '后来' in wrong.narrative
        assert not correction_targets(p,PRIVATE,B)
        assert apply_correction(p,read,incoming,targets)==[]
        assert p.memory.traces['scene:wrong'].rehearsals==0
        # 关闭再巩固仍保存明确纠正，原叙事不被改写。
        plain=add(p,'plain','这次米线是塔克送的','米线')
        targets=correction_targets(p,PRIVATE,A)
        index=next(i for i,(_,m) in enumerate(targets) if m is plain.moments[0])
        read=to_read_result('你记错了，米线是我送的',{'confidence':.9,'corrections':[{'target':str(index),'previous_quote':'米线是塔克送的','evidence':'米线是我送的'}]})
        set_option(p,'reconsolidation_enabled',False)
        apply_correction(p,read,'你记错了，米线是我送的',targets)
        assert plain.narrative=='这次米线是塔克送的' and len(plain.revisions)==1
        # 加固只在明确重访触发，并有每日去重。
        set_option(p,'rehearsal_enabled',True)
        p.memory.traces['scene:wrong'].last_recalled=0
        reinforced=rehearse(p,umo=PRIVATE,person=A,query='还记得上次奶茶吗')
        assert reinforced
        assert not rehearse(p,umo=PRIVATE,person=A,query='还记得上次奶茶吗')
        # 平淡经历归纳带来源，来源减少到两段时立即撤回。
        for i in range(3): add(p,f'daily{i}','我们聊了早餐','早餐')
        summaries=familiar_experiences(p.memory,A,'早餐')
        assert len(summaries)==1 and summaries[0]['count']==3 and len(summaries[0]['sources'])==3
        assert (await api(p,'delete_scene',{'scene_id':'daily2'}))['ok']
        assert not familiar_experiences(p.memory,A,'早餐')
        # 编辑与删除之后内存图、数据库图和恢复结果一致。
        result=await api(p,'update_scene',{'scene_id':'milk','narrative':'更新后的奶茶回忆'})
        assert result['ok'] and p.graph.nodes['scene:milk'].data['narrative']=='更新后的奶茶回忆'
        result=await api(p,'delete_scene',{'scene_id':'other'})
        assert result['ok'] and 'scene:other' not in p.graph.nodes
        assert not any('scene:other' in edges for edges in p.graph.edges.values())
        assert not p.store._db.execute("SELECT 1 FROM nodes WHERE key='scene:other'").fetchone()
        # 批量分区必须严格覆盖每条原始消息一次。
        for raw in ([{'start_index':4,'end_index':5}],
                    [{'start_index':0,'end_index':9},{'start_index':3,'end_index':4},{'start_index':5,'end_index':6}]):
            parts=to_segments(raw,10)
            assert [i for part in parts for i in range(part['start'],part['end']+1)]==list(range(10))
        set_option(p,'write_narrative',False)
        set_option(p,'batch_reading_enabled',True)
        p.scene_book.open[GROUP]=OpenScene(GROUP,'batch',[Moment(time.time(),B,f'消息{i}',umo=GROUP) for i in range(24)])
        host.model.calls.clear()
        await p._seal(GROUP,host.model)
        batch=p.memory.scenes['scene:batch']
        assert len(host.model.calls)==2 and len(batch.moments)==24
        assert [i for part in batch.segments for i in range(part['start'],part['end']+1)]==list(range(24))
        set_option(p,'batch_reading_enabled',False)
        # 正常消息只追加原有的一次分析，纠正关闭时不附额外字段要求。
        host.model.calls.clear()
        event=Event(host,GROUP,'20002','乙','奶茶不错',message_id='test1')
        await p.on_llm_request(event,None)
        assert len(host.model.calls)==1 and not p.scene_book.open[GROUP].moments[-1].is_hers
        assert p.scene_book.open[GROUP].moments[-1].understanding is not None
        # 场景边界先封存旧往来，当前来信留在新场景等待本轮回复。
        boundary_umo='qq-fixture:FriendMessage:boundary'
        old_time=time.time()-8000
        p.scene_book.open[boundary_umo]=OpenScene(boundary_umo,'old-boundary',[
            Moment(old_time,A,'旧来信',umo=boundary_umo),Moment(old_time+1,A,'旧回复',is_hers=True,umo=boundary_umo)])
        current=Moment(time.time(),A,'当前来信',umo=boundary_umo)
        await p._append_moment(boundary_umo,current,host.model,'奶茶')
        assert [m.text for m in p.memory.scenes['scene:old-boundary'].moments]==['旧来信','旧回复']
        assert [m.text for m in p.scene_book.open[boundary_umo].moments]==['当前来信']
        set_option(p,'correction_enabled',False)
        host.model.calls.clear()
        await p.on_llm_request(Event(host,GROUP,'20002','乙','奶茶好喝',message_id='test2'),None)
        assert len(host.model.calls)==1 and '额外记录 interpretation' not in host.model.calls[0]['system_prompt']
        assert p.scene_book.open[GROUP].moments[-1].understanding is None
        # 手动召回具有当前人物和来源起点，不再固定空结果。
        cmd=Event(host,PRIVATE,'10001','甲','/棱镜 想 奶茶')
        assert '奶茶' in (await p.prism_cmd(cmd)).get_plain_text()
        for key in ('share_mood','share_interperson','share_recall','share_recent_others','share_expression'):set_option(p,key,False)
        assert not p.read_context(umo=PRIVATE,person=A,query='奶茶')
        # 人格倍率与恢复实际生效，半衰期不再修改进程全局常量。
        trait=Personality(cares={'autonomy':2})
        assert trait.goals.weight('autonomy')>Personality().goals.weight('autonomy')
        trait.detailed_effects=False
        assert trait.goals.weight('autonomy')==Personality().goals.weight('autonomy')
        p.memory.set_half_life(10)
        second=PrismPlugin(Host(Path(path)/'second'),{})
        cleanup.callback(second.store.close)
        assert second.memory.half_life_hours==72 and all(t.half_life_hours==10 for t in p.memory.traces.values())
        p.personality=Personality(neuroticism=0,extraversion=1)
        start=time.time()-4*3600
        p.mood=Mood(1,1,1); p.mood_state=snapshot(p.mood); p.mood_state.updated_at=start
        p._mood_decay_at=0
        set_option(p,'personality_effects_enabled',True); p._relax_mood(); fast=p.mood.p
        p.mood=Mood(1,1,1); p.mood_state=snapshot(p.mood); p.mood_state.updated_at=start; p._mood_decay_at=0
        set_option(p,'personality_effects_enabled',False); p._relax_mood()
        assert fast<p.mood.p
        second.store.close();p.store.close()
    print('PASS: full snapshots/legacy data, actor/source recall, sharing switches, correction versions, rehearsal dedup, derived sources, graph sync, batch coverage, real hooks, no added normal call, personality and instance isolation')

if __name__=='__main__':asyncio.run(run())
