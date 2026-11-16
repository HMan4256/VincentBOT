"""Persistent channel-level controls for optional Discord behavior."""
import time
import uuid
from .store import json_text


class FeatureFlags:
    def __init__(self,store,settings,clock=time.time):self.store,self.settings,self.clock=store,settings,clock

    def enabled(self,guild,channel,feature):
        if feature not in ('unsolicited','reactions'):raise ValueError('Unknown feature')
        with self.store.lock:
            row=self.store.db.execute('SELECT enabled FROM feature_flags WHERE guild_id=? AND channel_id=? AND feature=?',(str(guild),str(channel),feature)).fetchone()
        default=self.settings.unsolicited_enabled if feature=='unsolicited' else self.settings.reactions_enabled
        return bool(row['enabled']) if row else default

    def any_unsolicited(self):
        if self.settings.unsolicited_enabled:return True
        with self.store.lock:return bool(self.store.db.execute("SELECT 1 FROM feature_flags WHERE feature='unsolicited' AND enabled=1 LIMIT 1").fetchone())

    def set(self,guild,channel,feature,enabled,actor):
        if feature not in ('unsolicited','reactions'):raise ValueError('Unknown feature')
        now=self.clock()
        with self.store.transaction() as db:
            db.execute('''INSERT INTO feature_flags(guild_id,channel_id,feature,enabled,updated_at) VALUES(?,?,?,?,?)
                ON CONFLICT(guild_id,channel_id,feature) DO UPDATE SET enabled=excluded.enabled,updated_at=excluded.updated_at''',
                (str(guild),str(channel),feature,int(enabled),now))
            if feature=='unsolicited' and not enabled:
                db.execute("UPDATE opportunities SET state='disabled' WHERE channel_id=? AND state='pending'",(str(channel),))
                db.execute("UPDATE queue SET status='cancelled',updated_at=? WHERE channel_id=? AND reason='observe' AND status IN ('pending','claimed')",(now,str(channel)))
            db.execute('INSERT INTO events(event_key,kind,guild_id,channel_id,author_id,occurred_at,ingested_at,payload) VALUES(?,?,?,?,?,?,?,?)',
                (f'flag:{uuid.uuid4().hex}','feature_changed',str(guild),str(channel),str(actor),now,now,json_text({'feature':feature,'enabled':bool(enabled)})))
