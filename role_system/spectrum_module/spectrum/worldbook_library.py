"""独立书库、两槽选择与整本交换；条目ID与原始正文保持稳定。"""
import json
import time
from . import worldbook


class WorldbookLibrary:
    def __init__(self, store):
        self.store = store
        db = store._db
        with store._lock:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS worldbook_books (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS worldbook_selection (
                    id INTEGER PRIMARY KEY CHECK(id=1), global_book INTEGER,
                    current_book INTEGER, strategy TEXT NOT NULL DEFAULT 'order');
            ''')
            columns = {row[1] for row in db.execute('PRAGMA table_info(worldbook)')}
            if 'book_id' not in columns:
                for row in db.execute('SELECT DISTINCT umo FROM worldbook_state').fetchall():
                    counts=db.execute("SELECT COUNT(*),SUM(kind IN ('received','delivered')) FROM entries WHERE umo=?",(row['umo'],)).fetchone()
                    offset=int(counts[0])-int(counts[1] or 0)
                    db.execute('UPDATE worldbook_state SET sticky_until=MAX(0,sticky_until-?),cooldown_until=MAX(0,cooldown_until-?),last_fired_seq=MAX(0,last_fired_seq-?) WHERE umo=?',
                               (offset,offset,offset,row['umo']))
            for name, definition in [('book_id', 'INTEGER NOT NULL DEFAULT 0'),
                                     ('extensions', "TEXT NOT NULL DEFAULT '{}'"),
                                     ('generation_triggers', "TEXT NOT NULL DEFAULT ''"),
                                     ('match_persona', 'INTEGER NOT NULL DEFAULT 0')]:
                if name not in columns:
                    db.execute(f'ALTER TABLE worldbook ADD COLUMN {name} {definition}')
            db.execute('INSERT OR IGNORE INTO worldbook_selection(id) VALUES(1)')
            if db.execute('SELECT 1 FROM worldbook WHERE book_id=0 LIMIT 1').fetchone():
                cursor = db.execute('INSERT INTO worldbook_books(name,created_at) VALUES(?,?)', ('原有世界书', time.time()))
                db.execute('UPDATE worldbook SET book_id=? WHERE book_id=0', (cursor.lastrowid,))
                db.execute('UPDATE worldbook_selection SET global_book=? WHERE global_book IS NULL', (cursor.lastrowid,))
            db.commit()

    def books(self):
        with self.store._lock:
            return [dict(row) for row in self.store._db.execute('''
                SELECT b.*, COUNT(e.id) AS count FROM worldbook_books b
                LEFT JOIN worldbook e ON e.book_id=b.id GROUP BY b.id ORDER BY b.id''')]

    def selection(self):
        with self.store._lock:
            return dict(self.store._db.execute('SELECT * FROM worldbook_selection WHERE id=1').fetchone())

    def selected_ids(self):
        selected = self.selection()
        return list(dict.fromkeys(value for key in ('global_book','current_book') if (value := selected[key]) is not None))

    def require_book(self, book_id):
        with self.store._lock:
            row = self.store._db.execute('SELECT * FROM worldbook_books WHERE id=?',(int(book_id),)).fetchone()
        if row is None:
            raise ValueError('这本世界书不存在，请刷新书库')
        return dict(row)

    def save(self, name, description='', book_id=None):
        name = str(name).strip()
        if not name:
            raise ValueError('请填写世界书名称')
        with self.store._lock:
            db = self.store._db
            if book_id:
                self.require_book(book_id)
                db.execute('UPDATE worldbook_books SET name=?,description=? WHERE id=?',(name,str(description),int(book_id)))
            else:
                book_id = db.execute('INSERT INTO worldbook_books(name,description,created_at) VALUES(?,?,?)',
                                     (name,str(description),time.time())).lastrowid
            db.commit()
        return int(book_id)

    def select(self, global_book=None, current_book=None, strategy='order'):
        if strategy not in ('order','global_first','current_first'):
            raise ValueError('未知的世界书排列方式')
        for value in (global_book,current_book):
            if value is not None:
                self.require_book(value)
        with self.store._lock:
            self.store._db.execute('UPDATE worldbook_selection SET global_book=?,current_book=?,strategy=? WHERE id=1',
                                  (global_book,current_book,strategy))
            self.store._db.commit()

    def default_book(self):
        selected = self.selected_ids()
        if selected:
            return selected[-1]
        books = self.books()
        book_id = books[0]['id'] if books else self.save('我的世界书')
        self.select(global_book=book_id)
        return book_id

    def delete(self, book_id):
        self.require_book(book_id)
        with self.store._lock:
            db = self.store._db
            ids = [r['id'] for r in db.execute('SELECT id FROM worldbook WHERE book_id=?',(int(book_id),))]
            db.execute('DELETE FROM worldbook_state WHERE entry_id IN (SELECT id FROM worldbook WHERE book_id=?)',(int(book_id),))
            db.execute('DELETE FROM worldbook WHERE book_id=?',(int(book_id),))
            db.execute('DELETE FROM worldbook_books WHERE id=?',(int(book_id),))
            db.execute('UPDATE worldbook_selection SET global_book=NULL WHERE global_book=?',(int(book_id),))
            db.execute('UPDATE worldbook_selection SET current_book=NULL WHERE current_book=?',(int(book_id),))
            db.commit()
        self.store._drop_vectors('worldbook',ids)
        return len(ids)

    def export_book(self, book_id):
        book = self.require_book(book_id)
        rows = [{key:row[key] for key in self.store._WORLDBOOK_COLS if key!='book_id'}
                for row in self.store.worldbook_all(book_id=book_id)]
        return {'format':'spectrum-worldbook-v1','name':book['name'],'description':book['description'],'entries':rows}

    def export_library(self):
        return {'format':'spectrum-worldbook-library-v1','books':[
                    {**self.export_book(book['id']),'source_id':book['id']} for book in self.books()],
                'selection':self.selection()}

    def import_book(self, document, *, book_id=None, replace=False, name=None):
        from .worldbook_exchange import read_book
        parsed, warnings = read_book(document, self.store._WORLDBOOK_COLS)
        if book_id is not None:
            self.require_book(book_id)
        # 先验证整本文件，再事务写入，失败不留下半本书。
        with self.store._lock:
            db = self.store._db
            db.execute('SAVEPOINT import_worldbook')
            removed = []
            try:
                if book_id is None:
                    book_id = db.execute('INSERT INTO worldbook_books(name,description,created_at) VALUES(?,?,?)',
                        (name or parsed['name'],parsed['description'],time.time())).lastrowid
                if replace:
                    removed = [row['id'] for row in db.execute('SELECT id FROM worldbook WHERE book_id=?',(book_id,))]
                    db.execute('DELETE FROM worldbook_state WHERE entry_id IN (SELECT id FROM worldbook WHERE book_id=?)',(book_id,))
                    db.execute('DELETE FROM worldbook WHERE book_id=?',(book_id,))
                for fields in parsed['entries']:
                    fields = {**fields,'book_id':book_id,'created_at':time.time(),'updated_at':time.time()}
                    db.execute(f"INSERT INTO worldbook({','.join(fields)}) VALUES({','.join('?' for _ in fields)})",tuple(fields.values()))
                db.execute('RELEASE import_worldbook')
            except Exception:
                db.execute('ROLLBACK TO import_worldbook');db.execute('RELEASE import_worldbook')
                raise
        self.store._drop_vectors('worldbook',removed)
        return {'id':int(book_id),'count':len(parsed['entries']),'warnings':warnings}

    def import_library(self, document):
        """导入新的世界书，保留原书与当前启用选择。"""
        from .worldbook_exchange import validate_library
        validate_library(document,self.store._WORLDBOOK_COLS)
        if isinstance(document,list):
            return self.import_book({'format':'spectrum-worldbook-v1','name':'导入的世界书','entries':document})
        from .worldbook_exchange import read_book
        for book in document['books']:
            read_book(book,self.store._WORLDBOOK_COLS)
        mapping={}
        with self.store._lock:
            db=self.store._db
            db.execute('SAVEPOINT import_library')
            try:
                for book in document['books']:
                    result=self.import_book(book)
                    mapping[book['source_id']]=result['id']
                db.execute('RELEASE import_library')
            except Exception:
                db.execute('ROLLBACK TO import_library');db.execute('RELEASE import_library')
                raise
        return {'count':len(mapping)}
