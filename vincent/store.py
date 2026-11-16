"""SQLite persistence. Each transaction holds the process-wide connection lock."""
from contextlib import contextmanager
from pathlib import Path
import json
import os
import sqlite3
import threading
import time


def json_text(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


class Store:
    def __init__(self, path):
        self.path = str(path)
        if self.path != ':memory:':
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            # Keep the trace store private even under a permissive umask.
            fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
            os.close(fd)
            os.chmod(self.path, 0o600)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('PRAGMA secure_delete=ON')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA busy_timeout=10000')
        self.migrate()

    def migrate(self):
        with self.lock:
            self.db.execute('CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at REAL NOT NULL)')
            for script in sorted((Path(__file__).parent / 'migrations').glob('[0-9]*.sql')):
                version = int(script.name.split('_', 1)[0])
                if self.db.execute('SELECT 1 FROM schema_migrations WHERE version=?', (version,)).fetchone():
                    continue
                # executescript handles its own transaction; version is recorded inside it.
                self.db.executescript('BEGIN IMMEDIATE;\n' + script.read_text(encoding='utf8') +
                                      f'\nINSERT INTO schema_migrations VALUES({version},{time.time()});\nCOMMIT;')

    @contextmanager
    def transaction(self):
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                yield self.db
                self.db.execute('COMMIT')
            except BaseException:
                self.db.execute('ROLLBACK')
                raise

    def close(self):
        with self.lock:
            self.db.close()

    def log(self, event_key, kind, *, guild_id=None, channel_id=None, message_id=None, author_id=None, occurred_at=None, payload=None):
        now = time.time()
        with self.transaction() as db:
            return db.execute('INSERT OR IGNORE INTO events(event_key,kind,guild_id,channel_id,message_id,author_id,occurred_at,ingested_at,payload) VALUES(?,?,?,?,?,?,?,?,?)',
                              (event_key, kind, guild_id, channel_id, message_id, author_id, now if occurred_at is None else occurred_at, now, json_text(payload or {}))).rowcount == 1

    def mark_routed(self,message_id):
        with self.transaction() as db:
            db.execute("UPDATE messages SET routing_state='done' WHERE message_id=?",(str(message_id),))

    def message_event(self, *, kind, message_id, guild_id, channel_id, author_id, content, created_at, changed_at, reply_to=None, metadata=None):
        if kind not in {'create', 'edit', 'delete'}:
            raise ValueError('Unsupported message event')
        # Metadata is intentionally allowlisted: never retain attachment bytes or raw embeds.
        allowed = {k: v for k, v in (metadata or {}).items() if k in {'mentions', 'media', 'author_bot', 'content_available', 'display_name', 'username'}}
        event_key = f'message:{message_id}:{kind}:{changed_at}'
        with self.transaction() as db:
            tombstone=db.execute('SELECT deleted FROM messages WHERE message_id=?',(str(message_id),)).fetchone()
            if tombstone and tombstone['deleted'] and kind!='delete':return False
            inserted = db.execute('INSERT OR IGNORE INTO events(event_key,kind,guild_id,channel_id,message_id,author_id,occurred_at,ingested_at,payload) VALUES(?,?,?,?,?,?,?,?,?)',
                                  (event_key, 'message_'+kind, str(guild_id), str(channel_id), str(message_id), str(author_id), changed_at, time.time(), json_text({'content': content, 'reply_to': reply_to, 'metadata': allowed}))).rowcount
            if not inserted:
                return False
            db.execute('''INSERT INTO messages(message_id,guild_id,channel_id,author_id,content,reply_to,created_at,changed_at,deleted,metadata)
                          VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(message_id) DO UPDATE SET
                          content=CASE WHEN excluded.deleted=1 THEN NULL ELSE excluded.content END,
                          reply_to=COALESCE(excluded.reply_to,messages.reply_to), changed_at=excluded.changed_at,
                          deleted=excluded.deleted,metadata=excluded.metadata
                          WHERE (excluded.deleted=1 OR excluded.changed_at>messages.changed_at) AND messages.deleted=0''',
                       (str(message_id),str(guild_id),str(channel_id),str(author_id), None if kind=='delete' else content,
                        str(reply_to) if reply_to else None,created_at,changed_at,int(kind=='delete'),json_text(allowed)))
            if allowed.get('author_bot') or kind=='delete':db.execute("UPDATE messages SET routing_state='done' WHERE message_id=?",(str(message_id),))
            return True
