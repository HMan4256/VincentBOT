CREATE TABLE IF NOT EXISTS memory_events(
 id INTEGER PRIMARY KEY, memory_id INTEGER, guild_id TEXT, user_id TEXT,
 action TEXT NOT NULL, occurred_at REAL NOT NULL, details TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS memory_events_owner ON memory_events(guild_id,user_id,occurred_at);
