"""brain/llm.py testleri - `think_and_respond_stream` artik `Agent.respond_stream()`
uzerinden calisiyor (Faz 6.2). Gercek Ollama gerekmez: `AgentFactory.create` sahte
bir ajanla degistiriliyor.

Cumle bolme, history yonetimi ve hata siniflandirmasi BILINCLI OLARAK brain/llm.py'de
kaldi (adapter'in respond_stream'i ham hatayi propagate ediyor) - bu testler tam
olarak o davranisi sabitliyor.

Calistirma: `python -m pytest tests/ -v` (repo kokunden, bkz. CLAUDE.md Komutlar).
"""

import inspect

import pytest

from src.jarvis.brain import llm as brain_module
from src.jarvis.brain.llm import MAX_HISTORY_MESSAGES, SYSTEM_PROMPT, think_and_respond_stream


class _StubAgent:
    """respond_stream() sabit chunk'lar yield eder veya bir hata firlatir."""

    def __init__(self, chunks=None, exc=None):
        self._chunks = chunks or []
        self._exc = exc

    def respond_stream(self, prompt, context=None):
        if self._exc is not None:
            raise self._exc
        for chunk in self._chunks:
            yield chunk


def _patch_agent(monkeypatch, stub):
    monkeypatch.setattr(
        brain_module.AgentFactory, "create", staticmethod(lambda role: stub)
    )


def _fresh_history():
    return [{"role": "system", "content": SYSTEM_PROMPT}]


def test_stream_splits_raw_chunks_into_sentences(monkeypatch):
    # Not: yanit tavani 2 cumle - bu test bolme mantigini test ettigi icin
    # 2 cumleyle sinirli tutuldu (tavan icin ayri test var).
    _patch_agent(monkeypatch, _StubAgent(chunks=["Bir cümle. ", "İki cümle."]))

    out = list(think_and_respond_stream("merhaba", _fresh_history()))

    assert out == ["Bir cümle.", "İki cümle."]


def test_stream_caps_at_max_chat_sentences(monkeypatch):
    # 2026-08-29: sesli asistan - 2 cumle sonrasi stream birakilir.
    _patch_agent(
        monkeypatch,
        _StubAgent(chunks=["Bir. ", "İki. ", "Üç. ", "Dört. ", "Beş."]),
    )
    out = list(think_and_respond_stream("anlat", _fresh_history()))
    assert out == ["Bir.", "İki."]


def test_stream_caps_a_runon_sentence_at_word_boundary(monkeypatch):
    # Noktalamasiz uzayan tek cumle: 240 char'da kelime sinirindan kesilir.
    runon = "kelime " * 80  # ~560 char, hic nokta yok
    _patch_agent(monkeypatch, _StubAgent(chunks=[runon, " son cümle."]))
    out = list(think_and_respond_stream("anlat", _fresh_history()))
    assert len(out) == 1
    assert len(out[0]) <= 240
    assert out[0].endswith("kelime")  # kelime sinirindan kesilmis, yarim kelime yok


def test_stream_appends_user_and_assistant_to_history(monkeypatch):
    _patch_agent(monkeypatch, _StubAgent(chunks=["Selam. ", "Nasılsın?"]))
    history = _fresh_history()

    list(think_and_respond_stream("merhaba", history))

    assert history[1] == {"role": "user", "content": "merhaba"}
    assert history[2] == {"role": "assistant", "content": "Selam. Nasılsın?"}


def test_stream_trims_history_but_keeps_system(monkeypatch):
    _patch_agent(monkeypatch, _StubAgent(chunks=["Tamam."]))
    history = _fresh_history()
    for i in range(MAX_HISTORY_MESSAGES + 4):
        history.append({"role": "user" if i % 2 == 0 else "assistant", "content": str(i)})

    list(think_and_respond_stream("son mesaj", history))

    assert history[0] == {"role": "system", "content": SYSTEM_PROMPT}
    assert len(history) - 1 <= MAX_HISTORY_MESSAGES


def test_stream_error_yields_message_and_keeps_user_only(monkeypatch):
    _patch_agent(monkeypatch, _StubAgent(exc=ConnectionError("ollama down")))
    history = _fresh_history()

    out = list(think_and_respond_stream("merhaba", history))

    assert len(out) == 1
    assert "Ollama" in out[0]
    assert history[1] == {"role": "user", "content": "merhaba"}
    assert all(msg["role"] != "assistant" for msg in history)


def test_think_and_respond_stream_signature_unchanged():
    # verify-brain-pipeline skill'i tam olarak bu 2-arg imzasina bagimli.
    params = list(inspect.signature(think_and_respond_stream).parameters)
    assert params == ["user_input", "history"]
