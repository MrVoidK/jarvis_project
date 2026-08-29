"""Router doğruluk bataryası - GERÇEK Ollama + qwen2.5:3b ile.

Normal `pytest tests/` turunda ATLANIR (yavaş + non-deterministik). Çalıştırmak
için:

    JARVIS_ROUTER_BATTERY=1 python -m pytest tests/test_router_accuracy.py -v

Amaç: 2026-08-29 canlı test regresyonları - "sesi kıs" ters yöne gitmesin,
"şarkıyı devam ettir" `run_command` uydurmasın, düz sohbet araç seçmesin.
Küçük router modeli deterministik değil; birkaç vaka ara sıra kayabilir -
%100 değil, ~%90+ hedeflenir. Kırılırsa `_ROUTER_SYSTEM_PROMPT` few-shot'ları
gözden geçirilir (bkz. docs/optimizasyon-plani.md Cluster C).
"""

import os

import pytest

_BATTERY_ON = os.environ.get("JARVIS_ROUTER_BATTERY") == "1"

pytestmark = pytest.mark.skipif(
    not _BATTERY_ON,
    reason="router bataryası opt-in: JARVIS_ROUTER_BATTERY=1 ile çalıştır",
)


# (transkript, beklenen intent adı) - "chat" = no_tool_needed / genel sohbet.
_CASES = [
    # düz sohbet - araç seçilmemeli
    ("Merhaba Jarvis nasılsın", "chat"),
    ("bugün kendini nasıl hissediyorsun", "chat"),
    ("teşekkür ederim", "chat"),
    ("adım Ömer bunu hatırla", "chat"),
    ("what is the capital of France", "chat"),
    # medya - yön ve eylem doğru olmalı
    ("sesi aç", "media_volume_up"),
    ("sesi biraz kıs", "media_volume_down"),
    ("volume up", "media_volume_up"),
    ("sıradaki şarkıya geç", "media_next_track"),
    ("önceki parçaya dön", "media_previous_track"),
    ("müziği durdur", "media_play_pause"),
    ("şarkıyı devam ettir", "media_play_pause"),
    ("Iron Man şarkısını çal", "search_music"),
    ("play Bohemian Rhapsody", "search_music"),
    # notlar / sistem
    ("notlarımı oku", "read_notes"),
    ("bir not al alışveriş listesi", "create_note"),
    ("sistem durumu nedir", "get_system_info"),
    ("CPU kullanımı ne kadar", "get_system_info"),
    # run_command - SADECE gerçekten dikte edilince
    ("run command dir", "run_command"),
    ("çalıştır git status", "run_command"),
    ("onayını bekliyorum terminale bak", "chat"),  # komut değil
    # çok adımlı
    ("sistem durumuna bak sonra bir not al", "delegate_complex"),
    # proje başlatma (Faz 6.7)
    ("yeni bir proje oluştur blog", "create_project"),
    ("create a project called scraper", "create_project"),
]


@pytest.fixture(scope="module")
def dispatcher():
    from src.jarvis.adapters.agent_factory import check_ollama_connection

    ok, msg = check_ollama_connection("qwen2.5:3b")
    if not ok:
        pytest.skip(f"Ollama/qwen2.5:3b erişilemiyor: {msg}")
    from src.jarvis.core.dispatcher import Dispatcher

    return Dispatcher()


# qwen2.5:3b'nin bilinen zayıf noktaları (kabul edilen 3B sınırı, roadmap Faz
# 6.2/6.3 emsali). Tehlikeli değiller (yanlış run_command / ters yön DEĞİL),
# sadece "ideal" seçimi kaçırıyorlar. xfail(strict=False): düzelirlerse test
# XPASS verir, o zaman listeden çıkarılır.
_KNOWN_3B_MISSES = {
    "adım Ömer bunu hatırla",  # -> create_note (hatırla=not sanıyor); Brain zaten hafıza tutar
    "sistem durumuna bak sonra bir not al",  # -> ilk adımı kapıyor, delegate_complex tetiklemiyor
    "sıradaki şarkıya geç",  # -> ara sıra chat; TR diakritik + 3B, flaky (few-shot'ta var)
}


@pytest.mark.parametrize("transcript,expected", _CASES)
def test_router_battery(request, dispatcher, transcript, expected):
    if transcript in _KNOWN_3B_MISSES:
        request.node.add_marker(pytest.mark.xfail(reason="bilinen qwen2.5:3b sınırı", strict=False))
    intent = dispatcher.classify(transcript)
    assert intent.name == expected, (
        f"{transcript!r} -> {intent.name!r} (beklenen {expected!r}), "
        f"params={intent.parameters}"
    )
