"""Durable reaction states and transition history; bot seeds are not votes."""
import time


class Feedback:
    def __init__(self,store,bot_id,positive='👍',negative='👎',clock=time.time):
        self.store,self.bot_id,self.positive,self.negative,self.clock=store,str(bot_id),positive,negative,clock

    def _apply(self,db,message_id,user_id,emoji,added,source,now):
        row=db.execute('SELECT active FROM feedback WHERE message_id=? AND user_id=? AND emoji=?',(message_id,user_id,emoji)).fetchone()
        # Keep a removal tombstone even if we never observed the add. An
        # in-flight older snapshot must not resurrect that removed vote.
        if row is not None and bool(row['active'])==bool(added):
            # A repeated gateway event is not a second vote, but it is fresh
            # evidence that an in-flight older snapshot must not overwrite.
            if source=='gateway':
                db.execute('UPDATE feedback SET updated_at=max(updated_at,?) WHERE message_id=? AND user_id=? AND emoji=?',(now,message_id,user_id,emoji))
            return False
        db.execute('''INSERT INTO feedback(message_id,user_id,emoji,active,updated_at) VALUES(?,?,?,?,?)
            ON CONFLICT(message_id,user_id,emoji) DO UPDATE SET active=excluded.active,updated_at=excluded.updated_at''',(message_id,user_id,emoji,int(added),now))
        db.execute('INSERT INTO feedback_events(message_id,user_id,emoji,active,occurred_at,source) VALUES(?,?,?,?,?,?)',(message_id,user_id,emoji,int(added),now,source))
        return True

    def update(self,message_id,user_id,emoji,added,*,source='gateway'):
        if str(user_id)==self.bot_id or emoji not in (self.positive,self.negative):return False
        with self.store.transaction() as db:
            return self._apply(db,str(message_id),str(user_id),emoji,added,source,self.clock())

    def mark_unverified(self,message_id,user_id,emoji):
        """Protect an in-flight snapshot without treating an unknown voter as human."""
        if str(user_id)==self.bot_id or emoji not in (self.positive,self.negative):return
        now=self.clock()
        with self.store.transaction() as db:
            db.execute('''INSERT INTO feedback(message_id,user_id,emoji,active,updated_at) VALUES(?,?,?,0,?)
                ON CONFLICT(message_id,user_id,emoji) DO UPDATE SET updated_at=max(feedback.updated_at,excluded.updated_at)''',
                (str(message_id),str(user_id),emoji,now))

    def reconcile(self,message_id,current,*,snapshot_started=None):
        """Replace state after a complete snapshot without overwriting newer events."""
        message_id=str(message_id);now=self.clock()
        valid={(str(user),emoji) for user,emoji in current if str(user)!=self.bot_id and emoji in (self.positive,self.negative)}
        # Hold a SQLite write transaction across read and updates, including
        # against another local process. A Python lock alone is not enough.
        with self.store.transaction() as db:
            rows=db.execute('SELECT user_id,emoji,active,updated_at FROM feedback WHERE message_id=?',(message_id,)).fetchall()
            existing={(r['user_id'],r['emoji']) for r in rows if r['active']}
            changed={(r['user_id'],r['emoji']) for r in rows if snapshot_started is not None and r['updated_at']>=snapshot_started}
            clears=db.execute('SELECT emoji_scope,updated_at FROM feedback_clear_marks WHERE message_id=?',(message_id,)).fetchall()
            cleared={r['emoji_scope'] for r in clears if snapshot_started is not None and r['updated_at']>=snapshot_started}
            # Clear events apply to users absent from the local ledger too.
            safe_valid={(u,e) for u,e in valid if '' not in cleared and e not in cleared}
            owner=db.execute('SELECT guild_id FROM queue WHERE response_message_id=? LIMIT 1',(message_id,)).fetchone()
            if owner:
                erased={r['user_id']:r['erased_at'] for r in db.execute('SELECT user_id,erased_at FROM privacy_tombstones WHERE guild_id=?',(owner['guild_id'],)).fetchall()}
                newest={(r['user_id'],r['emoji']):r for r in rows}
                safe_valid={(u,e) for u,e in safe_valid if u not in erased or ((u,e) in newest and newest[(u,e)]['active'] and newest[(u,e)]['updated_at']>erased[u])}
            for user,emoji in (safe_valid-existing)-changed:self._apply(db,message_id,user,emoji,True,'reconcile',now)
            for user,emoji in (existing-valid)-changed:self._apply(db,message_id,user,emoji,False,'reconcile',now)

    def clear(self,message_id,emoji=None):
        if emoji is not None and emoji not in (self.positive,self.negative):return 0
        message_id=str(message_id);now=self.clock()
        with self.store.transaction() as db:
            db.execute('INSERT INTO feedback_clear_marks(message_id,emoji_scope,updated_at) VALUES(?,?,?) ON CONFLICT(message_id,emoji_scope) DO UPDATE SET updated_at=excluded.updated_at',(message_id,emoji or '',now))
            if emoji is None:rows=db.execute('SELECT user_id,emoji FROM feedback WHERE message_id=? AND active=1',(message_id,)).fetchall()
            else:rows=db.execute('SELECT user_id,emoji FROM feedback WHERE message_id=? AND emoji=? AND active=1',(message_id,emoji)).fetchall()
            return sum(self._apply(db,message_id,r['user_id'],r['emoji'],False,'clear',now) for r in rows)

    def votes(self,message_id):
        with self.store.lock:
            rows=self.store.db.execute('SELECT user_id,emoji FROM feedback WHERE message_id=? AND active=1',(str(message_id),)).fetchall()
        result={}
        for row in rows:result.setdefault(row['user_id'],set()).add(row['emoji'])
        return {u:('mixed' if len(e)>1 else 'positive' if self.positive in e else 'negative') for u,e in result.items()}
