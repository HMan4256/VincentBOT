#!/usr/bin/env python3
"""Time-based, local budget/queue replay on recorded channel events.

No Discord or model calls. Routing uses historical bot output; generated replies,
classification accuracy, context quality and background jobs are not simulated.
"""
import argparse,collections,datetime as dt,json,os,sys
from pathlib import Path
from zoneinfo import ZoneInfo
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from vincent.budget import Budget,BudgetExceeded
from vincent.config import Settings
from vincent.queue import WorkQueue
from vincent.router import Router
from vincent.store import Store

BOT=os.environ.get('VINCENT_REPLAY_BOT_ID','9')

def simulate(path,date,*,latency=5,settle=5,day_limit=1000,minute_limit=18,concurrency=3,group_direct=True):
 with open(path,encoding='utf8') as f:raw=json.load(f)
 messages=sorted(raw,key=lambda m:(m['timestamp'],int(m['id'])));byid={m['id']:m for m in messages}
 selected=[m for m in messages if dt.datetime.fromisoformat(m['timestamp']).astimezone(ZoneInfo('Europe/Prague')).date().isoformat()==date]
 if not selected:raise ValueError('No data on that date')
 now=[dt.datetime.fromisoformat(selected[0]['timestamp']).timestamp()];s=Store(':memory:');q=WorkQueue(s,lambda:now[0]);settings=Settings(max_daily=day_limit,max_per_minute=minute_limit,background_ceiling=min(250,day_limit));budget=Budget(s,settings,lambda:now[0]);router=Router(BOT)
 last_bot={};counts=collections.Counter();delays=[];worker_at=now[0];active=[]
 def tick(at):
  nonlocal worker_at
  while worker_at<=at:
   now[0]=worker_at
   done=[entry for entry in active if entry[0]<=worker_at]
   for _,items in done:
    for claimed in items:
     if q.fresh(claimed['id'],lease_token=claimed['lease_token']):
      q.finish(claimed['id'],'sent',lease_token=claimed['lease_token'],response_message_id='simulated');counts['simulated_sent']+=1;delays.append(worker_at-claimed['created_at'])
     else:counts['stale_after_model']+=1
   active[:]=[entry for entry in active if entry[0]>worker_at]
   if len(active)<concurrency:
    item=q.claim(settle_seconds=settle)
    if item:
     items=[item]+(q.claim_compatible(item,settle_seconds=2) if group_direct else [])
     try:
      budget.reserve('direct');counts['model_calls']+=1
      if len(items)>1:counts['grouped_calls']+=1;counts['grouped_items']+=len(items)
      active.append((worker_at+latency,items))
     except BudgetExceeded as exc:
      counts['budget_deferrals']+=len(items)
      for member in items:q.defer(member['id'],lease_token=member['lease_token'],delay=exc.retry_after,reason=str(exc))
   worker_at+=2
 for msg in selected:
  stamp=dt.datetime.fromisoformat(msg['timestamp']).timestamp();tick(stamp);now[0]=stamp
  author=str(msg['author']['id']);channel=str(msg['channel_id']);parent=(msg.get('message_reference') or {}).get('message_id');referenced=byid.get(parent)
  if author==BOT:
   target=referenced['author']['id'] if referenced and not referenced['author'].get('bot') else None
   last_bot[channel]={'message_id':msg['id'],'target_user_id':target,'content':msg['content'],'created_at':stamp,'branch_id':parent or msg['id']};continue
  if msg['author'].get('bot') or not msg['content'].strip():continue
  mentions=[str(x['id']) for x in msg.get('mentions',[]) if isinstance(x,dict) and 'id' in x]
  route=router.route({'message_id':msg['id'],'author_id':author,'created_at':stamp,'content':msg['content'],'reply_to':parent,'mentions':mentions},referenced={'author_id':referenced['author']['id']} if referenced else None,recent_bot=last_bot.get(channel))
  if route.action in ('direct','candidate'):
   counts['candidate_messages']+=1
   branch=route.branch_id if parent else f'user:{author}'
   q.enqueue(guild_id=(msg.get('message_reference') or {}).get('guild_id','archive'),channel_id=channel,author_id=author,branch_id=branch,message_id=msg['id'],priority=20 if route.action=='direct' else 10,reason=route.reason)
 tick(now[0]+125)
 statuses={r['status']:r['n'] for r in s.db.execute('SELECT status,count(*) AS n FROM queue GROUP BY status')}
 merges=s.db.execute("SELECT count(*) FROM queue_events WHERE action='merged'").fetchone()[0]
 s.close();delays.sort()
 return {'date':date,'assumptions':{'latency_seconds':latency,'worker_tick_seconds':2,'worker_concurrency':concurrency,'group_direct':group_direct,'settle_seconds':settle,'ttl_seconds':120,'daily_cap':day_limit,'minute_cap':minute_limit},'counts':dict(counts),'queue_statuses':statuses,'merged_messages':merges,'delay_median_seconds':round(delays[len(delays)//2],1) if delays else None,'delay_p90_seconds':round(delays[int((len(delays)-1)*.9)],1) if delays else None,'caveat':'Historical Vincent replies drive routing; simulated replies are not fed back. Each claimed group consumes one call, and all its items succeed after fixed latency; malformed or omitted grouped outputs are not simulated. No provider errors, retries, memory, learning, unsolicited calls, model silence, or correctness are simulated. UTC budget resets may occur inside the replayed Prague day.'}

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--export',default='data/chat_history.json');p.add_argument('--date',default='2026-11-15');p.add_argument('--latency',type=float,default=5);p.add_argument('--concurrency',type=int,default=3);p.add_argument('--no-group',action='store_true');p.add_argument('--output',required=True);a=p.parse_args()
 result=simulate(a.export,a.date,latency=a.latency,concurrency=a.concurrency,group_direct=not a.no_group)
 with open(a.output,'x',encoding='utf8') as f:json.dump(result,f,indent=2);f.write('\n')
 print(json.dumps(result,indent=2))
