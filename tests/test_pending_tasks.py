"""core/pending_tasks.py - tasks tablosuna fail-soft bekleyen-onay kaydi."""

from src.jarvis.core import db as db_module
from src.jarvis.core.pending_tasks import record_pending, list_pending


def test_record_pending_writes_row(tmp_path):
    dbp = str(tmp_path / "t.db")
    task_id = record_pending("scheduled", "gunluk ozet", {"tool": "notes", "risk": "medium"}, db_path=dbp)
    assert isinstance(task_id, int)

    conn = db_module.get_connection(db_path=dbp)
    row = conn.execute("SELECT source, text, status, detail_json FROM tasks WHERE id = ?", (task_id,)).fetchone()
    assert row["source"] == "scheduled"
    assert row["text"] == "gunluk ozet"
    assert row["status"] == "pending"
    assert '"tool": "notes"' in row["detail_json"]


def test_list_pending_newest_first_and_limit(tmp_path):
    dbp = str(tmp_path / "t.db")
    for i in range(4):
        record_pending("continuous", f"olay {i}", {}, db_path=dbp)
    rows = list_pending(limit=2, db_path=dbp)
    assert [r["text"] for r in rows] == ["olay 3", "olay 2"]


def test_list_pending_empty_db(tmp_path):
    assert list_pending(db_path=str(tmp_path / "t.db")) == []


def test_record_pending_failsoft(monkeypatch):
    from src.jarvis.core import pending_tasks

    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(pending_tasks.db_module, "get_connection", boom)
    assert pending_tasks.record_pending("scheduled", "x", {}) is None
    assert pending_tasks.list_pending() == []
