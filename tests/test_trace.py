"""core/trace.py - data/jarvis.db:traces tablosuna fail-soft çağrı izleme.

Faz 6.9 (v2 §9). `pending_tasks.py`/`memory.py` ile aynı fail-soft ilkesi:
her DB istisnası → logger.warning + güvenli varsayılan (record_trace no-op,
list_traces []).
"""

import time

import pytest

from src.jarvis.core import db as db_module
from src.jarvis.core import trace


def test_record_trace_writes_row(tmp_path):
    dbp = str(tmp_path / "t.db")
    trace.record_trace(
        "router",
        model="qwen2.5:3b",
        input_summary="saat kaç",
        duration_ms=42,
        result="success",
        db_path=dbp,
    )

    conn = db_module.get_connection(db_path=dbp)
    row = conn.execute(
        "SELECT role, model, input_summary, duration_ms, result FROM traces"
    ).fetchone()
    assert row["role"] == "router"
    assert row["model"] == "qwen2.5:3b"
    assert row["input_summary"] == "saat kaç"
    assert row["duration_ms"] == 42
    assert row["result"] == "success"


def test_list_traces_newest_first_and_limit(tmp_path):
    dbp = str(tmp_path / "t.db")
    for i in range(5):
        trace.record_trace("tool_agent", input_summary=f"adim {i}", db_path=dbp)

    rows = trace.list_traces(limit=2, db_path=dbp)
    assert [r["input_summary"] for r in rows] == ["adim 4", "adim 3"]


def test_list_traces_empty_db(tmp_path):
    assert trace.list_traces(db_path=str(tmp_path / "t.db")) == []


def test_record_trace_failsoft_on_bad_db(monkeypatch):
    monkeypatch.setattr(
        trace.db_module,
        "get_connection",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")),
    )
    # istisna FIRLATMAMALI
    trace.record_trace("router", input_summary="x")
    assert trace.list_traces() == []


def test_record_trace_failsoft_on_invalid_result(tmp_path):
    dbp = str(tmp_path / "t.db")
    # CHECK: result IN ('success','error','guardrail_blocked','approval_denied')
    trace.record_trace("router", result="banana", db_path=dbp)
    assert trace.list_traces(db_path=dbp) == []  # yazılmadı, ama çökmedi


def test_summarize_truncates_long_text():
    long = "x" * 500
    out = trace._summarize(long)
    assert len(out) <= trace._SUMMARY_CHAR_LIMIT + 1  # + "…"
    assert out.endswith("…")
    assert trace._summarize("kısa") == "kısa"
    assert trace._summarize(None) is None


def test_traced_context_manager_records_duration(tmp_path):
    dbp = str(tmp_path / "t.db")
    with trace.traced("orchestrator", model="hermes3:8b", input_summary="merhaba", db_path=dbp):
        time.sleep(0.01)

    row = trace.list_traces(db_path=dbp)[0]
    assert row["role"] == "orchestrator"
    assert row["result"] == "success"
    assert row["duration_ms"] >= 5


def test_traced_marks_error_on_exception_and_reraises(tmp_path):
    dbp = str(tmp_path / "t.db")
    with pytest.raises(ValueError):
        with trace.traced("router", db_path=dbp):
            raise ValueError("boom")

    assert trace.list_traces(db_path=dbp)[0]["result"] == "error"


def test_traced_allows_result_and_token_override(tmp_path):
    dbp = str(tmp_path / "t.db")
    with trace.traced("tool:run_command", db_path=dbp) as t:
        t.result = "approval_denied"
        t.token_count = 17

    row = trace.list_traces(db_path=dbp)[0]
    assert row["result"] == "approval_denied"
    assert row["token_count"] == 17
