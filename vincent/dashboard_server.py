"""A small local-only dashboard for Vincent. Run with python -m vincent.dashboard_server."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from dotenv import load_dotenv
from .adaptation import Adaptation
from .budget import Budget
from .config import Settings
from .flags import FeatureFlags
from .memory import Memory
from .store import Store

ASSETS = Path(__file__).parent / 'dashboard'


def bot_running(database):
    path = Path(str(Path(database).resolve()) + '.runtime.lock')
    if not path.exists(): return False
    with path.open('r') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(handle, fcntl.LOCK_UN)
        return False


class Dashboard:
    def __init__(self, store, settings):
        self.store, self.settings = store, settings
        self.budget = Budget(store, settings)
        self.flags = FeatureFlags(store, settings)
        self.memory = Memory(store)
        self.adaptation = Adaptation(store)
        self.process = None
        self.process_lock = threading.Lock()
        self.process_log = None

    def stop_owned(self):
        with self.process_lock:
            if self.process and self.process.poll() is None:
                try:
                    self.process.send_signal(signal.SIGINT)
                    self.process.wait(timeout=10)
                except ProcessLookupError: pass
                except subprocess.TimeoutExpired: self.process.terminate()
            if self.process_log:
                self.process_log.close()
                self.process_log = None

    def data(self):
        with self.store.lock:
            db = self.store.db
            def rows(sql): return [dict(row) for row in db.execute(sql).fetchall()]
            events = rows('SELECT kind,occurred_at,guild_id,channel_id,author_id,message_id,payload FROM events ORDER BY id DESC LIMIT 100')
            requests = rows('SELECT r.request_id,r.model,r.created_at,r.finished_at,r.payload,r.response,r.error,a.kind,a.status,a.metadata FROM requests_log r JOIN request_attempts a USING(request_id) ORDER BY r.created_at DESC LIMIT 60')
            queue = rows('SELECT id,author_id,channel_id,reason,status,created_at,response_message_id FROM queue ORDER BY id DESC LIMIT 80')
            memories = rows("SELECT id,scope,guild_id,user_id,kind,content,source_type,confidence,updated_at FROM memories WHERE status='active' ORDER BY updated_at DESC LIMIT 300")
            versions = rows('SELECT id,status,text,created_at FROM prompt_versions ORDER BY id DESC LIMIT 30')
            channels = rows("SELECT guild_id,channel_id,max(created_at) AS last_seen FROM messages WHERE guild_id NOT IN ('unknown','None') GROUP BY guild_id,channel_id ORDER BY last_seen DESC LIMIT 30")
            feedback = rows('SELECT emoji,count(*) AS count FROM feedback WHERE active=1 GROUP BY emoji')
            profiles = rows('''SELECT p.guild_id,p.user_id,p.incoming_count,p.outgoing_count,p.last_interaction,
                (SELECT json_extract(m.metadata,'$.display_name') FROM messages m
                 WHERE m.guild_id=p.guild_id AND m.author_id=p.user_id AND m.deleted=0
                 ORDER BY m.created_at DESC LIMIT 1) AS display_name
                FROM user_profiles p ORDER BY p.last_interaction DESC LIMIT 100''')
            memory_total = db.execute("SELECT count(*) FROM memories WHERE status='active'").fetchone()[0]
            queue_counts = {row['status']: row['count'] for row in db.execute('SELECT status,count(*) AS count FROM queue GROUP BY status')}
        for row in channels:
            row['unsolicited'] = self.flags.enabled(row['guild_id'], row['channel_id'], 'unsolicited')
            row['reactions'] = self.flags.enabled(row['guild_id'], row['channel_id'], 'reactions')
        managed = self.process is not None and self.process.poll() is None
        log_path = Path('data/dashboard-bot.log')
        bot_log = ''
        if log_path.exists():
            with log_path.open('rb') as handle:
                handle.seek(0, os.SEEK_END)
                handle.seek(max(0, handle.tell() - 5000))
                bot_log = handle.read().decode('utf8', errors='replace')
        return {'running': managed or bot_running(self.settings.database), 'managed': managed, 'bot_log': bot_log, 'model': self.settings.model,
                'budget': self.budget.status(), 'channels': channels, 'events': events,
                'requests': requests, 'queue': queue, 'memories': memories, 'versions': versions,
                'feedback': feedback, 'profiles': profiles,
                'memory_total': memory_total, 'queue_counts': queue_counts,
                'configured_channels': [str(x) for x in sorted(self.settings.channels)]}

    def action(self, action, data):
        if action == 'start-bot':
            with self.process_lock:
                if (self.process and self.process.poll() is None) or bot_running(self.settings.database): raise ValueError('The bot is already running')
                self.settings.validate_runtime()
                if self.process_log: self.process_log.close()
                Path('data').mkdir(exist_ok=True)
                log_path = Path('data/dashboard-bot.log')
                fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                os.fchmod(fd, 0o600)
                self.process_log = os.fdopen(fd, 'a', encoding='utf8')
                env = os.environ.copy()
                env['VINCENT_DATABASE'] = str(self.settings.database)
                self.process = subprocess.Popen([sys.executable, '-m', 'vincent.app'], env=env, stdout=self.process_log, stderr=subprocess.STDOUT)
        elif action == 'stop-bot':
            with self.process_lock:
                if not self.process or self.process.poll() is not None: raise ValueError('Only a bot started from this dashboard can be stopped here')
                self.process.send_signal(signal.SIGINT)
        elif action == 'feature':
            guild, channel, feature, enabled = str(data['guild']), str(data['channel']), data['feature'], data['enabled']
            if not guild.isdigit() or not channel.isdigit() or int(channel) not in self.settings.channels or type(enabled) is not bool:
                raise ValueError('Invalid channel or value')
            self.flags.set(guild, channel, feature, enabled, 'local-dashboard')
        elif action == 'delete-memory':
            memory_id = int(data['id'])
            with self.store.lock:
                row = self.store.db.execute('SELECT scope,guild_id,user_id FROM memories WHERE id=? AND status=\'active\'', (memory_id,)).fetchone()
            if not row: raise ValueError('Memory not found')
            if row['scope'] == 'user':
                ok = self.memory.change_record(row['guild_id'], row['user_id'], memory_id)
            else:
                ok = self.memory.remove_shared(memory_id, guild=row['guild_id'], allow_global=True)
            if not ok: raise ValueError('This memory cannot be deleted')
        elif action == 'add-memory':
            scope, guild, content = data['scope'], str(data.get('guild', '')), data['content']
            if scope not in ('global', 'server') or (scope == 'server' and not guild.isdigit()): raise ValueError('Choose a server ID')
            self.memory.put_shared(scope=scope, guild=guild if scope == 'server' else None, content=content)
        elif action == 'activate-guidance':
            self.adaptation.activate(int(data['id']), approved=True)
        elif action == 'cancel-queue':
            with self.store.transaction() as db:
                changed = db.execute("UPDATE queue SET status='cancelled',reason='cancelled from dashboard',updated_at=? WHERE id=? AND status IN ('pending','claimed')", (time.time(), int(data['id']))).rowcount
                if changed: db.execute('INSERT INTO queue_events(queue_id,occurred_at,action,details) VALUES(?,?,?,?)', (int(data['id']),time.time(),'cancelled','{"reason":"dashboard"}'))
            if not changed: raise ValueError('Queue item is no longer pending or claimed')
        else:
            raise ValueError('Unknown action')
        self.store.log('dashboard:' + uuid.uuid4().hex, 'dashboard_action', payload={'action': action, 'id': data.get('id')})


def create_server(store, settings, port=8765):
    dashboard = Dashboard(store, settings)
    token = secrets.token_urlsafe(32)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args): pass
        def respond(self, status, body, content_type='application/json; charset=utf-8'):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)
        def host_ok(self):
            return self.headers.get('Host', '') in (f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}')
        def do_GET(self):
            if not self.host_ok(): return self.respond(403, b'Forbidden', 'text/plain')
            path = urlsplit(self.path).path
            if path == '/':
                page = (ASSETS / 'index.html').read_text(encoding='utf8').replace('__TOKEN__', token)
                return self.respond(200, page.encode(), 'text/html; charset=utf-8')
            if path in ('/style.css', '/app.js'):
                return self.respond(200, (ASSETS / path[1:]).read_bytes(), 'text/css; charset=utf-8' if path.endswith('.css') else 'text/javascript; charset=utf-8')
            if path == '/api/data' and secrets.compare_digest(self.headers.get('X-Dashboard-Token', ''), token):
                return self.respond(200, json.dumps(dashboard.data(), ensure_ascii=False).encode())
            self.respond(404, b'Not found', 'text/plain')
        def do_POST(self):
            if not self.host_ok() or not secrets.compare_digest(self.headers.get('X-Dashboard-Token', ''), token):
                return self.respond(403, b'Forbidden', 'text/plain')
            origin = self.headers.get('Origin')
            if origin and origin not in (f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}'):
                return self.respond(403, b'Forbidden', 'text/plain')
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 16384: raise ValueError('Invalid request size')
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict): raise ValueError('Invalid request')
                dashboard.action(urlsplit(self.path).path.removeprefix('/api/'), data)
                self.respond(200, b'{"ok":true}')
            except (ValueError, KeyError, TypeError, OSError, json.JSONDecodeError) as exc:
                self.respond(400, json.dumps({'error': str(exc)}).encode())
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.dashboard = dashboard
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--database', help='Override the configured database path')
    args = parser.parse_args()
    load_dotenv(os.getenv('VINCENT_ENV_FILE', '.env'))
    settings = Settings.from_env()
    if args.database:
        from dataclasses import replace
        settings = replace(settings, database=Path(args.database))
    store = Store(settings.database)
    try:
        server = create_server(store, settings, args.port)
        print(f'Dashboard: http://127.0.0.1:{server.server_port}', flush=True)
        try: server.serve_forever()
        except KeyboardInterrupt: pass
        finally:
            server.server_close()
            server.dashboard.stop_owned()
    finally: store.close()

if __name__ == '__main__': main()
