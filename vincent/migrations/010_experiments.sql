CREATE TABLE IF NOT EXISTS prompt_experiments(
 id INTEGER PRIMARY KEY, candidate_id INTEGER NOT NULL, incumbent_id INTEGER,
 state TEXT NOT NULL, created_at REAL NOT NULL, finished_at REAL,
 canary_percent INTEGER NOT NULL, evaluation TEXT NOT NULL, outcome TEXT,
 FOREIGN KEY(candidate_id) REFERENCES prompt_versions(id),
 FOREIGN KEY(incumbent_id) REFERENCES prompt_versions(id)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_running_experiment ON prompt_experiments(state) WHERE state='running';
CREATE TABLE IF NOT EXISTS prompt_assignments(
 queue_id INTEGER PRIMARY KEY, experiment_id INTEGER NOT NULL, variant TEXT NOT NULL,
 prompt_version INTEGER, assigned_at REAL NOT NULL,
 FOREIGN KEY(queue_id) REFERENCES queue(id),
 FOREIGN KEY(experiment_id) REFERENCES prompt_experiments(id)
);
