import unittest
from vincent.store import Store
from vincent.profiles import Profiles
from vincent.memory import Memory

class ProfileTests(unittest.TestCase):
 def test_interactions_are_idempotent_and_forget_disables_inflight_recreation(self):
  s=Store(':memory:');p=Profiles(s);m=Memory(s)
  self.assertTrue(p.incoming(1,2,10,message_id='10'));self.assertFalse(p.incoming(1,2,10,message_id='10'))
  self.assertTrue(p.outgoing(1,2,11,message_id='20'));self.assertFalse(p.outgoing(1,2,11,message_id='20'))
  self.assertEqual((p.get(1,2)['incoming_count'],p.get(1,2)['outgoing_count']),(1,1))
  m.forget(1,2);self.assertIsNone(p.get(1,2));self.assertFalse(p.outgoing(1,2,12,message_id='21'));s.close()
if __name__=='__main__':unittest.main()
