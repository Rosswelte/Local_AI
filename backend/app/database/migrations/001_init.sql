CREATE TABLE IF NOT EXISTS machine_profile (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    ram_total_mb INTEGER NOT NULL DEFAULT 0,
    ram_available_mb INTEGER NOT NULL DEFAULT 0,
    cpu_cores INTEGER NOT NULL DEFAULT 1,
    gpu_name TEXT,
    gpu_total_mb INTEGER NOT NULL DEFAULT 0,
    gpu_free_mb INTEGER NOT NULL DEFAULT 0,
    container_ram_limit_mb INTEGER,
    ram_budget_mb INTEGER,
    vram_budget_mb INTEGER,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS models (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    label TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT 'ollama',
    size_mb INTEGER NOT NULL DEFAULT 0,
    ram_min_mb INTEGER NOT NULL DEFAULT 0,
    vram_min_mb INTEGER NOT NULL DEFAULT 0,
    gpu_required INTEGER NOT NULL DEFAULT 0 CHECK (gpu_required IN (0, 1)),
    context_tokens INTEGER NOT NULL DEFAULT 4096,
    installed INTEGER NOT NULL DEFAULT 0 CHECK (installed IN (0, 1)),
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    pinned INTEGER NOT NULL DEFAULT 0 CHECK (pinned IN (0, 1)),
    discovered INTEGER NOT NULL DEFAULT 0 CHECK (discovered IN (0, 1)),
    config TEXT NOT NULL DEFAULT '{}',
    perf TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL DEFAULT 'Nouvelle conversation',
    model_id INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (model_id) REFERENCES models(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('system', 'user', 'assistant')),
    content TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'complete' CHECK (status IN ('streaming', 'complete', 'cancelled', 'error')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL DEFAULT 'text',
    provider TEXT NOT NULL DEFAULT 'ollama',
    model_id INTEGER,
    message_id INTEGER,
    state TEXT NOT NULL DEFAULT 'queued' CHECK (state IN ('queued', 'running', 'completed', 'failed', 'cancelled')),
    priority INTEGER NOT NULL DEFAULT 10,
    force INTEGER NOT NULL DEFAULT 0 CHECK (force IN (0, 1)),
    progress INTEGER NOT NULL DEFAULT 0 CHECK (progress >= 0 AND progress <= 100),
    cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK (cancel_requested IN (0, 1)),
    error_code TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TEXT,
    finished_at TEXT,
    FOREIGN KEY (model_id) REFERENCES models(id) ON DELETE SET NULL,
    FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS job_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL,
    sequence INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    data TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (job_id, sequence),
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_ready ON jobs(state, priority DESC, created_at ASC);
CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, id);
