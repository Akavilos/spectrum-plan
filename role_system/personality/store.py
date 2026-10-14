"""人格独立存储：来源只引用光谱原文，修订快照支持查看与撤销。"""
import json
import sqlite3
from uuid import uuid4

SCHEMA = '''
CREATE TABLE IF NOT EXISTS roles(
 id TEXT PRIMARY KEY, persona_hash TEXT NOT NULL, base TEXT NOT NULL DEFAULT '[]',
 locked INTEGER NOT NULL DEFAULT 1, source TEXT NOT NULL DEFAULT '手工底稿',
 revision INTEGER NOT NULL DEFAULT 1, draft TEXT NOT NULL DEFAULT '[]', draft_hash TEXT NOT NULL DEFAULT '',
 created REAL NOT NULL, watermark INTEGER NOT NULL DEFAULT 0, scanned INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS core_versions(
 role_id TEXT NOT NULL, revision INTEGER NOT NULL, snapshot TEXT NOT NULL, cause TEXT NOT NULL, ts REAL NOT NULL,
 PRIMARY KEY(role_id,revision));
CREATE TABLE IF NOT EXISTS insights(
 id INTEGER PRIMARY KEY, role_id TEXT NOT NULL, account TEXT NOT NULL, origin TEXT NOT NULL,
 person_id TEXT NOT NULL, topic TEXT NOT NULL, kind TEXT NOT NULL, content TEXT NOT NULL,
 topics TEXT NOT NULL, visibility TEXT NOT NULL, status TEXT NOT NULL, locked INTEGER NOT NULL DEFAULT 0,
 supports TEXT NOT NULL, doubts TEXT NOT NULL, proofs TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, ts REAL NOT NULL,
 UNIQUE(role_id,account,origin,person_id,topic));
CREATE TABLE IF NOT EXISTS insight_versions(
 insight_id INTEGER NOT NULL, revision INTEGER NOT NULL, snapshot TEXT NOT NULL, cause TEXT NOT NULL,
 ts REAL NOT NULL, PRIMARY KEY(insight_id,revision));
CREATE TABLE IF NOT EXISTS requests(
 role_id TEXT NOT NULL, day TEXT NOT NULL, purpose TEXT NOT NULL, ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS learning_queue(
 role_id TEXT NOT NULL, entry_id INTEGER NOT NULL, origin TEXT NOT NULL, person_id TEXT NOT NULL,
 PRIMARY KEY(role_id,entry_id));
'''
JSON_COLUMNS = {'base', 'draft', 'topics', 'visibility', 'supports', 'doubts', 'proofs'}


def record(row):
    if row is None: return None
    result = dict(row)
    for key in JSON_COLUMNS & result.keys(): result[key] = json.loads(result[key])
    return result


class PersonalityStore:
    def __init__(self, directory):
        self.db = sqlite3.connect(directory / 'personality.db')
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self): self.db.close()

    def role(self, role_id):
        return record(self.db.execute('SELECT * FROM roles WHERE id=?', (role_id,)).fetchone())

    def roles(self):
        return [dict(row) for row in self.db.execute('SELECT id,created,persona_hash FROM roles ORDER BY created DESC')]

    def core_history(self, role_id):
        return [dict(row) for row in self.db.execute('SELECT * FROM core_versions WHERE role_id=? ORDER BY revision DESC', (role_id,))]

    def pending_count(self, role_id):
        return self.db.execute('SELECT COUNT(*) FROM learning_queue WHERE role_id=?', (role_id,)).fetchone()[0]

    def new_role(self, persona_hash, watermark, now):
        role_id = uuid4().hex
        with self.db:
            self.db.execute('INSERT INTO roles(id,persona_hash,watermark,scanned,created) VALUES(?,?,?,?,?)',
                            (role_id, persona_hash, watermark, watermark, now))
            self._core_version(role_id, '建立空白档案', now)
        return role_id

    def _core_version(self, role_id, cause, now):
        row = self.role(role_id)
        self.db.execute('INSERT INTO core_versions VALUES(?,?,?,?,?)',
                        (role_id, row['revision'], json.dumps(row, ensure_ascii=False), cause, now))

    def save_core(self, role_id, base, locked, persona_hash, now, source='手工底稿'):
        with self.db:
            self.db.execute('UPDATE roles SET base=?,locked=?,persona_hash=?,source=?,revision=revision+1 WHERE id=?',
                            (json.dumps(base, ensure_ascii=False), int(locked), persona_hash, source, role_id))
            self._core_version(role_id, source, now)

    def save_draft(self, role_id, draft, fingerprint):
        with self.db:
            self.db.execute('UPDATE roles SET draft=?,draft_hash=? WHERE id=?',
                            (json.dumps(draft, ensure_ascii=False), fingerprint, role_id))

    def list_insights(self, role_id, origin='', person_id='', limit=100):
        query = 'SELECT * FROM insights WHERE role_id=?'
        args = [role_id]
        if origin: query += ' AND origin=?'; args.append(origin)
        if person_id: query += ' AND person_id=?'; args.append(person_id)
        return [record(row) for row in self.db.execute(query + ' ORDER BY ts DESC,id DESC LIMIT ?', (*args, limit))]

    def insight(self, insight_id):
        return record(self.db.execute('SELECT * FROM insights WHERE id=?', (insight_id,)).fetchone())

    def same_topic(self, role_id, origin, person_id, topic):
        row = self.db.execute('SELECT * FROM insights WHERE role_id=? AND origin=? AND person_id=? AND topic=?',
                              (role_id, origin, person_id, topic)).fetchone()
        return record(row)

    def write_insight(self, value, cause, now):
        keys = ['role_id', 'account', 'origin', 'person_id', 'topic', 'kind', 'content', 'topics',
                'visibility', 'status', 'locked', 'supports', 'doubts', 'proofs', 'revision', 'ts']
        args = [json.dumps(value[key], ensure_ascii=False) if key in JSON_COLUMNS else value[key] for key in keys]
        with self.db:
            if value.get('id'):
                self.db.execute('UPDATE insights SET ' + ','.join(key + '=?' for key in keys) + ' WHERE id=?', (*args, value['id']))
                insight_id = value['id']
            else:
                cursor = self.db.execute('INSERT INTO insights(' + ','.join(keys) + ') VALUES(' + ','.join('?' for _ in keys) + ')', args)
                insight_id = cursor.lastrowid
            saved = self.insight(insight_id)
            self.db.execute('INSERT INTO insight_versions VALUES(?,?,?,?,?)',
                            (insight_id, saved['revision'], json.dumps(saved, ensure_ascii=False), cause, now))
        return saved

    def history(self, insight_id):
        return [dict(row) for row in self.db.execute('SELECT * FROM insight_versions WHERE insight_id=? ORDER BY revision DESC', (insight_id,))]

    def requests_used(self, role_id, day):
        return self.db.execute('SELECT COUNT(*) FROM requests WHERE role_id=? AND day=?', (role_id, day)).fetchone()[0]

    def reserve(self, role_id, day, purpose, now):
        with self.db: self.db.execute('INSERT INTO requests VALUES(?,?,?,?)', (role_id, day, purpose, now))

    def last_request(self, role_id, purpose):
        return self.db.execute('SELECT COALESCE(MAX(ts),0) FROM requests WHERE role_id=? AND purpose=?', (role_id, purpose)).fetchone()[0]

    def enqueue(self, role_id, items, scanned):
        with self.db:
            self.db.executemany('INSERT OR IGNORE INTO learning_queue VALUES(?,?,?,?)',
                                [(role_id, item['id'], item['umo'], item['person_id']) for item in items])
            self.db.execute('UPDATE roles SET scanned=? WHERE id=?', (scanned, role_id))

    def reset_scan(self, role_id, scanned):
        with self.db: self.db.execute('UPDATE roles SET scanned=? WHERE id=?', (scanned, role_id))

    def queued_batch(self, role_id, minimum, limit):
        row = self.db.execute('SELECT origin,person_id,COUNT(*) AS n FROM learning_queue WHERE role_id=? '
                              'GROUP BY origin,person_id HAVING n>=? ORDER BY MIN(entry_id) LIMIT 1', (role_id, minimum)).fetchone()
        if row is None: return []
        return [dict(item) for item in self.db.execute('SELECT * FROM learning_queue WHERE role_id=? AND origin=? AND person_id=? '
                  'ORDER BY entry_id LIMIT ?', (role_id, row['origin'], row['person_id'], limit))]

    def consume(self, role_id, entry_ids):
        with self.db: self.db.executemany('DELETE FROM learning_queue WHERE role_id=? AND entry_id=?', [(role_id, eid) for eid in entry_ids])

    def clear_learning(self, role_id, watermark):
        with self.db:
            self.db.execute('DELETE FROM insight_versions WHERE insight_id IN (SELECT id FROM insights WHERE role_id=?)', (role_id,))
            self.db.execute('DELETE FROM insights WHERE role_id=?', (role_id,))
            self.db.execute('DELETE FROM learning_queue WHERE role_id=?', (role_id,))
            self.db.execute('UPDATE roles SET watermark=?,scanned=? WHERE id=?', (watermark, watermark, role_id))
