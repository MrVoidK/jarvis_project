"""Terminal komutu calistirma + uygulama baslatma - sistemin EN riskli yuzeyi.

Katmanli savunma (defense-in-depth), hicbiri tek basina yeterli sayilmadan:

1. `risk_level = HIGH` -> core/risk.py:requires_approval() sayesinde ISTISNASIZ her
   komut, icerigi ne olursa olsun, calismadan once insan onayindan gecer. "Zararsiz
   gorunen komutlari onaysiz gecir" (whitelist) yaklasimi bilincli olarak REDDEDILDI:
   whitelist'i yanlis tasarlamak sessiz bir guvenlik acigi yaratir, kullaniciyi bir
   kez fazla onay sormak ise yalnizca kucuk bir rahatsizliktir (OWASP LLM08).
2. Onay isteminde komutun TAM METNI kullaniciya gosterilir (bkz. core/app.py) -
   kullanici neyi onayladigini gorur, "gizli" bir komut calisamaz.
3. Onay SORULMADAN once, komut metni mevcut OutputSafetyCheck guardrail'inden
   gecirilir (bkz. core/app.py:_execute_tool) - bilinen yikici kaliplar (rm -rf,
   format, DROP TABLE, fork bomb...) kullaniciya sorulmadan reddedilir, boylece
   yanlislikla "Y"ye basma ihtimali bu kaliplar icin hic dogmaz.
4. Zaman asimi (COMMAND_TIMEOUT_S) - asili kalan bir komut Jarvis'i kilitlemez.

`shell=True` BILINCLI bir tercih ve BUGUN tek basina bir enjeksiyon acigi degil: bu
aracin tanimi zaten "kullanicinin dikte ettigi komutu calistir" - calistirilan metin
kullanicinin kendi komutu, guvenilmeyen bir veri kaynagindan gelen ve masum bir
komuta enjekte edilen bir parca degil. shell=True, Windows'ta `dir`/`echo` gibi
kabuk yerlesiklerinin (builtin) calisabilmesi icin gerekli. Bunun bedeli, kabuk
metakarakterleriyle (`&&`, `|`, `;`) komut zincirlenebilmesidir - bu risk yukaridaki
2. katmanla (kullanici tam metni gorur) ve 3. katmanla karsilaniyor.

!! GECIS TAMAMLANDI (Faz 3.3, semantic router) !!
Bu dosyanin eski hali (tools/shell.py) burada, aynen bu geciste, gecerliligini
yitirecegi ONCEDEN belgelenmis bir varsayima dayaniyordu: "icerik SADECE
dispatcher regex'inden geliyor; intent/parametre cikarimi LLM'e tasinirsa bu
savunma gecersiz olur." Artik `command` parametresi Ollama native tool-calling
(core/dispatcher.py:Dispatcher.classify()) uzerinden LLM tarafindan uretiliyor -
klasik prompt-injection -> RCE zinciri teorik olarak acik. Somut mitigasyon:
(a) core/app.py:_execute_tool artik intent.parameters'taki TUM string degerleri
(sadece bu aracin degil) OutputSafetyCheck'ten geciriyor, (b) core/console.py:
print_approval_panel() onay ekraninda LLM'in urettigi TAM parametreyi buyuk ve
net gosteriyor - kullanici kendi soylediginden farkli bir komutu onayliyor olsa
bile bunu GORUR, (c) bu dosya + dispatcher.py + app.py, security-reviewer
subagent'i ile bu gecisin hemen ardindan incelendi (bkz. CLAUDE.md).
"""

import logging
import subprocess

from src.jarvis.core.console import print_system
from src.jarvis.core.risk import RiskLevel
from src.jarvis.core.security_config import resolve_app_command
from src.jarvis.core.text import strip_trailing_punct
from src.jarvis.tools.base import Tool

logger = logging.getLogger("jarvis.tools.terminal")

COMMAND_TIMEOUT_S = 15
KILL_TIMEOUT_S = 5  # zaman asimi sonrasi surec agacini oldurmek icin verilen sure
OUTPUT_CHAR_LIMIT = 200  # cikti TTS'e okunacak - uzun ciktilar kirpiliyor

_EMPTY_MESSAGES = {
    "tr": "Calistirilacak bir komut alamadim.",
    "en": "I didn't get a command to run.",
}
_NO_OUTPUT_MESSAGES = {
    "tr": "Komut calisti, cikti uretmedi.",
    "en": "The command ran and produced no output.",
}
_TIMEOUT_MESSAGES = {
    "tr": "Komut zaman asimina ugradi, durduruldu.",
    "en": "The command timed out and was stopped.",
}
_FAILED_TEMPLATES = {
    "tr": "Komut {code} hata koduyla bitti: {output}",
    "en": "The command exited with code {code}: {output}",
}
_OK_TEMPLATES = {"tr": "Komut ciktisi: {output}", "en": "Command output: {output}"}


def _localized(messages: dict[str, str], lang: str) -> str:
    return messages.get(lang, messages["en"])


def _kill_process_tree(process: subprocess.Popen) -> None:
    """Zaman asimina ugrayan bir komutun TUM surec agacini oldurur.

    Windows'ta `taskkill /F /T` (/T = agac) kullaniliyor; process.kill() tek basina
    sadece dogrudan cocugu (cmd.exe) oldurur, onun baslattigi surecler yetim kalirdi.
    taskkill bulunamazsa/basarisiz olursa en azindan dogrudan cocuk oldurulur.
    """
    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(process.pid)],
            capture_output=True,
            timeout=KILL_TIMEOUT_S,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("taskkill basarisiz (%s) - sadece dogrudan surec olduruluyor.", exc)
    finally:
        # taskkill basarili olsa bile Popen nesnesinin kendi kaynaklarini serbest
        # birakmasi icin kill()+wait() cagriliyor (zaten olmusse no-op).
        try:
            process.kill()
            process.wait(timeout=KILL_TIMEOUT_S)
        except (OSError, subprocess.TimeoutExpired):
            pass


class RunCommandTool(Tool):
    """Kullanicinin dikte ettigi terminal komutunu (onay sonrasi) calistirir."""

    name = "run_command"
    description = "Kullanicinin soyledigi terminal komutunu calistirir."
    risk_level = RiskLevel.HIGH  # ISTISNASIZ - bkz. modul docstring'i, 1. katman
    parameters_schema: dict = {
        "command": {"type": "string", "description": "Calistirilacak Windows terminal komutu."}
    }
    required_parameters: list[str] = ["command"]

    def execute(self, params: dict) -> str:
        lang = params.get("lang", "en")
        # STT cumle sonuna noktalama ekliyor ("Run command ls." -> command="ls.") -
        # Windows'ta "ls." taninmiyor, bu yuzden komut olarak calistirilmadan once
        # noktalama temizleniyor (core/text.py:strip_trailing_punct, docs/TODO.md madde 1).
        command = strip_trailing_punct((params.get("command") or "").strip())
        if not command:
            return _localized(_EMPTY_MESSAGES, lang)

        logger.warning("Terminal komutu calistiriliyor (onaylandi): %r", command)
        print_system(f"Komut çalıştırılıyor: {command}", level="warning")
        # Popen + communicate(timeout) kullaniyoruz (subprocess.run degil): zaman
        # asiminda SADECE dogrudan cocugu (cmd.exe) degil, onun baslattigi TUM surec
        # agacini oldurmemiz gerekiyor. Guvenlik incelemesi (security-reviewer, Faz 3)
        # subprocess.run'in timeout'ta yalnizca cmd.exe'yi sonlandirdigini, `start ...`
        # veya `ping -t` gibi alt sureclerin yetim kalip calismaya devam ettigini
        # buldu - "asili kalan bir komut Jarvis'i kilitlemez" iddiasi Jarvis icin
        # dogruydu ama sistemde birakilan surec icin degildi.
        process = subprocess.Popen(
            command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        try:
            stdout, stderr = process.communicate(timeout=COMMAND_TIMEOUT_S)
            returncode = process.returncode
        except subprocess.TimeoutExpired:
            logger.error("Komut zaman asimina ugradi, surec agaci sonlandiriliyor: %r", command)
            _kill_process_tree(process)
            return _localized(_TIMEOUT_MESSAGES, lang)

        output = (stdout or stderr or "").strip()
        if len(output) > OUTPUT_CHAR_LIMIT:
            output = output[:OUTPUT_CHAR_LIMIT] + "..."

        if returncode != 0:
            return _localized(_FAILED_TEMPLATES, lang).format(
                code=returncode, output=output or "-"
            )
        if not output:
            return _localized(_NO_OUTPUT_MESSAGES, lang)
        return _localized(_OK_TEMPLATES, lang).format(output=output)


_UNKNOWN_APP_TEMPLATES = {
    "tr": "'{app_name}' adında bilinen bir uygulama bulamadım.",
    "en": "I don't know an application called '{app_name}'.",
}
_LAUNCHED_TEMPLATES = {
    "tr": "{app_name} açılıyor.",
    "en": "Opening {app_name}.",
}
_LAUNCH_FAILED_TEMPLATES = {
    "tr": "{app_name} açılırken bir hata oluştu.",
    "en": "Something went wrong while opening {app_name}.",
}


class LaunchAppTool(Tool):
    """config/security.yaml'daki known_applications allowlist'inden bir uygulama baslatir.

    LLM asla keyfi bir binary/path uretip calistiramaz - SADECE allowlist'teki
    isimler cozulur (bkz. core/security_config.py:resolve_app_command);
    eslesme yoksa subprocess HIC cagrilmaz. risk_level MEDIUM secildi (LOW
    degil): allowlist bugun kucuk olsa da, ileride genel-amacli bir uygulama
    eklenirse beklenmedik bir yan etki riskini onceden karsilamak icin -
    projede zaten "salt-okunur olsa da MEDIUM" gibi ihtiyatli emsaller var
    (bkz. notes_tool.py:ReadNotesTool).
    """

    name = "launch_app"
    description = "Bilinen (allowlist'teki) bir masaustu uygulamasini baslatir."
    risk_level = RiskLevel.MEDIUM
    parameters_schema: dict = {
        "app_name": {
            "type": "string",
            "description": "Baslatilacak uygulamanin kullanici dostu adi (orn. 'vs code', 'steam').",
        }
    }
    required_parameters: list[str] = ["app_name"]

    def execute(self, params: dict) -> str:
        lang = params.get("lang", "en")
        app_name = (params.get("app_name") or "").strip()
        if not app_name:
            return _localized(_UNKNOWN_APP_TEMPLATES, lang).format(app_name="")

        command = resolve_app_command(app_name)
        if command is None:
            logger.warning("Bilinmeyen uygulama istendi: %r", app_name)
            return _localized(_UNKNOWN_APP_TEMPLATES, lang).format(app_name=app_name)

        logger.info("Uygulama baslatiliyor (onaylandi): %r -> %r", app_name, command)
        # Cozulmus binary path ASLA TTS'e/kullaniciya gosterilmez - sadece
        # kullanici dostu app_name konusulur (bkz. core/console.py TTS disiplini).
        try:
            subprocess.Popen(command, shell=True)
        except OSError as exc:
            logger.error("Uygulama baslatilamadi (%r): %s", app_name, exc)
            return _localized(_LAUNCH_FAILED_TEMPLATES, lang).format(app_name=app_name)

        return _localized(_LAUNCHED_TEMPLATES, lang).format(app_name=app_name)
