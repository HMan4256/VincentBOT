import unittest
from vincent.store import Store
from vincent.config import Settings
from vincent.flags import FeatureFlags
from vincent.queue import WorkQueue

class FlagTests(unittest.TestCase):
 def test_channel_disable_cancels_inflight_observe_and_preserves_other_channels(self):
  s=Store(':memory:');settings=Settings(unsolicited_enabled=False);flags=FeatureFlags(s,settings);q=WorkQueue(s)
  flags.set(1,2,'unsolicited',True,actor=8);self.assertTrue(flags.enabled(1,2,'unsolicited'));self.assertFalse(flags.enabled(1,3,'unsolicited'))
  item=q.enqueue(guild_id=1,channel_id=2,author_id=3,branch_id=4,message_id=5,priority=1,reason='observe');claim=q.claim()
  flags.set(1,2,'unsolicited',False,actor=8);self.assertFalse(q.fresh(item,lease_token=claim['lease_token']))
  self.assertEqual(s.db.execute('SELECT status FROM queue').fetchone()[0],'cancelled');s.close()
if __name__=='__main__':unittest.main()
