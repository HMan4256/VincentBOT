CREATE TABLE IF NOT EXISTS profile_outgoing(
 message_id TEXT PRIMARY KEY, guild_id TEXT NOT NULL, user_id TEXT NOT NULL,
 occurred_at REAL NOT NULL
);
