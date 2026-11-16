import json
import os
import re
import signal
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from dataclasses import replace
from unittest.mock import patch

from vincent.config import Settings
from vincent.dashboard_server import create_server
from vincent.store import Store


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'test.sqlite')
        self.settings = Settings(database=Path(self.temp.name) / 'test.sqlite', channels=frozenset({2}))
        self.server = create_server(self.store, self.settings, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        page = urllib.request.urlopen(self.url).read().decode()
        self.token = re.search(r'name="dashboard-token" content="([^"]+)', page).group(1)

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.store.close()
        self.temp.cleanup()

    def api(self, path, payload=None, token=True):
        headers = {'X-Dashboard-Token': self.token} if token else {}
        if payload is not None:
            headers['Content-Type'] = 'application/json'
        request = urllib.request.Request(self.url + path, data=json.dumps(payload).encode() if payload is not None else None, headers=headers)
        return json.load(urllib.request.urlopen(request))

    def test_dashboard_reads_and_manages_memory_and_features(self):
        self.assertFalse(self.api('/api/data')['running'])
        self.api('/api/add-memory', {'scope': 'server', 'guild': '1', 'content': 'Server game night'})
        result = self.api('/api/data')
        memory = result['memories'][0]
        self.assertEqual(memory['content'], 'Server game night')
        self.api('/api/feature', {'guild': '1', 'channel': '2', 'feature': 'unsolicited', 'enabled': True})
        flag = self.store.db.execute('SELECT enabled FROM feature_flags').fetchone()
        self.assertEqual(flag['enabled'], 1)
        self.api('/api/delete-memory', {'id': memory['id']})
        self.assertEqual(self.api('/api/data')['memory_total'], 0)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM events WHERE kind='dashboard_action'").fetchone()[0], 3)

    def test_api_requires_token_and_rejects_wrong_origin(self):
        with self.assertRaises(urllib.error.HTTPError): self.api('/api/data', token=False)
        request = urllib.request.Request(self.url + '/api/add-memory', data=b'{}', headers={'X-Dashboard-Token': self.token, 'Origin': 'http://example.com'})
        with self.assertRaises(urllib.error.HTTPError) as error: urllib.request.urlopen(request)
        self.assertEqual(error.exception.code, 403)

    def test_profiles_include_latest_display_name_and_user_memories(self):
        from vincent.memory import Memory
        from vincent.profiles import Profiles
        self.store.message_event(kind='create',message_id='11',guild_id='1',channel_id='2',author_id='4',content='i like playing chess',created_at=1,changed_at=1,metadata={'display_name':'Chess friend'})
        Profiles(self.store).incoming(1,4,at=1,message_id=11)
        proposal={'kind':'interest','content':'likes chess','source_ids':['11'],'source_type':'self_report','confidence':.9}
        self.assertEqual(Memory(self.store).apply_user_proposals(1,4,0,[proposal]),1)
        result=self.api('/api/data')
        self.assertEqual(result['profiles'][0]['display_name'],'Chess friend')
        self.assertEqual(result['profiles'][0]['incoming_count'],1)
        self.assertEqual(result['memories'][0]['content'],'likes chess')

    def test_start_and_stop_only_manage_dashboard_process(self):
        dashboard = self.server.dashboard
        dashboard.settings = replace(self.settings, token='test-token', api_key='test-key')
        old_dir = os.getcwd()
        try:
            os.chdir(self.temp.name)
            with patch('vincent.dashboard_server.bot_running', return_value=False), patch('vincent.dashboard_server.subprocess.Popen') as popen:
                process = popen.return_value
                process.poll.return_value = None
                dashboard.action('start-bot', {})
                popen.assert_called_once()
                dashboard.action('stop-bot', {})
                process.send_signal.assert_called_once_with(signal.SIGINT)
                process.poll.return_value = 0
                dashboard.stop_owned()
        finally:
            os.chdir(old_dir)


if __name__ == '__main__': unittest.main()
