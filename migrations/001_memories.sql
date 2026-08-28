-- Faz 6.5: kalici semantic hafiza (oturumlar-arasi).
-- embedding = sentence-transformers paraphrase-multilingual-MiniLM-L12-v2 ciktisi, np.float32
-- 384-dim, L2-normalize edilmis, .tobytes() ile saklanir. core/memory.py
-- ilk recall()/remember()'da tum satirlari in-process bir matrise yukler.

CREATE TABLE IF NOT EXISTS memories (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,               -- ISO-8601 UTC
    text          TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',  -- {"source": "assistant_turn"|"user_stated", "lang": ...}
    embedding     BLOB NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_memories_ts ON memories(ts);
