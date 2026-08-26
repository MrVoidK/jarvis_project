"""Faz 3 arac katmani testleri - tool'lar, risk puanlama ve dispatcher kalip eslesmesi.

Dosya yazan araclar (notes, files) `monkeypatch` ile gecici bir dizine yonlendiriliyor -
testler gercek Obsidian vault'una veya jarvis_workspace/ dizinine dokunmaz.

Calistirma: `python -m pytest tests/ -v` (repo kokunden, bkz. CLAUDE.md Komutlar).
"""

from src.jarvis.core.dispatcher import Dispatcher
from src.jarvis.core.risk import RiskLevel, requires_approval
from src.jarvis.tools import files as files_module
from src.jarvis.tools import notes_tool as notes_module
from src.jarvis.tools.registry import TOOL_REGISTRY
from src.jarvis.tools.terminal_tool import LaunchAppTool, RunCommandTool

# --- risk puanlama ---


def test_only_low_risk_skips_approval():
    assert not requires_approval(RiskLevel.LOW)
    for level in (RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL):
        assert requires_approval(level), f"{level} onaysiz gecmemeli"


def test_shell_tool_is_always_high_risk():
    # Terminal komutu, icerigi ne olursa olsun ISTISNASIZ onay istemeli
    # (bkz. tools/terminal_tool.py modul docstring'i, 1. katman).
    assert RunCommandTool.risk_level is RiskLevel.HIGH


def test_registry_keys_match_tool_names():
    for name, tool in TOOL_REGISTRY.items():
        assert name == tool.name


# --- notes ---


def _patch_vault(monkeypatch, tmp_path):
    """notes_tool'u sahte bir vault'a yonlendirir - gercek Obsidian vault'una dokunulmaz."""
    vault = tmp_path / "vault"
    monkeypatch.setattr(notes_module, "get_obsidian_vault", lambda: vault)
    monkeypatch.setattr(notes_module, "is_path_safe", lambda path: True)
    return vault


def test_create_and_read_note(monkeypatch, tmp_path):
    _patch_vault(monkeypatch, tmp_path)

    create = notes_module.CreateNoteTool()
    read = notes_module.ReadNotesTool()

    assert "yok" in read.execute({"lang": "tr"})  # henuz not yok

    create.execute({"lang": "tr", "content": "sut al"})
    create.execute({"lang": "tr", "content": "faturayi ode"})

    result = read.execute({"lang": "tr"})
    assert "sut al" in result
    assert "faturayi ode" in result


def test_create_note_rejects_empty_content(monkeypatch, tmp_path):
    vault = _patch_vault(monkeypatch, tmp_path)

    result = notes_module.CreateNoteTool().execute({"lang": "en", "content": "   "})
    assert "empty" in result.lower()
    assert not (vault / notes_module.NOTES_SUBDIR).exists()  # dosya hic olusturulmamali


def test_create_note_blocked_by_unsafe_path(monkeypatch, tmp_path):
    """is_path_safe() False donerse not YAZILMAMALI - security.yaml yanlis
    yapilandirilsa bile vault disina yazma engellenir (bkz. modul docstring'i)."""
    vault = tmp_path / "vault"
    monkeypatch.setattr(notes_module, "get_obsidian_vault", lambda: vault)
    monkeypatch.setattr(notes_module, "is_path_safe", lambda path: False)

    result = notes_module.CreateNoteTool().execute({"lang": "en", "content": "test"})
    assert "security" in result.lower() or "güvenlik" in result.lower()
    assert not vault.exists()


# --- files ---


def test_list_files_reports_empty_and_populated(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    monkeypatch.setattr(files_module, "WORKSPACE_DIR", str(workspace))
    tool = files_module.ListFilesTool()

    assert "empty" in tool.execute({"lang": "en"}).lower()

    (workspace / "rapor.txt").write_text("x", encoding="utf-8")
    assert "rapor.txt" in tool.execute({"lang": "en"})


# --- terminal (run_command) ---


def test_run_command_executes_and_returns_output():
    result = RunCommandTool().execute({"lang": "en", "command": "echo jarvis_test_ok"})
    assert "jarvis_test_ok" in result


def test_run_command_rejects_empty_command():
    result = RunCommandTool().execute({"lang": "en", "command": ""})
    assert "didn't get a command" in result.lower()


def test_run_command_strips_trailing_punctuation():
    """Regresyon: docs/TODO.md madde 1 - "Run command ls." icin STT'nin ekledigi
    sondaki nokta command'a karisip Windows'ta 'ls.' calistirilmaya calisiliyordu."""
    result = RunCommandTool().execute({"lang": "en", "command": "echo jarvis_test_ok."})
    assert "jarvis_test_ok" in result
    assert "not recognized" not in result.lower()


# --- terminal (launch_app) ---


def test_launch_app_starts_known_application(monkeypatch):
    from src.jarvis.tools import terminal_tool as terminal_module

    monkeypatch.setattr(terminal_module, "resolve_app_command", lambda name: "code")
    calls: list[tuple] = []
    monkeypatch.setattr(
        terminal_module.subprocess, "Popen", lambda command, shell=False: calls.append((command, shell))
    )

    result = LaunchAppTool().execute({"lang": "en", "app_name": "vs code"})

    assert calls == [("code", True)]
    assert "vs code" in result.lower()
    assert "code" not in result.lower().replace("vs code", "")  # cozulmus binary ismi konusmaya SIZMAMALI


def test_launch_app_rejects_unknown_application(monkeypatch):
    from src.jarvis.tools import terminal_tool as terminal_module

    monkeypatch.setattr(terminal_module, "resolve_app_command", lambda name: None)
    monkeypatch.setattr(
        terminal_module.subprocess,
        "Popen",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("bilinmeyen uygulamada Popen cagrilmamali")),
    )

    result = LaunchAppTool().execute({"lang": "tr", "app_name": "discord"})
    assert "bilmiyorum" in result.lower() or "bulamadım" in result.lower()


def test_launch_app_is_medium_risk():
    assert LaunchAppTool.risk_level is RiskLevel.MEDIUM


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


def test_dispatcher_pause_music_tolerates_pass_mishearing():
    """Regresyon: docs/TODO.md madde 2 - Whisper "pause"u bazen "pass" diye
    transkribe ediyor. Bare "pass" (yaygin kelime, yanlis pozitif riski yuksek)
    tetiklenmemeli ama "pass music"/"pass the song" gibi acikca muzikle
    birlikte gecen hali pause_music'e eslesmeli."""
    dispatcher = Dispatcher()

    pass_music = dispatcher.match_rule("Pass music.")
    assert pass_music is not None
    assert pass_music.name == "pause_music"
    assert pass_music.parameters["lang"] == "en"

    assert dispatcher.match_rule("I'll pass on that, thanks.") is None


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


# Not: Spotify entegrasyonu (tools/spotify.py) kaldirildi - muzik kontrolu artik
# tools/media_tool.py (yerel Windows medya tuslari) uzerinden. Dispatcher'in
# play_music/pause_music/skip_track regex testleri, _RULES semantic router'a
# devredilirken (bkz. sonraki commit) buradan kaldirilacak.
