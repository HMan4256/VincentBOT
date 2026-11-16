CREATE TABLE IF NOT EXISTS provider_request_blocks(
 id INTEGER PRIMARY KEY CHECK(id=1),
 blocked_until REAL NOT NULL,
 reason TEXT NOT NULL,
 observed_at REAL NOT NULL
);
