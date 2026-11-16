"""Durable per-person conversational work queue."""
import json
import time
import uuid
from .store import json_text


class WorkQueue:
    def __init__(self,store,clock=time.time,max_backlog=200):
        if max_backlog<1:raise ValueError('max_backlog must be positive')
        self.store,self.clock,self.max_backlog=store,clock,max_backlog

    def recover_after_restart(self):
        """Reclaim pre-send work only while holding the exclusive runtime lock."""
        now=self.clock()
        with self.store.transaction() as db:
            rows=db.execute("SELECT id,expires_at FROM queue WHERE status='claimed'").fetchall()
            for row in rows:
                state='pending' if row['expires_at']>now else 'expired'
                db.execute('UPDATE queue SET status=?,lease_token=NULL,lease_until=NULL,updated_at=? WHERE id=?',(state,now,row['id']))
                db.execute('INSERT INTO queue_events(queue_id,occurred_at,action,details) VALUES(?,?,?,?)',(row['id'],now,'restart_recovery',json_text({'next_status':state})))
            return len(rows)

    def enqueue(self,*,guild_id,channel_id,author_id,branch_id,message_id,priority=10,ttl=120,merge_window=8,reason='direct'):
        now=self.clock(); ids=(str(guild_id),str(channel_id),str(author_id),str(branch_id))
        with self.store.transaction() as db:
            # A durable source may be replayed after a crash. Do not enqueue it
            # again even if the original item has already been completed.
            duplicate=db.execute('SELECT queue_id FROM queue_sources WHERE message_id=?',(str(message_id),)).fetchone()
            if duplicate:return duplicate['queue_id']
            existing=db.execute("SELECT * FROM queue WHERE guild_id=? AND channel_id=? AND author_id=? AND branch_id=? AND status='pending' AND expires_at>? AND updated_at>=? ORDER BY id DESC LIMIT 1",(*ids,now,now-merge_window)).fetchone()
            if existing:
                sources=json.loads(existing['source_ids'])
                if str(message_id) not in sources:sources.append(str(message_id))
                db.execute('UPDATE queue SET source_ids=?,priority=max(priority,?),updated_at=?,expires_at=? WHERE id=?',(json_text(sources),priority,now,now+ttl,existing['id']))
                db.execute('INSERT OR IGNORE INTO queue_sources(message_id,queue_id) VALUES(?,?)',(str(message_id),existing['id']))
                db.execute('INSERT INTO queue_events(queue_id,occurred_at,action,details) VALUES(?,?,?,?)',(existing['id'],now,'merged',json_text({'message_id':str(message_id),'priority':priority})))
                return existing['id']
            stale=db.execute("SELECT id FROM queue WHERE status IN ('pending','claimed') AND expires_at<=?",(now,)).fetchall()
            db.execute("UPDATE queue SET status='expired',updated_at=?,lease_token=NULL,lease_until=NULL WHERE status IN ('pending','claimed') AND expires_at<=?",(now,now))
            db.executemany('INSERT INTO queue_events(queue_id,occurred_at,action,details) VALUES(?,?,?,?)',[(r['id'],now,'expired',json_text({'reason':'stale during enqueue'})) for r in stale])
            active=db.execute("SELECT count(*) FROM queue WHERE status IN ('pending','claimed')").fetchone()[0]
            status='pending'
            if active>=self.max_backlog:
                needed=active-self.max_backlog+1
                victims=db.execute("SELECT id FROM queue WHERE status='pending' AND priority<? ORDER BY priority,created_at,id LIMIT ?",(priority,needed)).fetchall()
                if len(victims)==needed:
                    for victim in victims:
                        db.execute("UPDATE queue SET status='expired',updated_at=?,reason='displaced by higher-priority work' WHERE id=?",(now,victim['id']))
                        db.execute('INSERT INTO queue_events(queue_id,occurred_at,action,details) VALUES(?,?,?,?)',(victim['id'],now,'backlog_displaced',json_text({'incoming_message_id':str(message_id),'incoming_priority':priority})))
                else:status='expired'
            cursor=db.execute('INSERT INTO queue(guild_id,channel_id,author_id,branch_id,source_ids,priority,status,created_at,updated_at,expires_at,reason) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(*ids,json_text([str(message_id)]),priority,status,now,now,now+ttl,reason if status=='pending' else 'backlog capacity'))
            item_id=cursor.lastrowid
            db.execute('INSERT INTO queue_sources(message_id,queue_id) VALUES(?,?)',(str(message_id),item_id))
            db.execute('INSERT INTO queue_events(queue_id,occurred_at,action,details) VALUES(?,?,?,?)',(item_id,now,'enqueued' if status=='pending' else 'backlog_rejected',json_text({'message_id':str(message_id),'priority':priority,'reason':reason})))
            return item_id

    def source_changed(self,message_id,*,deleted=False):
        """Invalidate claimed drafts when an input was edited or deleted."""
        now=self.clock();mid=str(message_id);count=0
        with self.store.transaction() as db:
            rows=db.execute("""SELECT q.id,q.source_ids FROM queue q WHERE q.status IN ('pending','claimed')
                AND EXISTS (SELECT 1 FROM json_each(q.source_ids) s WHERE s.value=?)""",(mid,)).fetchall()
            for row in rows:
                source_ids=json.loads(row['source_ids'])
                remaining=[x for x in source_ids if str(x)!=mid] if deleted else source_ids
                state='pending' if remaining else 'cancelled'
                db.execute('UPDATE queue SET status=?,source_ids=?,updated_at=?,lease_token=NULL,lease_until=NULL WHERE id=?',(state,json_text(remaining),now,row['id']))
                db.execute('INSERT INTO queue_events(queue_id,occurred_at,action,details) VALUES(?,?,?,?)',(row['id'],now,'source_deleted' if deleted else 'source_edited',json_text({'message_id':mid,'remaining':len(remaining)})))
                count+=1
        return count

    def claim(self,lease_seconds=120,settle_seconds=0):
        now=self.clock()
        with self.store.transaction() as db:
            expired=db.execute("SELECT id FROM queue WHERE status IN ('pending','claimed') AND expires_at<=?",(now,)).fetchall()
            reclaimed=db.execute("SELECT id FROM queue WHERE status='claimed' AND lease_until<=? AND expires_at>?",(now,now)).fetchall()
            db.execute("UPDATE queue SET status='expired',updated_at=? WHERE status IN ('pending','claimed') AND expires_at<=?",(now,now))
            db.execute("UPDATE queue SET status='pending',lease_until=NULL,lease_token=NULL WHERE status='claimed' AND lease_until<=? AND expires_at>?",(now,now))
            db.executemany('INSERT INTO queue_events(queue_id,occurred_at,action,details) VALUES(?,?,?,?)',[(r['id'],now,'expired','{}') for r in expired]+[(r['id'],now,'lease_reclaimed','{}') for r in reclaimed])
            row=db.execute("""SELECT q.* FROM queue q WHERE q.status='pending' AND q.expires_at>? AND q.updated_at<=? AND q.available_at<=?
                AND NOT EXISTS (SELECT 1 FROM queue active WHERE active.channel_id=q.channel_id AND active.branch_id=q.branch_id
                    AND ((active.status='claimed' AND active.lease_until>?) OR (active.status='uncertain' AND active.expires_at>?)))
                ORDER BY q.priority DESC,q.created_at,q.id LIMIT 1""",(now,now-settle_seconds,now,now,now)).fetchone()
            if not row:return None
            token=uuid.uuid4().hex
            db.execute("UPDATE queue SET status='claimed',lease_until=?,lease_token=?,attempts=attempts+1,updated_at=? WHERE id=?",(now+lease_seconds,token,now,row['id']))
            db.execute('INSERT INTO queue_events(queue_id,occurred_at,action,details) VALUES(?,?,?,?)',(row['id'],now,'claimed',json_text({'lease_until':now+lease_seconds})))
            return dict(db.execute('SELECT * FROM queue WHERE id=?',(row['id'],)).fetchone())

    def claim_compatible(self,anchor,*,maximum=3,window=8,settle_seconds=5,lease_seconds=120):
        """Claim settled, distinct-user direct work from the same channel."""
        if anchor['priority']<20 or anchor['no_group'] or maximum<2:return []
        now=self.clock();claimed=[];users={anchor['author_id']};branches={anchor['branch_id']}
        with self.store.transaction() as db:
            rows=db.execute("""SELECT * FROM queue WHERE guild_id=? AND channel_id=? AND status='pending' AND priority>=20
                AND no_group=0 AND expires_at>? AND updated_at<=? AND available_at<=? AND abs(created_at-?)<=?
                ORDER BY created_at,id LIMIT 30""",(anchor['guild_id'],anchor['channel_id'],now,now-settle_seconds,now,anchor['created_at'],window)).fetchall()
            for row in rows:
                if row['author_id'] in users or row['branch_id'] in branches:continue
                if db.execute("SELECT 1 FROM queue WHERE channel_id=? AND branch_id=? AND ((status='claimed' AND lease_until>?) OR (status='uncertain' AND expires_at>?))",(row['channel_id'],row['branch_id'],now,now)).fetchone():continue
                token=uuid.uuid4().hex
                db.execute("UPDATE queue SET status='claimed',lease_until=?,lease_token=?,attempts=attempts+1,updated_at=? WHERE id=?",(now+lease_seconds,token,now,row['id']))
                db.execute('INSERT INTO queue_events(queue_id,occurred_at,action,details) VALUES(?,?,?,?)',(row['id'],now,'claimed_group',json_text({'anchor':anchor['id'],'lease_until':now+lease_seconds})))
                claimed.append(dict(db.execute('SELECT * FROM queue WHERE id=?',(row['id'],)).fetchone()))
                users.add(row['author_id']);branches.add(row['branch_id'])
                if len(claimed)>=maximum-1:break
        return claimed

    def retry_individually(self,item_id,*,lease_token,reason):
        now=self.clock()
        with self.store.transaction() as db:
            row=db.execute("SELECT expires_at FROM queue WHERE id=? AND status='claimed' AND lease_token=? AND lease_until>?",(item_id,lease_token,now)).fetchone()
            if not row:return False
            status='pending' if row['expires_at']>now+5 else 'expired'
            db.execute('UPDATE queue SET status=?,no_group=1,updated_at=?,available_at=?,lease_token=NULL,lease_until=NULL WHERE id=?',(status,now,now,item_id))
            db.execute('INSERT INTO queue_events(queue_id,occurred_at,action,details) VALUES(?,?,?,?)',(item_id,now,'group_fallback',json_text({'reason':reason,'next_status':status})))
            return status=='pending'

    def finish(self,item_id,status,*,lease_token,response_message_id=None,reason=''):
        if status not in {'sent','expired','cancelled','failed','pending','uncertain'}:raise ValueError('Invalid queue status')
        with self.store.transaction() as db:
            changed=db.execute("UPDATE queue SET status=?,response_message_id=?,reason=?,lease_until=NULL,updated_at=? WHERE id=? AND status='claimed' AND lease_token=? AND lease_until>?",(status,response_message_id,reason,self.clock(),item_id,lease_token,self.clock())).rowcount
            if changed:db.execute('INSERT INTO queue_events(queue_id,occurred_at,action,details) VALUES(?,?,?,?)',(item_id,self.clock(),status,json_text({'reason':reason,'response_message_id':response_message_id})))
            return changed==1

    def defer(self,item_id,*,lease_token,delay,reason):
        now=self.clock();ready=now+max(1.0,delay)
        with self.store.transaction() as db:
            row=db.execute("SELECT expires_at FROM queue WHERE id=? AND status='claimed' AND lease_token=? AND lease_until>?",(item_id,lease_token,now)).fetchone()
            if not row:return False
            status='pending' if ready<row['expires_at'] else 'expired'
            db.execute('UPDATE queue SET status=?,available_at=?,lease_token=NULL,lease_until=NULL,updated_at=? WHERE id=?',(status,ready,now,item_id))
            db.execute('INSERT INTO queue_events(queue_id,occurred_at,action,details) VALUES(?,?,?,?)',(item_id,now,'deferred' if status=='pending' else 'expired',json_text({'reason':reason,'available_at':ready})))
            return status=='pending'

    def retry_stale(self,item_id,*,lease_token):
        now=self.clock()
        with self.store.transaction() as db:
            row=db.execute("SELECT expires_at FROM queue WHERE id=? AND status='claimed' AND lease_token=? AND lease_until>?",(item_id,lease_token,now)).fetchone()
            if not row:return False
            status='pending' if row['expires_at']>now+5 else 'expired'
            db.execute('UPDATE queue SET status=?,available_at=?,updated_at=?,lease_token=NULL,lease_until=NULL WHERE id=?',(status,now,now,item_id))
            db.execute('INSERT INTO queue_events(queue_id,occurred_at,action,details) VALUES(?,?,?,?)',(item_id,now,'stale_context',json_text({'next_status':status})))
            return status=='pending'

    def begin_send(self,item_id,text,*,lease_token):
        nonce='v2'+uuid.uuid4().hex[:20]
        now=self.clock()
        with self.store.transaction() as db:
            changed=db.execute("UPDATE queue SET status='uncertain',send_text=?,send_nonce=?,lease_until=NULL,updated_at=? WHERE id=? AND status='claimed' AND lease_token=? AND lease_until>? AND expires_at>?",(text,nonce,now,item_id,lease_token,now,now)).rowcount
            if changed:db.execute('INSERT INTO queue_events(queue_id,occurred_at,action,details) VALUES(?,?,?,?)',(item_id,now,'send_started',json_text({'nonce':nonce})))
        return nonce if changed else None

    def reconcile_sent(self,item_id,message_id,*,sent_at=None):
        with self.store.transaction() as db:
            item=db.execute("SELECT channel_id,reason FROM queue WHERE id=? AND status='uncertain'",(item_id,)).fetchone()
            changed=db.execute("UPDATE queue SET status='sent',response_message_id=?,updated_at=? WHERE id=? AND status='uncertain'",(str(message_id),self.clock(),item_id)).rowcount
            if changed:
                db.execute('INSERT INTO queue_events(queue_id,occurred_at,action,details) VALUES(?,?,?,?)',(item_id,self.clock(),'sent',json_text({'response_message_id':str(message_id)})))
                if item['reason']=='observe':
                    db.execute('''INSERT INTO channel_activity(channel_id,last_auto_sent) VALUES(?,?) ON CONFLICT(channel_id)
                        DO UPDATE SET last_auto_sent=max(channel_activity.last_auto_sent,excluded.last_auto_sent)''',(item['channel_id'],self.clock() if sent_at is None else sent_at))
            return bool(changed)

    def fresh(self,item_id,*,lease_token,expected_versions=None,memory_generation=None,memory_versions=None):
        with self.store.lock:
            row=self.store.db.execute('SELECT status,expires_at,lease_until,lease_token,source_ids,reason,channel_id,guild_id,author_id FROM queue WHERE id=?',(item_id,)).fetchone()
            if not row or row['status']!='claimed' or row['lease_token']!=lease_token or row['lease_until']<=self.clock() or row['expires_at']<=self.clock():return False
            if memory_generation is not None:
                pref=self.store.db.execute('SELECT enabled,generation FROM memory_preferences WHERE guild_id=? AND user_id=?',(row['guild_id'],row['author_id'])).fetchone()
                current=(bool(pref['enabled']),pref['generation']) if pref else (True,0)
                if current!=tuple(memory_generation):return False
            if memory_versions is not None:
                for mid,version in memory_versions.items():
                    record=self.store.db.execute('SELECT updated_at,status FROM memories WHERE id=?',(mid,)).fetchone()
                    if not record or record['status']!='active' or record['updated_at']!=version:return False
            if expected_versions is not None:
                for mid,version in expected_versions.items():
                    source=self.store.db.execute('SELECT changed_at,deleted FROM messages WHERE message_id=?',(mid,)).fetchone()
                    if not source or source['deleted'] or source['changed_at']!=version:return False
            sources=json.loads(row['source_ids'])
            # Simulated callers may not populate the message table; if source records exist,
            # all explicitly deleted records cancel the response.
            records=[self.store.db.execute('SELECT created_at,deleted FROM messages WHERE message_id=?',(str(mid),)).fetchone() for mid in sources]
            if records and all(r is not None and r['deleted'] for r in records):return False
            if row['reason']=='observe':
                times=[r['created_at'] for r in records if r and not r['deleted']]
                latest=self.store.db.execute("SELECT max(created_at) FROM messages WHERE channel_id=? AND deleted=0 AND json_extract(metadata,'$.author_bot') IS NOT 1",(row['channel_id'],)).fetchone()[0]
                if not times or (latest is not None and latest>max(times)):return False
            return True
