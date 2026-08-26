"""Merkezi rich-tabanli konsol/loglama katmani - Faz 3 oncesi CLI DX yamasi.

Tum projenin terminal ciktisi (loglar, kullanici/Jarvis diyalogu, guardrail
kararlari, bekleme durumlari) buradan gecmeli. Amac ug bolgeye ayrilir:

1. `setup_logging()` - stdlib `logging` modulunu bir `RichHandler`'a baglar.
   Projedeki tum `logger = logging.getLogger("jarvis.<katman>")` cagrilari
   (ears, mouth, tools/*, adapters) DEGISMEDEN kalir - sadece FORMATLAMA
   degisir, tek merkezi bir yerden. Eskiden 3 ayri dosyada (ears/listener.py,
   mouth/tts.py, tools/spotify.py __main__) birbirinden bagimsiz ayni
   `logging.basicConfig(format="[%(levelname)s] %(message)s")` cagrisi
   tekrarlaniyordu (DRY ihlali) - hepsi artik bu tek fonksiyonu cagiriyor.
   `logging.basicConfig` stdlib'de idempotent (ilk cagiran kazanir) oldugu
   icin, hangisi once import edilirse edilsin sonuc AYNI kaliyor.
2. `print_system`/`print_agent`/`print_guardrail` - dogrudan `console.print`
   ile kullanicinin GORMESI gereken (log dosyasina degil, terminale) mesajlar
   icin. `logging`'in yerini almiyorlar - loglama disiplini (`.claude/rules/
   python-style.md`: "Loglama icin print yerine logging kullan") hala
   `logger.*` cagrilariyla korunuyor; bu fonksiyonlar SADECE kullaniciya
   dogrudan gorunmesi gereken ciktilar (diyalog, guardrail karari, sistem
   durumu) icindir - print_guardrail iceride ayrica logger.info/warning de
   cagirir ki structured log kaybolmasin.
3. `status_spinner`/`print_boot_sequence` - I/O beklemeleri (model yukleme,
   LLM cagrisi, transkripsiyon) ve acilis ekrani icin.

Kullanicidan gelen metin (transkript, tool ciktisi, hata mesaji) `rich`
markup'iyla CAKISABILECEGI icin (orn. bir dosya adi "[error]" iceriyorsa),
`_escape()` ile serbest metin HER ZAMAN kacisli hale getirilip stil etiketleri
(`[bold cyan]...`) elle ekleniyor - kullanicidan gelen bir string asla
dogrudan markup olarak yorumlanmiyor.
"""

import logging

from rich.console import Console
from rich.markup import escape as _escape
from rich.panel import Panel
from rich.status import Status

console = Console()

_AMBER = "#FFBF00"  # Kehribar - boot ekrani ve spinner'lar icin sabit marka rengi

# SADECE ASCII karakterler (# ve bosluk) - bkz. asagidaki UnicodeEncodeError notu.
_ASCII_ART = (
    "     ##    ###    ######   ##   ##  #####   #####\n"
    "     ##   ## ##   ##   ##  ##   ##    #    ##\n"
    "     ##  #######  ######    ## ##     #     ####\n"
    "##   ##  ##   ##  ## ##     ## ##     #         ##\n"
    " #####   ##   ##  ##  ###    ###    #####  #####"
)

# NOT: rich'in Unicode sembolleri (onay/red/uyari/bilgi ikonlari, kutu-cizim
# karakterleri vb.) BILINCLI OLARAK KULLANILMIYOR - bu makinede ve muhtemelen
# kullanicinin gercek PowerShell/cmd oturumunda konsol kod sayfasi Windows'un
# varsayilan yerel kod sayfasi (bu ortamda cp1254, Turkce Windows) - bu
# sayfalar genis Unicode sembol araligini KAPSAMAZ. rich'in "legacy Windows"
# render yolu, konsola YAZARKEN Python'un genel stdout encoding'ini degil
# DOGRUDAN Win32 konsol kod sayfasini kullaniyor (chcp 65001/UTF-8'e gecmek
# KULLANICI TARAFINDA ayri bir adim gerektirir, bu koddan kontrol edilemez) -
# gercek testte UnicodeEncodeError (charmap codec) olarak patladi. Bu yuzden
# TUM konsol ciktisi (ikonlar + ASCII sanati) sadece 7-bit ASCII
# karakterlerden olusuyor - hangi kod sayfasinda calisirsa calissin garanti
# calisir.
_SYSTEM_STYLES: dict[str, tuple[str, str]] = {
    # level -> (renk, ikon).
    "info": ("blue", "[i]"),
    "success": ("green", "[OK]"),
    "warning": ("yellow", "[!]"),
    "error": ("red", "[X]"),
}

_logger = logging.getLogger("jarvis.guardrail")  # guardrail/base.py ile AYNI isim - bkz. print_guardrail


def setup_logging(level: int = logging.INFO) -> None:
    """Root logger'i tek, merkezi bir RichHandler'a baglar.

    `force=False` (varsayilan) bilincli - `logging.basicConfig` zaten
    idempotent (ilk cagiran kazanir); ears/mouth/spotify gibi birden fazla
    entrypoint'in her biri kendi baslangicinda bu fonksiyonu cagirmasi
    guvenlidir (hepsi ayni sonuca varir), `force=True` KULLANILMIYOR ki
    -bir cagiran zaten kurulumu tamamladiysa- ikinci bir cagirinin
    handler'i sessizce degistirip beklenmedik yan etkiye yol acmasi
    engellensin.
    """
    from rich.logging import RichHandler

    logging.basicConfig(
        level=level,
        format="%(name)s: %(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True, show_path=False, markup=False)],
    )


def print_system(msg: str, level: str = "info") -> None:
    """Sistem durumu/basari/uyari/hata mesaji - renk+ikonla, kullaniciya dogrudan gorunur."""
    color, icon = _SYSTEM_STYLES.get(level, _SYSTEM_STYLES["info"])
    console.print(f"[bold {color}]{icon} {_escape(msg)}[/bold {color}]")


_USER_NAMES = {"user", "kullanici", "kullanıcı"}


def print_agent(agent_name: str, text: str) -> None:
    """Kullanici/Jarvis diyalogunu renkle ayirir - kullanici sari, Jarvis/Hermes cyan."""
    style = "bold yellow" if agent_name.strip().lower() in _USER_NAMES else "bold cyan"
    console.print(f"[{style}]{_escape(agent_name)}:[/{style}] {_escape(text)}")


def print_guardrail(check_name: str, passed: bool, reason: str) -> None:
    """Guardrail kabul/red kararini tek satirda gosterir; ayrica DEBUG seviyesinde loglar.

    guardrail/base.py'deki eski `logger.info("Guardrail [...]: kabul - ...")` /
    `logger.warning("Guardrail [...]: RED - ...")` cagrilarinin YERINE gecer. Bilincli
    olarak DEBUG kullaniyoruz (INFO/WARNING DEGIL): `setup_logging()` varsayilan
    seviyesi INFO oldugundan, bu satir bugun hicbir yere yazilmiyor GORUNMUYOR -
    amac, karari zaten guzelce basan `console.print` satiriyla AYNI seyi ikinci kez
    (RichHandler uzerinden ham log satiri olarak) ekrana bastirip yormamak. Yine de
    kod olarak duruyor: ileride bir dosya handler'i eklenip seviye DEBUG'a
    cekilirse, hicbir guardrail karari kaybolmamis olur.
    """
    if passed:
        console.print(f"[dim]Guardrail[/dim] [bold]{_escape(check_name)}[/bold] [bold green][PASS][/bold green] - {_escape(reason)}")
        _logger.debug("Guardrail [%s]: kabul - %s", check_name, reason)
    else:
        console.print(f"[dim]Guardrail[/dim] [bold]{_escape(check_name)}[/bold] [bold red][REJECTED][/bold red] - {_escape(reason)}")
        _logger.debug("Guardrail [%s]: RED - %s", check_name, reason)


def _format_parameters(parameters: dict) -> str:
    if not parameters:
        return "(parametre yok)"
    return "\n".join(f"{_escape(str(key))}: {_escape(str(value))}" for key, value in parameters.items())


def print_approval_panel(tool_name: str, risk_level: str, parameters: dict) -> None:
    """Riskli bir tool cagrisindan ONCE, TUM parametreleri buyuk bir panelde gosterir.

    Faz 3.3 (semantic router) gecisinden sonra `parameters` artik LLM'in
    URETTIGI degerler olabilir (kullanicinin soylediginden farkli olabilir) -
    bu panel core/app.py:_execute_tool'daki [Y/N] onayindan hemen once
    cagrilir, kullanici NEYI onayladigini ekranda buyuk ve net gorur (bkz.
    tools/terminal_tool.py modul docstring'i).
    """
    console.print(
        Panel(
            _format_parameters(parameters),
            title=f"[!] ONAY GEREKLİ - {_escape(tool_name)} (risk: {_escape(risk_level)})",
            border_style="bold yellow",
            expand=False,
        )
    )


def print_router_decision(tool_name: str, confidence: float, parameters: dict) -> None:
    """Semantic router'in sectigi arac + parametreleri gosterir (LLM'in "anladigi"
    seyi seffaflastirir) - `lang` parametresi kullaniciya anlamsiz oldugu icin
    gosterilmez.
    """
    visible_params = {key: value for key, value in parameters.items() if key != "lang"}
    console.print(
        Panel(
            f"[bold]{_escape(tool_name)}[/bold] (güven: {confidence:.2f})\n{_format_parameters(visible_params)}",
            title="Semantic Router Kararı",
            border_style=f"bold {_AMBER}",
            expand=False,
        )
    )


def status_spinner(msg: str) -> Status:
    """I/O beklemeleri (model yukleme, LLM cagrisi, transkripsiyon) icin amber spinner.

    `with status_spinner("..."):` seklinde kullanilir - `rich.console.Console.status()`
    dondurdugu Status nesnesi zaten bir context manager, ayrica sarmalamaya gerek yok.
    """
    return console.status(f"[bold {_AMBER}]{_escape(msg)}[/bold {_AMBER}]", spinner="dots")


def print_boot_sequence() -> None:
    """Ekrani temizleyip kehribar renkli ASCII 'J.A.R.V.I.S' logosunu basar.

    `markup=False` BILINCLI: ASCII sanati kutu-cizim karakterlerinden olusuyor
    (kullanicidan gelen serbest metin degil), ama yine de rich markup ayristirmasi
    devre disi birakiliyor ki ileride sanat degisirse `[`/`]` gibi karakterler
    yanlislikla stil etiketi olarak yorumlanmasin.
    """
    console.clear()
    console.print(_ASCII_ART, style=f"bold {_AMBER}", markup=False)
    console.print("Personal Autonomous Assistant System", style=f"dim {_AMBER}", justify="center")
    console.print()
