"""Single gateway for budgeted OpenRouter requests."""
import asyncio
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import hashlib
import math
import time
import httpx
from .store import json_text


class ModelError(Exception):
    pass


class ProviderRateLimited(ModelError):
    def __init__(self,retry_after):
        super().__init__('Provider rate limited')
        self.retry_after=max(1.0,float(retry_after))


def seconds_until_utc_reset(now):
    current=datetime.fromtimestamp(now,timezone.utc)
    tomorrow=current.date()+timedelta(days=1)
    return datetime(tomorrow.year,tomorrow.month,tomorrow.day,tzinfo=timezone.utc).timestamp()-now


def rate_limit_delay(headers,now):
    delays=[];retry=headers.get('Retry-After')
    if retry is not None:
        try:delay=float(retry)
        except ValueError:
            try:
                parsed=parsedate_to_datetime(retry)
                if parsed.tzinfo is None:parsed=parsed.replace(tzinfo=timezone.utc)
                delay=parsed.timestamp()-now
            except (TypeError,ValueError,OverflowError):delay=60
        if not math.isfinite(delay) or delay<0:delay=60
        delays.append(delay)
    reset=headers.get('X-RateLimit-Reset')
    if reset is not None:
        try:
            delay=float(reset)/1000-now
            if math.isfinite(delay) and delay>0:delays.append(delay)
        except ValueError:pass
    return max(delays) if delays else 30


class Gateway:
    def __init__(self, store, budget, settings, client):
        self.store,self.budget,self.settings,self.client=store,budget,settings,client

    async def complete(self, *, kind, messages, queue_id=None, queue_ids=None, prompt_version=None, temperature=1.0, max_tokens=None, retries=1, reasoning=None):
        if max_tokens is None:
            max_tokens=self.settings.background_max_output_tokens if kind in ('memory','adaptation') else self.settings.max_output_tokens
        payload={'model':self.settings.model,'messages':messages,'temperature':temperature,'max_tokens':max_tokens}
        if reasoning is not None:payload['reasoning']=reasoning
        prompt_hash=hashlib.sha256(json_text(messages).encode()).hexdigest()
        for attempt in range(retries+1):
            request_id=self.budget.reserve(kind)
            now=time.time()
            with self.store.transaction() as db:
                db.execute('INSERT INTO requests_log(request_id,queue_id,model,prompt_version,prompt_hash,payload,created_at) VALUES(?,?,?,?,?,?,?)',
                           (request_id,queue_id,self.settings.model,prompt_version,prompt_hash,json_text(payload),now))
                if queue_ids:
                    db.executemany('INSERT OR IGNORE INTO group_request_members(request_id,queue_id) VALUES(?,?)',[(request_id,int(member)) for member in queue_ids])
            try:
                kwargs={'timeout':240 if kind in ('memory','adaptation') else 90}
                response=await self.client.post('https://openrouter.ai/api/v1/chat/completions',headers={'Authorization':f'Bearer {self.settings.api_key}'},json=payload,**kwargs)
                if response.status_code==429:
                    try: details=response.json().get('error',{}).get('metadata',{}) or {}
                    except (ValueError,AttributeError):details={}
                    if not isinstance(details,dict):details={}
                    delay=rate_limit_delay(response.headers,time.time())
                    if details.get('limit_source')=='openrouter_free_tier_daily':
                        delay=max(delay,seconds_until_utc_reset(time.time())+2)
                        self.budget.block_provider_until(self.budget.clock()+delay,'free-model daily limit')
                    self._record(request_id,'rate_limited',error='HTTP 429',metadata={'http_status':429,'retry_after':delay})
                    if attempt < retries and delay<=10 and details.get('limit_source')!='openrouter_free_tier_daily':
                        await asyncio.sleep(max(0,delay));continue
                    raise ProviderRateLimited(delay)
                if 500<=response.status_code<=599 and attempt<retries:
                    self._record(request_id,'failed',error='Provider server error',metadata={'http_status':response.status_code})
                    await asyncio.sleep(1);continue
                response.raise_for_status()
                data=response.json(); choice=data['choices'][0];content=choice['message']['content']
                if not isinstance(content,str) or not content.strip():
                    reason=choice.get('finish_reason')
                    self._record(request_id,'failed',error='empty_content',metadata={'http_status':response.status_code,'finish_reason':reason,'usage':data.get('usage',{})})
                    raise ModelError(f'Provider returned empty content (finish_reason={reason})')
                self._record(request_id,'success',content=content,metadata={'http_status':response.status_code,'finish_reason':choice.get('finish_reason'),'usage':data.get('usage',{})})
                return content,request_id
            except asyncio.CancelledError:
                with self.store.lock:
                    current=self.store.db.execute('SELECT status FROM request_attempts WHERE request_id=?',(request_id,)).fetchone()
                if current and current['status']=='reserved':
                    self._record(request_id,'cancelled',error='Task cancelled',metadata={'error_type':'CancelledError'})
                self.store.log(f'request_cancelled:{request_id}','request_cancelled',payload={'request_id':request_id})
                raise
            except httpx.TransportError as exc:
                self._record(request_id,'failed',error=type(exc).__name__,metadata={'error_type':type(exc).__name__})
                if attempt<retries:
                    await asyncio.sleep(1);continue
                raise ModelError('Model transport failed: '+type(exc).__name__) from exc
            except (httpx.HTTPError,ValueError,KeyError,IndexError,TypeError) as exc:
                self._record(request_id,'failed',error=type(exc).__name__,metadata={'error_type':type(exc).__name__})
                raise ModelError(f'Model request failed: {type(exc).__name__}') from exc
        raise ModelError('Model request failed')

    async def refresh_quota(self):
        # Key-status request is not a chat completion; the result is a snapshot.
        observed_at=time.time()
        try:
            response=await self.client.get('https://openrouter.ai/api/v1/key',headers={'Authorization':f'Bearer {self.settings.api_key}'},timeout=10)
            response.raise_for_status();quota=response.json()['data']['free_model_daily_requests']
            remaining,limit=quota['remaining'],quota['limit'];used=quota.get('used')
            if type(remaining) is not int or type(limit) is not int or limit<=0 or remaining<0 or remaining>limit:raise ValueError('Invalid provider quota')
            if type(used) is not int or used<0:used=None
            with self.store.transaction() as db:
                # Requests can overlap; a slow older response must not replace
                # a more recent observation of the provider's quota.
                db.execute('''INSERT INTO provider_quota(id,observed_at,remaining,quota_limit,used) VALUES(1,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET observed_at=excluded.observed_at,remaining=excluded.remaining,
                    quota_limit=excluded.quota_limit,used=excluded.used
                    WHERE excluded.observed_at>provider_quota.observed_at''',(observed_at,remaining,limit,used))
            return {'remaining':remaining,'limit':limit,'used':used,'observed_at':observed_at}
        except (httpx.HTTPError,ValueError,TypeError,KeyError) as exc:
            raise ModelError('Unable to read provider quota: '+type(exc).__name__) from exc

    def _record(self,request_id,status,content=None,error=None,metadata=None):
        with self.store.transaction() as db:
            db.execute('UPDATE request_attempts SET status=?,finished_at=?,metadata=? WHERE request_id=?',(status,time.time(),json_text(metadata or {}),request_id))
            db.execute('UPDATE requests_log SET response=?,error=?,finished_at=? WHERE request_id=?',(content,error,time.time(),request_id))
