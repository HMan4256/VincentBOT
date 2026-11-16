"""Locally maintained interaction metadata, separate from inferred memories."""
import time


class Profiles:
    def __init__(self,store,clock=time.time):self.store,self.clock=store,clock

    def incoming(self,guild,user,at=None,*,message_id=None):
        guild,user=str(guild),str(user);at=self.clock() if at is None else at
        with self.store.transaction() as db:
            pref=db.execute('SELECT enabled FROM memory_preferences WHERE guild_id=? AND user_id=?',(guild,user)).fetchone()
            if pref and not pref['enabled']:return False
            if message_id is not None:
                added=db.execute('INSERT OR IGNORE INTO profile_interactions(message_id,guild_id,user_id,occurred_at) VALUES(?,?,?,?)',(str(message_id),guild,user,at)).rowcount
                if not added:return False
            db.execute('''INSERT INTO user_profiles(guild_id,user_id,first_interaction,last_interaction,incoming_count,outgoing_count)
                VALUES(?,?,?,?,1,0) ON CONFLICT(guild_id,user_id) DO UPDATE SET
                first_interaction=min(first_interaction,excluded.first_interaction),last_interaction=max(last_interaction,excluded.last_interaction),
                incoming_count=incoming_count+1''',(guild,user,at,at))
            return True

    def outgoing(self,guild,user,at=None,*,message_id=None):
        guild,user=str(guild),str(user);at=self.clock() if at is None else at
        with self.store.transaction() as db:
            pref=db.execute('SELECT enabled FROM memory_preferences WHERE guild_id=? AND user_id=?',(guild,user)).fetchone()
            if pref and not pref['enabled']:return False
            # Do not recreate a profile after its owner deleted it mid-send.
            if not db.execute('SELECT 1 FROM user_profiles WHERE guild_id=? AND user_id=?',(guild,user)).fetchone():return False
            if message_id is not None:
                added=db.execute('INSERT OR IGNORE INTO profile_outgoing(message_id,guild_id,user_id,occurred_at) VALUES(?,?,?,?)',(str(message_id),guild,user,at)).rowcount
                if not added:return False
            return bool(db.execute('UPDATE user_profiles SET outgoing_count=outgoing_count+1,last_interaction=max(last_interaction,?) WHERE guild_id=? AND user_id=?',(at,guild,user)).rowcount)

    def get(self,guild,user):
        with self.store.lock:
            pref=self.store.db.execute('SELECT enabled FROM memory_preferences WHERE guild_id=? AND user_id=?',(str(guild),str(user))).fetchone()
            if pref and not pref['enabled']:return None
            row=self.store.db.execute('SELECT first_interaction,last_interaction,incoming_count,outgoing_count FROM user_profiles WHERE guild_id=? AND user_id=?',(str(guild),str(user))).fetchone()
        return dict(row) if row else None
