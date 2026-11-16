"""Debounced local participation candidates; the model still decides to speak."""
import re
import time
import json
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo


class Opportunities:
    def __init__(self,store,clock=time.time,settle_seconds=20,cooldown_seconds=600):
        self.store,self.clock,self.settle_seconds,self.cooldown_seconds=store,clock,settle_seconds,cooldown_seconds

    def quiet(self,timezone,start,end):
        hour=datetime.fromtimestamp(self.clock(),ZoneInfo(timezone)).hour
        if start==end:return False
        return start<=hour<end if start<end else hour>=start or hour<end

    def invalidate_channel(self,channel_id):
        with self.store.transaction() as db:
            db.execute("UPDATE opportunities SET state='superseded' WHERE channel_id=? AND state='pending'",(str(channel_id),))

    def observe(self,*,guild_id,channel_id,author_id,message_id,content,branch_id=None):
        # Only queue a promising completed conversation window; not a claim it is funny.
        text=content.strip();now=self.clock()
        if len(text)<12:reason='short'
        elif text.startswith(('!', '/', 'http://','https://')):reason='command_or_link'
        elif not re.search(r'[?!]|\b(?:bro|vincent|vin|lmao|bruh)\b',text,re.I):reason='no_local_cue'
        else:reason='candidate'
        branch=str(branch_id or message_id)
        with self.store.transaction() as db:
            inserted=db.execute('INSERT OR IGNORE INTO events(event_key,kind,guild_id,channel_id,message_id,author_id,occurred_at,ingested_at,payload) VALUES(?,?,?,?,?,?,?,?,?)',
                (f'opportunity_score:{message_id}','opportunity_scored',str(guild_id),str(channel_id),str(message_id),str(author_id),now,now,json.dumps({'reason':reason}))).rowcount
            if not inserted or reason!='candidate':return False
            db.execute('''INSERT INTO opportunities(channel_id,branch_id,guild_id,message_id,author_id,due_at,updated_at,state)
                VALUES(?,?,?,?,?,?,?,'pending') ON CONFLICT(channel_id,branch_id) DO UPDATE SET
                message_id=excluded.message_id,author_id=excluded.author_id,due_at=excluded.due_at,
                updated_at=excluded.updated_at,state='pending' ''',
                (str(channel_id),branch,str(guild_id),str(message_id),str(author_id),now+self.settle_seconds,now))
        return True

    def next(self):
        now=self.clock()
        with self.store.transaction() as db:
            db.execute("UPDATE opportunities SET state='expired' WHERE state='pending' AND due_at<?",(now-60,))
            row=db.execute("""SELECT o.* FROM opportunities o LEFT JOIN channel_activity a ON a.channel_id=o.channel_id
                WHERE o.state='pending' AND o.due_at<=? AND COALESCE(a.last_auto_sent,0)<=?
                ORDER BY o.due_at LIMIT 1""",(now,now-self.cooldown_seconds)).fetchone()
            if not row:return None
            # An explicit queued request takes precedence and suppresses this opportunity.
            active=db.execute("SELECT 1 FROM queue WHERE channel_id=? AND status IN ('pending','claimed') AND priority>=10 AND expires_at>?",(row['channel_id'],now)).fetchone()
            state='suppressed' if active else 'consumed'
            db.execute('UPDATE opportunities SET state=? WHERE channel_id=? AND branch_id=?',(state,row['channel_id'],row['branch_id']))
            db.execute('INSERT INTO events(event_key,kind,guild_id,channel_id,message_id,author_id,occurred_at,ingested_at,payload) VALUES(?,?,?,?,?,?,?,?,?)',(f'opportunity:{uuid.uuid4().hex}','opportunity_decided',row['guild_id'],row['channel_id'],row['message_id'],row['author_id'],now,now,json.dumps({'state':state})))
            return None if active else dict(row)

    def sent(self,channel_id):
        with self.store.transaction() as db:
            db.execute('INSERT INTO channel_activity(channel_id,last_auto_sent) VALUES(?,?) ON CONFLICT(channel_id) DO UPDATE SET last_auto_sent=excluded.last_auto_sent',(str(channel_id),self.clock()))
