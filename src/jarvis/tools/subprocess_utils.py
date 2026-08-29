"""Ateşle-ve-unut alt süreç başlatma - `communicate()`/`wait()` YOK.

`RunCommandTool`'un `subprocess.Popen(...).communicate(timeout=15)` modeli
kısa komutlar için; bir Claude Code oturumu ise dakikalarca/saatlerce sürebilir
ve JARVIS'in ana thread'ini bloklaması kabul edilemez. `spawn_detached()` süreci
başlatıp DÖNER - JARVIS süreci kapansa bile çocuk yaşamaya devam eder (Windows'ta
`DETACHED_PROCESS` / `CREATE_NEW_PROCESS_GROUP`, diğer OS'te `start_new_session`).

Faz 6.7 (`CreateProjectTool`) ilk kullanıcısı; Faz 6.10.3'te (`claude -p` yazma
modu) yeniden kullanılabilir.
"""

import logging
import os
import subprocess

logger = logging.getLogger("jarvis.tools.subprocess_utils")

_WINDOWS = os.name == "nt"

# Jarvis'in başlattığı `claude` süreçleri ASLA API key kullanmamalı - kullanıcının
# mevcut aboneliğiyle (~/.claude oturumu) çalışmalı (kullanıcı kararı). Bu
# değişkenler çocuk env'inden çıkarılır ki .env'de/ortamda bir key olsa bile
# `claude` onu kullanamasın, abonelik girişine düşsün.
_API_KEY_ENV_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


def spawn_detached(
    cmd: "list[str] | str",
    cwd: str,
    *,
    new_console: bool = False,
    scrub_api_key: bool = True,
) -> None:
    """`cmd`'i `cwd` dizininde ateşle-ve-unut başlatır (Popen, `wait()`/
    `communicate()` çağrılmaz).

    `cmd` bir list ise doğrudan; bir str ise `shell=True` (Windows `start` gibi
    kabuk-builtin'leri için). `cwd` ÇAĞIRANIN sorumluluğunda doğrulanmış olmalı
    (Jarvis'te `CreateProjectTool` `_PROJECTS_ROOT` + `is_safe_component_name`).

    `new_console=True` (Windows): çocuk KENDİ görünür konsol penceresini alır
    (`CREATE_NEW_CONSOLE`) - `claude`'un interaktif/login akışı o pencerede
    görünsün diye. `False`: `DETACHED_PROCESS` (pencere yok, tam arka plan).

    `scrub_api_key=True` (varsayılan): çocuk env'inden `ANTHROPIC_API_KEY` /
    `ANTHROPIC_AUTH_TOKEN` çıkarılır (bkz. modül notu).

    Başlatma başarısızsa (`FileNotFoundError`/`OSError`) `RuntimeError` fırlatır -
    çağıran bunu kullanıcıya "claude PATH'te mi?" gibi net bir mesaja çevirebilir.
    """
    env = None
    if scrub_api_key:
        env = os.environ.copy()
        for key in _API_KEY_ENV_VARS:
            env.pop(key, None)

    kwargs: dict = dict(
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        shell=isinstance(cmd, str),
    )
    if _WINDOWS:
        base = subprocess.CREATE_NEW_CONSOLE if new_console else subprocess.DETACHED_PROCESS
        kwargs["creationflags"] = base | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True

    try:
        # noqa: S603 - ateşle-ve-unut; cwd çağıran tarafından doğrulanmış.
        subprocess.Popen(cmd, **kwargs)
    except (FileNotFoundError, OSError) as exc:
        raise RuntimeError(f"Süreç başlatılamadı ({cmd!r}): {exc}") from exc
    logger.info("Detached süreç başlatıldı: %r (cwd=%s)", cmd, cwd)
