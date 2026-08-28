"""core/continuous_runner.py - kosul izleyen daemon thread + FileMtimeWatcher."""

import os
import threading
import time

from src.jarvis.core.continuous_runner import (
    ContinuousWatcher,
    FileMtimeWatcher,
    ContinuousRunner,
    build_watchers,
)
from src.jarvis.core.scheduler import WatcherSpec


class _FakeHub:
    def __init__(self):
        self.events = []

    def submit_event(self, evt):
        self.events.append(evt)


def test_file_mtime_watcher_no_change_returns_none(tmp_path):
    f = tmp_path / "a.md"
    f.write_text("x", encoding="utf-8")
    w = FileMtimeWatcher("a", str(f), "degisti")
    assert w.check() is None


def test_file_mtime_watcher_fires_once_on_change(tmp_path):
    f = tmp_path / "a.md"
    f.write_text("x", encoding="utf-8")
    w = FileMtimeWatcher("a", str(f), "degisti")
    future = f.stat().st_mtime + 10
    os.utime(f, (future, future))
    assert w.check() == "degisti"
    assert w.check() is None  # ikinci kez tetiklenmez


def test_file_mtime_watcher_missing_file_returns_none(tmp_path):
    w = FileMtimeWatcher("a", str(tmp_path / "nope.md"), "x")
    assert w.check() is None


def test_build_watchers_creates_file_mtime():
    ws = build_watchers([WatcherSpec("inbox", "file_mtime", {"path": "p.md", "text": "t"})])
    assert len(ws) == 1 and isinstance(ws[0], FileMtimeWatcher) and ws[0].name == "inbox"


def test_poll_once_enqueues_on_trigger():
    class Firing(ContinuousWatcher):
        name = "f"

        def check(self):
            return "olay"

    hub = _FakeHub()
    ContinuousRunner(hub, threading.Event(), [Firing()])._poll_once()
    assert len(hub.events) == 1
    assert hub.events[0].source == "continuous" and hub.events[0].text == "olay"


def test_poll_once_survives_watcher_exception():
    class Boom(ContinuousWatcher):
        name = "boom"

        def check(self):
            raise RuntimeError("patladi")

    class Ok(ContinuousWatcher):
        name = "ok"

        def check(self):
            return "tamam"

    hub = _FakeHub()
    ContinuousRunner(hub, threading.Event(), [Boom(), Ok()])._poll_once()  # raise ETMEMELI
    assert [e.text for e in hub.events] == ["tamam"]


def test_runner_thread_honors_stop_event():
    stop = threading.Event()
    r = ContinuousRunner(_FakeHub(), stop, [], poll_interval=0.01)
    r.start()
    time.sleep(0.05)
    stop.set()
    r._thread.join(timeout=1)
    assert not r._thread.is_alive()
