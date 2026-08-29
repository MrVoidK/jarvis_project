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
edilmis bir shell komutu (orn. "spotify play '...'") uretmesine yol acti.

`SearchMusicTool` buna iki katmanli bir cevap: `tools/spotify_search.py`
(Client Credentials/app-only Spotify Web API - kisisel OAuth/giris YOK,
sadece SPOTIFY_CLIENT_ID/SECRET ile arama yapabilen bir "uygulama kimligi"
token'i) ile parcanin Spotify ID'sini bulup `spotify:track:<id>` URI'siyle
GERCEK otomatik calma tetikler; API yapilandirilmamis/basarisiz olursa
sessizce eski davranisa (`spotify:search:` ile sadece arama acma, kullanici
kendisi baslatir) duser - hicbir sekilde crash olmaz. Her iki durumda da
`os.startfile()` kullanilir (shell=True/subprocess YOK, enjeksiyon yuzeyi
yok).
"""

import ctypes
import logging
import os
import re
from urllib.parse import quote

from src.jarvis.core.risk import RiskLevel
from src.jarvis.tools import spotify_search
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

# Ses seviyesi (2026-08-29 "pre-6.10 cilalama"): artik MUTLAK kontrol de var.
# `pycaw` (Windows Core Audio) ile mevcut seviye okunup yuzde-puani cinsinden
# ayarlaniyor - "sesi 84 yap" (SetVolumeTool), "biraz/cok ac" (delta). pycaw
# yoksa/COM hatasi varsa FAIL-SOFT: eski VK_VOLUME_* keypress yoluna dusulur
# (her keypress ~%2, delta//2 kez basilir).
VOLUME_DELTA_DEFAULT = 12  # yuzde puani - "sesi ac/kis" (amount verilmezse)
VOLUME_DELTA_SMALL = 6     # "biraz" / "az" / "a bit"
VOLUME_DELTA_LARGE = 30    # "cok" / "epey" / "a lot"
_VOLUME_SMALL_WORDS = {"biraz", "az", "hafif", "a bit", "a little", "slightly"}
_VOLUME_LARGE_WORDS = {
    "cok", "çok", "fazla", "epey", "baya", "bayagi", "bayağı",
    "a lot", "lots", "way up", "much", "lot",
}

_VOLUME_ENDPOINT_UNSET = object()
_volume_endpoint = _VOLUME_ENDPOINT_UNSET  # lazy: ilk kullanimda pycaw denenir


def _get_volume_endpoint():
    """pycaw `IAudioEndpointVolume`'i lazy olusturur - FAIL-SOFT (pycaw kurulu
    degil / COM hatasi / ses aygiti yok -> None; cagiranlar keypress'e duser)."""
    global _volume_endpoint
    if _volume_endpoint is _VOLUME_ENDPOINT_UNSET:
        try:
            from ctypes import POINTER, cast

            from comtypes import CLSCTX_ALL
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

            speakers = AudioUtilities.GetSpeakers()
            interface = speakers.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            _volume_endpoint = cast(interface, POINTER(IAudioEndpointVolume))
        except Exception as exc:  # noqa: BLE001 - fail-soft (bkz. yukaridaki not)
            logger.warning("Mutlak ses kontrolu kullanilamiyor (pycaw): %s", exc)
            _volume_endpoint = None
    return _volume_endpoint


def pct_control_available() -> bool:
    return _get_volume_endpoint() is not None


def _get_volume_percent() -> "int | None":
    endpoint = _get_volume_endpoint()
    if endpoint is None:
        return None
    try:
        return round(endpoint.GetMasterVolumeLevelScalar() * 100)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Ses seviyesi okunamadi: %s", exc)
        return None


def _set_volume_percent(pct: int) -> bool:
    endpoint = _get_volume_endpoint()
    if endpoint is None:
        return False
    try:
        endpoint.SetMasterVolumeLevelScalar(max(0, min(100, int(pct))) / 100.0, None)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Ses seviyesi ayarlanamadi: %s", exc)
        return False


_ULONG_PTR = ctypes.c_uint64 if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_uint32


class _KeyBdInput(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class _MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class _HardwareInput(ctypes.Structure):
    _fields_ = [
        ("uMsg", ctypes.c_ulong),
        ("wParamL", ctypes.c_short),
        ("wParamH", ctypes.c_ushort),
    ]


class _InputUnion(ctypes.Union):
    # KRITIK: Windows'un gercek INPUT union'i (winuser.h) MOUSEINPUT/
    # KEYBDINPUT/HARDWAREINPUT'un UCUNU de icerir - union'in boyutu EN BUYUK
    # uyeye (MOUSEINPUT, 64-bit'te KEYBDINPUT'tan buyuk) gore belirlenir.
    # SADECE `ki`yi tanimlamak (eski hali) union'i 24 byte, tum _Input struct'ini
    # 32 byte yapiyordu - GERCEK sizeof(INPUT) 64-bit'te 40 byte. SendInput,
    # verilen cbSize parametresi kendi ic sizeof(INPUT)'iyla eslesmezse hicbir
    # hata/exception FIRLATMADAN sessizce 0 (basarisiz) donuyor - bu yuzden
    # tuslar "gonderildi" loglaniyordu ama fiziksel olarak hicbir sey olmuyordu
    # (gercek kullanim testinde bulundu - bkz. git log).
    _fields_ = [("ki", _KeyBdInput), ("mi", _MouseInput), ("hi", _HardwareInput)]


class _Input(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("union", _InputUnion)]


def _send_vk(vk_code: int, times: int = 1) -> None:
    """Verilen sanal tus kodu icin `times` kez key-down + key-up olayi gonderir.

    Medya tuslari "extended key" sayildigi icin KEYEVENTF_EXTENDEDKEY bayragi
    gerekiyor - bu bayrak olmadan bazi sistemlerde tus yok sayilabiliyor.

    `times` > 1 SADECE ses seviyesi araclari icin (D3) - VK_VOLUME_* tek
    keypress ~%2 oldugundan bir "kademe" birkac keypress. play/pause/next/prev
    icin varsayilan 1 (tekrarli gondermek anlamsiz).

    SendInput'un DONUS DEGERI (basariyla islenen olay sayisi) BURADA
    kontrol ediliyor - eskiden kontrol edilmiyordu, bu yuzden struct-boyutu
    uyumsuzlugu gibi sessiz basarisizliklar hic fark edilmiyordu (bkz.
    yukaridaki _InputUnion notu).
    """
    extra = _ULONG_PTR(0)
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
    for _ in range(max(1, times)):
        sent_down = ctypes.windll.user32.SendInput(1, ctypes.pointer(key_down), ctypes.sizeof(_Input))
        sent_up = ctypes.windll.user32.SendInput(1, ctypes.pointer(key_up), ctypes.sizeof(_Input))
        if sent_down == 0 or sent_up == 0:
            logger.warning(
                "SendInput basarisiz oldu (vk=0x%X, down=%d, up=%d) - "
                "tus fiilen gonderilmedi (Windows GetLastError: %d).",
                vk_code,
                sent_down,
                sent_up,
                ctypes.GetLastError(),
            )
            return


_PLAY_PAUSE_MESSAGES = {
    "tr": "Oynatma/duraklatma komutunu gönderdim.",
    "en": "I've toggled play/pause.",
}
_NEXT_TRACK_MESSAGES = {"tr": "Sonraki şarkıya geçtim.", "en": "Skipped to the next track."}
_PREV_TRACK_MESSAGES = {"tr": "Önceki şarkıya döndüm.", "en": "Went back to the previous track."}
_VOLUME_UP_MESSAGES = {"tr": "Sesi açtım.", "en": "I've turned the volume up."}
_VOLUME_DOWN_MESSAGES = {"tr": "Sesi kıstım.", "en": "I've turned the volume down."}
_VOLUME_SET_MESSAGES = {"tr": "Sesi %{pct} yaptım.", "en": "Volume is now {pct}%."}
_VOLUME_ABS_UNAVAILABLE_MESSAGES = {
    "tr": "Kesin ses seviyesi kontrolü şu an kullanılamıyor, sadece kademeli açıp kısabilirim.",
    "en": "Exact volume control isn't available right now; I can only step it up or down.",
}
_VOLUME_BAD_LEVEL_MESSAGES = {
    "tr": "Hangi seviyeye ayarlayacağımı anlayamadım (0-100 arası bir sayı söyleyin).",
    "en": "I didn't catch the level to set (say a number from 0 to 100).",
}


def _localized(messages: dict[str, str], lang: str) -> str:
    return messages.get(lang, messages["en"])


def _resolve_volume_delta(params: dict) -> int:
    """`amount` parametresinden yuzde-puani cinsinden (isaretsiz) miktar cikarir.

    'biraz'/'az' -> VOLUME_DELTA_SMALL, 'cok'/'epey' -> VOLUME_DELTA_LARGE,
    icinde bir sayi -> 1..100 clamp, aksi halde -> VOLUME_DELTA_DEFAULT.
    """
    amount = str(params.get("amount") or "").strip().lower()
    if not amount:
        return VOLUME_DELTA_DEFAULT
    digits = re.sub(r"[^\d]", "", amount)
    if digits:
        return max(1, min(100, int(digits)))
    if amount in _VOLUME_SMALL_WORDS:
        return VOLUME_DELTA_SMALL
    if amount in _VOLUME_LARGE_WORDS:
        return VOLUME_DELTA_LARGE
    return VOLUME_DELTA_DEFAULT


def _apply_relative_volume(direction: int, params: dict, lang: str) -> str:
    """direction = +1 (ac) / -1 (kis). pycaw varsa mevcut seviye + direction*delta
    olarak ayarlar ve yeni yuzdeyi soyler; yoksa keypress'e duser."""
    delta = _resolve_volume_delta(params)
    current = _get_volume_percent()
    if current is not None:
        target = max(0, min(100, current + direction * delta))
        if _set_volume_percent(target):
            logger.info("Ses seviyesi %d%% -> %d%%.", current, target)
            return _localized(_VOLUME_SET_MESSAGES, lang).format(pct=target)
    vk = VK_VOLUME_UP if direction > 0 else VK_VOLUME_DOWN
    presses = max(1, delta // 2)  # her keypress ~%2
    _send_vk(vk, times=presses)
    logger.info("Medya ses tusu gonderildi (keypress fallback, %d kez).", presses)
    return _localized(_VOLUME_UP_MESSAGES if direction > 0 else _VOLUME_DOWN_MESSAGES, lang)


class MediaPlayPauseTool(Tool):
    """Fiziksel play/pause medya tusunu (TOGGLE) gonderir."""

    name = "media_play_pause"
    description = (
        "Muzik oynatmayi/duraklatmayi acip kapatir (toggle - fiziksel bir "
        "tus, ayri 'play' ve 'pause' yoktur). Kullanici 'muzigi durdur', "
        "'duraklat', 'stop music', 'pause' gibi bir sey soyledigi HER "
        "SEFERINDE bu araci kullan."
    )
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
    description = (
        "Muzik calarda bir sonraki parcaya gecer (belirli bir sarki adi "
        "SOYLENMEDEN, sadece mevcut kuyrukta ilerler). Kullanici 'sarki "
        "degistir', 'sonraki sarki', 'gec', 'next song', 'skip', 'change "
        "song' gibi bir sey soyledigi HER SEFERINDE bu araci kullan - "
        "belirli bir sarki/sanatci ADI soylerse onun yerine search_music "
        "kullan."
    )
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
    description = (
        "Muzik calarda bir onceki parcaya doner. Kullanici 'onceki sarki', "
        "'geri git', 'previous song', 'go back' gibi bir sey soyledigi HER "
        "SEFERINDE bu araci kullan."
    )
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
    description = (
        "Sistem ses seviyesini GORECELI olarak artirir ('biraz', 'cok' veya "
        "belirtilmezse orta kademe). Kullanici 'sesi ac', 'sesi artir', 'volume "
        "up', 'louder' derse kullan. 'biraz'/'az'/'cok' ifadesini `amount` olarak "
        "gecir. KESIN bir seviye ('sesi 50 yap', 'set volume to 30') icin bunu "
        "DEGIL set_volume kullan."
    )
    risk_level = RiskLevel.LOW
    parameters_schema: dict = {
        "amount": {
            "type": "string",
            "description": "Istege bagli: 'biraz', 'cok' veya yuzde-puani miktari.",
        }
    }
    required_parameters: list[str] = []

    def execute(self, params: dict) -> str:
        return _apply_relative_volume(+1, params, params.get("lang", "en"))


class MediaVolumeDownTool(Tool):
    """Sistem sesini bir kademe azaltir."""

    name = "media_volume_down"
    description = (
        "Sistem ses seviyesini GORECELI olarak azaltir ('biraz', 'cok' veya "
        "belirtilmezse orta kademe). Kullanici 'sesi kis', 'sesi azalt', 'volume "
        "down', 'quieter' derse kullan. 'biraz'/'az'/'cok' ifadesini `amount` "
        "olarak gecir. KESIN bir seviye icin set_volume kullan."
    )
    risk_level = RiskLevel.LOW
    parameters_schema: dict = {
        "amount": {
            "type": "string",
            "description": "Istege bagli: 'biraz', 'cok' veya yuzde-puani miktari.",
        }
    }
    required_parameters: list[str] = []

    def execute(self, params: dict) -> str:
        return _apply_relative_volume(-1, params, params.get("lang", "en"))


class SetVolumeTool(Tool):
    """Sistem sesini KESIN bir yuzdeye ayarlar (pycaw; yoksa fail-soft mesaj)."""

    name = "set_volume"
    description = (
        "Sistem ses seviyesini KESIN bir yuzdeye ayarlar. Kullanici BELIRLI bir "
        "seviye soyledigi zaman kullan: 'sesi 84 yap', 'sesi %50 yap', 'set "
        "volume to 30', 'sesi yariya indir' (level=50). Sadece 'ac'/'kis'/'biraz'/"
        "'cok' derse bunu DEGIL media_volume_up / media_volume_down kullan."
    )
    risk_level = RiskLevel.LOW
    parameters_schema: dict = {
        "level": {"type": "string", "description": "Hedef ses seviyesi, 0-100 arasi bir sayi."}
    }
    required_parameters: list[str] = ["level"]

    def execute(self, params: dict) -> str:
        lang = params.get("lang", "en")
        digits = re.sub(r"[^\d]", "", str(params.get("level") or ""))
        if not digits:
            return _localized(_VOLUME_BAD_LEVEL_MESSAGES, lang)
        level = max(0, min(100, int(digits)))
        if _set_volume_percent(level):
            logger.info("Ses seviyesi %d%% olarak ayarlandi.", level)
            return _localized(_VOLUME_SET_MESSAGES, lang).format(pct=level)
        return _localized(_VOLUME_ABS_UNAVAILABLE_MESSAGES, lang)


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
_PLAYING_MESSAGES = {
    "tr": "'{query}' çalınıyor.",
    "en": "Playing '{query}'.",
}


class SearchMusicTool(Tool):
    """Belirli bir sarkiyi Spotify'da CALAR (yapilandirilmissa) veya en azindan
    aramasini acar (fallback).

    IKI KATMANLI DAVRANIS: once `spotify_search.find_track_id()` ile (Client
    Credentials/app-only Spotify Web API, kisisel OAuth YOK - bkz. o modulun
    docstring'i) parcanin Spotify ID'sini bulmayi dener; bulursa
    `spotify:track:<id>` URI'siyle yerel istemcide GERCEK otomatik calma
    tetiklenir. API yapilandirilmamis/basarisiz olursa (`.env`'de
    SPOTIFY_CLIENT_ID/SECRET yoksa veya ag hatasi varsa) sessizce eski
    davranisa duser: `spotify:search:` ile sadece arama acilir, kullanici
    kendisi baslatir. Ikisinde de `os.startfile()` kullanilir (shell=True/
    subprocess YOK, enjeksiyon yuzeyi yok).
    """

    name = "search_music"
    description = (
        "Kullanici belirli bir sarki/parca/sanatci CALMAK, ARAMAK veya DINLEMEK "
        "istedigi HER SEFERINDE bu araci kullan (orn. 'X sarkisini cal', "
        "'play X'). Mumkunse sarkiyi Spotify'da otomatik calar, degilse "
        "aramasini acar. Bunun icin run_command KULLANMA veya bir dosya "
        "yolu/URL UYDURMA - sadece bu araci cagir."
    )
    risk_level = RiskLevel.LOW  # sadece Spotify'i acar/calar, keyfi komut calistirmaz
    parameters_schema: dict = {
        "query": {"type": "string", "description": "Aranacak/calinacak sarki veya sanatci adi."}
    }
    required_parameters: list[str] = ["query"]

    def execute(self, params: dict) -> str:
        lang = params.get("lang", "en")
        query = (params.get("query") or "").strip()
        if not query:
            return _localized(_EMPTY_QUERY_MESSAGES, lang)

        track_id = spotify_search.find_track_id(query)
        if track_id:
            uri = f"spotify:track:{track_id}"
            success_messages = _PLAYING_MESSAGES
        else:
            uri = f"spotify:search:{quote(query)}"
            success_messages = _SEARCH_OPENED_MESSAGES

        try:
            os.startfile(uri)  # noqa: S606 - URI semasi, shell yorumlamasi yok
        except OSError:
            logger.warning("Spotify URI-semasi acilamadi (Spotify kurulu degil mi?): %r", uri)
            return _localized(_SPOTIFY_NOT_INSTALLED_MESSAGES, lang)

        logger.info("Spotify URI'si acildi: %r", uri)
        return _localized(success_messages, lang).format(query=query)
