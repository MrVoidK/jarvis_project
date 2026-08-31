"""Semantic router (Dispatcher.classify) testleri - gercek Ollama cagrisi YAPILMAZ.

AgentFactory.create("router") monkeypatch'lenip sahte bir Agent (call_tools()
onceden belirlenmis bir AgentToolResponse donduren) ile degistiriliyor - testler
Ollama'nin calisiyor olmasina bagimli degil.

MCP (Faz 4.5): `classify()` artik `tools/registry.py:all_tools()`/`get_tool()`
uzerinden MCP-kesfedilen araclari da goruyor (bkz. o dosyanin docstring'i) -
bu, gercek bir `config/mcp_servers.yaml` varsa (bu makinede oldugu gibi) her
`classify()` cagrisinin gercek bir npx alt sureci baslatmaya calismasi
anlamina gelirdi. Asagidaki autouse fixture, AgentFactory ile AYNI ilkeyle,
`all_tools`/`get_tool`'u SADECE yerel `TOOL_REGISTRY`'yi donduren sahte
surumlerle degistirip bu dosyadaki testleri MCP'den (ve makineye ozel
config'ten) tamamen izole ediyor.

Calistirma: `python -m pytest tests/ -v` (repo kokunden, bkz. CLAUDE.md Komutlar).
"""

import pytest

from src.jarvis.agents.base import AgentToolResponse, ToolCall
from src.jarvis.core import dispatcher as dispatcher_module
from src.jarvis.core.dispatcher import DEFAULT_INTENT_NAME, Dispatcher
from src.jarvis.tools.registry import TOOL_REGISTRY


@pytest.fixture(autouse=True)
def _no_real_mcp_calls(monkeypatch):
    monkeypatch.setattr(dispatcher_module, "all_tools", lambda: TOOL_REGISTRY)
    monkeypatch.setattr(dispatcher_module, "get_tool", TOOL_REGISTRY.get)


class _StubRouter:
    def __init__(self, response: AgentToolResponse):
        self._response = response

    def call_tools(self, prompt, tools, context=None):
        return self._response


def _patch_router(monkeypatch, response: AgentToolResponse):
    monkeypatch.setattr(
        dispatcher_module.AgentFactory, "create", staticmethod(lambda role: _StubRouter(response))
    )


def test_classify_fast_path_never_calls_agent_factory(monkeypatch):
    # get_time _RULES'ta oldugu icin AgentFactory.create HIC cagrilmamali.
    monkeypatch.setattr(
        dispatcher_module.AgentFactory,
        "create",
        staticmethod(lambda role: (_ for _ in ()).throw(AssertionError("cagrilmamali"))),
    )

    intent = Dispatcher().classify("saat kaç?")
    assert intent.name == "get_time"
    assert intent.source == "rule"


def test_classify_returns_known_tool_selected_by_router(monkeypatch):
    _patch_router(
        monkeypatch,
        AgentToolResponse(tool_calls=[ToolCall(name="media_next_track", arguments={})]),
    )

    # "şarkıyı değiştir" fast-path'e (bkz. _RULES media kuralları) TAKILMAZ -
    # yön işareti yok, "değiştir" bir play/track fiili değil - router yoluna düşer.
    intent = Dispatcher().classify("şarkıyı değiştir")

    assert intent.name == "media_next_track"
    assert intent.source == "llm"
    assert intent.confidence > 0.5
    assert "lang" in intent.parameters


def test_classify_passes_through_tool_arguments(monkeypatch):
    _patch_router(
        monkeypatch,
        AgentToolResponse(tool_calls=[ToolCall(name="create_note", arguments={"content": "sut al"})]),
    )

    intent = Dispatcher().classify("bir not al: süt al")

    assert intent.name == "create_note"
    assert intent.parameters["content"] == "sut al"


def test_classify_falls_back_to_chat_when_no_tool_selected(monkeypatch):
    _patch_router(monkeypatch, AgentToolResponse(tool_calls=[]))

    intent = Dispatcher().classify("bugün hava nasıl?")

    assert intent.name == DEFAULT_INTENT_NAME
    assert intent.source == "llm"


def test_classify_returns_chat_when_router_selects_no_tool_needed(monkeypatch):
    """Sentinel kacis yolu (bkz. dispatcher.py:_NO_TOOL_FUNCTION_NAME) - router
    ACIKCA 'no_tool_needed' secerse, jenerik 'bilinmeyen arac' yoluna DUSMEDEN
    (ayri, bilerek-yazilmis bir kontrolle) chat'e donmeli."""
    _patch_router(
        monkeypatch,
        AgentToolResponse(
            tool_calls=[ToolCall(name=dispatcher_module._NO_TOOL_FUNCTION_NAME, arguments={})]
        ),
    )

    intent = Dispatcher().classify("Görüşürüz.")

    assert intent.name == DEFAULT_INTENT_NAME
    assert intent.source == "llm"


def test_classify_sends_no_tool_needed_schema_to_router(monkeypatch):
    """`_NO_TOOL_SCHEMA`'nin gercekten `tools=` argumaniyla Ollama'ya (stub
    uzerinden) gonderildigini dogrular - semanin var OLMASI yetmez, fiilen
    gonderilen listeye eklenmis olmasi gerekiyor."""
    captured: list[list[dict]] = []

    class _CapturingStub:
        def call_tools(self, prompt, tools, context=None):
            captured.append(tools)
            return AgentToolResponse(tool_calls=[])

    monkeypatch.setattr(
        dispatcher_module.AgentFactory, "create", staticmethod(lambda role: _CapturingStub())
    )

    Dispatcher().classify("bir şeyler")

    assert len(captured) == 1
    tool_names = {schema["function"]["name"] for schema in captured[0]}
    assert dispatcher_module._NO_TOOL_FUNCTION_NAME in tool_names
    # Faz 6.3: delegasyon sentinel'leri de fiilen gonderilen listede olmali.
    assert dispatcher_module._DELEGATE_COMPLEX_FUNCTION_NAME in tool_names
    assert dispatcher_module._DELEGATE_CODE_FUNCTION_NAME in tool_names


def test_classify_falls_back_to_chat_when_router_hallucinates_unknown_tool(monkeypatch):
    _patch_router(
        monkeypatch,
        AgentToolResponse(tool_calls=[ToolCall(name="not_a_real_tool", arguments={})]),
    )

    intent = Dispatcher().classify("bir şeyler yap")

    assert intent.name == DEFAULT_INTENT_NAME
    assert intent.source == "llm"


def test_classify_falls_back_to_chat_when_router_produces_wrong_argument_type(monkeypatch):
    """security-reviewer bulgusu (Faz 3.3): kucuk yerel modeller JSON-Schema'ya
    her zaman uymayabilir - "content" bir string yerine bir liste gelirse
    (fail-closed validate_arguments) tum cagri reddedilmeli, dogrulanmamis
    deger asla Intent.parameters'a ulasmamali."""
    _patch_router(
        monkeypatch,
        AgentToolResponse(
            tool_calls=[ToolCall(name="create_note", arguments={"content": ["rm", "-rf", "/"]})]
        ),
    )

    intent = Dispatcher().classify("bir not al")

    assert intent.name == DEFAULT_INTENT_NAME
    assert intent.source == "llm"


# --- Faz 6.3: delegasyon sentinel'leri ---


def test_classify_returns_delegate_complex_intent(monkeypatch):
    _patch_router(
        monkeypatch,
        AgentToolResponse(
            tool_calls=[
                ToolCall(
                    name=dispatcher_module._DELEGATE_COMPLEX_FUNCTION_NAME,
                    arguments={"task": "durumu kontrol et ve not al"},
                )
            ]
        ),
    )

    intent = Dispatcher().classify("sistem durumunu kontrol et ve bir not al")

    assert intent.name == dispatcher_module.DELEGATE_COMPLEX_INTENT_NAME
    assert intent.source == "llm"
    assert intent.confidence == 0.7
    assert intent.parameters["task"] == "durumu kontrol et ve not al"
    assert "lang" in intent.parameters


def test_classify_returns_delegate_code_intent(monkeypatch):
    _patch_router(
        monkeypatch,
        AgentToolResponse(
            tool_calls=[
                ToolCall(
                    name=dispatcher_module._DELEGATE_CODE_FUNCTION_NAME,
                    arguments={"task": "dispatcher.py'yi analiz et"},
                )
            ]
        ),
    )

    intent = Dispatcher().classify("bu projedeki dispatcher.py'yi analiz et")

    assert intent.name == dispatcher_module.DELEGATE_CODE_INTENT_NAME
    assert intent.confidence == 0.7
    assert intent.parameters["task"] == "dispatcher.py'yi analiz et"


def test_classify_delegate_falls_back_to_text_when_task_arg_missing(monkeypatch):
    _patch_router(
        monkeypatch,
        AgentToolResponse(
            tool_calls=[ToolCall(name=dispatcher_module._DELEGATE_COMPLEX_FUNCTION_NAME, arguments={})]
        ),
    )

    intent = Dispatcher().classify("çok adımlı bir iş yap")

    assert intent.name == dispatcher_module.DELEGATE_COMPLEX_INTENT_NAME
    assert intent.parameters["task"] == "çok adımlı bir iş yap"


# --- 2026-08-29 Cluster C: run_command guard + gerçek confidence ---


def test_run_command_rejected_when_executable_not_in_transcript(monkeypatch):
    """C2: router bir müzik komutundan `taskmgr /restart` uydurdu (canli test) -
    dikte edilen komutun ilk token'i transkriptte geçmiyorsa çağrı reddedilir,
    chat'e düşer (HIGH onay kapısının üstüne defense-in-depth)."""
    _patch_router(
        monkeypatch,
        AgentToolResponse(
            tool_calls=[ToolCall(name="run_command", arguments={"command": "taskmgr /restart"})]
        ),
    )

    # "parçayı baştan al" fast-path media kurallarına takılmaz -> router yolu ->
    # C2 guard'ı devrede (transkriptte "taskmgr" geçmiyor -> reddet).
    intent = Dispatcher().classify("Jarvis parçayı baştan al")

    assert intent.name == DEFAULT_INTENT_NAME
    assert intent.source == "llm"


def test_run_command_accepted_when_executable_is_dictated(monkeypatch):
    """C2 karşı-testi: kullanıcı komutu gerçekten dikte ettiyse (ilk token
    transkriptte var) çağrı geçer."""
    _patch_router(
        monkeypatch,
        AgentToolResponse(tool_calls=[ToolCall(name="run_command", arguments={"command": "dir"})]),
    )

    intent = Dispatcher().classify("run command dir please")

    assert intent.name == "run_command"
    assert intent.parameters["command"] == "dir"


def test_router_confidence_reflects_selection_kind(monkeypatch):
    """C3: `confidence` artık hardcoded 0.9 değil - somut araç 0.8,
    no_tool_needed 0.6."""
    _patch_router(
        monkeypatch,
        AgentToolResponse(tool_calls=[ToolCall(name="media_next_track", arguments={})]),
    )
    # "şarkıyı değiştir" fast-path'e takılmaz -> router yolu -> gerçek confidence.
    assert Dispatcher().classify("şarkıyı değiştir").confidence == pytest.approx(0.8)

    _patch_router(
        monkeypatch,
        AgentToolResponse(
            tool_calls=[ToolCall(name=dispatcher_module._NO_TOOL_FUNCTION_NAME, arguments={})]
        ),
    )
    assert Dispatcher().classify("nasılsın bakalım").confidence == pytest.approx(0.6)


# --- 2026-09-01: ses/medya fast-path (router LLM'e HİÇ gitmez) ---------------


def _forbid_router(monkeypatch):
    """Fast-path eşleşen bir komut AgentFactory.create'i çağırmamalı."""
    monkeypatch.setattr(
        dispatcher_module.AgentFactory,
        "create",
        staticmethod(lambda role: (_ for _ in ()).throw(AssertionError("fast-path router'a gitmemeli"))),
    )


@pytest.mark.parametrize(
    "text,level",
    [
        ("Jarvis sesi 34 yap", "34"),
        ("sesi %50 yap", "50"),
        ("ses seviyesini 40 yap", "40"),
        ("sesi 34'e ayarla", "34"),
        ("sesi 100 yap", "100"),
        ("sesi sıfır yap", "sıfır"),
        ("sesi yarıya indir", "yarıya"),
        ("set volume to 30", "30"),
        ("make volume zero", "zero"),
        ("set the volume to half", "half"),
    ],
)
def test_fast_path_set_volume(monkeypatch, text, level):
    _forbid_router(monkeypatch)
    intent = Dispatcher().classify(text)
    assert intent.name == "set_volume"
    assert intent.source == "rule"
    assert intent.parameters["level"] == level
    assert intent.confidence == pytest.approx(1.0)


@pytest.mark.parametrize("text", ["Jarvis sesi kapat", "sesi kes", "mute"])
def test_fast_path_mute_routes_to_set_volume(monkeypatch, text):
    _forbid_router(monkeypatch)
    intent = Dispatcher().classify(text)
    assert intent.name == "set_volume"
    assert intent.source == "rule"


@pytest.mark.parametrize(
    "text",
    ["sesi aç", "sesi biraz aç", "sesi açar mısın", "louder", "turn the volume up", "volume up"],
)
def test_fast_path_volume_up(monkeypatch, text):
    _forbid_router(monkeypatch)
    intent = Dispatcher().classify(text)
    assert intent.name == "media_volume_up"
    assert intent.source == "rule"


@pytest.mark.parametrize(
    "text",
    ["sesi kıs", "sesi kısar mısın", "sesi biraz kıs", "quieter", "turn it down", "volume down"],
)
def test_fast_path_volume_down(monkeypatch, text):
    _forbid_router(monkeypatch)
    intent = Dispatcher().classify(text)
    assert intent.name == "media_volume_down"
    assert intent.source == "rule"


def test_fast_path_volume_amount_captured(monkeypatch):
    _forbid_router(monkeypatch)
    assert Dispatcher().classify("sesi biraz kıs").parameters.get("amount") == "biraz"
    assert Dispatcher().classify("sesi çok aç").parameters.get("amount") in ("çok", "cok")


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Jarvis önceki şarkıya geç", "media_previous_track"),
        ("bir sonraki şarkıya geç", "media_next_track"),
        ("sıradaki şarkı", "media_next_track"),
        ("önceki parçaya dön", "media_previous_track"),
        ("next track", "media_next_track"),
        ("previous song", "media_previous_track"),
    ],
)
def test_fast_path_track_direction(monkeypatch, text, expected):
    _forbid_router(monkeypatch)
    intent = Dispatcher().classify(text)
    assert intent.name == expected
    assert intent.source == "rule"


@pytest.mark.parametrize(
    "text", ["şarkıyı durdur", "müziği duraklat", "parçayı devam ettir", "pause the music"]
)
def test_fast_path_play_pause(monkeypatch, text):
    _forbid_router(monkeypatch)
    intent = Dispatcher().classify(text)
    assert intent.name == "media_play_pause"
    assert intent.source == "rule"


def test_fast_path_does_not_swallow_song_by_name(monkeypatch):
    """Bir şarkı ADI içeren istek fast-path'e TAKILMAMALI -> router -> search_music."""
    _patch_router(
        monkeypatch,
        AgentToolResponse(
            tool_calls=[ToolCall(name="search_music", arguments={"query": "Iron Man"})]
        ),
    )
    intent = Dispatcher().classify("Jarvis Iron Man çal")
    assert intent.name == "search_music"
    assert intent.source == "llm"


# --- 2026-09-01: parça-yönü guard (router YİNE çağrıldığında) ----------------


def test_router_track_direction_corrected_to_previous(monkeypatch):
    """qwen2.5:3b "geç" gibi yönsüz fiilde media_next_track seçti ama
    transkriptte açık "evvelki" işareti var -> media_previous_track'e düzelt."""
    _patch_router(
        monkeypatch,
        AgentToolResponse(tool_calls=[ToolCall(name="media_next_track", arguments={})]),
    )
    intent = Dispatcher().classify("bir evvelkine dönsene")
    assert intent.name == "media_previous_track"


def test_router_track_direction_corrected_to_next(monkeypatch):
    _patch_router(
        monkeypatch,
        AgentToolResponse(tool_calls=[ToolCall(name="media_previous_track", arguments={})]),
    )
    intent = Dispatcher().classify("skip ahead to the next one")
    assert intent.name == "media_next_track"


def test_router_track_direction_left_alone_when_ambiguous(monkeypatch):
    """Transkriptte net bir yön işareti yoksa router'ın seçimi korunur."""
    _patch_router(
        monkeypatch,
        AgentToolResponse(tool_calls=[ToolCall(name="media_next_track", arguments={})]),
    )
    intent = Dispatcher().classify("şarkıyı değiştir")
    assert intent.name == "media_next_track"
