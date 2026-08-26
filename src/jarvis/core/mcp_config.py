"""MCP sunucu yapilandirmasi - config/mcp_servers.yaml'dan okunur.

Faz 4.5 (bkz. docs/ARCHITECTURE.md SS9): MCP sadece bilgi/veri erisimi
icin kullanilir, hicbir MCP araci OS kontrolu yapamaz - bu dosya SADECE
hangi sunucularin nasil baslatilacagini ve hangi araclarinin (varsa
allowlist) acilacagini tanimlar; calisma zamani mantigi
adapters/mcp_client_adapter.py'de.

FAIL-SOFT (core/security_config.py'nin fail-loud deseninden BILINCLI
SAPMA): MCP, Spotify gibi opsiyonel bir katman (bkz. docs/ROADMAP.md
Faz 3.1 Spotify emsali) - config/mcp_servers.yaml yoksa/bossa/parse
edilemezse uygulama COKMEZ, sadece net bir uyari logu ile MCP devre
disi kalir (bos liste doner). security.yaml core.app'in HER tool
cagrisinin bagli oldugu bir on-kosul; mcp_servers.yaml ise ustune
eklenen, opsiyonel bir gelisim katmani.
"""

import logging
import os
from dataclasses import dataclass, field
from typing import Optional

import yaml

from src.jarvis.core.paths import PROJECT_ROOT
from src.jarvis.core.risk import RiskLevel

logger = logging.getLogger("jarvis.core.mcp_config")

CONFIG_PATH = os.path.join(PROJECT_ROOT, "config", "mcp_servers.yaml")
EXAMPLE_CONFIG_PATH = os.path.join(PROJECT_ROOT, "config", "mcp_servers.example.yaml")

_RISK_NAME_TO_LEVEL: dict[str, RiskLevel] = {level.value: level for level in RiskLevel}


@dataclass
class MCPServerConfig:
    """Tek bir MCP sunucusunun baslatma/erisim yapilandirmasi."""

    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: Optional[dict[str, str]] = None
    default_risk_level: RiskLevel = RiskLevel.MEDIUM
    # None => sunucunun TUM araclari acik (onerilmez, bkz. mcp_servers.example.yaml).
    allowed_tools: Optional[list[str]] = None
    enabled: bool = True


def _parse_server(raw: dict) -> Optional[MCPServerConfig]:
    """Tek bir YAML sunucu girisini dogrular/cevirir; gecersizse None + uyari."""
    name = raw.get("name")
    command = raw.get("command")
    if not name or not command:
        logger.warning("MCP sunucu girisi 'name'/'command' icermiyor, atlandi: %r", raw)
        return None

    risk_name = str(raw.get("default_risk_level", "medium")).strip().lower()
    risk_level = _RISK_NAME_TO_LEVEL.get(risk_name)
    if risk_level is None:
        logger.warning(
            "MCP sunucu '%s' icin bilinmeyen risk seviyesi %r, 'medium' kullanilacak.",
            name, risk_name,
        )
        risk_level = RiskLevel.MEDIUM
    elif risk_level is RiskLevel.LOW:
        # MCP araclari dis sunucudan gelen guvenilmeyen veri tasir - LOW asla
        # otomatik verilmez (bkz. docs/ARCHITECTURE.md SS9.2). Uygulamayi
        # cokertmiyoruz, ama zayiflatmiyoruz da: sessizce MEDIUM'a cekiliyor.
        logger.warning(
            "MCP sunucu '%s' icin 'low' risk seviyesi istendi, 'medium'a "
            "yukseltildi (MCP araclari asla LOW risk alamaz).", name,
        )
        risk_level = RiskLevel.MEDIUM

    allowed_tools = raw.get("allowed_tools")
    if allowed_tools is not None:
        allowed_tools = [str(tool_name) for tool_name in allowed_tools]

    raw_env = raw.get("env") or {}
    env = {str(key): str(value) for key, value in raw_env.items()} or None

    return MCPServerConfig(
        name=str(name),
        command=str(command),
        args=[str(arg) for arg in raw.get("args", [])],
        env=env,
        default_risk_level=risk_level,
        allowed_tools=allowed_tools,
        enabled=bool(raw.get("enabled", True)),
    )


def load_mcp_servers_config(path: str = CONFIG_PATH) -> list[MCPServerConfig]:
    """`config/mcp_servers.yaml`'i okuyup ETKIN sunucu listesine cevirir.

    FAIL-SOFT: dosya yoksa/bossa/parse edilemezse bos liste doner + net bir
    uyari loglanir (bkz. modul docstring'i) - `security_config.py`nin
    `FileNotFoundError`'indan bilincli sapma.
    """
    if not os.path.isfile(path):
        logger.warning(
            "MCP devre disi: '%s' bulunamadi. Etkinlestirmek icin '%s' "
            "dosyasini '%s' olarak kopyalayip duzenleyin.",
            path, EXAMPLE_CONFIG_PATH, path,
        )
        return []

    try:
        with open(path, "r", encoding="utf-8") as config_file:
            raw = yaml.safe_load(config_file) or {}
    except yaml.YAMLError as exc:
        logger.error("MCP devre disi: '%s' parse edilemedi: %s", path, exc)
        return []

    raw_servers = raw.get("servers") or []
    if not raw_servers:
        logger.warning("MCP devre disi: '%s' icinde tanimli sunucu yok.", path)
        return []

    servers = [
        parsed
        for parsed in (_parse_server(raw_server) for raw_server in raw_servers)
        if parsed is not None and parsed.enabled
    ]

    if not servers:
        logger.warning("MCP devre disi: '%s' icindeki hicbir sunucu etkin degil.", path)

    return servers
