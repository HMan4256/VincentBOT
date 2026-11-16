"""Aggregate diagnostics without emitting chat content, user IDs or secrets."""
import argparse
import datetime as dt
import json
from .store import Store


def report(store,start,end):
    if end<=start:raise ValueError('end must be after start')
    with store.lock:
        db=store.db
        attempts=db.execute('SELECT kind,status,count(*) AS n FROM request_attempts WHERE reserved_at>=? AND reserved_at<? GROUP BY kind,status',(start,end)).fetchall()
        routes=db.execute("SELECT json_extract(payload,'$.reason') AS reason,json_extract(payload,'$.action') AS action,count(*) AS n FROM events WHERE kind='route' AND occurred_at>=? AND occurred_at<? GROUP BY reason,action",(start,end)).fetchall()
        outcomes=db.execute('SELECT status,count(*) AS n FROM queue WHERE created_at>=? AND created_at<? GROUP BY status',(start,end)).fetchall()
        timings=db.execute("SELECT q.created_at,min(e.occurred_at) AS sent_at FROM queue q JOIN queue_events e ON e.queue_id=q.id AND e.action='sent' WHERE q.created_at>=? AND q.created_at<? GROUP BY q.id",(start,end)).fetchall()
        reactions=db.execute('SELECT active,source,count(*) AS n FROM feedback_events WHERE occurred_at>=? AND occurred_at<? GROUP BY active,source',(start,end)).fetchall()
        memories=db.execute('SELECT scope,kind,status,count(*) AS n FROM memories GROUP BY scope,kind,status').fetchall()
        versions=db.execute('SELECT status,count(*) AS n FROM prompt_versions GROUP BY status').fetchall()
    delays=sorted(r['sent_at']-r['created_at'] for r in timings if r['sent_at']>=r['created_at'])
    def quantile(f):return round(delays[int((len(delays)-1)*f)],2) if delays else None
    return {'window_utc':{'start':dt.datetime.fromtimestamp(start,dt.timezone.utc).isoformat(),'end':dt.datetime.fromtimestamp(end,dt.timezone.utc).isoformat()},
            'request_attempts':[dict(x) for x in attempts],'routing':[dict(x) for x in routes],
            'queue_outcomes':[dict(x) for x in outcomes], 'delivery_delay_seconds':{'count':len(delays),'median':quantile(.5),'p90':quantile(.9)},
            'reaction_transitions':[dict(x) for x in reactions],'current_memories':[dict(x) for x in memories],
            'current_guidance_versions':[dict(x) for x in versions],
            'notes':'Counts show recorded events, not unique people, exposure, model quality, or full provider quota. Current memory/version counts are not restricted to the time window.'}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--database',default='data/vincent-v2.sqlite3');p.add_argument('--days',type=int,default=1);a=p.parse_args()
    if a.days<1 or a.days>3650:p.error('--days must be between 1 and 3650')
    end=dt.datetime.now(dt.timezone.utc).timestamp();s=Store(a.database)
    try:print(json.dumps(report(s,end-a.days*86400,end),indent=2))
    finally:s.close()
if __name__=='__main__':main()
