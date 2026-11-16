import json,unittest
from vincent.store import Store
from vincent.queue import WorkQueue
from vincent.router import Router

class RoutingTests(unittest.TestCase):
 def test_another_user_can_answer_recent_channel_prompt(self):
  r=Router('9');bot={'message_id':'8','target_user_id':'3','created_at':100,'branch_id':'branch','content':"you need to admit the toaster won"}
  message={'message_id':'10','author_id':'4','created_at':116,'content':'okay i admit it, the toaster won','mentions':[]}
  route=r.route(message,recent_channel_bot=bot)
  self.assertEqual((route.action,route.reason,route.branch_id),('direct','channel_prompt_answer','branch'))
  self.assertEqual(r.route(message,recent_channel_bot={**bot,'interrupted':True}).reason,'channel_prompt_answer')
  self.assertEqual(r.route({**message,'created_at':131},recent_channel_bot=bot).action,'ignore')
  self.assertEqual(r.route({**message,'content':'i like playing chess'},recent_channel_bot=bot).action,'ignore')
  self.assertEqual(r.route({**message,'reply_to':'5'},referenced={'author_id':'5'},recent_channel_bot=bot).action,'ignore')
 def test_ambiguous_answer_to_question_stays_a_candidate(self):
  r=Router('9');bot={'message_id':'8','target_user_id':'3','created_at':100,'branch_id':'branch','content':'are you serious?'}
  message={'message_id':'10','author_id':'4','created_at':110,'content':'nah','mentions':[]}
  self.assertEqual(r.route(message,recent_channel_bot=bot).reason,'channel_question_answer')
 def test_followup_and_reply_to_other(self):
  r=Router('9');m={'message_id':'10','author_id':'3','created_at':110,'content':'unfortunately'}
  b={'message_id':'8','target_user_id':'3','created_at':100,'branch_id':'7'}
  self.assertEqual(r.route(m,recent_bot=b).reason,'recent_participant_followup')
  self.assertEqual(r.route(m,recent_bot=b,referenced={'author_id':'4'}).action,'ignore')
  self.assertEqual(r.route(m,referenced={'author_id':'9'}).action,'direct')
 def test_queue_separates_people_and_recovers_lease(self):
  s=Store(':memory:');now=[100];q=WorkQueue(s,lambda:now[0]);base=dict(guild_id=1,channel_id=2,branch_id=3)
  a=q.enqueue(**base,author_id=4,message_id=10);self.assertEqual(q.enqueue(**base,author_id=4,message_id=11),a)
  b=q.enqueue(**base,author_id=5,message_id=12);self.assertNotEqual(a,b)
  item=q.claim(lease_seconds=10);self.assertEqual(item['id'],a);self.assertEqual(json.loads(item['source_ids']),['10','11']);self.assertIsNone(q.claim())
  now[0]+=11;new=q.claim();self.assertEqual(new['id'],a);self.assertFalse(q.begin_send(a,'stale',lease_token=item['lease_token']));q.finish(a,'sent',lease_token=new['lease_token'],response_message_id='100');self.assertEqual(q.claim()['id'],b);s.close()
 def test_rate_limited_item_waits_then_retries_without_changing_mode(self):
  s=Store(':memory:');now=[100];q=WorkQueue(s,lambda:now[0])
  item=q.enqueue(guild_id=1,channel_id=2,author_id=3,branch_id=4,message_id=5,priority=1,reason='observe',ttl=120)
  old=q.claim();self.assertTrue(q.defer(item,lease_token=old['lease_token'],delay=10,reason='rolling minute'))
  self.assertIsNone(q.claim());now[0]+=10;new=q.claim();self.assertEqual(new['id'],item);self.assertEqual(new['reason'],'observe')
  self.assertFalse(q.begin_send(item,'late worker',lease_token=old['lease_token']));s.close()
 def test_unsolicited_draft_discarded_if_channel_moves_on(self):
  s=Store(':memory:');now=[100];q=WorkQueue(s,lambda:now[0])
  for mid,t in [('1',90),('2',95)]:
   if mid=='2':continue
   s.message_event(kind='create',message_id=mid,guild_id=1,channel_id=2,author_id=3,content='hello there',created_at=t,changed_at=t)
  item=q.enqueue(guild_id=1,channel_id=2,author_id=3,branch_id=1,message_id=1,priority=1,reason='observe');claim=q.claim()
  self.assertTrue(q.fresh(item,lease_token=claim['lease_token']))
  s.message_event(kind='create',message_id='2',guild_id=1,channel_id=2,author_id=4,content='new topic',created_at=95,changed_at=95)
  self.assertFalse(q.fresh(item,lease_token=claim['lease_token']));s.close()
 def test_edit_invalidates_claim_and_delete_keeps_other_merged_source(self):
  s=Store(':memory:');now=[100];q=WorkQueue(s,lambda:now[0]);args=dict(guild_id=1,channel_id=2,author_id=3,branch_id=4)
  item=q.enqueue(**args,message_id=5);q.enqueue(**args,message_id=6);old=q.claim()
  q.source_changed(5);self.assertFalse(q.fresh(item,lease_token=old['lease_token']));new=q.claim()
  self.assertNotEqual(old['lease_token'],new['lease_token']);q.source_changed(5,deleted=True)
  row=s.db.execute('SELECT status,source_ids FROM queue WHERE id=?',(item,)).fetchone();self.assertEqual(row['status'],'pending');self.assertEqual(json.loads(row['source_ids']),['6'])
  q.source_changed(6,deleted=True);self.assertEqual(s.db.execute('SELECT status FROM queue').fetchone()[0],'cancelled');s.close()
if __name__=='__main__':unittest.main()
