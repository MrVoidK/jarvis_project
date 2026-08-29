"""media_tool.py testleri - gercek SendInput cagrisi hicbir zaman testte tetiklenmez.

`_send_vk` monkeypatch'lenip hangi VK kodunun gonderildigi kaydediliyor -
gercek klavye/medya olayi uretilmiyor (bkz. CLAUDE.md Komutlar).
"""

import ctypes
import logging

from src.jarvis.tools import media_tool as media_module


def test_input_struct_matches_real_win32_size():
    """Regresyon: `_InputUnion` sadece `ki`yi tanimlarsa (MouseInput/HardwareInput
    olmadan) union kucuk kaliyor, tum _Input struct'i 32 byte oluyordu - gercek
    Win32 INPUT struct'i (64-bit'te) 40 byte. SendInput, cbSize parametresi
    kendi ic sizeof(INPUT)'iyla eslesmezse hicbir exception FIRLATMADAN sessizce
    basarisiz oluyor (0 donuyor) - gercek kullanim testinde tam olarak bu oldu:
    loglar "tus gonderildi" diyordu ama fiziksel olarak hicbir sey olmuyordu."""
    assert ctypes.sizeof(media_module._Input) == 40


def test_send_vk_uses_correct_cbsize_and_logs_on_failure(monkeypatch, caplog):
    """`_send_vk` gercek `ctypes.windll.user32.SendInput`'u cagirir (bu testte
    monkeypatch'lenir - gercek klavye olayi ASLA uretilmez) - dogru cbSize
    (sizeof(_Input)=40) gectigini ve donus degeri 0 (basarisiz) oldugunda
    bir uyari logladigini dogrular (eskiden donus degeri hic kontrol
    edilmiyordu, sessiz basarisizliklar fark edilmiyordu)."""
    calls: list[tuple] = []

    def _fake_send_input(count, pointer, cb_size):
        calls.append((count, cb_size))
        return 0  # basarisiz - Windows'un input'u reddettigini simule eder

    monkeypatch.setattr(media_module.ctypes.windll.user32, "SendInput", _fake_send_input)
    monkeypatch.setattr(media_module.ctypes, "GetLastError", lambda: 5)

    with caplog.at_level(logging.WARNING, logger="jarvis.tools.media"):
        media_module._send_vk(media_module.VK_MEDIA_PLAY_PAUSE)

    assert len(calls) == 2  # key-down + key-up
    assert all(cb_size == 40 for _, cb_size in calls)
    assert any("basarisiz" in record.message for record in caplog.records)


def _capture_vk(monkeypatch):
    """`_send_vk(vk, times=1)` -> her cagride vk kodunu `times` kez kaydeder
    (D3: ses seviyesi araclari artik kademe sayisi geciyor)."""
    calls: list[int] = []
    monkeypatch.setattr(media_module, "_send_vk", lambda vk, times=1: calls.extend([vk] * times))
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


# --- ses seviyesi: mutlak (pycaw) + goreceli (2026-08-29 pre-6.10) ---


def _no_pct_control(monkeypatch):
    """pycaw yolunu kapat - keypress fallback'ini test et."""
    monkeypatch.setattr(media_module, "_get_volume_percent", lambda: None)
    monkeypatch.setattr(media_module, "_set_volume_percent", lambda pct: False)


def _fake_pct_control(monkeypatch, current: int):
    """pycaw'i taklit et - _get_volume_percent sabit, _set_volume_percent'i yakala."""
    sets: list[int] = []
    monkeypatch.setattr(media_module, "_get_volume_percent", lambda: current)
    monkeypatch.setattr(media_module, "_set_volume_percent", lambda pct: (sets.append(pct), True)[1])
    return sets


def test_volume_up_uses_pct_control_when_available(monkeypatch):
    sets = _fake_pct_control(monkeypatch, current=50)
    result = media_module.MediaVolumeUpTool().execute({"lang": "en"})
    assert sets == [50 + media_module.VOLUME_DELTA_DEFAULT]
    assert "%" in result or str(50 + media_module.VOLUME_DELTA_DEFAULT) in result


def test_volume_up_cok_adds_large_delta(monkeypatch):
    sets = _fake_pct_control(monkeypatch, current=50)
    media_module.MediaVolumeUpTool().execute({"lang": "tr", "amount": "çok"})
    assert sets == [50 + media_module.VOLUME_DELTA_LARGE]


def test_volume_down_biraz_subtracts_small_delta_and_clamps(monkeypatch):
    sets = _fake_pct_control(monkeypatch, current=3)
    media_module.MediaVolumeDownTool().execute({"lang": "tr", "amount": "biraz"})
    assert sets == [0]  # 3 - 6 -> clamp 0


def test_volume_up_falls_back_to_keypress_without_pct_control(monkeypatch):
    _no_pct_control(monkeypatch)
    calls = _capture_vk(monkeypatch)
    media_module.MediaVolumeUpTool().execute({"lang": "en"})
    # delta 12 -> 12//2 = 6 keypress
    assert calls == [media_module.VK_VOLUME_UP] * (media_module.VOLUME_DELTA_DEFAULT // 2)


def test_set_volume_sets_exact_percent(monkeypatch):
    sets = _fake_pct_control(monkeypatch, current=20)
    result = media_module.SetVolumeTool().execute({"lang": "tr", "level": "84"})
    assert sets == [84]
    assert "84" in result


def test_set_volume_parses_percent_sign_and_clamps(monkeypatch):
    sets = _fake_pct_control(monkeypatch, current=20)
    media_module.SetVolumeTool().execute({"lang": "en", "level": "%150"})
    assert sets == [100]


def test_set_volume_unavailable_returns_soft_message(monkeypatch):
    _no_pct_control(monkeypatch)
    result = media_module.SetVolumeTool().execute({"lang": "en", "level": "50"})
    assert "available" in result.lower() or "kullanıl" in result.lower()


def test_set_volume_bad_level(monkeypatch):
    _fake_pct_control(monkeypatch, current=20)
    result = media_module.SetVolumeTool().execute({"lang": "en", "level": "loud"})
    assert "didn't catch" in result.lower() or "anlayamad" in result.lower()


def test_search_music_opens_spotify_search_uri_when_track_not_found(monkeypatch):
    """spotify_search.find_track_id() None donerse (API yapilandirilmamis/basarisiz) -
    eski davranisa (sadece arama ac) sessizce duser."""
    monkeypatch.setattr(media_module.spotify_search, "find_track_id", lambda query: None)
    calls: list[str] = []
    monkeypatch.setattr(media_module.os, "startfile", calls.append)

    result = media_module.SearchMusicTool().execute({"lang": "en", "query": "Bohemian Rhapsody"})

    assert calls == ["spotify:search:Bohemian%20Rhapsody"]
    assert "Bohemian Rhapsody" in result


def test_search_music_auto_plays_when_track_id_found(monkeypatch):
    """spotify_search.find_track_id() bir ID donerse - GERCEK otomatik calma
    (`spotify:track:<id>`) tetiklenmeli, sadece arama degil."""
    monkeypatch.setattr(media_module.spotify_search, "find_track_id", lambda query: "abc123")
    calls: list[str] = []
    monkeypatch.setattr(media_module.os, "startfile", calls.append)

    result = media_module.SearchMusicTool().execute({"lang": "en", "query": "Bohemian Rhapsody"})

    assert calls == ["spotify:track:abc123"]
    assert "playing" in result.lower()
    assert "Bohemian Rhapsody" in result


def test_search_music_rejects_empty_query(monkeypatch):
    monkeypatch.setattr(
        media_module.spotify_search,
        "find_track_id",
        lambda query: (_ for _ in ()).throw(AssertionError("cagrilmamali")),
    )
    monkeypatch.setattr(
        media_module.os, "startfile", lambda uri: (_ for _ in ()).throw(AssertionError("cagrilmamali"))
    )

    result = media_module.SearchMusicTool().execute({"lang": "tr", "query": ""})
    assert "anla" in result.lower()


def test_search_music_handles_missing_spotify_handler(monkeypatch):
    monkeypatch.setattr(media_module.spotify_search, "find_track_id", lambda query: None)

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
        media_module.SetVolumeTool,
    ):
        assert tool_cls.risk_level is RiskLevel.LOW
