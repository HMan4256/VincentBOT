import json,unittest
from vincent.store import Store
from vincent.queue import WorkQueue
from vincent.report import report

class ReportTests(unittest.TestCase):
 def test_summary_has_metrics_without_message_or_identity(self):
  s=Store(':memory:');now=[100];q=WorkQueue(s,lambda:now[0]);s.log('route:1','route',guild_id='secret-guild',author_id='secret-user',occurred_at=100,payload={'action':'direct','reason':'mention','content':'secret-chat'})
  item=q.enqueue(guild_id=1,channel_id=2,author_id='secret-user',branch_id=1,message_id=5);claim=q.claim();now[0]+=3;q.finish(item,'sent',lease_token=claim['lease_token'],response_message_id='6')
  out=report(s,90,110);serialized=json.dumps(out);self.assertNotIn('secret',serialized);self.assertEqual(out['delivery_delay_seconds']['median'],3);self.assertEqual(out['routing'][0]['n'],1);s.close()
if __name__=='__main__':unittest.main()
