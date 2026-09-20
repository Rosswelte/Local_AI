CREATE TABLE IF NOT EXISTS services (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL,
    url TEXT NOT NULL,
    is_remote INTEGER NOT NULL DEFAULT 0 CHECK (is_remote IN (0, 1)),
    api_key_enc TEXT,
    extra_headers_enc TEXT,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    last_check_at TEXT,
    last_status TEXT
);

CREATE TABLE IF NOT EXISTS behaviors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    system_prompt TEXT NOT NULL,
    default_model_id INTEGER REFERENCES models(id) ON DELETE SET NULL,
    allowed_tools TEXT NOT NULL DEFAULT '[]',
    params TEXT NOT NULL DEFAULT '{}',
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE models ADD COLUMN display_name TEXT;
ALTER TABLE models ADD COLUMN service_id INTEGER REFERENCES services(id) ON DELETE CASCADE;
ALTER TABLE models ADD COLUMN type TEXT NOT NULL DEFAULT 'text';
ALTER TABLE models ADD COLUMN capabilities TEXT NOT NULL DEFAULT '[]';
ALTER TABLE models ADD COLUMN ram_recommended_mb INTEGER;
ALTER TABLE models ADD COLUMN vram_recommended_mb INTEGER;
ALTER TABLE models ADD COLUMN disk_mb INTEGER;
ALTER TABLE models ADD COLUMN spec TEXT NOT NULL DEFAULT '{}';

ALTER TABLE messages ADD COLUMN model_id INTEGER REFERENCES models(id) ON DELETE SET NULL;
ALTER TABLE messages ADD COLUMN kind TEXT NOT NULL DEFAULT 'text';

ALTER TABLE jobs ADD COLUMN conversation_id INTEGER REFERENCES conversations(id) ON DELETE SET NULL;
ALTER TABLE jobs ADD COLUMN progress_message TEXT;
ALTER TABLE jobs ADD COLUMN input TEXT NOT NULL DEFAULT '{}';
ALTER TABLE jobs ADD COLUMN output TEXT;
ALTER TABLE jobs ADD COLUMN error TEXT;

ALTER TABLE settings RENAME TO settings_v1;
CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value TEXT,
    category TEXT NOT NULL DEFAULT 'limits'
);
INSERT INTO settings(key, value, category)
SELECT key, value, 'limits' FROM settings_v1;
DROP TABLE settings_v1;

CREATE INDEX IF NOT EXISTS idx_models_service ON models(service_id);
CREATE INDEX IF NOT EXISTS idx_jobs_conversation ON jobs(conversation_id);
CREATE INDEX IF NOT EXISTS idx_behaviors_default_model ON behaviors(default_model_id);
