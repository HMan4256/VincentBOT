CREATE TABLE IF NOT EXISTS channel_context(
 guild_id TEXT NOT NULL, channel_id TEXT NOT NULL, reset_at REAL NOT NULL,
 PRIMARY KEY(guild_id,channel_id)
);
