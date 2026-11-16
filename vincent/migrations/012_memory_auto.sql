ALTER TABLE memory_preferences ADD COLUMN auto_enabled INTEGER NOT NULL DEFAULT 1;
CREATE TABLE IF NOT EXISTS legacy_imports(
 id INTEGER PRIMARY KEY, source_fingerprint TEXT NOT NULL UNIQUE,
 imported_at REAL NOT NULL, report TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS legacy_import_records(
 import_id INTEGER NOT NULL, memory_id INTEGER NOT NULL,
 FOREIGN KEY(import_id) REFERENCES legacy_imports(id),
 FOREIGN KEY(memory_id) REFERENCES memories(id) ON DELETE CASCADE,
 PRIMARY KEY(import_id,memory_id)
);
CREATE TABLE IF NOT EXISTS legacy_import_preferences(
 import_id INTEGER NOT NULL, guild_id TEXT NOT NULL, user_id TEXT NOT NULL,
 previous TEXT, applied TEXT NOT NULL,
 PRIMARY KEY(import_id,guild_id,user_id),
 FOREIGN KEY(import_id) REFERENCES legacy_imports(id)
);
