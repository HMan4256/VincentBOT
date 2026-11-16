import json,unittest
from vincent.store import Store
from vincent.memory import Memory
from vincent.extraction import Extractor

class FakeGateway:
 def __init__(self,proposals):self.proposals=proposals;self.calls=0
 async def complete(self,**kwargs):self.calls+=1;self.last_kwargs=kwargs;return json.dumps({'proposals':self.proposals}),'request'

class ExtractionTests(unittest.IsolatedAsyncioTestCase):
 async def test_manual_batch_claim_is_isolated_and_released(self):
  from vincent.memory_now import claim,release,snapshot_pending
  s=Store(':memory:');m=Memory(s);g=FakeGateway([]);e=Extractor(s,m,g,clock=lambda:100)
  for i in range(3):
   mid=str(i+1)
   s.message_event(kind='create',message_id=mid,guild_id='2',channel_id='3',author_id='4',content='i enjoy chess daily',created_at=i,changed_at=i)
   e.collect(2,4,mid)
  ids=snapshot_pending(s,100);self.assertEqual(len(ids),3)
  self.assertEqual(len(claim(s,ids[:2],'manual:test')),2)
  self.assertEqual(len(snapshot_pending(s,100)),1)
  self.assertEqual(await e.run_batch(minimum=1,candidate_state='manual:test'),0)
  self.assertEqual(s.db.execute("SELECT count(*) FROM memory_candidates WHERE state='processed'").fetchone()[0],2)
  claim(s,ids[2:],'manual:unfinished');self.assertEqual(release(s,'manual:unfinished'),1)
  self.assertEqual(len(snapshot_pending(s,100)),1);s.close()
 async def test_batch_includes_recent_chat_without_abandoning_backlog(self):
  s=Store(':memory:');clock=[100.0]
  class Capture:
   async def complete(self,**kwargs):
    self.batch=json.loads(kwargs['messages'][1]['content'])
    return '{"proposals":[],"server_proposals":[]}','request'
  gateway=Capture();e=Extractor(s,Memory(s),gateway,clock=lambda:clock[0])
  for i in range(40):
   s.message_event(kind='create',message_id=str(i),guild_id='2',channel_id='3',author_id=str(i+10),content='a message here',created_at=i,changed_at=i)
   e.collect(2,i+10,i,interaction=i%2==0);clock[0]+=1
  await e.run_batch(minimum=1,maximum=8)
  ids=[int(x['message_id']) for x in gateway.batch]
  self.assertEqual(len(ids),8);self.assertEqual(len(set(ids)),8)
  self.assertIn(0,ids);self.assertIn(1,ids);self.assertIn(38,ids);self.assertIn(39,ids)
  s.close()
 async def test_batch_reserves_room_for_interactions_and_general_chat(self):
  s=Store(':memory:');m=Memory(s)
  class Capture:
   async def complete(self,**kwargs):
    self.batch=json.loads(kwargs['messages'][1]['content'])
    return '{"proposals":[],"server_proposals":[]}','request'
  gateway=Capture();clock=[100.0];e=Extractor(s,m,gateway,clock=lambda:clock[0])
  for i in range(12):
   s.message_event(kind='create',message_id=str(i),guild_id='2',channel_id='3',author_id=str(i+10),content='a message here',created_at=i,changed_at=i)
   e.collect(2,i+10,i,interaction=i>=9);clock[0]+=1
  await e.run_batch(minimum=1,maximum=8)
  ids={x['message_id'] for x in gateway.batch}
  self.assertEqual(len(ids),8);self.assertTrue({'9','10','11'}.issubset(ids))
  self.assertTrue(any(int(mid)<9 for mid in ids));s.close()
 async def test_all_users_short_messages_are_collected_and_interaction_is_upgraded(self):
  s=Store(':memory:');m=Memory(s);e=Extractor(s,m,FakeGateway([]))
  for mid,user,text in [('1','4','I am 19'),('2','5','chess'),('3','6','   ')]:
   s.message_event(kind='create',message_id=mid,guild_id='2',channel_id='3',author_id=user,content=text,created_at=1,changed_at=1)
   self.assertEqual(e.collect(2,user,mid),mid!='3')
  self.assertTrue(e.collect(2,4,1,interaction=True))
  self.assertEqual([tuple(row) for row in s.db.execute('SELECT message_id,user_id,interaction FROM memory_candidates ORDER BY message_id')],[('1','4',1),('2','5',0)])
  s.close()
 async def test_memory_off_clears_buffer_and_old_candidates_are_not_sent(self):
  s=Store(':memory:');m=Memory(s)
  class CaptureGateway:
   def __init__(self):self.batches=[]
   async def complete(self,**kwargs):
    self.batches.append(json.loads(kwargs['messages'][1]['content']))
    return '{"proposals":[],"server_proposals":[]}','request'
  g=CaptureGateway();e=Extractor(s,m,g)
  for mid,user,text in [('1','4','i prefer private hobbies'),('2','5','i like playing chess')]:
   s.message_event(kind='create',message_id=mid,guild_id='2',channel_id='3',author_id=user,content=text,created_at=1,changed_at=1);e.collect(2,user,mid)
  m.set_enabled(2,4,False)
  self.assertEqual(s.db.execute("SELECT count(*) FROM memory_candidates WHERE user_id='4'").fetchone()[0],0)
  # Simulate an old-version leftover or stale imported candidate.
  with s.transaction() as db:db.execute("INSERT INTO memory_candidates(message_id,guild_id,user_id,generation,created_at,interaction) VALUES('1','2','4',0,1,0)")
  await e.run_batch(minimum=1)
  self.assertEqual([x['user_id'] for x in g.batches[0]],['5'])
  m.set_enabled(2,4,True)
  self.assertEqual(s.db.execute("SELECT count(*) FROM memory_candidates WHERE user_id='4'").fetchone()[0],0)
  s.close()
 async def test_small_batch_waits_then_flushes_once_without_new_messages(self):
  s=Store(':memory:');m=Memory(s);clock=[1000.0]
  gateway=FakeGateway([{'user_id':'4','kind':'interest','content':'likes chess','source_ids':['1'],'source_type':'self_report','confidence':.9}])
  extractor=Extractor(s,m,gateway,clock=lambda:clock[0])
  s.message_event(kind='create',message_id='1',guild_id='2',channel_id='3',author_id='4',content='i like playing chess',created_at=999,changed_at=999)
  extractor.collect(2,4,1)
  self.assertEqual(await extractor.run_batch(max_wait=3600),0);self.assertEqual(gateway.calls,0)
  clock[0]+=3600
  self.assertEqual(await extractor.run_batch(max_wait=3600),1);self.assertEqual(gateway.calls,1)
  self.assertEqual(await extractor.run_batch(max_wait=3600),0);self.assertEqual(gateway.calls,1)
  s.close()
 async def test_context_reply_is_provenance_and_invalidates_derived_memory(self):
  from vincent.provenance import invalidate_source
  s=Store(':memory:');m=Memory(s)
  s.message_event(kind='create',message_id='8',guild_id=2,channel_id=3,author_id=9,content='you play chess?',created_at=1,changed_at=1,metadata={'author_bot':True})
  s.message_event(kind='create',message_id='9',guild_id=2,channel_id=3,author_id=4,content='i like playing chess',reply_to='8',created_at=2,changed_at=2)
  g=FakeGateway([{'user_id':'4','kind':'interest','content':'likes chess','source_ids':['9'],'source_type':'self_report','confidence':.9}]);e=Extractor(s,m,g)
  e.collect(2,4,9,interaction=True);self.assertEqual(await e.run_batch(minimum=1),1)
  self.assertEqual(set(json.loads(s.db.execute('SELECT source_ids FROM memories').fetchone()[0])),{'8','9'})
  self.assertEqual(invalidate_source(s,8,deleted=True),1)
  self.assertEqual(m.search(2,4,'chess'),[]);s.close()
 async def test_changed_context_during_extraction_keeps_candidate_pending(self):
  s=Store(':memory:');m=Memory(s)
  base=dict(message_id='8',guild_id=2,channel_id=3,author_id=9,created_at=1,metadata={'author_bot':True})
  s.message_event(kind='create',content='you play chess?',changed_at=1,**base)
  s.message_event(kind='create',message_id='9',guild_id=2,channel_id=3,author_id=4,content='i like playing chess',reply_to='8',created_at=2,changed_at=2)
  class EditingGateway:
   async def complete(self,**kwargs):
    s.message_event(kind='edit',content='you play tennis?',changed_at=3,**base)
    return json.dumps({'proposals':[{'user_id':'4','kind':'interest','content':'likes chess','source_ids':['9'],'source_type':'self_report','confidence':.9}]}),'request'
  e=Extractor(s,m,EditingGateway());e.collect(2,4,9,interaction=True)
  self.assertEqual(await e.run_batch(minimum=1),0)
  self.assertEqual(s.db.execute('SELECT state FROM memory_candidates').fetchone()[0],'pending')
  s.close()
 async def test_batched_provenance_and_no_per_message_call(self):
  s=Store(':memory:');m=Memory(s);g=FakeGateway([{'user_id':'4','kind':'interest','content':'likes chess','source_ids':['1'],'source_type':'self_report','confidence':.9},{'user_id':'5','kind':'fact','content':'stolen fact','source_ids':['1'],'source_type':'self_report','confidence':.9}]);e=Extractor(s,m,g)
  for n in range(1,9):
   s.message_event(kind='create',message_id=str(n),guild_id='2',channel_id='3',author_id='4',content='i like playing chess daily',created_at=n,changed_at=n);e.collect(2,4,n)
  self.assertEqual(g.calls,0);self.assertEqual(await e.run_batch(),1);self.assertEqual(g.calls,1);self.assertEqual(len(m.search(2,4,'chess')),1);self.assertEqual(m.search(2,5,'chess'),[]);s.close()
  self.assertNotIn('max_tokens',g.last_kwargs);self.assertNotIn('reasoning',g.last_kwargs)
 async def test_forget_between_collection_and_extraction(self):
  s=Store(':memory:');m=Memory(s);g=FakeGateway([{'user_id':'4','kind':'interest','content':'likes chess','source_ids':['1'],'source_type':'self_report','confidence':.9}]);e=Extractor(s,m,g)
  s.message_event(kind='create',message_id='1',guild_id='2',channel_id='3',author_id='4',content='i like playing chess',created_at=1,changed_at=1);e.collect(2,4,1);m.forget(2,4)
  self.assertEqual(await e.run_batch(minimum=1),0);self.assertEqual(m.search(2,4,'chess'),[]);s.close()
 async def test_edited_source_is_not_stored_from_stale_batch(self):
  s=Store(':memory:');m=Memory(s)
  s.message_event(kind='create',message_id='1',guild_id='2',channel_id='3',author_id='4',content='i like playing chess',created_at=1,changed_at=1)
  class EditingGateway:
   async def complete(self,**kwargs):
    s.message_event(kind='edit',message_id='1',guild_id='2',channel_id='3',author_id='4',content='i no longer play chess',created_at=1,changed_at=2)
    return json.dumps({'proposals':[{'user_id':'4','kind':'interest','content':'likes chess','source_ids':['1'],'source_type':'self_report','confidence':.9}]}),'request'
  e=Extractor(s,m,EditingGateway());e.collect(2,4,1)
  self.assertEqual(await e.run_batch(minimum=1),0);self.assertEqual(m.search(2,4,'chess'),[]);s.close()
 async def test_short_followup_includes_linked_bot_context_but_not_attribution(self):
  s=Store(':memory:');m=Memory(s)
  s.message_event(kind='create',message_id='8',guild_id=2,channel_id=3,author_id=9,content='you lost again',created_at=1,changed_at=1,metadata={'author_bot':True})
  s.message_event(kind='create',message_id='9',guild_id=2,channel_id=3,author_id=4,content='rude',reply_to='8',created_at=2,changed_at=2)
  class CaptureGateway:
   async def complete(self,**kwargs):
    batch=json.loads(kwargs['messages'][1]['content']);self.batch=batch
    return '{"proposals":[],"server_proposals":[]}','req'
  g=CaptureGateway();e=Extractor(s,m,g)
  self.assertTrue(e.collect(2,4,9,interaction=True));await e.run_batch(minimum=1)
  self.assertEqual(g.batch[0]['bot_reply']['text'],'you lost again');self.assertEqual(g.batch[0]['user_id'],'4');s.close()
if __name__=='__main__':unittest.main()
