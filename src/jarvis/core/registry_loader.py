"""Dinamik arac yukleyici - agents/registry/*.yaml manifest'lerinden Tool uretir.

`tools/registry.py:TOOL_REGISTRY`'nin statik dict olmasi bilincli bir guvenlik
ozelligi ("bir arac yanlislikla kayitli olamaz"). Bu modul o ilkeyi BOZMADAN
dinamik genisleme saglar: bir manifest dosyasi koymak TEK BASINA yeterli
degildir - dosya kokunun (stem) ayrica `config/security.yaml:
enabled_dynamic_agents` allowlist'inde de bulunmasi gerekir. Iki elle adim,
hicbiri otomatik degil (v2 §3, docs/ROADMAP.md Faz 6.4).

FAIL-SOFT (core/security_config.py'nin fail-loud'undan bilincli sapma,
core/mcp_config.py deseniyle ayni): bozuk/uyumsuz/import edilemeyen bir
manifest yalnizca ATLANIR (uyari logu + print_system), Jarvis yine baslar.
Buradaki "fail-closed" = *yuklenmez/aktive olmaz*, *cokmez* demek.

Metadata kaynagi: manifest `module:class`'i instantiate edilir; asil otorite
Tool ALT SINIFI'dir (statik araclarla ayni self-describing sozlesme). Manifest
`name`/`risk_level` sinifla uyusmazsa manifest fail-closed atlanir - boylece
bir manifest, gercekte HIGH olan bir sinifi "MEDIUM" diye kayda geciremez;
allowlist'i onaylayan insan gercek riski gorur.
"""

import importlib
import logging
from pathlib import Path
from typing import Optional

import yaml

from src.jarvis.core.console import print_system
from src.jarvis.core.guardrail.base import GuardrailChain
from src.jarvis.core.guardrail.input_checks import InputInjectionCheck
from src.jarvis.core.paths import PROJECT_ROOT
from src.jarvis.core.risk import RiskLevel
from src.jarvis.core.security_config import _get_config
from src.jarvis.tools.base import Tool

logger = logging.getLogger("jarvis.core.registry_loader")

REGISTRY_DIR = Path(PROJECT_ROOT) / "agents" / "registry"

# Manifest `execution_mode` alani (v2 §3.2/§5). `on_demand` = klasik router
# tetiklemeli arac; `scheduled`/`continuous` = Faz 6.6 zamanlayici/surekli
# girdi kaynaklari. Bilinmeyen bir deger fail-closed atlanir.
_VALID_EXECUTION_MODES = {"on_demand", "scheduled", "continuous"}

# RiskLevel.value ("medium") -> RiskLevel. Manifest'ler riski "MEDIUM" (buyuk)
# yazabilir; asagida .strip().lower() ile normalize ediyoruz (mcp_config.py
# ayni desen).
_RISK_NAME_TO_LEVEL: dict[str, RiskLevel] = {level.value: level for level in RiskLevel}

_REQUIRED_FIELDS = ("name", "kind", "risk_level", "module", "class")

# security-reviewer emsali (mcp_client_adapter.py:_DESCRIPTION_GUARDRAIL,
# "tool poisoning"): manifest description'i router LLM'in promptuna giriyor -
# kullanici girdisiyle AYNI injection taramasindan gecirilir, takilan manifest
# atlanir.
_DESCRIPTION_GUARDRAIL = GuardrailChain([InputInjectionCheck()])

# Argumansiz cagrinin sonucu (security_config._get_config deseni). Testler
# explicit registry_dir/allowlist gecerek bu cache'i bypass eder.
_dynamic_tools_cache: Optional[dict[str, Tool]] = None


def load_dynamic_tools(
    registry_dir: Optional[Path] = None,
    allowlist: Optional[list[str]] = None,
) -> dict[str, Tool]:
    """Allowlist'te adi gecen `agents/registry/*.yaml` manifest'lerini yukler.

    Allowlist disi ya da `*.example` uzantili manifest'ler SESSIZCE atlanir
    (beklenen "dosyayi koy, allowlist'e ekleyene kadar atil" akisi). Bozuk/
    uyumsuz bir manifest uyari + `print_system` ile atlanir ama uygulama
    calismaya devam eder.

    Argumansiz cagrida sonuc modul-seviyesinde cache'lenir; testler
    `registry_dir` VE `allowlist`'i birlikte vererek taze okuma yapar.
    """
    global _dynamic_tools_cache
    use_cache = registry_dir is None and allowlist is None
    if use_cache and _dynamic_tools_cache is not None:
        return _dynamic_tools_cache

    resolved_dir = REGISTRY_DIR if registry_dir is None else Path(registry_dir)
    if allowlist is None:
        allowlist = _get_config().enabled_dynamic_agents

    result = _discover(resolved_dir, allowlist)

    if use_cache:
        _dynamic_tools_cache = result
    return result


def _discover(registry_dir: Path, allowlist: list[str]) -> dict[str, Tool]:
    if not registry_dir.is_dir():
        logger.debug("Dinamik arac dizini yok, atlaniyor: %s", registry_dir)
        return {}

    enabled = {str(entry).strip() for entry in (allowlist or []) if str(entry).strip()}
    if not enabled:
        logger.debug("enabled_dynamic_agents bos - hicbir dinamik arac yuklenmeyecek.")
        return {}

    result: dict[str, Tool] = {}
    for path in sorted(registry_dir.glob("*.yaml")):
        stem = path.stem
        if stem.endswith(".example") or stem not in enabled:
            logger.debug("Manifest allowlist'te degil, atlandi: %s", path.name)
            continue

        loaded = _load_manifest(path)
        if loaded is None:
            continue

        name, tool = loaded
        if name in result:
            logger.warning(
                "Iki manifest ayni arac adini uretti ('%s') - ikincisi (%s) atlandi.",
                name, path.name,
            )
            continue
        result[name] = tool
        logger.info("Dinamik arac yuklendi: %s (%s)", name, path.name)

    return result


def _skip(path: Path, reason: str) -> None:
    """Bir manifest'i atlarken hem log hem kullaniciya gorunur uyari basar."""
    logger.warning("Manifest atlandi (%s): %s", path.name, reason)
    print_system(f"Dinamik arac manifesti atlandi ({path.name}): {reason}", level="warning")


def _param_descriptions(parameters_schema: dict) -> str:
    """Manifest parametre aciklamalarini injection taramasi icin birlestirir."""
    if not isinstance(parameters_schema, dict):
        return ""
    return " ".join(
        str(prop.get("description", ""))
        for prop in parameters_schema.values()
        if isinstance(prop, dict)
    )


def _load_manifest(path: Path) -> Optional[tuple[str, Tool]]:
    """Tek bir manifest'i dogrular ve `(tool.name, Tool)` dondurur; hata -> None.

    Adimlarin sirasi bilincli: ucuz/statik dogrulamalar (alan/enum/risk kapisi/
    injection) once, pahali `importlib.import_module` en sona - bozuk bir
    manifest icin gereksiz modul yuklemesi yapilmaz.
    """
    try:
        with open(path, "r", encoding="utf-8") as manifest_file:
            raw = yaml.safe_load(manifest_file)
    except (OSError, yaml.YAMLError) as exc:
        _skip(path, f"okunamadi/parse edilemedi: {exc}")
        return None

    if not isinstance(raw, dict):
        _skip(path, "gecerli bir YAML sozlugu degil")
        return None

    missing = [field for field in _REQUIRED_FIELDS if not raw.get(field)]
    if missing:
        _skip(path, f"zorunlu alan(lar) eksik: {', '.join(missing)}")
        return None

    kind = str(raw["kind"]).strip().lower()
    if kind != "tool":
        # kind: agent (bir alt-ajan rolu kaydetmek) ileride; 6.4 sadece tool.
        logger.info("Manifest kind=%r henuz desteklenmiyor, atlandi: %s", kind, path.name)
        return None

    risk_level = _RISK_NAME_TO_LEVEL.get(str(raw["risk_level"]).strip().lower())
    if risk_level is None:
        _skip(path, f"bilinmeyen risk_level: {raw['risk_level']!r}")
        return None

    execution_mode = str(raw.get("execution_mode", "on_demand")).strip().lower()
    if execution_mode not in _VALID_EXECUTION_MODES:
        _skip(path, f"bilinmeyen execution_mode: {execution_mode!r}")
        return None

    # v2 §5.3 risk kapisi (Faz 6.4 -> 6.6 baglantisi): otomatik (scheduled/
    # continuous) tetiklenen bir arac onaysiz calisacagi icin yalnizca LOW
    # riskli olabilir - MEDIUM+ beyan eden boyle bir manifest boot'ta reddedilir.
    if execution_mode in {"scheduled", "continuous"} and risk_level is not RiskLevel.LOW:
        _skip(
            path,
            f"execution_mode={execution_mode} + risk_level={risk_level.value} "
            "(otomatik tetiklenen arac MEDIUM+ risk tasiyamaz)",
        )
        return None

    description = str(raw.get("description", ""))
    scan_text = " ".join(
        [str(raw["name"]), description, _param_descriptions(raw.get("parameters_schema", {}))]
    )
    safety = _DESCRIPTION_GUARDRAIL.run(scan_text)
    if not safety.allowed:
        _skip(path, f"injection taramasina takildi: {safety.reason}")
        return None

    try:
        module = importlib.import_module(str(raw["module"]))
    except ImportError as exc:
        _skip(path, f"module import edilemedi ({raw['module']!r}): {exc}")
        return None

    cls = getattr(module, str(raw["class"]), None)
    if not isinstance(cls, type) or not issubclass(cls, Tool):
        _skip(path, f"{raw['module']}.{raw['class']} bir Tool alt sinifi degil")
        return None

    try:
        tool = cls()
    except Exception as exc:  # noqa: BLE001 - guvenilmeyen 3. parti sinif; fail-closed
        _skip(path, f"instantiate edilemedi: {exc}")
        return None

    # Kimlik/risk capraz-kontrol (fail-closed): manifest, sinifin gercek
    # adini/riskini yanlis beyan edemez.
    if tool.name != str(raw["name"]):
        _skip(path, f"manifest name={raw['name']!r} ama sinif .name={tool.name!r}")
        return None
    if tool.risk_level is not risk_level:
        _skip(
            path,
            f"manifest risk_level={risk_level.value} ama sinif={tool.risk_level.value}",
        )
        return None

    # Advisory (yalnizca uyari): sema surukleniyorsa bakim kokusu, ama guvenlik
    # sinirini asmaz - sinif zaten otorite.
    declared = raw.get("parameters_schema")
    if isinstance(declared, dict) and set(declared) != set(tool.parameters_schema):
        logger.warning(
            "Manifest parameters_schema anahtarlari sinifla uyusmuyor (%s): "
            "manifest=%s, sinif=%s",
            path.name, sorted(declared), sorted(tool.parameters_schema),
        )

    return tool.name, tool
