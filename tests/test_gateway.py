import unittest
import httpx
from vincent.store import Store
from vincent.config import Settings
from vincent.budget import Budget
from vincent.llm import Gateway,ModelError

class GatewayTests(unittest.IsolatedAsyncioTestCase):
 async def test_all_request_types_use_configured_output_limits(self):
  import json
  s=Store(':memory:');cfg=Settings(max_daily=10,max_per_minute=10,background_ceiling=8,max_output_tokens=12345,background_max_output_tokens=23456);seen=[]
  def handler(request):
   seen.append((json.loads(request.content)['max_tokens'],request.extensions['timeout']['read']))
   return httpx.Response(200,json={'choices':[{'message':{'content':'ok'}}]})
  async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
   gateway=Gateway(s,Budget(s,cfg),cfg,client)
   for kind in ('direct','observe','memory','adaptation'):await gateway.complete(kind=kind,messages=[])
  self.assertEqual(seen,[(12345,90),(12345,90),(23456,240),(23456,240)]);s.close()
 async def test_empty_reasoning_only_response_records_finish_reason_and_usage(self):
  s=Store(':memory:');cfg=Settings(max_daily=5,max_per_minute=5,background_ceiling=2)
  def handler(request):
   import json
   self.assertEqual(json.loads(request.content)['reasoning'],{'enabled':False})
   self.assertEqual(request.extensions['timeout']['read'],240)
   return httpx.Response(200,json={'choices':[{'finish_reason':'length','message':{'content':None,'reasoning':'private reasoning'}}],'usage':{'completion_tokens':1500}})
  async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
   with self.assertRaisesRegex(ModelError,'empty content'):
    await Gateway(s,Budget(s,cfg),cfg,client).complete(kind='memory',messages=[],reasoning={'enabled':False})
  row=s.db.execute('SELECT a.metadata,r.error FROM request_attempts a JOIN requests_log r USING(request_id)').fetchone()
  self.assertEqual(row['error'],'empty_content');self.assertIn('length',row['metadata']);self.assertNotIn('private reasoning',row['metadata']);s.close()
 async def test_retry_counts_every_http_attempt_and_logs_no_key(self):
  s=Store(':memory:');cfg=Settings(api_key='sensitive-secret',max_daily=10,max_per_minute=10,background_ceiling=3);attempt=[0]
  def handler(request):
   attempt[0]+=1
   if attempt[0]==1:return httpx.Response(429,headers={'Retry-After':'0'})
   return httpx.Response(200,json={'choices':[{'message':{'content':'hello'}}], 'usage':{'total_tokens':10}})
  async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
   g=Gateway(s,Budget(s,cfg),cfg,client);content,_=await g.complete(kind='direct',messages=[{'role':'user','content':'hello'}])
  self.assertEqual(content,'hello');self.assertEqual(attempt[0],2);self.assertEqual(Budget(s,cfg).status()['local_used'],2)
  self.assertNotIn('sensitive-secret',repr([tuple(r) for r in s.db.execute('SELECT * FROM requests_log')]));s.close()
 async def test_http_failure_is_counted(self):
  s=Store(':memory:');cfg=Settings(max_daily=5,max_per_minute=5,background_ceiling=2)
  async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(500))) as client:
   with self.assertRaises(ModelError):await Gateway(s,Budget(s,cfg),cfg,client).complete(kind='direct',messages=[])
  self.assertEqual(s.db.execute('SELECT status FROM request_attempts').fetchone()[0],'failed');s.close()
if __name__=='__main__':unittest.main()

class QuotaSnapshotTests(unittest.IsolatedAsyncioTestCase):
 async def test_slow_older_quota_request_cannot_overwrite_newer_snapshot(self):
  import asyncio
  s=Store(':memory:');cfg=Settings(max_daily=10,max_per_minute=10,background_ceiling=3);budget=Budget(s,cfg)
  started=asyncio.Event();release=asyncio.Event();calls=[0]
  async def handler(req):
   calls[0]+=1;number=calls[0]
   if number==1:
    started.set();await release.wait()
   return httpx.Response(200,json={'data':{'free_model_daily_requests':{'remaining':9 if number==1 else 1,'limit':1000}}})
  async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
   gateway=Gateway(s,budget,cfg,client)
   old=asyncio.create_task(gateway.refresh_quota());await started.wait()
   await gateway.refresh_quota();release.set();await old
  self.assertEqual(budget.status()['provider_remaining'],1)
  s.close()
 async def test_remote_snapshot_is_conservative_and_explicit(self):
  from vincent.budget import BudgetExceeded
  s=Store(':memory:');cfg=Settings(max_daily=10,max_per_minute=10,background_ceiling=3);budget=Budget(s,cfg)
  def handler(req):return httpx.Response(200,json={'data':{'free_model_daily_requests':{'remaining':1,'limit':1000,'used':999}}})
  async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
   snapshot=await Gateway(s,budget,cfg,client).refresh_quota()
  self.assertEqual(snapshot['remaining'],1);self.assertEqual(budget.status()['provider_remaining'],1)
  budget.reserve('direct')
  with self.assertRaises(BudgetExceeded):budget.reserve('direct')
  s.close()

class RateLimitTests(unittest.IsolatedAsyncioTestCase):
 async def test_unspecified_429_waits_without_immediate_retries(self):
  from vincent.llm import ProviderRateLimited
  s=Store(':memory:');cfg=Settings(max_daily=5,max_per_minute=5,background_ceiling=2);calls=[0]
  def handler(request):calls[0]+=1;return httpx.Response(429)
  async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
   with self.assertRaises(ProviderRateLimited) as ctx:await Gateway(s,Budget(s,cfg),cfg,client).complete(kind='direct',messages=[])
  self.assertEqual(ctx.exception.retry_after,30);self.assertEqual(calls[0],1);s.close()
 def test_rate_limit_headers_support_dates_and_provider_reset(self):
  from vincent.llm import rate_limit_delay
  from email.utils import formatdate
  now=1700000000
  self.assertEqual(rate_limit_delay(httpx.Headers({'Retry-After':formatdate(now+45,usegmt=True)}),now),45)
  self.assertEqual(rate_limit_delay(httpx.Headers({'Retry-After':'5','X-RateLimit-Reset':str((now+20)*1000)}),now),20)
 async def test_daily_429_does_not_retry_or_expose_error_payload(self):
  from vincent.llm import ProviderRateLimited,seconds_until_utc_reset
  from vincent.budget import BudgetExceeded
  import time
  s=Store(':memory:');cfg=Settings(max_daily=5,max_per_minute=5,background_ceiling=2)
  async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(429,json={'error':{'metadata':{'limit_source':'openrouter_free_tier_daily'}}}))) as client:
   gateway=Gateway(s,Budget(s,cfg),cfg,client);before=time.time()
   with self.assertRaises(ProviderRateLimited) as ctx:await gateway.complete(kind='direct',messages=[])
   self.assertGreaterEqual(ctx.exception.retry_after,seconds_until_utc_reset(before))
   with self.assertRaises(BudgetExceeded):await gateway.complete(kind='memory',messages=[])
  self.assertEqual(s.db.execute('SELECT count(*) FROM request_attempts').fetchone()[0],1);self.assertIsNotNone(Budget(s,cfg).status()['provider_blocked_until']);s.close()

 async def test_daily_provider_block_survives_database_reopen(self):
  import tempfile
  from pathlib import Path
  from vincent.budget import BudgetExceeded
  from vincent.llm import ProviderRateLimited
  with tempfile.TemporaryDirectory() as directory:
   path=Path(directory)/'db';s=Store(path);cfg=Settings(max_daily=5,max_per_minute=5,background_ceiling=2)
   async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(429,json={'error':{'metadata':{'limit_source':'openrouter_free_tier_daily'}}}))) as client:
    with self.assertRaises(ProviderRateLimited):await Gateway(s,Budget(s,cfg),cfg,client).complete(kind='direct',messages=[])
   s.close();s=Store(path)
   with self.assertRaises(BudgetExceeded):Budget(s,cfg).reserve('adaptation')
   s.close()

class CancellationTests(unittest.IsolatedAsyncioTestCase):
 async def test_cancelled_request_remains_counted_and_logged(self):
  import asyncio
  s=Store(':memory:');cfg=Settings(max_daily=5,max_per_minute=5,background_ceiling=2);started=asyncio.Event()
  async def handler(request):
   started.set();await asyncio.Event().wait()
  async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
   task=asyncio.create_task(Gateway(s,Budget(s,cfg),cfg,client).complete(kind='direct',messages=[]));await started.wait();task.cancel()
   with self.assertRaises(asyncio.CancelledError):await task
  self.assertEqual(s.db.execute('SELECT status FROM request_attempts').fetchone()[0],'cancelled')
  self.assertEqual(s.db.execute("SELECT count(*) FROM events WHERE kind='request_cancelled'").fetchone()[0],1);s.close()

class GroupTraceTests(unittest.IsolatedAsyncioTestCase):
 async def test_every_retry_records_all_group_members(self):
  from unittest.mock import patch,AsyncMock
  from vincent.queue import WorkQueue
  s=Store(':memory:');cfg=Settings(max_daily=10,max_per_minute=10,background_ceiling=3);q=WorkQueue(s)
  ids=[q.enqueue(guild_id=1,channel_id=2,author_id=i,branch_id=i,message_id=i) for i in (1,2)];calls=[0]
  def handler(request):
   calls[0]+=1
   if calls[0]==1:return httpx.Response(500)
   return httpx.Response(200,json={'choices':[{'message':{'content':'ok'}}]})
  async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
   with patch('vincent.llm.asyncio.sleep',new_callable=AsyncMock):
    await Gateway(s,Budget(s,cfg),cfg,client).complete(kind='direct',messages=[],queue_id=ids[0],queue_ids=ids)
  self.assertEqual(calls[0],2);self.assertEqual(s.db.execute('SELECT count(*) FROM group_request_members').fetchone()[0],4);s.close()

class MalformedRateLimitTests(unittest.IsolatedAsyncioTestCase):
 async def test_bad_provider_metadata_does_not_crash_or_retry_unboundedly(self):
  from vincent.llm import ProviderRateLimited
  s=Store(':memory:');cfg=Settings(max_daily=5,max_per_minute=5,background_ceiling=2)
  async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(429,headers={'Retry-After':'NaN'},json={'error':{'metadata':['unexpected']}}))) as client:
   with self.assertRaises(ProviderRateLimited) as ctx:await Gateway(s,Budget(s,cfg),cfg,client).complete(kind='direct',messages=[])
  self.assertEqual(ctx.exception.retry_after,60);self.assertEqual(s.db.execute('SELECT count(*) FROM request_attempts').fetchone()[0],1);s.close()
