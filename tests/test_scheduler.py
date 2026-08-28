"""core/scheduler.py - cron-zamanli InputEvent uretici + config yukleme."""

import threading
from datetime import datetime

import pytest

from src.jarvis.core.scheduler import (
    ScheduledTask,
    WatcherSpec,
    Scheduler,
    load_scheduled_config,
)


def _write(p, body: str):
    p.write_text(body, encoding="utf-8")
    return str(p)


def test_load_missing_file_returns_empty(tmp_path):
    assert load_scheduled_config(str(tmp_path / "nope.yaml")) == ([], [])


def test_load_valid_config(tmp_path):
    path = _write(
        tmp_path / "s.yaml",
        "scheduled:\n"
        "  - name: morning\n"
        "    cron: '0 8 * * *'\n"
        "    text: gunluk ozet\n"
        "watchers:\n"
        "  - name: inbox\n"
        "    type: file_mtime\n"
        "    path: jarvis_workspace/inbox.md\n"
        "    text: inbox degisti\n",
    )
    tasks, watchers = load_scheduled_config(path)
    assert tasks == [ScheduledTask("morning", "0 8 * * *", "gunluk ozet")]
    assert watchers == [
        WatcherSpec("inbox", "file_mtime", {"path": "jarvis_workspace/inbox.md", "text": "inbox degisti"})
    ]


def test_load_missing_field_raises(tmp_path):
    path = _write(tmp_path / "s.yaml", "scheduled:\n  - name: x\n    cron: '0 8 * * *'\n")  # text yok
    with pytest.raises(ValueError):
        load_scheduled_config(path)


def test_load_invalid_cron_raises(tmp_path):
    path = _write(tmp_path / "s.yaml", "scheduled:\n  - name: x\n    cron: 'bogus cron'\n    text: y\n")
    with pytest.raises(ValueError):
        load_scheduled_config(path)


def test_load_unknown_watcher_type_raises(tmp_path):
    path = _write(tmp_path / "s.yaml", "watchers:\n  - name: x\n    type: quantum_sensor\n")
    with pytest.raises(ValueError):
        load_scheduled_config(path)


def test_due_detects_cron_hit_once_per_window():
    sched = Scheduler(hub=None, stop_event=threading.Event(),
                      tasks=[ScheduledTask("m", "0 8 * * *", "ozet")])
    before = datetime(2026, 1, 1, 7, 59)
    after = datetime(2026, 1, 1, 8, 1)
    assert [t.name for t in sched._due(before, after)] == ["m"]
    # ayni saat ilerlemezse tetiklenmez
    assert sched._due(after, datetime(2026, 1, 1, 8, 5)) == []
    # iki periyot atlansa bile TEK gorev (birikmez)
    assert [t.name for t in sched._due(datetime(2026, 1, 1, 7, 0), datetime(2026, 1, 1, 10, 0))] == ["m"]


def test_run_thread_honors_stop_event():
    import time

    class _FakeHub:
        def submit_event(self, evt): pass

    stop = threading.Event()
    sched = Scheduler(_FakeHub(), stop, tasks=[], poll_interval=0.01)
    sched.start()
    time.sleep(0.05)
    stop.set()
    sched._thread.join(timeout=1)
    assert not sched._thread.is_alive()


def test_run_submits_event_when_due(monkeypatch):
    events = []

    class _FakeHub:
        def submit_event(self, evt): events.append(evt)

    # now_fn ilk cagrida 07:59, sonraki cagrilarda 08:01 -> ilk poll'da due
    seq = iter([datetime(2026, 1, 1, 7, 59), datetime(2026, 1, 1, 8, 1),
                datetime(2026, 1, 1, 8, 1), datetime(2026, 1, 1, 8, 1)])
    stop = threading.Event()

    def fake_now():
        try:
            return next(seq)
        except StopIteration:
            stop.set()
            return datetime(2026, 1, 1, 8, 1)

    sched = Scheduler(_FakeHub(), stop, tasks=[ScheduledTask("m", "0 8 * * *", "ozet")],
                      poll_interval=0.0, now_fn=fake_now)
    sched.start()
    sched._thread.join(timeout=1)
    assert [e.text for e in events] == ["ozet"]
    assert events[0].source == "scheduled"
