"""core/db.py testleri - merkezi SQLite baglantisi + sirali migration.

Calistirma: `python -m pytest tests/ -v` (repo kokunden, bkz. CLAUDE.md).
"""

from src.jarvis.core import db as db_module


def test_get_connection_applies_migrations(tmp_path):
    conn = db_module.get_connection(db_path=tmp_path / "t.db")

    tables = {
        r["name"]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "memories" in tables
    assert "schema_version" in tables


def test_wal_mode_enabled(tmp_path):
    conn = db_module.get_connection(db_path=tmp_path / "t.db")
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_migrations_are_idempotent(tmp_path):
    path = tmp_path / "t.db"
    db_module.get_connection(db_path=path)
    # ikinci kez ac - migration'lar yeniden KOSMAMALI (hata da vermemeli)
    conn2 = db_module.get_connection(db_path=path)

    versions = [r["version"] for r in conn2.execute("SELECT version FROM schema_version").fetchall()]
    assert versions == sorted(set(versions))  # tekrar/duplike yok
    assert versions == [1, 2]  # 001_memories.sql + 002_structural_tables.sql


def test_structural_tables_created(tmp_path):
    conn = db_module.get_connection(db_path=tmp_path / "t.db")
    tables = {
        r["name"]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert {"traces", "tasks", "calendar_cache", "iot_devices"} <= tables


def test_schema_version_reaches_2(tmp_path):
    conn = db_module.get_connection(db_path=tmp_path / "t.db")
    current = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()["v"]
    assert current == 2


def test_traces_result_check_constraint(tmp_path):
    import sqlite3

    conn = db_module.get_connection(db_path=tmp_path / "t.db")
    conn.execute(
        "INSERT INTO traces (ts, role, result) VALUES (?, ?, ?)",
        ("2026-08-28T00:00:00Z", "orchestrator", "success"),
    )
    try:
        conn.execute(
            "INSERT INTO traces (ts, role, result) VALUES (?, ?, ?)",
            ("2026-08-28T00:00:01Z", "orchestrator", "banana"),
        )
        raised = False
    except sqlite3.IntegrityError:
        raised = True
    assert raised, "traces.result gecersiz deger kabul etti (CHECK yok)"


def test_tasks_status_check_constraint(tmp_path):
    import sqlite3

    conn = db_module.get_connection(db_path=tmp_path / "t.db")
    conn.execute(
        "INSERT INTO tasks (ts, source, text, status) VALUES (?, ?, ?, ?)",
        ("2026-08-28T00:00:00Z", "scheduled", "sabah brifingi", "pending"),
    )
    try:
        conn.execute(
            "INSERT INTO tasks (ts, source, text, status) VALUES (?, ?, ?, ?)",
            ("2026-08-28T00:00:01Z", "voice", "x", "weird"),
        )
        raised = False
    except sqlite3.IntegrityError:
        raised = True
    assert raised, "tasks.status gecersiz deger kabul etti (CHECK yok)"


def test_calendar_cache_generated_columns_from_json(tmp_path):
    conn = db_module.get_connection(db_path=tmp_path / "t.db")
    raw = '{"id": "evt1", "start": {"dateTime": "2026-09-01T10:00:00Z"}}'
    conn.execute(
        "INSERT INTO calendar_cache (ts_synced, raw_json) VALUES (?, ?)",
        ("2026-08-28T00:00:00Z", raw),
    )
    row = conn.execute(
        "SELECT event_id, start_ts FROM calendar_cache WHERE event_id = 'evt1'"
    ).fetchone()
    assert row["event_id"] == "evt1"
    assert row["start_ts"] == "2026-09-01T10:00:00Z"


def test_iot_devices_generated_columns_from_json(tmp_path):
    conn = db_module.get_connection(db_path=tmp_path / "t.db")
    raw = '{"entity_id": "light.kitchen", "state": "on"}'
    conn.execute(
        "INSERT INTO iot_devices (ts_updated, raw_json) VALUES (?, ?)",
        ("2026-08-28T00:00:00Z", raw),
    )
    row = conn.execute(
        "SELECT entity_id, state FROM iot_devices WHERE entity_id = 'light.kitchen'"
    ).fetchone()
    assert row["entity_id"] == "light.kitchen"
    assert row["state"] == "on"


def test_schema_version_advances(tmp_path):
    conn = db_module.get_connection(db_path=tmp_path / "t.db")
    current = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()["v"]
    assert current >= 1


def test_write_lock_is_shared_singleton():
    assert db_module.write_lock() is db_module.write_lock()
