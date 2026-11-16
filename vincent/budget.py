"""Durable request accounting shared by all model calls."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import time
import uuid
from .store import json_text


class BudgetExceeded(Exception):
    def __init__(self, message, retry_after):
        super().__init__(message)
        self.retry_after=max(1.0,float(retry_after))


class Budget:
    def __init__(self, store, settings, clock=time.time):
        self.store, self.settings, self.clock = store, settings, clock

    def day_start(self, now):
        tz=ZoneInfo(self.settings.reset_timezone)
        day=datetime.fromtimestamp(now,tz).date()
        start=self._reset_on(day,tz)
        if now<start:start=self._reset_on(day-timedelta(days=1),tz)
        return start

    def _reset_on(self,day,tz):
        # Use the first occurrence of an ambiguous hour. For a nonexistent
        # spring-forward hour, zoneinfo maps it to the first valid hour after
        # the gap. Compare timestamps, never ambiguous local wall times.
        return datetime(day.year,day.month,day.day,self.settings.reset_hour,tzinfo=tz,fold=0).timestamp()

    def next_reset(self,now):
        tz=ZoneInfo(self.settings.reset_timezone)
        day=datetime.fromtimestamp(now,tz).date()
        reset=self._reset_on(day,tz)
        while reset<=now:
            day+=timedelta(days=1);reset=self._reset_on(day,tz)
        return reset

    def reserve(self, kind):
        if kind not in {'direct','observe','memory','adaptation'}:
            raise ValueError('Unknown request kind')
        now=self.clock(); request_id=uuid.uuid4().hex
        with self.store.transaction() as db:
            blocked=db.execute('SELECT blocked_until,reason FROM provider_request_blocks WHERE id=1').fetchone()
            if blocked and blocked['blocked_until']>now:raise BudgetExceeded('Provider requests paused: '+blocked['reason'],blocked['blocked_until']-now)
            rows=db.execute('SELECT kind FROM request_attempts WHERE reserved_at>=?',(self.day_start(now),)).fetchall()
            minute=db.execute('SELECT count(*),min(reserved_at) FROM request_attempts WHERE reserved_at>?',(now-60,)).fetchone()
            background=sum(r['kind']!='direct' for r in rows)
            reset=self.next_reset(now)
            daily_wait=max(1,reset-now)
            if len(rows)>=self.settings.max_daily:raise BudgetExceeded('Local daily request limit reached',daily_wait)
            if minute[0]>=self.settings.max_per_minute:raise BudgetExceeded('Rolling-minute request limit reached',minute[1]+60-now+0.01)
            remote=db.execute('SELECT observed_at,remaining FROM provider_quota WHERE id=1').fetchone()
            if remote and remote['observed_at']>=self.day_start(now) and now-remote['observed_at']<=1800:
                since=db.execute('SELECT count(*) FROM request_attempts WHERE reserved_at>=?',(remote['observed_at'],)).fetchone()[0]
                if remote['remaining']-since<=0:raise BudgetExceeded('Last provider quota reading exhausted',max(1,remote['observed_at']+1801-now))
            if kind!='direct' and background>=self.settings.background_ceiling:
                raise BudgetExceeded('Background request allocation exhausted',daily_wait)
            category_limit={'observe':self.settings.observe_limit,'memory':self.settings.memory_limit,'adaptation':self.settings.adaptation_limit}.get(kind)
            if category_limit is not None and sum(r['kind']==kind for r in rows)>=category_limit:
                raise BudgetExceeded(f'{kind} request allocation exhausted',daily_wait)
            db.execute('INSERT INTO request_attempts(request_id,kind,reserved_at,status) VALUES(?,?,?,?)',(request_id,kind,now,'reserved'))
        return request_id

    def block_provider_until(self,until,reason):
        now=self.clock()
        with self.store.transaction() as db:
            db.execute('''INSERT INTO provider_request_blocks(id,blocked_until,reason,observed_at) VALUES(1,?,?,?)
                ON CONFLICT(id) DO UPDATE SET blocked_until=max(provider_request_blocks.blocked_until,excluded.blocked_until),
                reason=excluded.reason,observed_at=excluded.observed_at''',(until,reason,now))

    def finish(self, request_id, status, metadata=None):
        with self.store.transaction() as db:
            db.execute('UPDATE request_attempts SET status=?,finished_at=?,metadata=? WHERE request_id=?',
                       (status,self.clock(),json_text(metadata or {}),request_id))

    def status(self):
        now=self.clock()
        with self.store.lock:
            rows=self.store.db.execute('SELECT kind,count(*) AS n FROM request_attempts WHERE reserved_at>=? GROUP BY kind',(self.day_start(now),)).fetchall()
        counts={r['kind']:r['n'] for r in rows}
        with self.store.lock:
            remote=self.store.db.execute('SELECT * FROM provider_quota WHERE id=1').fetchone()
            blocked=self.store.db.execute('SELECT blocked_until,reason FROM provider_request_blocks WHERE id=1').fetchone()
        return {'local_used':sum(counts.values()),'local_limit':self.settings.max_daily,'by_kind':counts,'provider_remaining':remote['remaining'] if remote else None,'provider_observed_at':remote['observed_at'] if remote else None,'provider_blocked_until':blocked['blocked_until'] if blocked and blocked['blocked_until']>now else None}
