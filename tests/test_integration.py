"""离线集成验收：不连接 QQ，不访问任何模型服务。"""
import asyncio
import json
import logging
import tempfile
import time
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from role_system.host import Host
from role_system.service import RoleSystem
from role_system.ports import Event
from role_system.provider import ModelResponse
from role_system.spectrum_module.spectrum import freeze, contract_audit
from role_system.prism_module.prism.affect import Mood, Emotion, update
from role_system.prism_module.prism.reading_model import to_read_result
from role_system.prism_module.prism.reading import RelationRead, read_relation
from role_system.prism_module.prism.resolve import resolve
from role_system.prism_module.prism.appraisal import appraise_full
from role_system.prism_module.prism.goals import GoalProfile


class FixtureModel:
    ready = True
    config = {}
    def __init__(self):
        self.calls = []
        self.extra = {}
    def role_mode(self, override=""):
        return "contract"
    async def text_chat(self, **kwargs):
        self.calls.append(kwargs)
        if '"same"' in kwargs.get('system_prompt', ''):
            return ModelResponse('{"same":true,"confidence":0.9}')
        if '"belonging"' in kwargs.get('system_prompt', ''):
            return ModelResponse(json.dumps({"belonging":0,"esteem":0,"autonomy":0,"safety":0,"novelty":0,
                "fairness":0,"no_harm":0,"competence":0,"aesthetic":0,"confidence":0.8,"topic":"家常聊天","tone":"轻松"}))
        reply = {"seen":True, "reply":{"mode":"immediate","content":"吃过了，你呢？"}, **self.extra}
        return ModelResponse('<life>我接着聊了两句。</life><reply>'+json.dumps(reply,ensure_ascii=False)+'</reply>')
    async def close(self):
        pass


def enable(module, key, value):
    from importlib import import_module
    settings = import_module(module.__module__.rsplit('.',1)[0]+'.'+{'SpectrumCorePlugin':'spectrum','PrismPlugin':'prism','DeliveryGuardPlugin':'guard'}[type(module).__name__]+'.settings')
    module.config[settings.FIELD_GROUP[key]][key] = value


async def run():
    logging.basicConfig(level=logging.WARNING)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
        host = Host(directory)
        system = RoleSystem(host)
        model = FixtureModel()
        host.provider = model
        sent = []
        async def sender(umo, chain):
            sent.append((umo,chain.get_plain_text()))
            return {"message_id":len(sent)}
        host.transport = sender
        assert system.status()['sessions'] == [] and system.spectrum.store._db.execute('SELECT COUNT(*) FROM worldbook').fetchone()[0] == 0
        host.config['enabled'] = True
        host.config['operators'] = ['10001']
        enable(system.spectrum,'enabled',True)
        # Message batching has its own tests; this suite exercises each supplied event.
        enable(system.spectrum,'private_message_merge_enabled',False)
        enable(system.prism,'enabled',True)
        enable(system.guard,'enabled',True)
        assert contract_audit.audit() == [], contract_audit.audit()
        await system.start()
        umo='qq-90001:FriendMessage:10001'
        event=Event(host,umo,'10001','甲','晚饭吃了吗',message_id='1')
        assert await system.receive(event) == 'sent'
        assert sent[-1][1]=='吃过了，你呢？'
        assert host.history(umo)==[{'role':'user','content':'晚饭吃了吗'},{'role':'assistant','content':'吃过了，你呢？'}]
        assert event.get_extra('_spectrum_reply_delivery').recorded
        assert system.spectrum.store.count_persons()==1
        assert len(model.calls)==2, len(model.calls)
        assert all('我的通信恢复' not in item['content'] for item in host.history(umo))
        # Disabled Prism neither calls its model nor changes its state.
        enable(system.prism,'enabled',False)
        before=len(model.calls)
        await system.receive(Event(host,umo,'10001','甲','你呢',message_id='2'))
        assert len(model.calls)==before+1
        # A different ID with the same display name remains a different person.
        umo2='qq-90001:FriendMessage:10002'
        await system.receive(Event(host,umo2,'10002','甲','刚才甲送的奶茶还好吗',message_id='3'))
        assert system.spectrum.store.count_persons()==2
        # Sleep receives and preserves unread messages without a model call.
        system.spectrum.store.set_sleep_asleep(time.time()+3600)
        before=len(model.calls)
        assert await system.receive(Event(host,umo,'10001','甲','睡醒告诉我')) == 'sleeping'
        assert len(model.calls)==before and system.spectrum.store.get_sleep_inbox()
        system.spectrum.store.set_sleep_awake()
        # Frozen messages remain received and cannot become new actions or replies.
        epoch=freeze.checkpoint()
        freeze.manual('freeze')
        assert not freeze.may_commit(epoch)
        before=len(model.calls)
        assert await system.receive(Event(host,umo,'10001','甲','醒了吗'))=='frozen'
        assert len(model.calls)==before
        freeze.manual('thaw')
        # A real send failure leaves no phantom assistant message or confirmed receipt.
        async def fail_sender(umo,chain):
            raise RuntimeError('fixture send failure')
        host.transport=fail_sender
        before=host.history(umo)
        failed_event=Event(host,umo,'10001','甲','再聊两句',message_id='4')
        try:
            await system.receive(failed_event)
        except RuntimeError as error:
            assert str(error)=='fixture send failure'
        else:
            raise AssertionError('send failure swallowed')
        assert host.history(umo)[-1]['role']=='user'
        assert not failed_event.get_extra('_spectrum_reply_delivery').recorded
        host.transport=sender
        # Known blocked delivery stops before any model and preserves the incoming message.
        system.guard.core.set_blocked(umo,'fixture')
        before=len(model.calls)
        assert await system.receive(Event(host,umo,'10001','甲','这条无法送达'))=='delivery_blocked'
        assert len(model.calls)==before and system.guard.store.count_inbox(umo)>0
        system.guard.core.set_ok(umo)
        # Administrator commands remain available; narrative commands are silent.
        before=len(model.calls)
        await system.receive(Event(host,umo,'10001','甲','/光谱核心 status'))
        assert len(model.calls)==before
        await system.receive(Event(host,umo,'10001','甲','/叙述事实 桌上放着一只空杯子。'))
        assert system.spectrum.store.observed_rows()
        assert len(model.calls)==before
        assert len(host.routes) > 50
        # Cross-session recollection keeps the original source/date and active promise.
        store=system.spectrum.store
        source_umo='qq-90001:FriendMessage:10003'
        system.spectrum.persons.observe(source_umo,'10003','送礼人','qq-90001')
        yesterday=time.time()-86400
        lid=system.spectrum.l2.life.ensure_life()
        source_id=store.add_entry(source_umo,'received','奶茶和米线是昨天我派人送给你的。',ts=yesterday,
            life_id=lid,meta={'sender':'送礼人','sender_id':'10003'})
        knowledge={'kind':'knowledge','subject':'奶茶和米线','text':'送礼人昨天派人送给我的奶茶和米线',
                   'basis':'received','state':'active','sourceEntryId':source_id}
        knowledge_id=store.record_shared_life_event(knowledge,source_umo=source_umo,source_entry_id=source_id,life_id=lid,ts=yesterday)
        assert store.record_shared_life_event(knowledge,source_umo=source_umo,source_entry_id=source_id,life_id=lid,ts=yesterday)==knowledge_id
        promise_id=store.add_entry(umo,'delivered','我答应你以后不再去吃米线。',ts=yesterday,life_id=lid)
        store.record_shared_life_event({'kind':'commitment','subject':'吃米线','text':'我答应甲以后不再去吃米线',
            'basis':'reply','state':'active','movement':'stay'},source_umo=umo,source_entry_id=promise_id,life_id=lid,ts=yesterday)
        event=Event(host,umo2,'10002','甲','送礼人送的奶茶是谁送来的，今天还去吃米线吗',message_id='5')
        assert await system.receive(event)=='sent'
        background='\n'.join(part.text for part in model.calls[-1]['extra_user_content_parts'])
        assert '送礼人昨天派人送给我的奶茶和米线' in background
        assert '我答应甲以后不再去吃米线' in background
        assert '不表示此刻刚发生' in background
        # Worldbook remains editable and injects only after matching the requested keyword.
        enable(system.spectrum,'worldbook_enabled',True)
        store.upsert_worldbook(title='咖啡馆',text='我在一家小咖啡馆工作。',keys='咖啡',enabled=1,position='turn')
        await system.receive(Event(host,umo2,'10002','甲','聊聊咖啡',message_id='6'))
        background='\n'.join(part.text for part in model.calls[-1]['extra_user_content_parts'])
        assert '我在一家小咖啡馆工作。' in background
        # Cross-session sends use the same transport, history and receipt registration.
        from role_system.ports import ToolContext
        tool_event=Event(host,umo,'10001','甲','帮我告诉送礼人谢谢')
        outcome=await host.tools.tools[0].call(ToolContext(host,tool_event),session=source_umo,
                                              messages=[{'type':'plain','text':'谢谢你送的奶茶。'}])
        assert outcome=='Message sent to session '+source_umo
        assert tool_event.get_extra('_spectrum_delivery_receipts')[source_umo]>0
        assert host.history(source_umo)[-1]['content']=='谢谢你送的奶茶。'
        assert system.guard.store.sent_today(source_umo)==1
        # Group arrivals preserve the real speaker and are recorded once, including addressed messages.
        enable(system.spectrum,'scope','all')
        group_umo='qq-90001:GroupMessage:20001'
        await system.receive(Event(host,group_umo,'10001','甲','群聊的第一句',message_id='7'))
        await system.receive(Event(host,group_umo,'10002','同名甲','接着刚才说',addressed=False,message_id='8'))
        assert store._db.execute("SELECT COUNT(*) FROM entries WHERE umo=? AND kind='received'",(group_umo,)).fetchone()[0]==2
        history=host.history(group_umo)
        assert history[0]['content'].startswith('[来信者：甲 · 10001]')
        assert history[-1]['content'].startswith('[来信者：同名甲 · 10002]')
        # Recovery replays the queued content and confirms its promise only after actual send.
        from role_system.spectrum_module.spectrum.parse import Decision
        promise={'kind':'commitment','subject':'补发约定','text':'我答应甲明天再聊', 'basis':'reply','state':'active'}
        payload={'communication':{'turn_id':'replay-fixture','source_actor':'qq-90001:10001'},'events':[promise]}
        oid=system.guard.store.push_outbox(umo,'明天再聊。','fixture',payload=payload)
        enable(system.guard,'flush_throttle_seconds',0)
        await system.guard.flusher._flush(umo,probe_run=False)
        assert system.guard.store._db.execute('SELECT status FROM outbox WHERE id=?',(oid,)).fetchone()[0]=='sent'
        assert any(row['subject']=='补发约定' for row in store.shared_life_events(lid))
        host.config.save_config()
        for cfg in system.configs.values():cfg.save_config()
        await system.close()
        host2=Host(directory);system2=RoleSystem(host2)
        assert system2.spectrum.store.count_persons()==3
        assert system2.spectrum.store.observed_rows()
        assert host2.history(umo)
        # No default QQ numbers, roles, or worldbook entries were introduced.
        assert system2.spectrum.store._db.execute('SELECT COUNT(*) FROM worldbook').fetchone()[0]==1
        await system2.start();await system2.close()
    # Neutrality, bounded repetition, and baseline immutability are deterministic invariants.
    neutral=to_read_result('晚饭吃了吗',{'belonging':0,'confidence':0.1})
    appraisal=resolve(neutral.event,GoalProfile(),neutral.relation.to_relation())
    assert appraise_full(appraisal)==[]
    mood=Mood(.8,.4,.2)
    shifted=update(mood,[Emotion('喜悦',.1)]).after
    assert .4 <= shifted.p < .8
    baseline=RelationRead()
    current=read_relation(['亲爱的','晚饭吃了吗'],baseline=baseline)
    assert baseline.closeness==.3 and current.closeness==.3
    print('PASS: integrated turn, identity, history, switches, sleep, freeze, send failure, guard, commands, clean start, persistence, neutral affect')


if __name__=='__main__':
    asyncio.run(run())
