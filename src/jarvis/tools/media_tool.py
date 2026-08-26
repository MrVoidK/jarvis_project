"""Yerel Windows medya tuslari - hicbir dis API/pip bagimliligi olmadan.

`tools/spotify.py`'nin yerini alir: Spotify Web API (OAuth, ag, rate-limit,
hesap gerekliligi) yerine, isletim sisteminin kendi sanal medya tuslarini
(`ctypes.windll.user32.SendInput`) simule ediyoruz - hangi uygulama
calarsa calsin (Spotify, YouTube, herhangi bir oynatici) calisir, cunku
bu tuslar OS seviyesinde, uygulamadan bagimsiz. Yeni pip bagimliligi
(`keyboard`/`pyautogui`) BILINCLI OLARAK eklenmedi - stdlib `ctypes` zaten
Win32 SendInput API'sine dogrudan erisim icin yeterli (CLAUDE.md'nin
"gereksiz dis bagimliliktan kacin" ilkesi).

ONEMLI DAVRANIS FARKI (spotify.py'ye kiyasla): VK_MEDIA_PLAY_PAUSE fiziksel
bir TOGGLE tusu - ayri bir "play" ve "pause" sanal kodu yok (spotipy'nin
ayri start_playback()/pause_playback() uc noktalarinin aksine). Jarvis,
tusu gonderdikten sonra muzigin gercekten calip calmadigini BILEMEZ - bu
yuzden MediaPlayPauseTool'un mesaji kesin bir durum iddia ETMEZ.
"""

import ctypes
import logging

from src.jarvis.core.risk import RiskLevel
from src.jarvis.tools.base import Tool

logger = logging.getLogger("jarvis.tools.media")

# Windows sanal tus kodlari (bkz. winuser.h VK_MEDIA_*/VK_VOLUME_*).
VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1
VK_MEDIA_PLAY_PAUSE = 0xB3
VK_VOLUME_DOWN = 0xAE
VK_VOLUME_UP = 0xAF

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
INPUT_KEYBOARD = 1


class _KeyBdInput(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _InputUnion(ctypes.Union):
    _fields_ = [("ki", _KeyBdInput)]


class _Input(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("union", _InputUnion)]


def _send_vk(vk_code: int) -> None:
    """Verilen sanal tus kodu icin bir key-down + key-up olayi gonderir.

    Medya tuslari "extended key" sayildigi icin KEYEVENTF_EXTENDEDKEY bayragi
    gerekiyor - bu bayrak olmadan bazi sistemlerde tus yok sayilabiliyor.
    """
    extra = ctypes.pointer(ctypes.c_ulong(0))
    key_down = _Input(
        type=INPUT_KEYBOARD,
        union=_InputUnion(
            ki=_KeyBdInput(vk_code, 0, KEYEVENTF_EXTENDEDKEY, 0, extra)
        ),
    )
    key_up = _Input(
        type=INPUT_KEYBOARD,
        union=_InputUnion(
            ki=_KeyBdInput(vk_code, 0, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, 0, extra)
        ),
    )
    ctypes.windll.user32.SendInput(1, ctypes.pointer(key_down), ctypes.sizeof(_Input))
    ctypes.windll.user32.SendInput(1, ctypes.pointer(key_up), ctypes.sizeof(_Input))


_PLAY_PAUSE_MESSAGES = {
    "tr": "Oynatma/duraklatma komutunu gönderdim.",
    "en": "I've toggled play/pause.",
}
_NEXT_TRACK_MESSAGES = {"tr": "Sonraki şarkıya geçtim.", "en": "Skipped to the next track."}
_PREV_TRACK_MESSAGES = {"tr": "Önceki şarkıya döndüm.", "en": "Went back to the previous track."}
_VOLUME_UP_MESSAGES = {"tr": "Sesi biraz açtım.", "en": "I've turned the volume up a bit."}
_VOLUME_DOWN_MESSAGES = {"tr": "Sesi biraz kıstım.", "en": "I've turned the volume down a bit."}


def _localized(messages: dict[str, str], lang: str) -> str:
    return messages.get(lang, messages["en"])


class MediaPlayPauseTool(Tool):
    """Fiziksel play/pause medya tusunu (TOGGLE) gonderir."""

    name = "media_play_pause"
    description = "Muzik oynatmayi/duraklatmayi acip kapatir (toggle)."
    risk_level = RiskLevel.LOW
    parameters_schema: dict = {}
    required_parameters: list[str] = []

    def execute(self, params: dict) -> str:
        lang = params.get("lang", "en")
        _send_vk(VK_MEDIA_PLAY_PAUSE)
        logger.info("Medya play/pause tusu gonderildi.")
        return _localized(_PLAY_PAUSE_MESSAGES, lang)


class MediaNextTrackTool(Tool):
    """Sonraki parcaya gecer."""

    name = "media_next_track"
    description = "Muzik calarda bir sonraki parcaya gecer."
    risk_level = RiskLevel.LOW
    parameters_schema: dict = {}
    required_parameters: list[str] = []

    def execute(self, params: dict) -> str:
        lang = params.get("lang", "en")
        _send_vk(VK_MEDIA_NEXT_TRACK)
        logger.info("Medya sonraki-parca tusu gonderildi.")
        return _localized(_NEXT_TRACK_MESSAGES, lang)


class MediaPreviousTrackTool(Tool):
    """Onceki parcaya doner."""

    name = "media_previous_track"
    description = "Muzik calarda bir onceki parcaya doner."
    risk_level = RiskLevel.LOW
    parameters_schema: dict = {}
    required_parameters: list[str] = []

    def execute(self, params: dict) -> str:
        lang = params.get("lang", "en")
        _send_vk(VK_MEDIA_PREV_TRACK)
        logger.info("Medya onceki-parca tusu gonderildi.")
        return _localized(_PREV_TRACK_MESSAGES, lang)


class MediaVolumeUpTool(Tool):
    """Sistem sesini bir kademe artirir."""

    name = "media_volume_up"
    description = "Sistem ses seviyesini artirir."
    risk_level = RiskLevel.LOW
    parameters_schema: dict = {}
    required_parameters: list[str] = []

    def execute(self, params: dict) -> str:
        lang = params.get("lang", "en")
        _send_vk(VK_VOLUME_UP)
        logger.info("Medya ses-artir tusu gonderildi.")
        return _localized(_VOLUME_UP_MESSAGES, lang)


class MediaVolumeDownTool(Tool):
    """Sistem sesini bir kademe azaltir."""

    name = "media_volume_down"
    description = "Sistem ses seviyesini azaltir."
    risk_level = RiskLevel.LOW
    parameters_schema: dict = {}
    required_parameters: list[str] = []

    def execute(self, params: dict) -> str:
        lang = params.get("lang", "en")
        _send_vk(VK_VOLUME_DOWN)
        logger.info("Medya ses-azalt tusu gonderildi.")
        return _localized(_VOLUME_DOWN_MESSAGES, lang)
