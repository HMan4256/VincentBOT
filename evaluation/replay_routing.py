#!/usr/bin/env python3
"""Offline historical routing replay. Never sends data to a model or Discord."""
import argparse,collections,datetime as dt,json,os,sys
from pathlib import Path
from zoneinfo import ZoneInfo
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from vincent.router import Router

BOT=os.environ.get('VINCENT_REPLAY_BOT_ID','9')

def replay(path,date,tz='Europe/Prague'):
    with open(path,encoding='utf8') as f:raw=json.load(f)
    messages=sorted(raw,key=lambda m:(m['timestamp'],int(m['id'])))
    byid={m['id']:m for m in messages};router=Router(BOT);last_bot={};counts=collections.Counter(); candidates=[]
    for msg in messages:
        stamp=dt.datetime.fromisoformat(msg['timestamp']);local=stamp.astimezone(ZoneInfo(tz))
        if local.date().isoformat()!=date:continue
        channel=msg['channel_id'];author=msg['author']['id'];parent=(msg.get('message_reference') or {}).get('message_id');referenced=byid.get(parent)
        if author==BOT:
            # The target can be established only for explicit replies in the export.
            target=referenced['author']['id'] if referenced and not referenced['author'].get('bot') else None
            last_bot[channel]={'message_id':msg['id'],'target_user_id':target,'content':msg['content'],'created_at':stamp.timestamp(),'branch_id':parent or msg['id']};counts['historical_vincent_messages']+=1;continue
        if msg['author'].get('bot') or not msg['content'].strip():continue
        mentions=[str(u['id']) for u in msg.get('mentions',[]) if isinstance(u,dict) and 'id' in u]
        routed=router.route({'message_id':msg['id'],'author_id':author,'created_at':stamp.timestamp(),'content':msg['content'],'reply_to':parent,'mentions':mentions},referenced={'author_id':referenced['author']['id']} if referenced else None,recent_bot=last_bot.get(channel))
        counts['human_text_messages']+=1;counts['action_'+routed.action]+=1;counts['reason_'+routed.reason]+=1
        if routed.action!='ignore':candidates.append({'id':msg['id'],'action':routed.action,'reason':routed.reason,'timestamp':msg['timestamp']})
    return {'date':date,'timezone':tz,'counts':dict(counts),'candidates':len(candidates),'method':'Routes recorded historical chat using recorded Vincent outputs, not simulated new outputs. Target for a previous bot response is known only if it was an explicit reply. No queue merging, model, response quality, budget or actual request counts are simulated. Actions are candidates, not accuracy labels.'}

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--export',default='data/chat_history.json');p.add_argument('--date',default='2026-11-15');p.add_argument('--output',required=True);args=p.parse_args()
 result=replay(args.export,args.date)
 with open(args.output,'x',encoding='utf8') as f:json.dump(result,f,indent=2);f.write('\n')
 print(json.dumps(result,indent=2))
