-- Faz 6.5.1: Genel yapisal veri katmani (6.5'in semantic hafizasindan AYRI).
-- Kesin/yapisal sorgu gerektiren veri: cagri izleme, gorev takibi, takvim
-- cache'i, IoT cihaz durumu. NoSQL DEGIL cunku tek makine/tek kullanici,
-- yatay olcekleme yok - SQLite'in JSON1 uzantisi sema-esnek veri icin yeterli.
-- Bu migration YALNIZCA sema kurar; satir yazan kod ayri fazlara ait
-- (traces -> Faz 6.9 core/trace.py; tasks -> Faz 6.6 execution modes;
-- calendar_cache/iot_devices -> Faz 6.8 MCP genislemesi).

-- --- traces (v2 §9) --------------------------------------------------------
-- Her agent/tool cagrisi icin bir satir. input_summary TAM METIN DEGIL -
-- kirpilmis/hash'lenmis (hassas veri birikimini onlemek icin, v2 §9).
CREATE TABLE IF NOT EXISTS traces (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,                       -- ISO-8601 UTC
    role          TEXT NOT NULL,                       -- orchestrator / tool_agent / ...
    model         TEXT,                                -- model adi (opsiyonel)
    input_summary TEXT,                                -- kirpilmis/hash'li ozet, tam metin YOK
    duration_ms   INTEGER,
    token_count   INTEGER,
    result        TEXT NOT NULL DEFAULT 'success'
                  CHECK (result IN ('success', 'error', 'guardrail_blocked', 'approval_denied'))
);

CREATE INDEX IF NOT EXISTS idx_traces_ts ON traces(ts);

-- --- tasks (Faz 6.6 execution modes gorev/pending-approval takibi) --------
CREATE TABLE IF NOT EXISTS tasks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,                         -- ISO-8601 UTC (olusturulma)
    source      TEXT NOT NULL,                         -- scheduled / continuous / voice / text
    text        TEXT NOT NULL,                         -- calistirilacak/onaylanacak komut metni
    status      TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'approved', 'denied', 'done', 'error')),
    detail_json TEXT NOT NULL DEFAULT '{}'             -- sonuc/hata/onay meta verisi (JSON1)
);

CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);

-- --- calendar_cache (Faz 6.8 Google Calendar MCP cache'i) ----------------
-- JSON1 iskeleti: ham API yaniti raw_json'da, sik sorgulanan alanlar
-- json_extract ile turetilmis (VIRTUAL) sutunlar - sema Google'in event
-- sekli degisirse migration gerektirmeden esner.
CREATE TABLE IF NOT EXISTS calendar_cache (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_synced  TEXT NOT NULL,                          -- bu satirin cachelendigi an
    raw_json   TEXT NOT NULL,
    event_id   TEXT GENERATED ALWAYS AS (json_extract(raw_json, '$.id')) VIRTUAL,
    start_ts   TEXT GENERATED ALWAYS AS (json_extract(raw_json, '$.start.dateTime')) VIRTUAL
);

CREATE INDEX IF NOT EXISTS idx_calendar_cache_event_id ON calendar_cache(event_id);
CREATE INDEX IF NOT EXISTS idx_calendar_cache_start_ts ON calendar_cache(start_ts);

-- --- iot_devices (Faz 6.8 Home Assistant durum okumasi) -----------------
CREATE TABLE IF NOT EXISTS iot_devices (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_updated TEXT NOT NULL,
    raw_json   TEXT NOT NULL,
    entity_id  TEXT GENERATED ALWAYS AS (json_extract(raw_json, '$.entity_id')) VIRTUAL,
    state      TEXT GENERATED ALWAYS AS (json_extract(raw_json, '$.state')) VIRTUAL
);

CREATE INDEX IF NOT EXISTS idx_iot_devices_entity_id ON iot_devices(entity_id);
