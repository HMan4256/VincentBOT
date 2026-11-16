import json,tempfile,types,unittest
from pathlib import Path
from vincent.app import Vincent
from vincent.config import Settings

class AdminTests(unittest.IsolatedAsyncioTestCase):
 async def test_guild_admin_cannot_change_global_guidance(self):
  with tempfile.TemporaryDirectory() as d:
   app=Vincent(Settings(database=Path(d)/'db',channels=frozenset({2}),owner_id=99))
   version=app.adaptation.propose('Use more specific roasts.',{'sample_size':10,'distinct_raters':3,'hypothesis':'fit','source_ids':['x']})
   replies=[]
   async def reply(text,**kw):replies.append(text)
   member=types.SimpleNamespace(id=10,guild_permissions=types.SimpleNamespace(manage_guild=True))
   msg=types.SimpleNamespace(id=5,author=member,guild=types.SimpleNamespace(id=1),channel=types.SimpleNamespace(id=2),reply=reply)
   initial=app.adaptation.active()['id'];await app.admin_command(msg,f'activate {version}')
   self.assertEqual(app.adaptation.active()['id'],initial);self.assertIn('only the configured owner',replies[-1])
   member.id=99;await app.admin_command(msg,f'activate {version}');self.assertEqual(app.adaptation.active()['id'],version)
   events=[json.loads(r['payload']) for r in app.store.db.execute("SELECT payload FROM events WHERE kind='admin_action' ORDER BY rowid")]
   self.assertEqual(events,[{'operation':'activate','outcome':'attempted'},{'operation':'activate','outcome':'permission_denied'},{'operation':'activate','outcome':'attempted'},{'operation':'activate','outcome':'completed'}])
   member.guild_permissions.manage_guild=False;member.id=10
   await app.admin_command(msg,'remember server some private text')
   event=app.store.db.execute("SELECT payload FROM events WHERE kind='admin_action' ORDER BY rowid DESC LIMIT 1").fetchone()[0]
   self.assertEqual(json.loads(event),{'operation':'remember server','outcome':'permission_denied'})
   self.assertNotIn('private text',event)
   await app.close()
if __name__=='__main__':unittest.main()
