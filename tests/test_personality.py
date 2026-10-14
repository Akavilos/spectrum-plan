"""人格首版的真实编排与来源验收；使用可控模型，不访问 QQ 或付费接口。"""
import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI, Request, HTTPException
from role_system.host import Host
from role_system.service import RoleSystem
from role_system.web import install_panel
from role_system.ports import Event, ModelRequest
from role_system.provider import ModelResponse
from role_system.personality.learning import DRAFT_PROMPT, LEARNING_PROMPT, candidates, source_record, source_rows, model_result
from role_system.personality.context import topic_score
from role_system.spectrum_module.spectrum import freeze
from test_http import ASGIClient


class Model:
    ready = True
    def __init__(self):
        self.calls = []; self.action = 'new'; self.block = None; self.started = asyncio.Event()
    def role_mode(self, override=''): return 'contract'
    async def close(self): pass
    async def text_chat(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get('system_prompt') == DRAFT_PROMPT:
            return ModelResponse(json.dumps({'base': ['我喜欢读书，也会认真表达不同看法。', '我关心别人时更愿意谈具体事情。']}, ensure_ascii=False))
        if kwargs.get('system_prompt') != LEARNING_PROMPT: return ModelResponse('吃过了，你呢？')
        data = json.loads(kwargs['prompt']); evidence = data['new_received_evidence']; pid = evidence[0]['person_id']
        old = next((item for item in data['existing_recognitions'] if item['person_id'] == pid), None)
        if self.block:
            self.started.set(); await self.block.wait()
        content = '我和这位朋友聊小说时感到自在，但不据此认定对方每次都有空。'
        if self.action == 'revise': content = '我和这位朋友聊小说时会留意对方是否有空，不把暂时拒绝当成疏远。'
        if old and self.action in ('support', 'question'): content = old['content']
        row = {'target_id': old['id'] if old else None, 'topic': '聊小说的相处感受', 'kind': 'person',
               'person_id': pid, 'content': content, 'topics': ['小说', '读书'],
               'action': self.action, 'evidence_ids': [item['id'] for item in evidence]}
        return ModelResponse(json.dumps({'candidates': [row]}, ensure_ascii=False))


async def run():
    assert topic_score('接着聊那本小说的结尾吧', ['小说讨论', '观点分歧'])
    assert not topic_score('我们今天晚饭吃了吗', ['我们讨论小说', '今天读书', '真实观点', '今天', '我们'])
    for response in (ModelResponse('', finish_reason='length', reasoning_content='仅思考'),
                     ModelResponse('', reasoning_content='仅思考'), ModelResponse('{"base":[')):
        try: model_result(response); raise AssertionError('incomplete model output accepted')
        except ValueError as error: assert '未保存' in str(error)
    assert model_result(ModelResponse('```json\n{"base":[]}\n```')) == {'base': []}
    with tempfile.TemporaryDirectory() as directory:
        app = FastAPI()
        def authorize(request: Request):
            if request.cookies.get('role_panel') != 'fixture-key': raise HTTPException(401)
        host = Host(directory, app, authorize); system = RoleSystem(host); p = system.personality
        install_panel(app, host, system, authorize, 'fixture-key')
        model = Model(); host.provider = model
        freeze.install(lambda key, default=None: default, store=system.spectrum.store)
        a = 'qq-90001:FriendMessage:10001'; b = 'qq-90001:FriendMessage:10002'
        group = 'qq-90001:GroupMessage:20001'; other_account = 'qq-90002:FriendMessage:10001'
        pid = 'qq-90001:10001'
        def evidence(umo=a, sender='10001', text='我们今天一起讨论这本小说，里面的角色很有意思。', age=0, kind='received'):
            return system.spectrum.store.add_entry(umo, kind, text, ts=time.time() - age,
                meta={'sender_id': sender, 'sender': '同名朋友'})
        def save(**options):
            value = p.export(); value['settings'].update(options); p.save(value, confirm=True)
        first = evidence()
        assert not p.config.values['enabled'] and not p.store.list_insights(p.config.role_id)
        assert p.context(a, pid, '小说') == ''
        assert (await p.learn(force=True))['status'] == 'disabled_or_paused' and not model.calls
        req = ModelRequest('吃了吗', host.persona_prompt(), [], host.tools)
        await system._generate(Event(host, a, '10001', '同名朋友', '吃了吗'), req)
        assert len(model.calls) == 1 and not req.extra_user_content_parts
        host.config['persona'] = '我喜欢读书，我温柔，也有自己的判断。'
        save(enabled=True, learning_enabled=True, daily_budget=24)
        assert first <= p.role()['scanned']
        for umo, uid in ((a, '10001'), (b, '10002')):
            system.spectrum.persons.observe(umo, uid, '同名朋友', 'qq-90001')
        draft = await p.draft(); assert p.role()['draft'] == draft and p.role()['base'] == []
        try: p.apply_draft(); raise AssertionError('locked draft applied')
        except ValueError: pass
        data = p.export(); data['locked'] = False; p.save(data); p.apply_draft()
        assert p.role()['locked'] and p.role()['base'] == draft
        core = list(p.role()['base'])
        one = evidence(age=86400); two = evidence()
        evidence(kind='script'); evidence(kind='delivered'); evidence(kind='think')
        result = await p.learn(force=True); assert result['accepted'] == 1, result
        note = p.store.list_insights(p.config.role_id)[0]
        assert note['status'] == 'tentative' and note['supports'] == [one, two]
        assert p.role()['base'] == core
        context = p.context(a, pid, '聊小说'); assert note['content'] in context
        assert note['content'] not in p.context(a, pid, '吃饭了吗')
        assert note['content'] not in p.context(b, 'qq-90001:10002', '小说')
        assert note['content'] not in p.context(group, pid, '同名朋友的小说')
        assert note['content'] not in p.context(other_account, 'qq-90002:10001', '小说')
        current_sources = [source_record(row) for row in p.sources.entries_by_ids([one, two])]
        candidate = {'target_id': note['id'], 'topic': note['topic'], 'kind': 'person', 'person_id': pid,
                     'content': note['content'], 'topics': note['topics'], 'action': 'support', 'evidence_ids': [one, two]}
        accepted, rejected = candidates({'candidates': [candidate]}, current_sources, [note], p.config.role_id, time.time())
        assert not accepted and not rejected
        bad = {**candidate, 'person_id': 'qq-90001:10002'}
        assert candidates({'candidates': [bad]}, current_sources, [note], p.config.role_id, time.time())[1]
        bad = {**candidate, 'evidence_ids': [999999]}
        assert candidates({'candidates': [bad]}, current_sources, [note], p.config.role_id, time.time())[1]
        count = len(model.calls)
        assert (await p.learn(force=True))['status'] == 'waiting_for_evidence' and len(model.calls) == count
        model.action = 'support'; evidence(age=172800)
        assert (await p.learn(force=True))['accepted'] == 1
        note = p.store.insight(note['id']); assert note['status'] == 'formed' and len(note['supports']) == 3
        formed_version = note['revision']
        normal = ModelRequest('聊小说', host.persona_prompt(), [], host.tools)
        await system._generate(Event(host, a, '10001', '同名朋友', '聊小说'), normal)
        foreground = [part.text for part in normal.extra_user_content_parts]
        await system.generate_background(a, prompt='聊小说', system_prompt=host.persona_prompt())
        background = [part.text for part in model.calls[-1]['extra_user_content_parts']]
        assert foreground == background and note['content'] in ''.join(background)
        original_text = p.sources.entries_by_ids([one])[0]['text']
        p.sources.update_entry_text(one, '这条原话已更正，现在讨论的是另一件事。')
        assert note['content'] not in p.context(a, pid, '小说')
        p.sources.update_entry_text(one, original_text)
        assert note['content'] in p.context(a, pid, '小说')
        model.action = 'question'; evidence(text='今天我不太想讨论这本小说，先暂时聊点别的。')
        await p.learn(force=True); assert p.store.insight(note['id'])['status'] == 'revising'
        assert note['content'] not in p.context(a, pid, '小说')
        model.action = 'revise'; evidence(text='我只是这几天比较忙，等空下来还想和你聊这本小说。')
        await p.learn(force=True); revised = p.store.insight(note['id'])
        assert revised['status'] == 'tentative' and len(revised['supports']) == 1
        p.edit(note['id'], {'action': 'rollback', 'revision': formed_version})
        restored = p.store.insight(note['id']); assert restored['locked'] and restored['status'] == 'formed'
        p.edit(note['id'], {'action': 'revoke'}); assert p.store.insight(note['id'])['status'] == 'revoked'
        assert note['content'] not in p.context(a, pid, '小说')
        public_id = evidence(umo=group)
        public_sources = [source_record(row) for row in source_rows(p.sources, [public_id])]
        public_candidate = {**candidate, 'target_id': None, 'action': 'new', 'content': '我与这位朋友聊小说时更愿意讨论不同的看法。', 'evidence_ids': [public_id]}
        public_values, rejected = candidates({'candidates': [public_candidate]}, public_sources, [], p.config.role_id, time.time())
        assert not rejected and public_values[0][0]['visibility'] == ['public']
        public_note = p.store.write_insight(*public_values[0], time.time())
        assert public_note['content'] in p.context(b, 'qq-90001:10002', '我想聊qq-90001:10001的小说')
        assert public_note['content'] not in p.context(other_account, 'qq-90002:10001', 'qq-90001:10001的小说')
        assert public_note['content'] not in p.context(b, 'qq-90001:10002', '同名朋友的小说')
        evidence(); before = p.view(); count = len(model.calls)
        freeze.manual('freeze')
        assert (await p.learn(force=True))['status'] == 'disabled_or_paused'
        assert p.store.pending_count(p.config.role_id) == before['pending_evidence'] and len(model.calls) == count
        freeze.manual('thaw')
        host.config['persona'] += '我还喜欢甜点。'
        assert not p.compatible() and not p.context(a, pid, '小说')
        assert (await p.learn(force=True))['status'] == 'disabled_or_paused'
        save()
        old_role = p.config.role_id; new_role = p.new_role()
        assert new_role != old_role and not p.role()['base'] and not p.store.list_insights(new_role)
        assert p.store.list_insights(old_role)
        save(enabled=True, learning_enabled=True, daily_budget=24)
        evidence(); model.action = 'new'; model.block = asyncio.Event(); model.started.clear()
        running = asyncio.create_task(p.learn(force=True)); await asyncio.wait_for(model.started.wait(), 2)
        # 等待模型时管理操作仍能获得统一锁；清空后旧请求不能复活认识。
        core_revision = p.role()['revision']
        async with system.lock:
            p.clear_learning()
            assert p.role()['revision'] == core_revision
        model.block.set(); result = await running
        assert result['status'] == 'context_changed' and not p.store.list_insights(new_role)
        model.block = None
        data = p.export(); data['base'] = ['我在意真实、具体的相处。']; p.save(data)
        client = ASGIClient(app)
        assert (await client.get('/api/personality')).status_code == 401
        client.cookie = 'role_panel=fixture-key'
        exported = (await client.get('/api/personality/export')).json()
        assert set(exported['sections']['personality']) == {'settings', 'base', 'locked'}
        assert 'evidence_ids' not in json.dumps(exported)
        assert (await client.post('/api/data/import', {'data': exported})).status_code == 200
        p.switch_role(old_role)
        detail = (await client.get('/api/personality/insights/' + str(note['id']))).json()
        assert detail['evidence'] and len(detail['versions']) >= 5
        total = system.spectrum.store.count_rows('entries'); persona = host.config['persona']
        assert (await client.post('/api/personality/clear-learning', {})).status_code == 200
        assert not p.store.list_insights(old_role) and p.role()['base'] == core
        assert system.spectrum.store.count_rows('entries') == total and host.config['persona'] == persona
        await system.close(); system.spectrum.store.close(); system.prism.store.close(); system.guard.store.close()
    print('PASS personality: disabled compatibility, evidence/identity/privacy, foreground/background, consolidation, revision/rollback, freeze, role changes, nonblocking learning, API/export/clear')


if __name__ == '__main__': asyncio.run(run())
