"""空素材库的自然联系、沉默冷却与普通调度首轮验收；仅使用替身模型和发送器。"""
import asyncio
import json
import logging
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from role_system.host import Host
from role_system.service import RoleSystem
from role_system.provider import ModelResponse
from role_system.ports import Event
from role_system.spectrum_module.spectrum import freeze
from role_system.spectrum_module.spectrum.director import Turn
from role_system.spectrum_module.spectrum.l2 import L2
from test_integration import enable


class Model:
    ready = True

    def __init__(self):
        self.calls = []
        self.silent = False

    def role_mode(self, override=''): return 'contract'

    async def text_chat(self, **kwargs):
        self.calls.append(kwargs)
        decision = {'seen': True, 'audience': 'nobody' if self.silent else 'here',
            'reply': {'mode': 'none' if self.silent else 'immediate',
                      'content': '' if self.silent else '刚才想起你说的那件事了'}}
        return ModelResponse('<life>我接着做手头的事。</life><reply>' + json.dumps(decision, ensure_ascii=False) + '</reply>')

    async def close(self): pass


async def run():
    logging.basicConfig(level=logging.WARNING)
    with tempfile.TemporaryDirectory() as directory:
        host = Host(directory)
        host.config['enabled'] = True
        host.config['persona'] = '我有自己的生活，可以联系熟人，也可以安静。'
        system = RoleSystem(host)
        host.provider = model = Model()
        for key, value in [('enabled', True), ('director_enabled', True),
                           ('l2_advance_enabled', True), ('l2_sleep_enabled', True)]:
            enable(system.spectrum, key, value)
        sent = []

        async def sender(umo, chain):
            sent.append((umo, chain.get_plain_text()))
            return {'message_id': len(sent)}

        host.transport = sender
        await system.start()
        try:
            umo = 'qq-fixture:FriendMessage:1'
            await system.receive(Event(host, umo, '1', '朋友', '今天有点忙'))
            l2 = system.spectrum.l2
            director = l2.director
            store = system.spectrum.store
            now = time.time()
            store._db.execute('UPDATE entries SET ts=?', (now - 3600,))
            store._db.commit()
            store.set_willingness(umo, 0.7)
            assert store.storylets_all() == []
            before_calls, before_sent = len(model.calls), len(sent)
            plan = await director.advance(now)
            idle = [turn for turn in plan.turns if turn.kind == 'idle']
            assert len(idle) == 1 and idle[0].umo == umo
            await director.execute(idle[0], set())
            assert len(model.calls) == before_calls + 1 and len(sent) == before_sent + 1
            assert float(store.get_state(umo, 'last_advance_ts')) >= now
            assert store.storylets_all() == []
            assert not any(t.kind == 'idle' for t in (await director.advance(now + 10)).turns)

            # 重建调度器后，同一个持久化冷却仍有效。
            restarted = L2(store, host, system.spectrum._cfg)
            ctx = director._build_ctx(store.get_story_state(), now + 10, [umo])
            assert restarted.director._idle_turns(ctx, now + 10, []) == []
            store.set_state(umo, 'last_advance_ts', '0')
            assert director._idle_turns(ctx, now, [Turn(kind='intent', umo=umo)]) == []
            store.set_willingness(umo, 0.1)
            assert not any(t.kind == 'idle' for t in (await director.advance(now)).turns)
            store.set_willingness(umo, 0.7)
            store.set_sleep_asleep(now + 3600)
            assert not any(t.kind == 'idle' for t in (await director.advance(now)).turns)
            store.set_sleep_awake()
            enable(system.spectrum, 'l2_advance_enabled', False)
            assert not any(t.kind == 'idle' for t in (await director.advance(now)).turns)
            enable(system.spectrum, 'l2_advance_enabled', True)
            freeze.manual('freeze')
            before_calls = len(model.calls)
            await l2.tick()
            assert len(model.calls) == before_calls
            freeze.manual('thaw')

            # 模型选择沉默仍记录冷却，不会十秒后再问同一问题。
            model.silent = True
            store._db.execute('UPDATE entries SET ts=?', (now - 3600,))
            store._db.commit()
            plan = await director.advance(now)
            idle = next(t for t in plan.turns if t.kind == 'idle')
            before_sent = len(sent)
            await director.execute(idle, set())
            assert len(sent) == before_sent and float(store.get_state(umo, 'last_advance_ts')) > 0
            assert not any(t.kind == 'idle' for t in (await director.advance(now + 10)).turns)

            # 普通调度原先会用当前时间作为不存在的冷却起点，导致首轮永久不触发。
            store.set_state(umo, 'last_advance_ts', '0')
            store._db.execute('UPDATE entries SET ts=?', (now - 3600,))
            store._db.commit()
            before_calls = len(model.calls)
            await l2._maybe_advance(now, set())
            assert len(model.calls) == before_calls + 1
            assert float(store.get_state(umo, 'last_advance_ts')) > 0
        finally:
            await system.close()
    print('PASS: empty library, actual background generation and delivery, silence, persisted cooldown, gates and legacy first turn')


if __name__ == '__main__': asyncio.run(run())
