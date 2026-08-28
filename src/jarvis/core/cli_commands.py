"""Geliştirici (slash) komutları - `core/app.py:run_jarvis()`'in hibrit girdi
döngüsünde `/` ile başlayan HER metin BURAYA yönlendirilir, Guardrail/
Dispatcher/Brain'e HİÇ gitmez (bkz. `core/input_hub.py`).

Çıktı SADECE `core/console.py` üzerinden basılır ("Tüm projenin terminal
çıktısı buradan geçmeli" ilkesi, bkz. o dosyanın docstring'i) - bunlar
debug/DX araçları, kullanıcıya SESLİ okunacak bir "yanıt" değiller;
`mouth.tts.speak()` bu modülden KESİNLİKLE çağrılmaz.

SRP: bu modül komutların NE YAPTIĞI (politika); girdi kaynaklarının nasıl
toplandığı (mekanizma) `core/input_hub.py`'de, ayrı bir sorumluluk.
"""

import logging
import threading
from typing import Optional

from src.jarvis.adapters.agent_factory import ROUTER_MODEL_NAME
from src.jarvis.adapters.tool_schema import validate_arguments
from src.jarvis.brain.llm import MAX_HISTORY_MESSAGES, SYSTEM_PROMPT
from src.jarvis.brain.llm import MODEL_NAME as BRAIN_MODEL_NAME
from src.jarvis.core import pending_tasks
from src.jarvis.core.console import console, print_panel, print_system, print_table
from src.jarvis.core.dispatcher import Intent
from src.jarvis.core.input_hub import InputEvent, InputHub
from src.jarvis.tools.base import Tool
from src.jarvis.tools.registry import all_tools, get_tool

logger = logging.getLogger("jarvis.cli")

# Modul-seviyesi durum (tek surecli bir CLI icin process-genis bir toggle,
# ears/mouth/brain'in modul-seviyesi Singleton yukleme deseniyle ayni ruhta) -
# ayri bir sinifa sarmalamaya gerek yok, tek bir bool.
_debug_enabled = False

_COMMANDS: dict[str, str] = {
    "/help": "Kullanılabilir komutları listeler.",
    "/status": "Sistem durumunu (modeller, araçlar, hafıza) gösterir.",
    "/debug": "Ayrıntılı (DEBUG) log seviyesini açar/kapatır.",
    "/clear": "Sohbet geçmişini sıfırlar ve ekranı temizler.",
    "/test <araç_adı> [key=value ...]": "Router'ı atlayıp doğrudan bir aracı çalıştırır.",
    "/exit": "Jarvis'i güvenli şekilde kapatır (Ctrl+C ile aynı, ayrıca sesli 'sistemi kapat' ile de tetiklenir).",
}


def is_cli_command(text: str) -> bool:
    """Metin `/` ile mi başlıyor - `run_jarvis()`'in Guardrail/Dispatcher'a
    gitmeden BURAYA yönlendireceği turları ayırt eder."""
    return text.strip().startswith("/")


def handle_cli_command(
    text: str,
    *,
    history: list[dict],
    stop_event: Optional[threading.Event] = None,
    input_hub: Optional[InputHub] = None,
    pending: Optional[list[InputEvent]] = None,
    speaking_event: Optional[threading.Event] = None,
) -> None:
    """`/`-prefiksli bir metni ayrıştırıp işler. Bilinmeyen bir komut sessizce
    yutulmaz - `/help`'e yönlendiren bir uyarı basılır."""
    parts = text.strip().split(maxsplit=1)
    command = parts[0].lower()
    argument = parts[1] if len(parts) > 1 else ""

    if command == "/help":
        _cmd_help()
    elif command == "/status":
        _cmd_status(history)
    elif command == "/debug":
        _cmd_debug()
    elif command == "/clear":
        _cmd_clear(history)
    elif command == "/test":
        _cmd_test(
            argument,
            stop_event=stop_event,
            input_hub=input_hub,
            pending=pending,
            speaking_event=speaking_event,
        )
    elif command == "/exit":
        _cmd_exit(stop_event)
    else:
        print_system(f"Bilinmeyen komut: {command} (bkz. /help)", level="warning")


def _tool_risk_label(tool: Tool) -> str:
    """`"<isim> (risk: <seviye>)"` - `_cmd_status()` ve `_cmd_help()`'in araç
    listesi satırlarında AYNI formatı kullanması için tek noktadan (bu görev
    /help'e ikinci bir araç tablosu eklerken ortaya çıkan bir DRY düzeltmesi)."""
    return f"{tool.name} (risk: {tool.risk_level.value})"


def _cmd_help() -> None:
    print_table(
        "Jarvis Geliştirici Komutları",
        ["Komut", "Açıklama"],
        list(_COMMANDS.items()),
    )

    console.print(
        "\n[dim]Aşağıdaki yetenekler hem SESLİ (mikrofon) hem YAZILI (doğal dil) "
        "olarak, tıpkı aynı cümleyle, aynı router üzerinden tetiklenir - ikisi "
        "arasında bir fark yoktur. '/test <araç_adı> [key=value ...]' ise "
        "router'ı atlayıp bir aracı DOĞRUDAN çalıştırmanın yoludur (örnek "
        "parametreler için isim üstüne gelin ya da /status'a bakın).[/dim]"
    )
    print_table(
        "Jarvis Yetenekleri (araçlar)",
        ["Araç", "Ne yapar (sesli/yazılı aynı cümleyle)", "Risk"],
        [(name, tool.description, tool.risk_level.value) for name, tool in sorted(all_tools().items())],
    )


def _cmd_status(history: list[dict]) -> None:
    # Gecikmeli import - bkz. core/input_hub.py:_mic_producer()'daki AYNI
    # gerekce yorumu: ears/mouth modullerinin ustu gercek modelleri yukler,
    # bunu SADECE /status gercekten calistiginda odemek istiyoruz (test
    # paketinin hizli kalmasi icin de onemli).
    from src.jarvis.ears.listener import get_active_device as ears_device
    from src.jarvis.mouth.tts import get_active_device as mouth_device

    tools = all_tools()
    lines = [
        f"[bold]Ears:[/bold] faster-whisper turbo ({ears_device()})",
        f"[bold]Brain:[/bold] {BRAIN_MODEL_NAME} · Router: {ROUTER_MODEL_NAME} (Ollama)",
        f"[bold]Mouth:[/bold] XTTS-v2 ({mouth_device()})",
        f"[bold]Debug modu:[/bold] {'AÇIK' if _debug_enabled else 'kapalı'}",
        f"[bold]Hafıza:[/bold] {max(len(history) - 1, 0)}/{MAX_HISTORY_MESSAGES} mesaj",
        f"[bold]Aktif araçlar ({len(tools)}):[/bold]",
    ]
    for _, tool in sorted(tools.items()):
        lines.append(f"  - {_tool_risk_label(tool)}")
    pending_rows = pending_tasks.list_pending(limit=5)
    if pending_rows:
        lines.append(f"[bold]Bekleyen onaylar ({len(pending_rows)}):[/bold]")
        for row in pending_rows:
            text = row["text"]
            snippet = (text[:60] + "…") if len(text) > 60 else text
            lines.append(f"  - #{row['id']} · {row['source']} · {snippet}")
    else:
        lines.append("[bold]Bekleyen onaylar:[/bold] yok")
    print_panel("Jarvis Durumu", "\n".join(lines), border_style="bold cyan")


def _cmd_debug() -> None:
    global _debug_enabled
    _debug_enabled = not _debug_enabled
    level = logging.DEBUG if _debug_enabled else logging.INFO
    # Root logger seviyesini calisma zamaninda degistiriyoruz - core/console.py:
    # setup_logging()'in kurdugu tek RichHandler'a dokunmadan (bkz. o fonksiyonun
    # "hangisi once import edilirse edilsin sonuc AYNI kalir" idempotentlik notu).
    # Bu seviyeye baglanan somut yeni gorunurluk: core/guardrail/base.py'nin
    # her check icin surdugu sure + core/dispatcher.py'nin router'in ham
    # tool_calls cevabi (ikisi de daha once DEBUG seviyesinde eklendi).
    logging.getLogger().setLevel(level)
    print_system(f"Debug modu {'açıldı' if _debug_enabled else 'kapandı'}.", level="info")


def _cmd_exit(stop_event: Optional[threading.Event]) -> None:
    """Ctrl+C ile AYNI kapatma yolunu tetikler (bkz. `core/app.py:run_jarvis()`nin
    `while not stop_event.is_set()` kosulu) - sadece stop_event'i set ediyor,
    dongu bir SONRAKI iterasyonda kendiliginden cikiyor. Bu modul SESLI bir
    yanit uretmez (bkz. dosya-ustu docstring) - sesli "sistemi kapat" esdegeri
    icin bkz. `core/dispatcher.py:SHUTDOWN_INTENT_NAME` + `core/app.py:_handle_turn()`."""
    print_system("Kapatma istendi (/exit) - güvenli şekilde kapatılıyor...", level="warning")
    if stop_event is not None:
        stop_event.set()


def _cmd_clear(history: list[dict]) -> None:
    history[:] = [{"role": "system", "content": SYSTEM_PROMPT}]
    console.clear()
    print_system("Sohbet geçmişi ve ekran temizlendi.", level="success")


def _parse_test_arguments(raw: str) -> tuple[str, dict[str, str]]:
    """`"<araç_adı> key=value key2=value2"` -> (`araç_adı`, `{"key": "value", ...}`).

    `=` icermeyen ekstra token'lar sessizce yoksayilir (yanlis kullanimda
    calistirmayi REDDETMEK yerine en makul yorumu denemek, /test'in bir
    debug araci olma amacina uygun).
    """
    tokens = raw.split()
    if not tokens:
        return "", {}
    name = tokens[0]
    parameters: dict[str, str] = {}
    for token in tokens[1:]:
        if "=" in token:
            key, _, value = token.partition("=")
            parameters[key] = value
    return name, parameters


def _cmd_test(
    argument: str,
    *,
    stop_event: Optional[threading.Event],
    input_hub: Optional[InputHub],
    pending: Optional[list[InputEvent]],
    speaking_event: Optional[threading.Event] = None,
) -> None:
    """Dispatcher'ın "hangi aracı seçeceğim" kararını (`Dispatcher.classify()`)
    BYPASS edip doğrudan bir aracı çalıştırır.

    GÜVENLİK NOTU: bu, `core/app.py:_execute_tool()`'un KENDİ guardrail +
    risk + onay zincirini ATLAMAZ - `tools/base.py`'nin ilkesi gereği
    ("güvenlik kararı tool'un KENDİSİNE bırakılmaz, tek merkezi kontrol
    noktası") bu zincir Intent'in kaynağından (router/rule/manuel test)
    BAĞIMSIZ olarak her zaman çalışır. Ayrıca `validate_arguments()`
    (`adapters/tool_schema.py`) BİLİNÇLİ OLARAK burada da çağrılıyor
    (security-reviewer bulgusu): sadece tip kontrolü için değil, tool'un
    KENDİ şemasında TANIMLI OLMAYAN anahtarları elemek için de - aksi
    halde `/test mcp_<sunucu>_<araç> extra_param=değer` gibi bir çağrı,
    MCP aracının (`tools/mcp_tool.py:MCPTool.execute()`, sadece `"lang"`ı
    süzer) şemasında olmayan rastgele bir parametreyi doğrudan dış/uzak
    MCP sunucusuna iletebilirdi - router yolunda bu ikinci savunma katmanı
    (şema-kapsamlama) hep vardı, `/test` bunu atlamamalı.

    KAPSAM/TEHDİT MODELİ NOTU: `/test` konsola yazabilen HERKESE açıktır,
    ayrı bir kimlik doğrulaması yoktur - bu, "konsola erişimi olan zaten en
    az terminal-eşdeğeri güvene sahiptir" varsayımına dayanır (tek
    kullanıcılı/yerel dağıtım). Onay kapısı sesle aynı olduğu için ek bir
    *onaysız* yetenek açmaz, ama `core/dispatcher.py:_ROUTER_SYSTEM_PROMPT`'un
    (yalnızca prompt seviyesindeki, kod tarafından zorlanmayan) kısıtlamalarını
    tamamen atlar - Jarvis'in konsolu ileride düşük-güvenli bir tarafa
    (uzaktan erişim, kiosk vb.) açılırsa `/test` ayrıca kısıtlanmalı/devre
    dışı bırakılmalıdır.
    """
    name, extra_parameters = _parse_test_arguments(argument)
    if not name:
        print_system("Kullanım: /test <araç_adı> [key=value ...] (bkz. /status için isim listesi)", level="warning")
        return

    tool = get_tool(name)
    if tool is None:
        print_system(f"'{name}' adında bir araç bulunamadı (bkz. /status).", level="error")
        return

    validated_parameters = validate_arguments(tool, extra_parameters)
    if validated_parameters is None:
        print_system(f"'{name}' için verilen parametreler şemaya uymuyor.", level="error")
        return

    fake_intent = Intent(
        name=name,
        confidence=1.0,
        parameters={**validated_parameters, "lang": extra_parameters.get("lang", "tr")},
        source="rule",
    )

    # Dongusel import (core.app <-> core.cli_commands) BILINCLI OLARAK
    # burada, cagri aninda (deferred) cozuluyor - modul-seviyesinde
    # (dosyanin ustunde) import edilseydi ikisi de yuklenemezdi. Cagri
    # aninda iki modul de zaten tam yuklu oldugu icin sorunsuz.
    from src.jarvis.core.app import _execute_tool

    result = _execute_tool(
        tool, fake_intent, stop_event, input_hub=input_hub, pending=pending, speaking_event=speaking_event
    )
    console.print(f"[bold cyan]/test sonucu ({name}):[/bold cyan] {result}")
