"""跨功能边界回归：假模型与假发送器，不连接 QQ 或模型服务。"""
import asyncio
import json
import logging
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from role_system.host import Host
from role_system.service import RoleSystem
from role_system.ports import Event, MessageChain, Plain, NativeSegment
from role_system.provider import ModelResponse
from role_system.spectrum_module.spectrum import freeze
from role_system.spectrum_module.spectrum.lifecycle import intent_ready
from role_system.spectrum_module.spectrum.director import Turn, DirectorPlan
from test_integration import enable


BODY = '<life>我顺着当前话题聊。</life><reply>{"seen":true,"audience":"here","reply":{"mode":"immediate","content":"这事我也听说了"}}</reply>'


class Model:
    ready = True

    def __init__(self):
        self.calls = []
        self.tools = []

    def role_mode(self, override=''): return 'contract'

    async def text_chat(self, **kwargs):
        self.calls.append(kwargs)
        if self.tools:
            calls, self.tools = self.tools, []
            return ModelResponse('', tool_calls=calls, input_messages=[
                {'role': 'system', 'content': kwargs['system_prompt']},
                {'role': 'user', 'content': kwargs['prompt']}])
        return ModelResponse(BODY)

    async def close(self): pass


async def run():
    logging.basicConfig(level=logging.ERROR)
    with tempfile.TemporaryDirectory() as directory:
        host = Host(directory)
        host.config.update(enabled=True, persona='我有自己的生活，也能参与群聊。')
        system = RoleSystem(host)
        host.provider = model = Model()
        enable(system.prism, 'enabled', False)
        for key, value in [('enabled', True), ('scope', 'all'), ('l3_interject_enabled', True),
                           ('l3_interject_threshold', 0.8), ('l2_advance_enabled', False)]:
            enable(system.spectrum, key, value)
        sent = []

        async def sender(umo, chain):
            sent.append((umo, chain))
            return {'message_id': len(sent)}

        host.transport = sender
        await system.start()
        try:
            store, l2 = system.spectrum.store, system.spectrum.l2
            group = 'qq-fixture:GroupMessage:100'
            enable(system.spectrum, 'worldbook_enabled', True)
            enable(system.spectrum, 'worldbook_scan_depth', 0)
            store.upsert_worldbook(keys='桥边', text='桥边的书店叫青禾书店', position='turn')
            pid = system.spectrum.persons.observe('qq-fixture:FriendMessage:9', '9', '阿岚', 'qq-fixture')
            system.spectrum.persons.remember(pid, '阿岚公开说过喜欢这家书店', 'qq-fixture:GroupMessage:999')
            system.spectrum.persons.remember(pid, '阿岚私下说过一个隐藏秘密', 'qq-fixture:FriendMessage:9')
            store.set_willingness(group, 0.99)
            assert not store.get_state(group, 'sys_core')
            await system.receive(Event(host, group, '1', '甲', '阿岚说新开的书店就在桥边', addressed=False))
            assert len(sent) == 1 and len(model.calls) == 1, 'fresh group interjection never fired'
            context = '\n'.join(p.text for p in model.calls[-1]['extra_user_content_parts'])
            assert '新开的书店就在桥边' in context and '身份 1' in context and '刚收到的新消息' in context
            assert '青禾书店' in context and '阿岚公开说过喜欢这家书店' in context
            assert '隐藏秘密' not in context, 'private memory leaked into group'
            assert '新开的书店' not in model.calls[-1]['system_prompt'], 'dynamic group content entered stable prefix'
            store.set_willingness(group, 0.99)
            await system.receive(Event(host, group, '2', '乙', '我明天想去看看', addressed=False))
            assert len(sent) == 1, 'reply cooldown ignored'
            past = l2.recent_exchange_context(group, turns=6)
            assert '身份 1' in past and '身份 2' in past and time.strftime('%Y-') in past, 'group senders or timestamps lost'
            other = 'qq-fixture:GroupMessage:200'
            enable(system.spectrum, 'scope', 'private')
            store.set_willingness(other, 0.99)
            await system.receive(Event(host, other, '1', '甲', '仅私聊时不在群里插话', addressed=False))
            assert len(sent) == 1
            enable(system.spectrum, 'scope', 'all')
            enable(system.spectrum, 'l3_interject_enabled', False)
            await system.receive(Event(host, other, '1', '甲', '关闭插话时不发送', addressed=False))
            assert len(sent) == 1

            private = 'qq-fixture:FriendMessage:1'
            store.set_sleep_asleep(time.time() + 3600)
            assert await system.receive(Event(host, private, '1', '甲', '醒来再看')) == 'sleeping'
            unread = store.get_sleep_inbox()
            enable(system.spectrum, 'l2_sleep_enabled', False)
            freeze.manual('freeze')
            l2.sync_sleep_setting()
            assert store.is_asleep(), 'frozen state advanced'
            freeze.manual('thaw')
            assert await system.receive(Event(host, private, '1', '甲', '已经关闭睡眠了')) == 'sent'
            assert not store.is_asleep() and store.get_sleep_inbox() == unread
            assert store.get_state('global', 'last_sleep')

            enable(system.spectrum, 'memory_vector_enabled', True)
            assert l2.get_cfg('enabled', False) and system.spectrum._vector_tables()
            enable(system.spectrum, 'enabled', False)
            assert system.spectrum._vector_tables() == (), 'disabled module still schedules embeddings'
            enable(system.spectrum, 'enabled', True)
            enable(system.spectrum, 'memory_vector_enabled', False)

            # 已延期的素材继续等待，普通任务不消耗剧情预算。
            director = l2.director
            now = time.time()
            story = {'deferred': {'held': now + 60}, 'deferred_since': {'held': now}}
            held = Turn(kind='beat', umo=private, payload={'cand': SimpleNamespace(
                storylet=SimpleNamespace(sid='held', effects={}))})
            idle = Turn(kind='idle', umo=private)
            plan = DirectorPlan()
            assert director._apply_budget([held, idle], story, now, plan) == [idle]
            assert plan.deferred == ['held']
            assert held in director._apply_budget([held], story, now + 61, DirectorPlan())

            # 错误工具参数只拒绝这次工具操作，模型仍能完成当前回复。
            tool = host.tools.tools[0].name
            cases = [(tool, json.dumps({'session': private, 'messages': [{'type': 'plain', 'text': '不走工具重复发送'}]})),
                     ('unknown_tool', '{}'), (tool, '{bad json'), (tool, json.dumps({'session': private}))]
            model.tools = [{'id': str(i), 'type': 'function', 'function': {'name': name, 'arguments': args}}
                           for i, (name, args) in enumerate(cases)]
            before = len(sent)
            assert await system.receive(Event(host, private, '1', '甲', '工具误用也能继续聊')) == 'sent'
            results = [m for m in model.calls[-1]['contexts'] if m['role'] == 'tool']
            assert len(results) == 4 and all('未执行' in m['content'] for m in results), results
            assert len(sent) == before + 1, 'invalid tools sent extra messages'
            event = Event(host, private, '1', '甲', 'fixture')
            event.set_extra('_spectrum_request', {'tools': host.tools})
            failures, before = freeze._INSTANCE._fail_streak, len(sent)
            await system.spectrum._run_tool_calls(event, private, [
                {'name': tool, 'arguments': {}},
                {'name': tool, 'arguments': {'session': 'qq-fixture:FriendMessage:404', 'messages': [{'type': 'plain', 'text': '未认识'}]}}])
            assert freeze._INSTANCE._fail_streak == failures and len(sent) == before

            # 显式失败回执不进入真实聊天历史；媒体队列按协议内容恢复。
            async def refused(umo, chain): return False
            host.transport = refused
            before_history = host.history(private)
            try:
                await host.send_message(private, MessageChain([Plain('这条没有送达')]))
                raise AssertionError('false receipt accepted as success')
            except RuntimeError:
                pass
            assert host.history(private) == before_history
            host.transport = sender
            enable(system.guard, 'flush_throttle_seconds', 0)
            oid = system.guard.store.push_outbox(private, '', 'fixture', payload={
                'segments': [{'type': 'image', 'data': {'file': 'https://fixture.invalid/image.png'}}]})
            await system.guard.flusher._flush(private)
            assert isinstance(sent[-1][1].chain[0], NativeSegment)
            assert system.guard.store._db.execute('SELECT status FROM outbox WHERE id=?', (oid,)).fetchone()[0] == 'sent'
            assert await system.guard.flusher._send_raw(private, '') is None
            oid = system.guard.store.push_outbox(private, '', 'fixture')
            await system.guard.flusher._flush(private)
            assert system.guard.store._db.execute('SELECT status FROM outbox WHERE id=?', (oid,)).fetchone()[0] == 'cancelled'
            host.output_rules.save({'enabled': True, 'rules': [{
                'id': 'fixture-drop', 'name': 'fixture', 'pattern': '^裁掉这条$', 'replacement': '', 'enabled': True}]})
            oid = system.guard.store.push_outbox(private, '裁掉这条', 'fixture')
            before = len(sent)
            await system.guard.flusher._flush(private)
            assert len(sent) == before
            assert system.guard.store._db.execute('SELECT status FROM outbox WHERE id=?', (oid,)).fetchone()[0] == 'cancelled'
            host.output_rules.save({'enabled': False, 'rules': []})

            # 模拟一次 100 秒冻结，统一验证承诺等待与补发有效期。
            now = time.time()
            iid = store.add_intent(private, 'followup', '稍后联系', now - 60, meta={'expires_at': now - 30})
            store._db.execute('UPDATE intents SET created_ts=? WHERE id=?', (now - 120, iid))
            store._db.commit()
            oid = system.guard.store.push_outbox(private, '还未过期', 'fixture')
            system.guard.store._db.execute('UPDATE outbox SET created_ts=? WHERE id=?', (now - 120, oid))
            system.guard.store._db.commit()
            control = freeze._INSTANCE
            original = control._wall_clock
            control._wall_clock = lambda: now - 110
            freeze.manual('freeze')
            control._wall_clock = lambda: now - 10
            freeze.manual('thaw')
            control._wall_clock = original
            row = store.list_intents(private)[0]
            assert not intent_ready(store, l2.life, row, now)
            assert store.list_intents(private)[0]['status'] == 'waiting', 'frozen promise expired'
            assert intent_ready(store, l2.life, row, now + 41)
            assert any(r['id'] == oid for r in system.guard.store.due_outbox(private, 60)), 'frozen outbox expired'
            assert not intent_ready(store, l2.life, row, now + 81)
            assert store.list_intents(private)[0]['status'] == 'cancelled'
        finally:
            await system.close()
    print('PASS: group trigger/context/identity/cooldown/scope, sleep disable and freeze, vector switch, tool errors, delivery receipts, media/cancelled recovery, paused promise/outbox clocks')


if __name__ == '__main__': asyncio.run(run())
