import unittest
from vincent.store import Store
from vincent.memory import Memory
from vincent.extraction import Extractor
from vincent.privacy import erase_user

class PrivacyTests(unittest.TestCase):
 def test_erasure_cancels_queue_and_removes_derived_source(self):
  s=Store(':memory:');m=Memory(s)
  s.message_event(kind='create',message_id='10',guild_id='2',channel_id='3',author_id='4',content='i like playing chess',created_at=1,changed_at=1)
  e=Extractor(s,m,None);e.collect(2,4,10)
  m.put_shared(scope='server',guild='2',content='derived claim',source_ids=['10'],source_type='inference')
  from vincent.queue import WorkQueue
  q=WorkQueue(s);qid=q.enqueue(guild_id=2,channel_id=3,author_id=4,branch_id=1,message_id=10);claim=q.claim()
  erase_user(s,2,4)
  self.assertFalse(q.fresh(qid,lease_token=claim['lease_token']));self.assertEqual(m.shared(2),[]);self.assertEqual(s.db.execute('SELECT count(*) FROM memory_candidates').fetchone()[0],0)
  self.assertEqual(tuple(s.db.execute('SELECT content,deleted FROM messages').fetchone()),(None,1));self.assertEqual(m.generation(2,4)[0],False);s.close()
 def test_erasure_redacts_derived_guidance_and_bot_replies(self):
  from vincent.queue import WorkQueue
  from vincent.adaptation import Adaptation
  s=Store(':memory:');q=WorkQueue(s);a=Adaptation(s)
  s.message_event(kind='create',message_id='10',guild_id=2,channel_id=3,author_id=4,content='private detail',created_at=1,changed_at=1)
  qid=q.enqueue(guild_id=2,channel_id=3,author_id=4,branch_id=10,message_id=10);claim=q.claim();q.finish(qid,'sent',lease_token=claim['lease_token'],response_message_id='20')
  s.message_event(kind='create',message_id='20',guild_id=2,channel_id=3,author_id=9,content='response with private detail',created_at=2,changed_at=2)
  version=a.propose('Be specific about this scenario',{'sample_size':10,'distinct_raters':3,'hypothesis':'better','source_ids':['20']});a.activate(version,approved=True)
  erase_user(s,2,4)
  self.assertIsNone(a.active());self.assertEqual(s.db.execute('SELECT status FROM prompt_versions WHERE id=?',(version,)).fetchone()[0],'redacted')
  self.assertIsNone(s.db.execute('SELECT content FROM messages WHERE message_id=20').fetchone()[0])
  with self.assertRaises(ValueError):a.activate(version,approved=True)
  s.close()
if __name__=='__main__':unittest.main()

class PrivacyStorageTests(unittest.TestCase):
 def test_erasure_removes_request_snapshot_feedback_and_voter_derived_guidance(self):
  import json,tempfile
  from pathlib import Path
  from vincent.feedback import Feedback
  from vincent.adaptation import Adaptation
  with tempfile.TemporaryDirectory() as directory:
   path=Path(directory)/'private.sqlite';s=Store(path);a=Adaptation(s);secret='fictional-private-marker-82'
   s.message_event(kind='create',message_id='1001',guild_id='20',channel_id='30',author_id='40',content=secret,created_at=1,changed_at=1)
   with s.transaction() as db:
    db.execute("INSERT INTO request_attempts(request_id,kind,reserved_at,status) VALUES('request','direct',1,'success')")
    db.execute("INSERT INTO requests_log(request_id,model,payload,response,created_at) VALUES('request','fixture',?,?,1)",(json.dumps({'author_id':'40','content':secret}),secret))
   Feedback(s,'90').update('2000','40','👍',True)
   version=a.propose('Use more specific conversational reactions.',{'sample_size':10,'distinct_raters':3,'hypothesis':'fit','source_ids':['2000'],'rater_ids':['40']});a.activate(version,approved=True)
   erase_user(s,20,40)
   self.assertEqual(s.db.execute('SELECT count(*) FROM requests_log').fetchone()[0],0)
   self.assertEqual(s.db.execute('SELECT count(*) FROM feedback WHERE user_id=40').fetchone()[0],0)
   self.assertIsNone(a.active());self.assertEqual(s.db.execute('SELECT status FROM prompt_versions WHERE id=?',(version,)).fetchone()[0],'redacted')
   self.assertNotIn(secret,path.read_bytes().decode('latin1'));s.close()

class FeedbackErasureTests(unittest.TestCase):
 def test_snapshot_does_not_restore_erased_reaction_but_new_vote_is_allowed(self):
  import time
  from vincent.feedback import Feedback
  from vincent.queue import WorkQueue
  s=Store(':memory:');q=WorkQueue(s);item=q.enqueue(guild_id=2,channel_id=3,author_id=5,branch_id=1,message_id=1);claim=q.claim();q.finish(item,'sent',lease_token=claim['lease_token'],response_message_id='20')
  f=Feedback(s,'9');f.update('20','4','👍',True);erase_user(s,2,4)
  f.reconcile('20',[('4','👍')],snapshot_started=time.time());self.assertEqual(f.votes('20'),{})
  # A fresh reaction event after erasure is a new explicit vote.
  f.update('20','4','👍',True);self.assertEqual(f.votes('20'),{'4':'positive'})
  f.reconcile('20',[('4','👍')],snapshot_started=time.time());self.assertEqual(f.votes('20'),{'4':'positive'});s.close()
