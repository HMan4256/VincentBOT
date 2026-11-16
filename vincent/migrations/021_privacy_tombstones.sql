CREATE TABLE IF NOT EXISTS privacy_tombstones(
 guild_id TEXT NOT NULL, user_id TEXT NOT NULL, erased_at REAL NOT NULL,
 PRIMARY KEY(guild_id,user_id)
);
