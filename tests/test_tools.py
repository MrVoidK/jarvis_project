"""Faz 3 arac katmani testleri - tool'lar, risk puanlama ve dispatcher kalip eslesmesi.

Dosya yazan araclar (notes, files) `monkeypatch` ile gecici bir dizine yonlendiriliyor -
testler gercek notes/ veya jarvis_workspace/ dizinine dokunmaz. Spotify tool'lari
`spotify.get_client` monkeypatch'lenerek sahte bir client'a bagliyor - gercek ag/hesap/
credential gerektirmez.

Calistirma: `python -m pytest tests/ -v` (repo kokunden, bkz. CLAUDE.md Komutlar).
"""

import os

import spotipy

from src.jarvis.core.dispatcher import Dispatcher
from src.jarvis.core.risk import RiskLevel, requires_approval
from src.jarvis.tools import files as files_module
from src.jarvis.tools import notes as notes_module
from src.jarvis.tools import spotify as spotify_module
from src.jarvis.tools.registry import TOOL_REGISTRY
from src.jarvis.tools.shell import RunCommandTool

# --- risk puanlama ---


def test_only_low_risk_skips_approval():
    assert not requires_approval(RiskLevel.LOW)
    for level in (RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL):
        assert requires_approval(level), f"{level} onaysiz gecmemeli"


def test_shell_tool_is_always_high_risk():
    # Terminal komutu, icerigi ne olursa olsun ISTISNASIZ onay istemeli
    # (bkz. tools/shell.py modul docstring'i, 1. katman).
    assert RunCommandTool.risk_level is RiskLevel.HIGH


def test_registry_keys_match_tool_names():
    for name, tool in TOOL_REGISTRY.items():
        assert name == tool.name


# --- notes ---


def test_create_and_read_note(monkeypatch, tmp_path):
    notes_dir = tmp_path / "notes"
    monkeypatch.setattr(notes_module, "NOTES_DIR", str(notes_dir))
    monkeypatch.setattr(notes_module, "NOTES_PATH", str(notes_dir / "notes.txt"))

    create = notes_module.CreateNoteTool()
    read = notes_module.ReadNotesTool()

    assert "yok" in read.execute({"lang": "tr"})  # henuz not yok

    create.execute({"lang": "tr", "content": "sut al"})
    create.execute({"lang": "tr", "content": "faturayi ode"})

    result = read.execute({"lang": "tr"})
    assert "sut al" in result
    assert "faturayi ode" in result


def test_create_note_rejects_empty_content(monkeypatch, tmp_path):
    notes_dir = tmp_path / "notes"
    monkeypatch.setattr(notes_module, "NOTES_DIR", str(notes_dir))
    monkeypatch.setattr(notes_module, "NOTES_PATH", str(notes_dir / "notes.txt"))

    result = notes_module.CreateNoteTool().execute({"lang": "en", "content": "   "})
    assert "empty" in result.lower()
    assert not (notes_dir / "notes.txt").exists()  # dosya hic olusturulmamali


# --- files ---


def test_list_files_reports_empty_and_populated(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    monkeypatch.setattr(files_module, "WORKSPACE_DIR", str(workspace))
    tool = files_module.ListFilesTool()

    assert "empty" in tool.execute({"lang": "en"}).lower()

    (workspace / "rapor.txt").write_text("x", encoding="utf-8")
    assert "rapor.txt" in tool.execute({"lang": "en"})


# --- shell ---


def test_run_command_executes_and_returns_output():
    result = RunCommandTool().execute({"lang": "en", "content": "echo jarvis_test_ok"})
    assert "jarvis_test_ok" in result


def test_run_command_rejects_empty_command():
    result = RunCommandTool().execute({"lang": "en", "content": ""})
    assert "didn't get a command" in result.lower()


# --- dispatcher kalip eslesmesi ---


def test_dispatcher_extracts_content_and_language():
    dispatcher = Dispatcher()

    note_tr = dispatcher.match_rule("not tut: yarin toplanti var")
    assert note_tr.name == "create_note"
    assert note_tr.parameters["lang"] == "tr"
    assert note_tr.parameters["content"] == "yarin toplanti var"

    note_en = dispatcher.match_rule("take a note: buy milk")
    assert note_en.name == "create_note"
    assert note_en.parameters["lang"] == "en"
    assert note_en.parameters["content"] == "buy milk"

    command = dispatcher.match_rule("run command: dir")
    assert command.name == "run_command"
    assert command.parameters["content"] == "dir"


def test_dispatcher_matches_contentless_intents():
    dispatcher = Dispatcher()

    for text, expected_name, expected_lang in [
        ("notlarımı oku", "read_notes", "tr"),
        ("read my notes", "read_notes", "en"),
        ("sistem durumu nedir", "get_system_info", "tr"),
        ("system status", "get_system_info", "en"),
    ]:
        intent = dispatcher.match_rule(text)
        assert intent is not None, f"eslesmedi: {text!r}"
        assert intent.name == expected_name
        assert intent.parameters["lang"] == expected_lang
        assert "content" not in intent.parameters


def test_dispatcher_returns_none_for_plain_chat():
    assert Dispatcher().match_rule("bugün hava nasıl?") is None


def test_dispatcher_matches_music_intents():
    """Dispatcher SADECE ham icerigi yakalar - "the"/"song"/"via spotify" gibi
    dolgu kelimelerini ayiklamak tools/spotify.py:_clean_query()'nin isi (bkz.
    test_play_music_query_cleanup), burada sadece dogru intent'e yonlendigi ve
    HAM content'in kaybolmadan tasindigi test ediliyor.
    """
    dispatcher = Dispatcher()

    play = dispatcher.match_rule("şarkı çal: Bohemian Rhapsody")
    assert play.name == "play_music"
    assert play.parameters["lang"] == "tr"
    assert play.parameters["content"] == "Bohemian Rhapsody"

    play_en = dispatcher.match_rule("play song: Shape of You")
    assert play_en.name == "play_music"
    assert play_en.parameters["content"] == "song: Shape of You"

    for text, expected_name, expected_lang in [
        ("müziği duraklat", "pause_music", "tr"),
        ("pause music", "pause_music", "en"),
        ("şarkıyı geç", "skip_track", "tr"),
        ("skip track", "skip_track", "en"),
    ]:
        intent = dispatcher.match_rule(text)
        assert intent is not None, f"eslesmedi: {text!r}"
        assert intent.name == expected_name
        assert intent.parameters["lang"] == expected_lang


def test_dispatcher_matches_real_world_music_phrasings():
    """Gercek kullanim testinde ILK haliyle eslesmeyen iki gercek deneme - regresyon.

    Ikisi de Brain'e dusup LLM'in sarkiyi CALMADIGI halde "caliyor" diye
    halusinasyon gormesine yol acmisti (bkz. core/dispatcher.py _RULES yorumu).
    """
    dispatcher = Dispatcher()

    # "Sarki calin." - formal/cogul cekim, sarki adi SOYLENMEDI (bos icerik beklenir)
    play_formal = dispatcher.match_rule("Şarkı çalın.")
    assert play_formal is not None
    assert play_formal.name == "play_music"
    assert play_formal.parameters["lang"] == "tr"

    # "Play the X via Spotify?" - "the"/"via Spotify" dolgu ifadeleri iceriyor
    play_natural = dispatcher.match_rule("Play the Should I Stay or Should I Go via Spotify?")
    assert play_natural is not None
    assert play_natural.name == "play_music"


# --- spotify ---


class _FakeSpotifyClient:
    """spotipy.Spotify'in test icin sahte, ag'e cikmayan bir yerine gecirimi.

    Gercek kullanim testinde Spotify ACIKKEN bile ilk playback cagrisi 404 "No
    active device" donebiliyor (Spotify Connect'in "aktif" kavrami sadece acik
    olmayi yetmiyor) - `raise_status`/`raise_on_retry`/`available_devices`
    kombinasyonu bu senaryoyu (ve tools/spotify.py:_with_device_fallback()'in
    devices()'tan bulup ACIKCA hedefleyerek kurtarmasini) simule ediyor.
    """

    def __init__(
        self,
        track: dict | None = None,
        raise_status: int | None = None,
        raise_on_retry: bool = True,
        available_devices: list[dict] | None = None,
    ):
        self._track = track
        self._raise_status = raise_status
        self._raise_on_retry = raise_on_retry
        self._available_devices = available_devices if available_devices is not None else []
        self.started_uris: list[str] | None = None
        self.started_device_id: str | None = "NOT_CALLED"
        self.last_search_query: str | None = None
        self.paused = False
        self.skipped = False

    def _maybe_raise(self, device_id):
        if self._raise_status is None:
            return
        # Ilk deneme (device_id=None) her zaman "aktif cihaz yok" senaryosunu
        # tetikler; fallback (device_id ACIKCA verilmis) sadece raise_on_retry=True
        # ise patlamaya devam eder (yani cihaz bulunsa da hala kullanilamiyor).
        if device_id is None or self._raise_on_retry:
            raise spotipy.SpotifyException(self._raise_status, -1, "test", headers={})

    def devices(self):
        return {"devices": self._available_devices}

    def search(self, q, type, limit):
        self.last_search_query = q
        items = [self._track] if self._track else []
        return {"tracks": {"items": items}}

    def start_playback(self, uris, device_id=None):
        self._maybe_raise(device_id)
        self.started_uris = uris
        self.started_device_id = device_id

    def pause_playback(self, device_id=None):
        self._maybe_raise(device_id)
        self.paused = True

    def next_track(self, device_id=None):
        self._maybe_raise(device_id)
        self.skipped = True


_SAMPLE_TRACK = {
    "uri": "spotify:track:abc123",
    "name": "Bohemian Rhapsody",
    "artists": [{"name": "Queen"}],
}


def test_play_music_not_configured(monkeypatch):
    monkeypatch.setattr(spotify_module, "get_client", lambda: (None, "not_configured"))
    result = spotify_module.PlayMusicTool().execute({"lang": "en", "content": "Bohemian Rhapsody"})
    assert "configured" in result.lower()


def test_play_music_not_authorized(monkeypatch):
    monkeypatch.setattr(spotify_module, "get_client", lambda: (None, "not_authorized"))
    result = spotify_module.PlayMusicTool().execute({"lang": "en", "content": "Bohemian Rhapsody"})
    assert "authorized" in result.lower()


def test_play_music_empty_query_short_circuits(monkeypatch):
    # get_client hic cagrilmamali - bos sorguda erken donulmeli.
    monkeypatch.setattr(
        spotify_module, "get_client", lambda: (_ for _ in ()).throw(AssertionError("cagrilmamali"))
    )
    result = spotify_module.PlayMusicTool().execute({"lang": "tr", "content": ""})
    assert "anlamadım" in result


def test_play_music_success(monkeypatch):
    fake = _FakeSpotifyClient(track=_SAMPLE_TRACK)
    monkeypatch.setattr(spotify_module, "get_client", lambda: (fake, None))

    result = spotify_module.PlayMusicTool().execute({"lang": "en", "content": "Bohemian Rhapsody"})
    assert "Bohemian Rhapsody" in result
    assert "Queen" in result
    assert fake.started_uris == ["spotify:track:abc123"]


def test_play_music_not_found(monkeypatch):
    fake = _FakeSpotifyClient(track=None)
    monkeypatch.setattr(spotify_module, "get_client", lambda: (fake, None))

    result = spotify_module.PlayMusicTool().execute({"lang": "en", "content": "asdkjaskldj"})
    assert "couldn't find" in result.lower()


def test_play_music_no_active_device(monkeypatch):
    """Hic listelenen cihaz da yoksa (devices() bos) fallback denenmeden hata donmeli."""
    fake = _FakeSpotifyClient(track=_SAMPLE_TRACK, raise_status=404, available_devices=[])
    monkeypatch.setattr(spotify_module, "get_client", lambda: (fake, None))

    result = spotify_module.PlayMusicTool().execute({"lang": "tr", "content": "Bohemian Rhapsody"})
    assert "Spotify" in result and "aç" in result
    assert fake.started_uris is None  # fallback denenmedi cunku hic cihaz yoktu


def test_play_music_falls_back_to_listed_device_when_none_active(monkeypatch):
    """Gercek kullanim testinde gozlemlendi: Spotify ACIKKEN bile ilk cagri 404 "No
    active device" donuyordu. devices() o cihazi (aktif olmasa da) listeliyor -
    ona ACIKCA device_id ile hedefleyerek kurtarmasi gerekiyor (bkz. tools/
    spotify.py:_with_device_fallback).
    """
    fake = _FakeSpotifyClient(
        track=_SAMPLE_TRACK,
        raise_status=404,
        raise_on_retry=False,
        available_devices=[{"id": "device-123", "name": "DESKTOP-ABC"}],
    )
    monkeypatch.setattr(spotify_module, "get_client", lambda: (fake, None))

    result = spotify_module.PlayMusicTool().execute({"lang": "en", "content": "Bohemian Rhapsody"})

    assert fake.started_device_id == "device-123"
    assert "Bohemian Rhapsody" in result


def test_play_music_no_active_device_even_after_fallback(monkeypatch):
    """Cihaz listelense bile ona hedeflenen tekrar deneme de basarisiz olursa hala
    net bir hata mesaji donmeli - sessizce takilip kalmamali."""
    fake = _FakeSpotifyClient(
        track=_SAMPLE_TRACK,
        raise_status=404,
        raise_on_retry=True,
        available_devices=[{"id": "device-123", "name": "DESKTOP-ABC"}],
    )
    monkeypatch.setattr(spotify_module, "get_client", lambda: (fake, None))

    result = spotify_module.PlayMusicTool().execute({"lang": "tr", "content": "Bohemian Rhapsody"})
    assert "Spotify" in result and "aç" in result


def test_pause_and_skip_music(monkeypatch):
    fake = _FakeSpotifyClient()
    monkeypatch.setattr(spotify_module, "get_client", lambda: (fake, None))

    pause_result = spotify_module.PauseMusicTool().execute({"lang": "en"})
    assert fake.paused
    assert "paused" in pause_result.lower()

    skip_result = spotify_module.SkipTrackTool().execute({"lang": "en"})
    assert fake.skipped
    assert "skipped" in skip_result.lower()


def test_clean_query_strips_filler_and_trailing_spotify_mention():
    assert spotify_module._clean_query("song: Shape of You") == "Shape of You"
    assert (
        spotify_module._clean_query("the Should I Stay or Should I Go via Spotify?")
        == "Should I Stay or Should I Go"
    )
    assert spotify_module._clean_query(".") == ""  # "Sarki calin." -> icerik yok
    assert spotify_module._clean_query("  ") == ""
    assert spotify_module._clean_query("Bohemian Rhapsody") == "Bohemian Rhapsody"


def test_play_music_end_to_end_with_real_world_phrasing(monkeypatch):
    """Gercek kullanim testinde basarisiz olan tam ifade - dispatcher'dan PlayMusicTool'a
    kadar butun zinciri (regex eslesme + _clean_query temizligi) tek testte dogrular.
    """
    fake = _FakeSpotifyClient(track=_SAMPLE_TRACK)
    monkeypatch.setattr(spotify_module, "get_client", lambda: (fake, None))

    intent = Dispatcher().match_rule("Play the Should I Stay or Should I Go via Spotify?")
    assert intent is not None and intent.name == "play_music"

    result = spotify_module.PlayMusicTool().execute(intent.parameters)

    assert fake.last_search_query == "Should I Stay or Should I Go"
    assert fake.started_uris == ["spotify:track:abc123"]
    assert "Bohemian Rhapsody" in result  # _SAMPLE_TRACK'in adi (arama sonucu sabit)


def test_play_music_empty_song_name_asks_which_song(monkeypatch):
    """"Sarki calin." (sarki adi soylenmeden) - Spotify'a hic gidilmemeli, net bir
    "hangi sarki?" mesaji donmeli."""
    monkeypatch.setattr(
        spotify_module, "get_client", lambda: (_ for _ in ()).throw(AssertionError("cagrilmamali"))
    )
    intent = Dispatcher().match_rule("Şarkı çalın.")
    assert intent is not None and intent.name == "play_music"

    result = spotify_module.PlayMusicTool().execute(intent.parameters)
    assert "anlamadım" in result
