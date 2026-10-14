"""编排人格读取与低频学习；学习请求在回合锁之外等待模型。"""
import asyncio
import hashlib
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from .settings import Settings, settings, base_lines, first_person
from .store import PersonalityStore
from .learning import DRAFT_PROMPT, LEARNING_PROMPT, model_result, source_record, source_rows, candidates, formed, valid_sources
from .context import select, render
from ..ports import logger
from ..spectrum_module.spectrum import freeze
from ..spectrum_module.spectrum.person import make_person_id


def fingerprint(text): return hashlib.sha256(text.encode()).hexdigest()


class Personality:
    def __init__(self, system):
        self.system = system
        self.host = system.host
        self.sources = system.spectrum.store
        directory = self.host.data_dir / 'personality'
        directory.mkdir(exist_ok=True)
        self.config = Settings(directory)
        self.store = PersonalityStore(directory)
        if not self.config.role_id:
            role_id = self.store.new_role(self.persona_hash(), self.latest_entry(), freeze.business_now())
            self.config.save(role_id=role_id)
        self.task = None
        self.busy = False
        self.last_result = {}
        self.epoch = 0

    def persona_hash(self): return fingerprint(self.host.persona_prompt())
    def role(self): return self.store.role(self.config.role_id)

    def latest_entry(self):
        with self.sources._lock:
            return self.sources._db.execute('SELECT COALESCE(MAX(id),0) FROM entries').fetchone()[0]

    def start(self): self.task = asyncio.create_task(self._run())

    async def close(self):
        if self.task:
            self.task.cancel()
            try: await self.task
            except asyncio.CancelledError: pass
        self.store.close()

    def compatible(self): return self.role()['persona_hash'] == self.persona_hash()

    def context(self, umo, person_id, query):
        if not self.config.values['enabled'] or not self.compatible() or freeze.is_frozen(): return ''
        options = self.config.values
        notes = select(self.store, self.sources, self.config.role_id, umo, person_id, query, options['max_context'])
        return render(self.role(), notes, self.sources, query, options['context_chars'])

    def export(self):
        role = self.role()
        return {'settings': dict(self.config.values), 'base': role['base'], 'locked': bool(role['locked'])}

    @staticmethod
    def validate_export(data):
        if not isinstance(data, dict) or set(data) != {'settings', 'base', 'locked'} or not isinstance(data['locked'], bool):
            raise ValueError('人格底稿文件需包含 settings、base 和 locked')
        return {'settings': settings(data['settings']), 'base': base_lines(data['base']), 'locked': data['locked']}

    def save(self, data, confirm=False, source='手工底稿'):
        values = self.validate_export(data)
        provider_id = values['settings']['provider_id']
        if provider_id not in ('', 'role', 'default') and provider_id not in self.host.config['providers']:
            raise ValueError('请从已保存的模型配置中选择人格整理模型')
        if not self.compatible() and not confirm: raise ValueError('人设已变化，请先确认沿用档案或建立新角色档案')
        learning_started = values['settings']['enabled'] and values['settings']['learning_enabled'] and not (
            self.config.values['enabled'] and self.config.values['learning_enabled'])
        changed_base = values['base'] != self.role()['base'] or values['locked'] != bool(self.role()['locked']) or confirm
        if changed_base:
            self.store.save_core(self.config.role_id, values['base'], values['locked'], self.persona_hash(), freeze.business_now(), source)
        if learning_started or confirm: self.store.reset_scan(self.config.role_id, self.latest_entry())
        self.config.save(values['settings'])
        self.epoch += 1

    def new_role(self):
        self.config.save({**self.config.values, 'learning_enabled': False},
            self.store.new_role(self.persona_hash(), self.latest_entry(), freeze.business_now()))
        self.epoch += 1
        return self.config.role_id

    def switch_role(self, role_id):
        if self.store.role(role_id) is None: raise ValueError('没有这个角色档案')
        self.config.save({**self.config.values, 'learning_enabled': False}, role_id)
        self.epoch += 1

    def clear_learning(self):
        self.store.clear_learning(self.config.role_id, self.latest_entry())
        self.epoch += 1

    def _budget(self, purpose):
        if freeze.is_frozen(): raise ValueError('系统冻结期间不请求人格整理')
        if self.host.provider_for(self.config.values['provider_id']) is None: raise ValueError('请先配置人格整理使用的模型')
        now = freeze.business_now()
        day = datetime.fromtimestamp(now, ZoneInfo('Asia/Shanghai')).date().isoformat()
        if self.store.requests_used(self.config.role_id, day) >= self.config.values['daily_budget']:
            raise ValueError('今日人格整理预算已用完；底稿整理也计入预算')
        self.store.reserve(self.config.role_id, day, purpose, now)
        return now

    async def draft(self):
        if self.busy: raise ValueError('已有一项人格整理正在进行')
        async with self.system.lock:
            if self.busy: raise ValueError('已有一项人格整理正在进行')
            self._budget('draft')
            epoch = freeze.checkpoint(); role_id = self.config.role_id; hashed = self.persona_hash(); generation = self.epoch
            text = self.host.persona_prompt()
            self.busy = True
        try:
            response = await freeze.call_model(self.host.provider_for(self.config.values['provider_id']), system_prompt=DRAFT_PROMPT,
                prompt=json.dumps({'persona': text}, ensure_ascii=False), role_mode='contract', max_tokens=8192)
            lines = base_lines(model_result(response)['base'])
            async with self.system.lock:
                if not freeze.may_commit(epoch) or role_id != self.config.role_id or hashed != self.persona_hash() or generation != self.epoch:
                    raise ValueError('整理期间角色或冻结状态已变化，本次草稿未写入')
                self.store.save_draft(role_id, lines, hashed)
            return lines
        finally: self.busy = False

    def apply_draft(self):
        role = self.role()
        if role['locked']: raise ValueError('底稿已锁定，请先保存为解锁状态再应用草稿')
        if role['draft_hash'] != self.persona_hash(): raise ValueError('草稿来源的人设已变化，请重新整理')
        self.save({'settings': self.config.values, 'base': role['draft'], 'locked': True}, confirm=True, source='从当前人设显式整理并确认')

    def collect(self):
        role = self.role()
        with self.sources._lock:
            rows = self.sources._db.execute('SELECT * FROM entries WHERE id>? ORDER BY id LIMIT 200', (role['scanned'],)).fetchall()
        picked = []
        for row in rows:
            if row['kind'] != 'received': continue
            item = source_record(row)
            if not item['person_id'] or len(item['text'].strip()) < 8: continue
            picked.append(item)
        if rows: self.store.enqueue(role['id'], picked, rows[-1]['id'])

    async def learn(self, force=False):
        if self.busy: return {'status': 'busy'}
        async with self.system.lock:
            if self.busy: return {'status': 'busy'}
            values = dict(self.config.values)
            if not values['enabled'] or not values['learning_enabled'] or not self.compatible() or freeze.is_frozen():
                return {'status': 'disabled_or_paused'}
            if self.sources.is_asleep(): return {'status': 'sleeping'}
            if self.host.provider_for(values['provider_id']) is None: return {'status': 'model_unconfigured'}
            now = freeze.business_now()
            day = datetime.fromtimestamp(now, ZoneInfo('Asia/Shanghai')).date().isoformat()
            if self.store.requests_used(self.config.role_id, day) >= values['daily_budget']:
                return {'status': 'budget'}
            if not force and now - self.store.last_request(self.config.role_id, 'learning') < values['interval_hours'] * 3600:
                return {'status': 'interval'}
            self.collect()
            queue = self.store.queued_batch(self.config.role_id, 1 if force else values['min_messages'], values['batch_size'])
            if not queue: return {'status': 'waiting_for_evidence'}
            ids = [row['entry_id'] for row in queue]
            sources = [source_record(row) for row in source_rows(self.sources, ids) if row['kind'] == 'received']
            if not sources:
                self.store.consume(self.config.role_id, ids)
                return {'status': 'sources_removed'}
            origin = queue[0]['origin']; pid = queue[0]['person_id']
            sources = [row for row in sources if row['umo'] == origin and row['person_id'] == pid]
            if not sources:
                self.store.consume(self.config.role_id, ids)
                return {'status': 'sources_changed'}
            existing = self.store.list_insights(self.config.role_id, origin=origin, limit=200)
            now = self._budget('learning'); epoch = freeze.checkpoint()
            role_id = self.config.role_id; role_revision = self.role()['revision']; hashed = self.persona_hash(); generation = self.epoch
            received = [{'id': row['id'], 'time': datetime.fromtimestamp(row['ts'], ZoneInfo('Asia/Shanghai')).isoformat(),
                         'person_id': row['person_id'], 'name': row['sender_name'], 'source_session': row['umo'],
                         'text': row['text'][:1000], 'truncated': len(row['text']) > 1000} for row in sources]
            related = [{'id': row['id'], 'person_id': row['person_id'], 'topic': row['topic'], 'kind': row['kind'],
                        'content': row['content'], 'topics': row['topics'], 'status': row['status'], 'locked': bool(row['locked']),
                        'source_valid': valid_sources(self.sources, row)} for row in existing]
            person = self.sources.get_person(pid)
            relation = {key: person[key] for key in ('affinity', 'trust')} if person else {}
            with self.sources._lock:
                marks = ','.join('?' for _ in sources)
                events = [dict(row) for row in self.sources._db.execute(
                    'SELECT id,text,source_entry_id,basis,state FROM shared_life_events WHERE source_entry_id IN (' + marks + ') LIMIT 6',
                    [row['id'] for row in sources])]
            payload = {'base': self.role()['base'], 'new_received_evidence': received, 'existing_recognitions': related,
                       'relationship_background_not_new_evidence': relation, 'related_life_events_not_new_evidence': events}
            self.busy = True
        try:
            response = await freeze.call_model(self.host.provider_for(values['provider_id']), system_prompt=LEARNING_PROMPT,
                prompt=json.dumps(payload, ensure_ascii=False), role_mode='contract', max_tokens=8192)
            notes, rejected = candidates(model_result(response), sources, existing, role_id, now)
            async with self.system.lock:
                if not freeze.may_commit(epoch) or role_id != self.config.role_id or role_revision != self.role()['revision'] or hashed != self.persona_hash() or values != self.config.values or generation != self.epoch:
                    return {'status': 'context_changed', 'written': 0}
                current_sources = {row['id']: dict(row) for row in source_rows(self.sources, [row['id'] for row in sources])}
                if any(row['id'] not in current_sources or any(current_sources[row['id']][key] != row[key] for key in ('text', 'meta', 'umo', 'ts', 'kind', 'audience')) for row in sources):
                    return {'status': 'sources_changed', 'written': 0}
                # 请求期间的管理员修订不能被先前的模型快照覆盖。
                written = 0
                for value, cause in notes:
                    if value.get('id'):
                        current = self.store.insight(value['id'])
                        old = next(row for row in existing if row['id'] == value['id'])
                        if current['revision'] != old['revision'] or current['locked']: continue
                        if value.get('_check_formed') and not valid_sources(self.sources, old):
                            rejected.append('旧认识的原话已变化，本次不能直接巩固'); continue
                    if value.pop('_check_formed', False):
                        if formed(source_rows(self.sources, value['supports'])): value['status'] = 'formed'
                    self.store.write_insight(value, cause, now)
                    written += 1
                self.store.consume(role_id, ids)
                self.last_result = {'status': 'completed', 'accepted': written, 'rejected': rejected, 'evidence': len(sources)}
                logger.info('[人格成长] 整理 %d 条新原话，接受 %d 条候选，越界拒绝 %d 条', len(sources), written, len(rejected))
                return self.last_result
        finally: self.busy = False

    def edit(self, insight_id, data):
        old = self.store.insight(insight_id)
        if old is None or old['role_id'] != self.config.role_id: raise ValueError('当前角色没有这条认识')
        if not isinstance(data, dict): raise ValueError('认识操作需为对象')
        value = dict(old); value['revision'] += 1; value['ts'] = freeze.business_now()
        action = data.get('action')
        if action == 'revoke': value.update(status='revoked', locked=1)
        elif action == 'rollback':
            history = self.store.history(insight_id)
            snapshot = next((r for r in history if r['revision'] == data.get('revision')), None)
            if snapshot is None: raise ValueError('没有这个修订版本')
            restored = json.loads(snapshot['snapshot'])
            value = {**restored, 'revision': old['revision'] + 1, 'locked': 1, 'ts': freeze.business_now()}
        elif action == 'edit':
            value['content'] = first_person(data.get('content'))
            if value['content'] != old['content'] and value['status'] in ('tentative', 'formed'): value['status'] = 'tentative'
            if not isinstance(data.get('locked'), bool): raise ValueError('锁定状态使用布尔值')
            value['locked'] = int(data['locked'])
            if 'topics' in data:
                topics = data['topics']
                if not isinstance(topics, list) or not 1 <= len(topics) <= 6 or any(not isinstance(t, str) or not 2 <= len(t.strip()) <= 20 for t in topics):
                    raise ValueError('相关关键词需为1–6个，每个2–20字')
                value['topics'] = list(dict.fromkeys(t.strip() for t in topics))
        else: raise ValueError('不支持这项认识操作')
        saved = self.store.write_insight(value, '管理员' + action, freeze.business_now())
        self.epoch += 1
        return saved

    def view(self):
        role = self.role()
        now = freeze.business_now()
        day = datetime.fromtimestamp(now, ZoneInfo('Asia/Shanghai')).date().isoformat()
        return {'settings': dict(self.config.values), 'role': role, 'persona_changed': not self.compatible(),
                'used_today': self.store.requests_used(role['id'], day), 'busy': self.busy,
                'last_result': self.last_result, 'pending_evidence': self.store.pending_count(role['id']),
                'roles': self.store.roles(), 'insights': [{**row, 'source_valid': valid_sources(self.sources, row), 'name': self.sources.get_person_name(row['person_id']) if row['person_id'] else ''}
                                                          for row in self.store.list_insights(role['id'])]}

    async def _run(self):
        while True:
            await asyncio.sleep(30)
            if not self.host.config['enabled'] or not self.system.spectrum._cfg('enabled', False): continue
            try: await self.learn()
            except asyncio.CancelledError: raise
            except Exception:
                self.last_result = {'status': 'failed', 'message': '本次整理未完成，正常聊天继续；按原频率等待下次整理'}
                logger.exception('[人格成长] 后台整理未完成')
