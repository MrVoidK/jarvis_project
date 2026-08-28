"""Zamanlanmis gorevler - cron ifadesiyle tetiklenen, onceden tanimli komut
metinleri. `config/scheduled_tasks.yaml` (OPT-IN: dosya yoksa ozellik kapali).

Uretici deseni `core/input_hub.py:_mic_producer`/`_text_producer` ile ayni:
kendi daemon thread'inde calisir, `hub.submit_event(InputEvent("scheduled",
text))` ile ortak kuyruga yazar - AYRI bir guvenlik yolu YOK, olay normal
guardrail/dispatcher/risk zincirinden gecer (v2 §5).

APScheduler DEGIL (asiri agir - kendi thread pool'u/job store'u; ihtiyacimiz
"kuyruga metin koy"). Elde yazilmis cron parser DEGIL (kirilgan). `croniter`
(kucuk, saf-Python) ile - import GECIKMELI: croniter kurulu degilse net
`ImportError` yalnizca gercekten cron degerlendirilen yolda cikar.

Ic saat NAIVE `datetime.now()` (yerel saat) - cron ifadeleri yerel saatte
yorumlanir (croniter varsayilani). `tasks.ts` (pending_tasks) ayri: o UTC.
"""

import logging
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import yaml

from src.jarvis.core.input_hub import InputEvent
from src.jarvis.core.paths import PROJECT_ROOT

logger = logging.getLogger("jarvis.core.scheduler")

CONFIG_PATH = str(Path(PROJECT_ROOT) / "config" / "scheduled_tasks.yaml")

_VALID_WATCHER_TYPES = {"file_mtime"}


@dataclass(frozen=True)
class ScheduledTask:
    name: str
    cron: str
    text: str


@dataclass(frozen=True)
class WatcherSpec:
    name: str
    type: str
    params: dict


def _now() -> datetime:
    return datetime.now()  # naive/yerel - bkz. modul docstring'i


def _validate_cron(name: str, expr: str) -> None:
    from croniter import croniter  # gecikmeli import (bkz. modul docstring'i)

    if not croniter.is_valid(expr):
        raise ValueError(f"scheduled task {name!r}: gecersiz cron ifadesi {expr!r}.")


def load_scheduled_config(
    path: str = CONFIG_PATH,
) -> "tuple[list[ScheduledTask], list[WatcherSpec]]":
    """`config/scheduled_tasks.yaml`'i okur.

    Dosya YOKSA -> ([], []) (opt-in ozellik, sessizce kapali). Dosya VARSA ama
    bozuk / eksik alanli / gecersiz cron / bilinmeyen watcher type -> ValueError
    (core/security_config.py deseni: "dosyayi sen olusturdun, dogru olmali").
    """
    p = Path(path)
    if not p.is_file():
        return [], []

    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"scheduled_tasks.yaml parse edilemedi: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(
            "scheduled_tasks.yaml kokte bir YAML sozlugu olmali ('scheduled:' / 'watchers:')."
        )

    tasks: list[ScheduledTask] = []
    for i, item in enumerate(raw.get("scheduled") or []):
        if not isinstance(item, dict) or not all(item.get(k) for k in ("name", "cron", "text")):
            raise ValueError(f"scheduled[{i}]: 'name', 'cron', 'text' alanlari zorunlu.")
        _validate_cron(str(item["name"]), str(item["cron"]))
        tasks.append(ScheduledTask(str(item["name"]), str(item["cron"]), str(item["text"])))

    watchers: list[WatcherSpec] = []
    for i, item in enumerate(raw.get("watchers") or []):
        if not isinstance(item, dict) or not all(item.get(k) for k in ("name", "type")):
            raise ValueError(f"watchers[{i}]: 'name', 'type' alanlari zorunlu.")
        wtype = str(item["type"]).strip().lower()
        if wtype not in _VALID_WATCHER_TYPES:
            raise ValueError(
                f"watchers[{i}] ({item['name']}): bilinmeyen type {wtype!r} "
                f"(desteklenen: {sorted(_VALID_WATCHER_TYPES)})."
            )
        if wtype == "file_mtime" and not all(item.get(k) for k in ("path", "text")):
            raise ValueError(
                f"watchers[{i}] ({item['name']}): file_mtime icin 'path' ve 'text' zorunlu."
            )
        params = {k: v for k, v in item.items() if k not in ("name", "type")}
        watchers.append(WatcherSpec(str(item["name"]), wtype, params))

    return tasks, watchers


class Scheduler:
    """Cron-tetiklemeli `InputEvent` uretici. Tek daemon thread; `stop_event`
    ile kesilebilir uyku (`stop_event.wait`)."""

    def __init__(
        self,
        hub,
        stop_event: threading.Event,
        tasks: "list[ScheduledTask]",
        poll_interval: float = 30.0,
        now_fn: Callable[[], datetime] = _now,
    ) -> None:
        self._hub = hub
        self._stop_event = stop_event
        self._tasks = list(tasks)
        self._poll_interval = poll_interval
        self._now_fn = now_fn
        self._thread = threading.Thread(target=self._run, name="jarvis-scheduler", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _due(self, last_check: datetime, now: datetime) -> "list[ScheduledTask]":
        """(last_check, now] araligina denk gelen cron'lari olan gorevler.
        Bir due-window'da birden fazla cron hit olsa bile gorev BIR KEZ doner."""
        from croniter import croniter

        out: list[ScheduledTask] = []
        for task in self._tasks:
            try:
                nxt = croniter(task.cron, last_check).get_next(datetime)
            except Exception as exc:  # noqa: BLE001 - bir bozuk ifade digerlerini durdurmasin
                logger.warning("scheduler: '%s' cron degerlendirilemedi: %s", task.name, exc)
                continue
            if last_check < nxt <= now:
                out.append(task)
        return out

    def _run(self) -> None:
        last_check = self._now_fn()
        while not self._stop_event.wait(self._poll_interval):
            try:
                now = self._now_fn()
                # DST/saat-geri-alma korumasi: yerel saat geri alinirsa (sonbahar
                # DST donusu, NTP duzeltmesi) [last_check, now] TERS bir pencereye
                # doner - `_due` bunu yanlis degerlendirip bir fire'i bastirabilir
                # veya (bir sonraki normal pencerede) cift tetikleyebilir. Pencereyi
                # sifirlayip bir sonraki poll'e birak.
                if now < last_check:
                    last_check = now
                    continue
                for task in self._due(last_check, now):
                    logger.info("scheduler: '%s' tetiklendi -> kuyruga", task.name)
                    self._hub.submit_event(InputEvent(source="scheduled", text=task.text))
                last_check = now
            except Exception:  # noqa: BLE001 - bir hata daemon thread'i sessizce oldurmesin
                logger.exception("scheduler: poll dongusunde beklenmeyen hata")
