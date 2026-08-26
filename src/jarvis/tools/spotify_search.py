"""Spotify Web API'sinin Client Credentials (app-only) akisi - SADECE bir sarki
adini Spotify parca ID'sine cevirmek icin. `tools/media_tool.py:SearchMusicTool`
bu ID'yi `spotify:track:<id>` URI'sine sararak yerel Spotify istemcisinde GERCEK
otomatik calma tetikler.

BILINCLI TASARIM (eski tools/spotify.py'den kritik fark): kullanici OAuth'u/
kisisel giris/`.spotify_cache` YOK. Client Credentials akisi sadece bir
"uygulama kimligi" token'i doner - bu token'in kullanicinin hesabina, calma
gecmisine veya kisisel verisine HICBIR erisimi yok, SADECE genel arama
uc noktasini kullanabiliyor (bkz. Spotify Web API dokumantasyonu). Gercek
"calma" eylemi API uzerinden DEGIL, yerel URI protokolu (`os.startfile`)
uzerinden - zaten oturum acmis olan Spotify masaustu istemcisi bunu
karsiliyor. Bu yuzden token asla diske yazilmiyor (bellek-ici, process
omrunce cache'leniyor - kisisel bir refresh token gibi kalici/hassas
degil, sadece 1 saatlik bir uygulama erisim anahtari).

Yapilandirilmamis (.env'de SPOTIFY_CLIENT_ID/SPOTIFY_CLIENT_SECRET yoksa)
veya ag/API hatasi olursa `find_track_id()` sessizce `None` doner -
SearchMusicTool bu durumda otomatik calma yerine sadece arama acmaya
(eski davranis) duser, hicbir sekilde crash olmaz (bkz. docs/ROADMAP.md
Faz 3.1'deki Spotify'in "opsiyonel, credential yoksa cokmez" ilkesiyle
ayni).
"""

import logging
import os
import time
from typing import Optional

import requests
from dotenv import load_dotenv

from src.jarvis.core.paths import PROJECT_ROOT

logger = logging.getLogger("jarvis.tools.spotify_search")

load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

_CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID")
_CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET")

_TOKEN_URL = "https://accounts.spotify.com/api/token"
_SEARCH_URL = "https://api.spotify.com/v1/search"
_REQUEST_TIMEOUT_S = 5
_TOKEN_EXPIRY_SAFETY_MARGIN_S = 60  # token tam dolmadan yenile

# Bellek-ici token onbellegi - dosyaya YAZILMIYOR (bkz. modul docstring'i).
_token_cache: dict = {"access_token": None, "expires_at": 0.0}


def is_configured() -> bool:
    """`.env`'de SPOTIFY_CLIENT_ID/SPOTIFY_CLIENT_SECRET tanimli mi?"""
    return bool(_CLIENT_ID and _CLIENT_SECRET)


def _get_access_token() -> Optional[str]:
    if not is_configured():
        return None
    if _token_cache["access_token"] and time.time() < _token_cache["expires_at"]:
        return _token_cache["access_token"]

    try:
        response = requests.post(
            _TOKEN_URL,
            data={"grant_type": "client_credentials"},
            auth=(_CLIENT_ID, _CLIENT_SECRET),
            timeout=_REQUEST_TIMEOUT_S,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("Spotify token alinamadi: %s", exc)
        return None

    payload = response.json()
    token = payload.get("access_token")
    if not token:
        return None
    expires_in = payload.get("expires_in", 3600)
    _token_cache["access_token"] = token
    _token_cache["expires_at"] = time.time() + expires_in - _TOKEN_EXPIRY_SAFETY_MARGIN_S
    return token


def find_track_id(query: str) -> Optional[str]:
    """`query` icin en populer eslesen parcanin Spotify ID'sini dondurur.

    Yapilandirilmamis/ag hatasi/sonuc yoksa None (SearchMusicTool bunu
    "otomatik calma yerine arama ac" fallback'i olarak yorumluyor).
    """
    token = _get_access_token()
    if token is None:
        return None

    try:
        response = requests.get(
            _SEARCH_URL,
            params={"q": query, "type": "track", "limit": 5},
            headers={"Authorization": f"Bearer {token}"},
            timeout=_REQUEST_TIMEOUT_S,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("Spotify arama basarisiz: %s", exc)
        return None

    items = response.json().get("tracks", {}).get("items", [])
    if not items:
        return None

    # Birden fazla aday arasindan en yuksek popularity'ye sahip olan secilir -
    # limit=1 + kor kor ilk sonuca guvenmek eski tools/spotify.py'de alakasiz
    # sonuclara yol acmisti (bkz. docs/TODO.md madde 4), ayni onlem burada da.
    best = max(items, key=lambda track: track.get("popularity", 0))
    return best.get("id")
