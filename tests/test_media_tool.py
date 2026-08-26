"""media_tool.py testleri - gercek SendInput cagrisi hicbir zaman testte tetiklenmez.

`_send_vk` monkeypatch'lenip hangi VK kodunun gonderildigi kaydediliyor -
gercek klavye/medya olayi uretilmiyor (bkz. CLAUDE.md Komutlar).
"""

from src.jarvis.tools import media_tool as media_module


def _capture_vk(monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(media_module, "_send_vk", calls.append)
    return calls


def test_play_pause_sends_correct_vk_and_neutral_message(monkeypatch):
    calls = _capture_vk(monkeypatch)
    result = media_module.MediaPlayPauseTool().execute({"lang": "en"})

    assert calls == [media_module.VK_MEDIA_PLAY_PAUSE]
    # "paused"/"playing" gibi kesin bir durum iddia ETMEMELI (toggle tusu -
    # gercek durum bilinmiyor, bkz. modul docstring'i).
    assert "paused" not in result.lower()
    assert "playing" not in result.lower()
    assert "toggled" in result.lower()


def test_next_track_sends_correct_vk(monkeypatch):
    calls = _capture_vk(monkeypatch)
    result = media_module.MediaNextTrackTool().execute({"lang": "tr"})

    assert calls == [media_module.VK_MEDIA_NEXT_TRACK]
    assert "sonraki" in result.lower()


def test_previous_track_sends_correct_vk(monkeypatch):
    calls = _capture_vk(monkeypatch)
    result = media_module.MediaPreviousTrackTool().execute({"lang": "en"})

    assert calls == [media_module.VK_MEDIA_PREV_TRACK]
    assert "previous" in result.lower()


def test_volume_up_sends_correct_vk(monkeypatch):
    calls = _capture_vk(monkeypatch)
    media_module.MediaVolumeUpTool().execute({"lang": "en"})
    assert calls == [media_module.VK_VOLUME_UP]


def test_volume_down_sends_correct_vk(monkeypatch):
    calls = _capture_vk(monkeypatch)
    media_module.MediaVolumeDownTool().execute({"lang": "en"})
    assert calls == [media_module.VK_VOLUME_DOWN]


def test_search_music_opens_spotify_search_uri(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(media_module.os, "startfile", calls.append)

    result = media_module.SearchMusicTool().execute({"lang": "en", "query": "Bohemian Rhapsody"})

    assert calls == ["spotify:search:Bohemian%20Rhapsody"]
    assert "Bohemian Rhapsody" in result


def test_search_music_rejects_empty_query(monkeypatch):
    monkeypatch.setattr(
        media_module.os, "startfile", lambda uri: (_ for _ in ()).throw(AssertionError("cagrilmamali"))
    )

    result = media_module.SearchMusicTool().execute({"lang": "tr", "query": ""})
    assert "anla" in result.lower()


def test_search_music_handles_missing_spotify_handler(monkeypatch):
    def _raise(uri):
        raise OSError("no application associated")

    monkeypatch.setattr(media_module.os, "startfile", _raise)

    result = media_module.SearchMusicTool().execute({"lang": "en", "query": "Bohemian Rhapsody"})
    assert "install" in result.lower() or "n't" in result.lower()


def test_search_music_is_low_risk():
    from src.jarvis.core.risk import RiskLevel

    assert media_module.SearchMusicTool.risk_level is RiskLevel.LOW


def test_all_media_tools_are_low_risk():
    from src.jarvis.core.risk import RiskLevel

    for tool_cls in (
        media_module.MediaPlayPauseTool,
        media_module.MediaNextTrackTool,
        media_module.MediaPreviousTrackTool,
        media_module.MediaVolumeUpTool,
        media_module.MediaVolumeDownTool,
    ):
        assert tool_cls.risk_level is RiskLevel.LOW
