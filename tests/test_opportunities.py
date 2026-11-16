import unittest
from vincent.store import Store
from vincent.opportunities import Opportunities
from vincent.queue import WorkQueue

class OpportunityTests(unittest.TestCase):
 def test_debounce_once_and_cooldown(self):
  s=Store(':memory:');t=[1000];o=Opportunities(s,lambda:t[0],settle_seconds=10,cooldown_seconds=30)
  args=dict(guild_id='1',channel_id='2',author_id='3',message_id='4',content='bro what happened here?',branch_id='5')
  self.assertTrue(o.observe(**args));self.assertIsNone(o.next());t[0]+=10;self.assertIsNotNone(o.next());self.assertIsNone(o.next());o.sent('2')
  self.assertFalse(o.observe(**args));args['message_id']='6';self.assertTrue(o.observe(**args))
  t[0]+=10;self.assertIsNone(o.next());t[0]+=20;self.assertIsNotNone(o.next())
  args['message_id']='4';self.assertFalse(o.observe(**args));self.assertIsNone(o.next());s.close()
 def test_direct_queue_suppresses_opportunity(self):
  s=Store(':memory:');t=[1000];o=Opportunities(s,lambda:t[0],settle_seconds=0);q=WorkQueue(s,lambda:t[0])
  o.observe(guild_id=1,channel_id=2,author_id=3,message_id=4,content='bro what is going on?');q.enqueue(guild_id=1,channel_id=2,author_id=3,branch_id=4,message_id=4)
  self.assertIsNone(o.next());self.assertEqual(s.db.execute('SELECT state FROM opportunities').fetchone()[0],'suppressed');s.close()
if __name__=='__main__':unittest.main()
