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

KAPSAM SINIRI: VK_MEDIA_* tuslari SADECE su an calan/kuyruktaki parcayi
kontrol eder - "filanca sarkiyi cal" gibi belirli-parca istekleri
karsilayamaz (bu, Spotify Web API'sinin arama+oynatma-baslatma yetenegiydi,
API kaldirilirken bilincli olarak birlikte gitti). Gercek kullanim testinde
bu bosluk, router'in boyle bir istek icin bir arac bulamayip HALUSINE
edilmis bir shell komutu (orn. "spotify play '...'") uretmesine yol acti -
`SearchMusicTool` bu bosluga API'siz bir cevap: Spotify'in kendi `spotify:
search:` URI semasini `os.startfile()` ile acar (shell=True/subprocess YOK,
enjeksiyon yuzeyi yok) - kullanici Spotify'da arama sonuclarini gorup
KENDISI oynatmayi baslatir (tam otomatik degil, ama isimle arama artik
mumkun ve API/OAuth gerekmiyor).
"""

import ctypes
import logging
import os
from urllib.parse import quote

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


_EMPTY_QUERY_MESSAGES = {
    "tr": "Hangi şarkıyı arayacağımı anlayamadım.",
    "en": "I didn't catch which song to search for.",
}
_SPOTIFY_NOT_INSTALLED_MESSAGES = {
    "tr": "Spotify yüklü değil gibi görünüyor, açamadım.",
    "en": "Spotify doesn't seem to be installed, I couldn't open it.",
}
_SEARCH_OPENED_MESSAGES = {
    "tr": "Spotify'da '{query}' aramasını açtım.",
    "en": "I've opened a Spotify search for '{query}'.",
}


class SearchMusicTool(Tool):
    """Spotify'i belirli bir sarki/sanatci aramasiyla acar - `spotify:search:`
    URI semasi, `os.startfile()` ile (shell=True/subprocess YOK, enjeksiyon
    yuzeyi yok). Kullanici sonucu goruntuleyip oynatmayi KENDISI baslatir -
    bkz. modul docstring'i, "KAPSAM SINIRI".
    """

    name = "search_music"
    description = (
        "Kullanicinin soyledigi sarki/sanatci adiyla Spotify'da arama acar "
        "(otomatik calmaz, kullanici sonra kendisi baslatir)."
    )
    risk_level = RiskLevel.LOW  # sadece Spotify'i acar, keyfi komut calistirmaz
    parameters_schema: dict = {
        "query": {"type": "string", "description": "Aranacak sarki veya sanatci adi."}
    }
    required_parameters: list[str] = ["query"]

    def execute(self, params: dict) -> str:
        lang = params.get("lang", "en")
        query = (params.get("query") or "").strip()
        if not query:
            return _localized(_EMPTY_QUERY_MESSAGES, lang)

        uri = f"spotify:search:{quote(query)}"
        try:
            os.startfile(uri)  # noqa: S606 - URI semasi, shell yorumlamasi yok
        except OSError:
            logger.warning("Spotify URI-semasi acilamadi (Spotify kurulu degil mi?): %r", uri)
            return _localized(_SPOTIFY_NOT_INSTALLED_MESSAGES, lang)

        logger.info("Spotify arama URI'si acildi: %r", uri)
        return _localized(_SEARCH_OPENED_MESSAGES, lang).format(query=query)
