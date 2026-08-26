"""spotify_search.py testleri - gercek Spotify Web API'sine hicbir istek gitmez.

`requests.post`/`requests.get` monkeypatch'lenip sahte response nesneleriyle
degistiriliyor (bkz. CLAUDE.md Komutlar).
"""

import requests

from src.jarvis.tools import spotify_search as spotify_search_module


class _FakeResponse:
    def __init__(self, json_data: dict, status_code: int = 200):
        self._json_data = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._json_data


def _reset_state(monkeypatch, client_id="fake_id", client_secret="fake_secret"):
    monkeypatch.setattr(spotify_search_module, "_CLIENT_ID", client_id)
    monkeypatch.setattr(spotify_search_module, "_CLIENT_SECRET", client_secret)
    monkeypatch.setattr(
        spotify_search_module, "_token_cache", {"access_token": None, "expires_at": 0.0}
    )


def test_is_configured_reflects_client_credentials_presence(monkeypatch):
    _reset_state(monkeypatch, client_id="x", client_secret="y")
    assert spotify_search_module.is_configured()

    _reset_state(monkeypatch, client_id=None, client_secret=None)
    assert not spotify_search_module.is_configured()


def test_find_track_id_returns_none_when_not_configured(monkeypatch):
    _reset_state(monkeypatch, client_id=None, client_secret=None)
    monkeypatch.setattr(
        requests, "post", lambda *a, **k: (_ for _ in ()).throw(AssertionError("cagrilmamali"))
    )

    assert spotify_search_module.find_track_id("Bohemian Rhapsody") is None


def test_find_track_id_picks_most_popular_result(monkeypatch):
    _reset_state(monkeypatch)
    monkeypatch.setattr(
        spotify_search_module.requests,
        "post",
        lambda *a, **k: _FakeResponse({"access_token": "tok123", "expires_in": 3600}),
    )

    obscure = {"id": "obscure_id", "popularity": 5}
    famous = {"id": "famous_id", "popularity": 90}
    monkeypatch.setattr(
        spotify_search_module.requests,
        "get",
        lambda *a, **k: _FakeResponse({"tracks": {"items": [obscure, famous]}}),
    )

    assert spotify_search_module.find_track_id("Back in Black") == "famous_id"


def test_find_track_id_returns_none_when_no_results(monkeypatch):
    _reset_state(monkeypatch)
    monkeypatch.setattr(
        spotify_search_module.requests,
        "post",
        lambda *a, **k: _FakeResponse({"access_token": "tok123", "expires_in": 3600}),
    )
    monkeypatch.setattr(
        spotify_search_module.requests,
        "get",
        lambda *a, **k: _FakeResponse({"tracks": {"items": []}}),
    )

    assert spotify_search_module.find_track_id("asdkjaskldj") is None


def test_find_track_id_returns_none_on_token_request_failure(monkeypatch):
    _reset_state(monkeypatch)
    monkeypatch.setattr(
        spotify_search_module.requests,
        "post",
        lambda *a, **k: _FakeResponse({}, status_code=401),
    )

    assert spotify_search_module.find_track_id("Bohemian Rhapsody") is None


def test_find_track_id_returns_none_on_search_request_failure(monkeypatch):
    _reset_state(monkeypatch)
    monkeypatch.setattr(
        spotify_search_module.requests,
        "post",
        lambda *a, **k: _FakeResponse({"access_token": "tok123", "expires_in": 3600}),
    )
    monkeypatch.setattr(
        spotify_search_module.requests,
        "get",
        lambda *a, **k: _FakeResponse({}, status_code=500),
    )

    assert spotify_search_module.find_track_id("Bohemian Rhapsody") is None


def test_access_token_is_cached_between_calls(monkeypatch):
    _reset_state(monkeypatch)
    token_calls: list[int] = []

    def _fake_post(*a, **k):
        token_calls.append(1)
        return _FakeResponse({"access_token": "tok123", "expires_in": 3600})

    monkeypatch.setattr(spotify_search_module.requests, "post", _fake_post)
    monkeypatch.setattr(
        spotify_search_module.requests,
        "get",
        lambda *a, **k: _FakeResponse({"tracks": {"items": [{"id": "x", "popularity": 1}]}}),
    )

    spotify_search_module.find_track_id("Song A")
    spotify_search_module.find_track_id("Song B")

    assert len(token_calls) == 1  # ikinci cagride onbellekteki token kullanildi
