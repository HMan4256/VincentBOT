import concurrent.futures, tempfile, unittest
from pathlib import Path
from vincent.store import Store
from vincent.config import Settings
from vincent.budget import Budget,BudgetExceeded

class FoundationTests(unittest.TestCase):
 def test_message_versions_idempotence_and_delete(self):
  s=Store(':memory:');base=dict(message_id='1',guild_id='2',channel_id='3',author_id='4',created_at=1,reply_to=None)
  self.assertTrue(s.message_event(kind='edit',content='new',changed_at=3,**base))
  self.assertTrue(s.message_event(kind='create',content='old',changed_at=1,**base))
  self.assertFalse(s.message_event(kind='create',content='old',changed_at=1,**base))
  self.assertEqual(s.db.execute('SELECT content FROM messages').fetchone()[0],'new')
  # A deletion is authoritative even if the local observation clock is
  # behind the server timestamp of a previously received edit.
  s.message_event(kind='delete',content=None,changed_at=2,**base)
  s.message_event(kind='edit',content='revive',changed_at=5,**base)
  row=s.db.execute('SELECT content,deleted FROM messages').fetchone();self.assertEqual(tuple(row),(None,1));s.close()
 def test_budget_atomic_and_persistent(self):
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/'db';s=Store(path);cfg=Settings(max_daily=10,max_per_minute=100,background_ceiling=3);b=Budget(s,cfg,clock=lambda:100000)
   with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
    def attempt(_):
     try:b.reserve('direct');return 1
     except BudgetExceeded:return 0
    self.assertEqual(sum(pool.map(attempt,range(50))),10)
   s.close();s=Store(path);self.assertEqual(Budget(s,cfg,clock=lambda:100000).status()['local_used'],10);s.close()
 def test_background_limit_does_not_exhaust_direct(self):
  s=Store(':memory:');b=Budget(s,Settings(max_daily=5,max_per_minute=100,background_ceiling=2),clock=lambda:100000)
  b.reserve('memory');b.reserve('observe')
  with self.assertRaises(BudgetExceeded):b.reserve('adaptation')
  b.reserve('direct');self.assertEqual(b.status()['local_used'],3);s.close()
 def test_budget_atomic_across_independent_database_connections(self):
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/'db';stores=[Store(path) for _ in range(4)]
   cfg=Settings(max_daily=10,max_per_minute=100,background_ceiling=3)
   budgets=[Budget(store,cfg,clock=lambda:100000) for store in stores]
   def attempt(i):
    try:budgets[i%len(budgets)].reserve('direct');return 1
    except BudgetExceeded:return 0
   with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
    self.assertEqual(sum(pool.map(attempt,range(60))),10)
   self.assertTrue(all(b.status()['local_used']==10 for b in budgets))
   for store in stores:store.close()
 def test_secret_excluded_from_repr(self):
  self.assertNotIn('secret',repr(Settings(token='secret',api_key='secret')))
if __name__=='__main__':unittest.main()

class DeferredBudgetTests(unittest.TestCase):
 def test_repeated_dst_hour_does_not_reset_budget_twice(self):
  from datetime import datetime
  from zoneinfo import ZoneInfo
  tz=ZoneInfo('Europe/Prague');clock=[datetime(2025,10,26,2,30,tzinfo=tz,fold=0).timestamp()]
  s=Store(':memory:');cfg=Settings(max_daily=1,max_per_minute=10,background_ceiling=0,reset_timezone='Europe/Prague',reset_hour=2);b=Budget(s,cfg,clock=lambda:clock[0])
  b.reserve('direct');clock[0]=datetime(2025,10,26,2,15,tzinfo=tz,fold=1).timestamp()
  with self.assertRaises(BudgetExceeded) as ctx:b.reserve('direct')
  expected=datetime(2025,10,27,2,0,tzinfo=tz).timestamp()
  self.assertEqual(b.next_reset(clock[0]),expected);self.assertAlmostEqual(ctx.exception.retry_after,expected-clock[0])
  clock[0]=expected;self.assertTrue(b.reserve('direct'));s.close()
 def test_skipped_dst_hour_does_not_shift_following_days_reset(self):
  from datetime import datetime
  from zoneinfo import ZoneInfo
  tz=ZoneInfo('Europe/Prague');now=datetime(2025,3,30,3,30,tzinfo=tz).timestamp()
  s=Store(':memory:');b=Budget(s,Settings(reset_timezone='Europe/Prague',reset_hour=2),clock=lambda:now)
  self.assertEqual(b.day_start(now),datetime(2025,3,30,3,0,tzinfo=tz).timestamp())
  self.assertEqual(b.next_reset(now),datetime(2025,3,31,2,0,tzinfo=tz).timestamp());s.close()
 def test_rolling_minute_has_retry_time(self):
  s=Store(':memory:');clock=[100000.0];b=Budget(s,Settings(max_daily=10,max_per_minute=1,background_ceiling=2),clock=lambda:clock[0]);b.reserve('direct')
  with self.assertRaises(BudgetExceeded) as ctx:b.reserve('direct')
  self.assertGreaterEqual(ctx.exception.retry_after,60);clock[0]+=ctx.exception.retry_after;b.reserve('direct');s.close()
