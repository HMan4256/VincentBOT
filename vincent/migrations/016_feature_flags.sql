CREATE TABLE IF NOT EXISTS feature_flags(
 guild_id TEXT NOT NULL, channel_id TEXT NOT NULL, feature TEXT NOT NULL,
 enabled INTEGER NOT NULL, updated_at REAL NOT NULL,
 PRIMARY KEY(guild_id,channel_id,feature)
);
