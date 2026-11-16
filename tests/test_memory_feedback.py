import unittest
from vincent.store import Store
from vincent.memory import Memory
from vincent.feedback import Feedback

class MemoryFeedbackTests(unittest.TestCase):
 def setUp(self):
  self.s=Store(':memory:');self.m=Memory(self.s,clock=lambda:100)
  self.s.message_event(kind='create',message_id='1',guild_id='2',channel_id='3',author_id='4',content='I like chess',created_at=1,changed_at=1)
 def tearDown(self):self.s.close()
 def test_provenance_and_forget_race(self):
  p={'kind':'interest','content':'likes chess','source_ids':['1'],'source_type':'self_report','confidence':.9}
  self.assertEqual(self.m.apply_user_proposals(2,5,0,[p]),0)
  self.assertEqual(self.m.apply_user_proposals(2,4,0,[p]),1)
  self.assertEqual(len(self.m.search(2,4,'chess')),1)
  self.m.forget(2,4);self.assertEqual(self.m.apply_user_proposals(2,4,0,[p]),0);self.assertEqual(self.m.search(2,4,'chess'),[])
 def test_bot_seeds_and_vote_changes(self):
  f=Feedback(self.s,'9');self.assertFalse(f.update(10,9,'👍',True));f.update(10,4,'👍',True);f.update(10,4,'👎',True)
  self.assertEqual(f.votes(10),{'4':'mixed'});f.update(10,4,'👍',False);self.assertEqual(f.votes(10),{'4':'negative'});f.update(10,4,'👎',False);self.assertEqual(f.votes(10),{})
 def test_feedback_reconciliation_and_history(self):
  f=Feedback(self.s,'9');f.update(10,4,'👍',True);f.update(10,4,'👍',True)
  self.assertEqual(self.s.db.execute('SELECT count(*) FROM feedback_events').fetchone()[0],1)
  f.reconcile(10,[('9','👍'),('5','👎')]);self.assertEqual(f.votes(10),{'5':'negative'})
  self.assertEqual(self.s.db.execute('SELECT count(*) FROM feedback_events').fetchone()[0],3)
 def test_shared_memory_is_scoped_and_delete_generation_checked(self):
  self.s.message_event(kind='create',message_id='2',guild_id='2',channel_id='3',author_id='5',content='server game night friday',created_at=2,changed_at=2)
  proposal={'kind':'server_event','content':'server game night on friday','source_ids':['1','2']}
  self.assertEqual(self.m.apply_server_proposals('2',[proposal],{'1':1,'2':2},{'1':0,'2':0}),1)
  self.assertEqual(len(self.m.shared('2')),1);self.assertEqual(self.m.shared('7'),[])
  self.m.forget('2','4');self.assertEqual(self.m.shared('2'),[])
  proposal['content']='another shared event'
  self.assertEqual(self.m.apply_server_proposals('2',[proposal],{'1':1,'2':2},{'1':0,'2':0}),0)
 def test_correct_and_delete_are_owner_scoped_and_invalidate_batches(self):
  p={'kind':'interest','content':'likes chess','source_ids':['1'],'source_type':'self_report','confidence':.9}
  self.m.apply_user_proposals(2,4,0,[p]);mid=self.s.db.execute('SELECT id FROM memories').fetchone()[0]
  self.assertFalse(self.m.change_record(2,5,mid,replacement='something else',source_id='1'))
  self.assertTrue(self.m.change_record(2,4,mid,replacement='plays checkers',source_id='1'))
  self.assertEqual(self.m.search(2,4,'checkers')[0]['content'],'plays checkers')
  self.assertEqual(self.m.apply_user_proposals(2,4,0,[p]),0)
  self.assertTrue(self.m.change_record(2,4,mid));self.assertEqual(self.m.search(2,4,'checkers'),[])
 def test_age_requires_explicit_number_and_observed_time_orders_changes(self):
  for mid,content,created in [('2',"i am 19 years old",200),('3',"i am 18 years old",100)]:
   self.s.message_event(kind='create',message_id=mid,guild_id='2',channel_id='3',author_id='4',content=content,created_at=created,changed_at=created)
  def age(content,source):return {'kind':'age','content':content,'source_ids':[source],'source_type':'self_report','confidence':.9}
  self.assertEqual(self.m.apply_user_proposals(2,4,0,[age('I am 20','2')]),0)
  self.assertEqual(self.m.apply_user_proposals(2,4,0,[age('I am 19','2')]),1)
  self.assertEqual(self.m.apply_user_proposals(2,4,0,[age('I am 18','3')]),0)
  record=self.s.db.execute("SELECT content,observed_at FROM memories WHERE kind='age' AND status='active'").fetchone();self.assertEqual(tuple(record),('I am 19',200))
 def test_shared_deletion_preserves_scope_and_identity(self):
  global_id=self.m.put_shared(scope='global',content='a global shared fact')
  server_id=self.m.put_shared(scope='server',guild='2',content='a server shared fact')
  identity=self.m.put_shared(scope='global',kind='identity',content='the owner is HMAN')
  self.assertFalse(self.m.remove_shared(server_id,guild='3'))
  self.assertFalse(self.m.remove_shared(global_id,guild='2'))
  self.assertFalse(self.m.remove_shared(identity,guild='2',allow_global=True))
  self.assertTrue(self.m.remove_shared(server_id,guild='2'))
  self.assertTrue(self.m.remove_shared(global_id,guild='2',allow_global=True))
 def test_reaction_clear_records_removals(self):
  f=Feedback(self.s,'9');f.update(10,4,'👍',True);f.update(10,5,'👎',True)
  self.assertEqual(f.clear(10,'👍'),1);self.assertEqual(f.votes(10),{'5':'negative'})
  self.assertEqual(f.clear(10),1);self.assertEqual(f.votes(10),{})
 def test_multilingual_self_reference_allows_supported_proposals(self):
  for mid,text in [('2','mujhe chess khelna pasand hai'),('3','já mám rád šachy')]:
   self.s.message_event(kind='create',message_id=mid,guild_id='2',channel_id='3',author_id='4',content=text,created_at=int(mid),changed_at=int(mid))
  for mid,content in [('2','enjoys chess in hindi'),('3','enjoys chess in czech')]:
   p={'kind':'interest','content':content,'source_ids':[mid],'source_type':'self_report','confidence':.8}
   self.assertEqual(self.m.apply_user_proposals(2,4,0,[p]),1)
 def test_unknown_removal_tombstone_beats_stale_snapshot(self):
  now=[10];f=Feedback(self.s,'9',clock=lambda:now[0]);snapshot_started=10
  now[0]=11;self.assertTrue(f.update(10,4,'👍',False))
  now[0]=12;f.reconcile(10,[('4','👍')],snapshot_started=snapshot_started)
  self.assertEqual(f.votes(10),{});self.assertEqual(self.s.db.execute('SELECT active FROM feedback').fetchone()[0],0)
 def test_duplicate_gateway_events_protect_against_stale_snapshots(self):
  now=[1];f=Feedback(self.s,'9',clock=lambda:now[0])
  self.assertTrue(f.update(10,4,'👍',True))
  now[0]=3;self.assertFalse(f.update(10,4,'👍',True))
  now[0]=4;f.reconcile(10,[],snapshot_started=2)
  self.assertEqual(f.votes(10),{'4':'positive'})
  now[0]=5;self.assertTrue(f.update(10,4,'👍',False))
  now[0]=7;self.assertFalse(f.update(10,4,'👍',False))
  now[0]=8;f.reconcile(10,[('4','👍')],snapshot_started=6)
  self.assertEqual(f.votes(10),{})
  self.assertEqual(self.s.db.execute('SELECT count(*) FROM feedback_events').fetchone()[0],2)
 def test_unverified_reactor_cannot_be_counted_by_older_snapshot(self):
  now=[3];f=Feedback(self.s,'9',clock=lambda:now[0])
  f.mark_unverified(10,4,'👍')
  now[0]=4;f.reconcile(10,[('4','👍')],snapshot_started=2)
  self.assertEqual(f.votes(10),{})
  now[0]=6;f.reconcile(10,[('4','👍')],snapshot_started=5)
  self.assertEqual(f.votes(10),{'4':'positive'})
 def test_unseen_vote_not_restored_after_clear_event(self):
  now=[10];f=Feedback(self.s,'9',clock=lambda:now[0]);snapshot_started=10
  now[0]=11;self.assertEqual(f.clear(10),0)
  now[0]=12;f.reconcile(10,[('4','👍'),('5','👎')],snapshot_started=snapshot_started)
  self.assertEqual(f.votes(10),{})
  # A later live reaction remains valid.
  now[0]=13;f.update(10,4,'👍',True);self.assertEqual(f.votes(10),{'4':'positive'})
if __name__=='__main__':unittest.main()
