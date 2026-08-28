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
    assert versions == [1]  # su an tek migration: 001_memories.sql


def test_schema_version_advances(tmp_path):
    conn = db_module.get_connection(db_path=tmp_path / "t.db")
    current = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()["v"]
    assert current >= 1


def test_write_lock_is_shared_singleton():
    assert db_module.write_lock() is db_module.write_lock()
