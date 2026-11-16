import json,unittest
from vincent.store import Store
from vincent.memory import Memory
from vincent.context import build_context

class ContextTests(unittest.TestCase):
 def test_target_and_ancestors_survive_busy_channel_window(self):
  s=Store(':memory:');m=Memory(s)
  for i in range(25):
   s.message_event(kind='create',message_id=str(i+1),guild_id='2',channel_id='3',author_id='4',content=f'message {i}',reply_to='1' if i==1 else None,created_at=i,changed_at=i)
  item={'source_ids':json.dumps(['2']),'guild_id':'2','channel_id':'3','author_id':'4','priority':20,'reason':'direct'}
  context=build_context(s,m,item,recent_limit=2);self.assertEqual({r['message_id'] for r in context['recent_messages']},{'1','2','24','25'})
  s.message_event(kind='delete',message_id='2',guild_id='2',channel_id='3',author_id='4',content=None,created_at=1,changed_at=50)
  self.assertIsNone(build_context(s,m,item));s.close()
 def test_changed_reply_parent_invalidates_inflight_context(self):
  from vincent.queue import WorkQueue
  s=Store(':memory:');m=Memory(s);q=WorkQueue(s)
  for mid,text,ref,t in [('1','old parent',None,1),('2','reply','1',2)]:
   s.message_event(kind='create',message_id=mid,guild_id='2',channel_id='3',author_id='4',content=text,reply_to=ref,created_at=t,changed_at=t)
  item_id=q.enqueue(guild_id=2,channel_id=3,author_id=4,branch_id=1,message_id=2);item=q.claim();versions=build_context(s,m,item)['_input_versions']
  s.message_event(kind='edit',message_id='1',guild_id='2',channel_id='3',author_id='4',content='edited parent',created_at=1,changed_at=5)
  self.assertFalse(q.fresh(item_id,lease_token=item['lease_token'],expected_versions=versions));self.assertTrue(q.retry_stale(item_id,lease_token=item['lease_token']))
  self.assertEqual(s.db.execute('SELECT status FROM queue').fetchone()[0],'pending');s.close()
 def test_profile_disable_invalidates_inflight_context(self):
  from vincent.queue import WorkQueue
  s=Store(':memory:');m=Memory(s);q=WorkQueue(s)
  s.message_event(kind='create',message_id='1',guild_id='2',channel_id='3',author_id='4',content='ask about chess',created_at=1,changed_at=1)
  mid=q.enqueue(guild_id=2,channel_id=3,author_id=4,branch_id=1,message_id=1);item=q.claim();ctx=build_context(s,m,item)
  m.set_enabled(2,4,False)
  self.assertFalse(q.fresh(mid,lease_token=item['lease_token'],memory_generation=ctx['_memory_generation'],memory_versions=ctx['_memory_versions']))
  self.assertTrue(q.retry_stale(mid,lease_token=item['lease_token']));s.close()
 def test_clear_cancels_work_and_hides_old_ancestors_without_deleting_logs(self):
  from vincent.context import clear_channel
  from vincent.queue import WorkQueue
  s=Store(':memory:');m=Memory(s);q=WorkQueue(s,clock=lambda:10)
  s.message_event(kind='create',message_id='1',guild_id='2',channel_id='3',author_id='4',content='old',created_at=1,changed_at=1)
  old=q.enqueue(guild_id=2,channel_id=3,author_id=4,branch_id=1,message_id=1);claim=q.claim();clear_channel(s,2,3,actor=8,now=5)
  self.assertFalse(q.fresh(old,lease_token=claim['lease_token']));self.assertEqual(s.db.execute('SELECT content FROM messages WHERE message_id=1').fetchone()[0],'old')
  s.message_event(kind='create',message_id='2',guild_id='2',channel_id='3',author_id='4',content='new',reply_to='1',created_at=6,changed_at=6)
  q.enqueue(guild_id=2,channel_id=3,author_id=4,branch_id=2,message_id=2);item=q.claim();ctx=build_context(s,m,item)
  self.assertEqual([r['message_id'] for r in ctx['recent_messages']],['2']);s.close()
if __name__=='__main__':unittest.main()
