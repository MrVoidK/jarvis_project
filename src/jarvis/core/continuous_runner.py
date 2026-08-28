"""Surekli izleme - bir kosulu (bu fazda: bir dosyanin mtime'i) izleyen daemon
thread; kosul tetiklenince `hub.submit_event(InputEvent("continuous", text))`.

`core/scheduler.py` ile ayni uretici deseni; `stop_event` ile kesilebilir uyku.
CERCEVE (ContinuousWatcher ABC) + TEK somut tip (FileMtimeWatcher). IoT/MCP
kaynaklari ileride ayni ABC'yi implemente eder - `_poll_once()` govdesi degismez.
"""

import logging
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from src.jarvis.core.input_hub import InputEvent
from src.jarvis.core.scheduler import WatcherSpec

logger = logging.getLogger("jarvis.core.continuous_runner")


class ContinuousWatcher(ABC):
    """Bir kosulu yoklar; tetiklendiyse uretilecek olay metnini, aksi halde
    None dondurur. `check()` HIZLI olmali (poll dongusunde senkron cagrilir)."""

    name: str

    @abstractmethod
    def check(self) -> Optional[str]:
        raise NotImplementedError


class FileMtimeWatcher(ContinuousWatcher):
    """Bir dosyanin son-degistirilme zamani (mtime) degistiginde tetiklenir.
    Ilk `check()`'te tetiklenmez (kurulustaki mtime taban alinir)."""

    def __init__(self, name: str, path: str, text: str) -> None:
        self.name = name
        self._path = Path(path)
        self._text = text
        self._last_mtime = self._current_mtime()

    def _current_mtime(self) -> Optional[float]:
        try:
            return self._path.stat().st_mtime
        except OSError:
            return None

    def check(self) -> Optional[str]:
        mtime = self._current_mtime()
        if mtime is not None and mtime != self._last_mtime:
            self._last_mtime = mtime
            return self._text
        return None


def build_watchers(specs: "list[WatcherSpec]") -> "list[ContinuousWatcher]":
    """WatcherSpec listesini somut watcher'lara cevirir. Bilinmeyen type zaten
    `scheduler.load_scheduled_config`'te elenmis - buraya yalnizca bilinen
    tipler gelir (else dali savunma amacli)."""
    watchers: list[ContinuousWatcher] = []
    for spec in specs:
        if spec.type == "file_mtime":
            watchers.append(FileMtimeWatcher(spec.name, spec.params["path"], spec.params["text"]))
        else:  # normalde ulasilamaz
            logger.warning("build_watchers: bilinmeyen type atlandi: %s", spec.type)
    return watchers


class ContinuousRunner:
    """Watcher'lari periyodik yoklayan tek daemon thread (jarvis-continuous)."""

    def __init__(
        self,
        hub,
        stop_event: threading.Event,
        watchers: "list[ContinuousWatcher]",
        poll_interval: float = 5.0,
    ) -> None:
        self._hub = hub
        self._stop_event = stop_event
        self._watchers = list(watchers)
        self._poll_interval = poll_interval
        self._thread = threading.Thread(target=self._run, name="jarvis-continuous", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _poll_once(self) -> None:
        for watcher in self._watchers:
            try:
                result = watcher.check()
            except Exception as exc:  # noqa: BLE001 - bir bozuk watcher thread'i oldurmesin
                logger.warning(
                    "continuous: '%s' check() hatasi: %s", getattr(watcher, "name", "?"), exc
                )
                continue
            if result:
                logger.info("continuous: '%s' tetiklendi -> kuyruga", watcher.name)
                self._hub.submit_event(InputEvent(source="continuous", text=result))

    def _run(self) -> None:
        while not self._stop_event.wait(self._poll_interval):
            self._poll_once()
