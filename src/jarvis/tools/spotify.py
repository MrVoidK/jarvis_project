"""Spotify muzik kontrolu - projenin ilk dis API entegrasyonu (Faz 3.1).

Kurulum (kullanicinin kendisinin yapmasi gereken, bkz. docs/ROADMAP.md):
1. developer.spotify.com/dashboard'da bir app olustur, redirect URI olarak
   AYNEN `http://127.0.0.1:8888/callback` ekle.
2. Client ID/Secret'i proje kokundeki .env dosyasina SPOTIFY_CLIENT_ID/
   SPOTIFY_CLIENT_SECRET olarak ekle.
3. Bir kere `python -m src.jarvis.tools.spotify` calistirip tarayicidan izin ver
   (token cache'e yazilir, bundan sonra sessizce yenilenir).

Spotify OPSIYONEL bir ozellik - TTS'in zorunlu referans sesinin aksine, .env'de
credential yoksa uygulama CRASH ETMEZ, sadece Spotify tool'lari calismaz (net bir
TR/EN mesajla). Bu bilincli bir tercih: bir kullanicinin Spotify kullanmak
istememesi butun Jarvis'i kirmamalidir.
"""

import logging
import os
from typing import Optional

import spotipy
from dotenv import load_dotenv
from spotipy.oauth2 import SpotifyOAuth

from src.jarvis.core.paths import PROJECT_ROOT
from src.jarvis.core.risk import RiskLevel
from src.jarvis.tools.base import Tool

logger = logging.getLogger("jarvis.tools.spotify")

# Mutlak yollar (CWD'ye bagimli DEGIL) - core/paths.py'deki notes/workspace
# dersinin ayni sekilde uygulanmasi: .env her zaman proje kokunden okunur,
# Jarvis nereden baslatilirsa baslatilsin.
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

DEFAULT_REDIRECT_URI = "http://127.0.0.1:8888/callback"
SCOPE = "user-modify-playback-state user-read-playback-state"
CACHE_PATH = os.path.join(PROJECT_ROOT, ".spotify_cache")

_NOT_CONFIGURED_MESSAGES = {
    "tr": "Spotify henüz yapılandırılmamış. .env dosyasına SPOTIFY_CLIENT_ID ve SPOTIFY_CLIENT_SECRET eklemelisiniz.",
    "en": "Spotify isn't configured yet - add SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET to your .env file.",
}
_NOT_AUTHORIZED_MESSAGES = {
    "tr": "Spotify henüz yetkilendirilmedi. Terminalde 'python -m src.jarvis.tools.spotify' çalıştırıp tarayıcıdan izin verin.",
    "en": "Spotify hasn't been authorized yet. Run 'python -m src.jarvis.tools.spotify' in a terminal and approve it in your browser.",
}
_NO_ACTIVE_DEVICE_MESSAGES = {
    "tr": "Aktif bir Spotify cihazı bulamadım, lütfen Spotify uygulamasını açın.",
    "en": "I couldn't find an active Spotify device - please open the Spotify app.",
}
_NOT_FOUND_MESSAGES = {"tr": "Bu şarkıyı bulamadım.", "en": "I couldn't find that song."}
_EMPTY_QUERY_MESSAGES = {
    "tr": "Hangi şarkıyı çalacağımı anlamadım.",
    "en": "I didn't catch which song to play.",
}
_PLAYING_TEMPLATES = {"tr": "{track} çalıyor.", "en": "Playing {track}."}
_PAUSED_MESSAGES = {"tr": "Müziği duraklattım.", "en": "I've paused the music."}
_SKIPPED_MESSAGES = {"tr": "Sıradaki şarkıya geçtim.", "en": "Skipped to the next song."}
_ERROR_MESSAGES = {
    "tr": "Spotify ile bir sorun oluştu.",
    "en": "Something went wrong with Spotify.",
}


def _localized(messages: dict[str, str], lang: str) -> str:
    return messages.get(lang, messages["en"])


_auth_manager: Optional[SpotifyOAuth] = None  # modul-seviyesi, tek seferlik olusturma


def _get_auth_manager() -> Optional[SpotifyOAuth]:
    """SpotifyOAuth'u bir kez olusturup onbelllege alir; credential yoksa None doner."""
    global _auth_manager
    if _auth_manager is not None:
        return _auth_manager

    client_id = os.environ.get("SPOTIFY_CLIENT_ID")
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET")
    if not client_id or not client_secret:
        return None

    _auth_manager = SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=os.environ.get("SPOTIFY_REDIRECT_URI", DEFAULT_REDIRECT_URI),
        scope=SCOPE,
        cache_path=CACHE_PATH,
        open_browser=True,
    )
    return _auth_manager


def get_client() -> tuple[Optional[spotipy.Spotify], Optional[str]]:
    """Yetkilendirilmis bir Spotify client'i dondurur; olamiyorsa (None, hata_anahtari).

    hata_anahtari: "not_configured" (credential yok) veya "not_authorized" (credential
    var ama hic OAuth tamamlanmamis). `cache_handler.get_cached_token()` diskteki
    cache dosyasina BAKAR (ag/tarayici gerektirmez); `validate_token()` sadece token
    SUSU dolmussa sessizce (ag ile, tarayici ACMADAN) yeniler. Boylece "hic
    yetkilendirme yapilmadi" durumunu, spotipy'nin sesli bir komutun ortasinda
    beklenmedik sekilde tarayici acip bloke olmasina izin vermeden tespit ediyoruz.
    """
    auth_manager = _get_auth_manager()
    if auth_manager is None:
        return None, "not_configured"

    cached = auth_manager.cache_handler.get_cached_token()
    if not auth_manager.validate_token(cached):
        return None, "not_authorized"

    return spotipy.Spotify(auth_manager=auth_manager), None


class PlayMusicTool(Tool):
    """Bir sarki/sanatci arar ve o an aktif Spotify cihazinda calar."""

    name = "play_music"
    description = "Spotify'da bir şarkı arar ve çalar."
    # Geri alinabilir, guvenlik/gizlilik maliyeti yok (read_notes'un aksine) - yanlis
    # tetiklenmenin bedeli sadece "yanlis sarki calmasi", bu yuzden Dusuk risk.
    risk_level = RiskLevel.LOW

    def execute(self, params: dict) -> str:
        lang = params.get("lang", "en")
        query = (params.get("content") or "").strip()
        if not query:
            return _localized(_EMPTY_QUERY_MESSAGES, lang)

        client, error = get_client()
        if client is None:
            messages = _NOT_CONFIGURED_MESSAGES if error == "not_configured" else _NOT_AUTHORIZED_MESSAGES
            return _localized(messages, lang)

        try:
            results = client.search(q=query, type="track", limit=1)
            items = results.get("tracks", {}).get("items", [])
            if not items:
                return _localized(_NOT_FOUND_MESSAGES, lang)

            track = items[0]
            client.start_playback(uris=[track["uri"]])
        except spotipy.SpotifyException as exc:
            if exc.http_status == 404:
                logger.info("Aktif Spotify cihazi yok.")
                return _localized(_NO_ACTIVE_DEVICE_MESSAGES, lang)
            logger.error("Spotify play_music basarisiz: %s", exc)
            return _localized(_ERROR_MESSAGES, lang)

        track_name = track["name"]
        artist_name = track["artists"][0]["name"] if track["artists"] else ""
        display = f"{track_name} - {artist_name}" if artist_name else track_name
        logger.info("Caliniyor: %s", display)
        return _localized(_PLAYING_TEMPLATES, lang).format(track=display)


class PauseMusicTool(Tool):
    """Aktif Spotify cihazinda oynatmayi duraklatir."""

    name = "pause_music"
    description = "Spotify'da çalan müziği duraklatır."
    risk_level = RiskLevel.LOW

    def execute(self, params: dict) -> str:
        lang = params.get("lang", "en")
        client, error = get_client()
        if client is None:
            messages = _NOT_CONFIGURED_MESSAGES if error == "not_configured" else _NOT_AUTHORIZED_MESSAGES
            return _localized(messages, lang)

        try:
            client.pause_playback()
        except spotipy.SpotifyException as exc:
            if exc.http_status == 404:
                return _localized(_NO_ACTIVE_DEVICE_MESSAGES, lang)
            logger.error("Spotify pause_music basarisiz: %s", exc)
            return _localized(_ERROR_MESSAGES, lang)

        return _localized(_PAUSED_MESSAGES, lang)


class SkipTrackTool(Tool):
    """Aktif Spotify cihazinda siradaki sarkiya gecer."""

    name = "skip_track"
    description = "Spotify'da bir sonraki şarkıya geçer."
    risk_level = RiskLevel.LOW

    def execute(self, params: dict) -> str:
        lang = params.get("lang", "en")
        client, error = get_client()
        if client is None:
            messages = _NOT_CONFIGURED_MESSAGES if error == "not_configured" else _NOT_AUTHORIZED_MESSAGES
            return _localized(messages, lang)

        try:
            client.next_track()
        except spotipy.SpotifyException as exc:
            if exc.http_status == 404:
                return _localized(_NO_ACTIVE_DEVICE_MESSAGES, lang)
            logger.error("Spotify skip_track basarisiz: %s", exc)
            return _localized(_ERROR_MESSAGES, lang)

        return _localized(_SKIPPED_MESSAGES, lang)


if __name__ == "__main__":
    # Tek seferlik manuel yetkilendirme: tarayici acilip kullanici izin verince
    # token cache_path'e yazilir - bundan sonra voice-tetiklemeli cagrilar sessizce
    # calisir, tarayici tekrar acilmaz (bkz. get_client()).
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    auth_manager = _get_auth_manager()
    if auth_manager is None:
        print("SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET .env'de bulunamadi.")
    else:
        client = spotipy.Spotify(auth_manager=auth_manager)
        me = client.current_user()
        print(f"Yetkilendirme basarili: {me['display_name']} olarak giris yapildi.")
