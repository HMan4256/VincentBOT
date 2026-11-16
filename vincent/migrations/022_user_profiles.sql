CREATE TABLE IF NOT EXISTS user_profiles(
 guild_id TEXT NOT NULL, user_id TEXT NOT NULL,
 first_interaction REAL NOT NULL, last_interaction REAL NOT NULL,
 incoming_count INTEGER NOT NULL DEFAULT 0, outgoing_count INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(guild_id,user_id)
);
