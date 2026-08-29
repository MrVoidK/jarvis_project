"""TOOL_REGISTRY - intent adi -> Tool eslemesi.

core/handlers.py'deki HANDLERS deseniyle ayni: her tool ACIKCA import edilip statik
bir dict'e konuyor. Otomatik kesif/dinamik import bilincli olarak kullanilmiyor -
hangi araclarin sisteme kayitli oldugunun tek bakista, dosya okunarak gorulebilmesi
bir guvenlik ozelligi (bir arac "yanlislikla" kayitli olamaz).

Anahtarlar, core/dispatcher.py:Dispatcher.classify()'in semantic router yolunun
(Ollama native tool-calling) modele bildirdigi arac adlariyla ayni olmali -
`adapters/tool_schema.py:build_ollama_tools()` burasi TOOL_REGISTRY'yi dogrudan
tarayarak semayi uretir. `_RULES` (core/dispatcher.py) artik SADECE `get_time`
icin fast-path tanimliyor; geri kalan TUM araclar buradaki isimleriyle semantic
router'a aciliyor.

MCP (Faz 4.5, bkz. docs/ARCHITECTURE.md SS9.2): TOOL_REGISTRY BILINCLI
OLARAK degismiyor - MCP sunucularindan kesfedilen araclar buraya asla
sessizce enjekte edilmez (yukaridaki "bir arac yanlislikla kayitli olamaz"
ilkesi MCP'nin DINAMIK dogasiyla uyusmaz). Bunun yerine `all_tools()`/
`get_tool()` altta, TOOL_REGISTRY'yi degistirmeden, cagiran koda (dispatcher/
app) hem yerel hem MCP araclarini gosteren bir BIRLESTIRME (view) katmani
sunuyor.

Faz 6.4: `all_tools()` artik UC kaynagi birlestirir - statik `TOOL_REGISTRY` +
`core/registry_loader.py:load_dynamic_tools()` (allowlist'li agents/registry/
*.yaml manifest'leri) + MCP kesfi. Ad cakismasinda statik HER ZAMAN kazanir;
oncelik: statik > dinamik manifest > MCP. Hicbir kaynak digerine sessizce
enjekte olmaz (ayni "statik onay" felsefesi, bkz. docs/ARCHITECTURE.md SS12).
"""

import logging
from typing import Optional

from src.jarvis.adapters.mcp_client_adapter import get_default_adapter
from src.jarvis.core.registry_loader import load_dynamic_tools
from src.jarvis.tools.base import Tool
from src.jarvis.tools.files import ListFilesTool
from src.jarvis.tools.media_tool import (
    MediaNextTrackTool,
    MediaPlayPauseTool,
    MediaPreviousTrackTool,
    MediaVolumeDownTool,
    MediaVolumeUpTool,
    SearchMusicTool,
    SetVolumeTool,
)
from src.jarvis.tools.notes_tool import (
    CreateNoteTool,
    ListNotesTool,
    MergeNotesTool,
    OpenNoteTool,
    ReadNotesTool,
)
from src.jarvis.tools.project_tool import CreateProjectTool
from src.jarvis.tools.system_info import SystemInfoTool
from src.jarvis.tools.terminal_tool import LaunchAppTool, RunCommandTool

logger = logging.getLogger("jarvis.tools.registry")

TOOL_REGISTRY: dict[str, Tool] = {
    tool.name: tool
    for tool in (
        CreateNoteTool(),
        ReadNotesTool(),
        ListNotesTool(),
        OpenNoteTool(),
        MergeNotesTool(),
        ListFilesTool(),
        RunCommandTool(),
        LaunchAppTool(),
        CreateProjectTool(),
        SystemInfoTool(),
        MediaPlayPauseTool(),
        MediaNextTrackTool(),
        MediaPreviousTrackTool(),
        MediaVolumeUpTool(),
        MediaVolumeDownTool(),
        SetVolumeTool(),
        SearchMusicTool(),
    )
}


def all_tools() -> dict[str, Tool]:
    """Statik `TOOL_REGISTRY` + dinamik manifest + MCP araclarinin BIRLESIK view'i.

    `TOOL_REGISTRY`'nin KENDISI degismez (bkz. modul docstring'i) - bu
    fonksiyon SADECE cagiran kodun (core/dispatcher.py, core/app.py) tek bir
    yerden butun araclari gorebilmesi icin bir birlestirme katmani. Hem
    `load_dynamic_tools()` hem MCP kesif sonucunu kendi icinde cache'ledigi
    icin her turda diskten/sunuculardan yeniden okunmaz.

    Oncelik: statik > dinamik manifest > MCP. Bir dinamik/MCP araci statik bir
    arac adiyla cakisirsa dusurulur (statik kazanir) + uyari loglanir.
    """
    dynamic = load_dynamic_tools()
    mcp = get_default_adapter().discover_tools()
    merged: dict[str, Tool] = {}
    for label, source in (("MCP", mcp), ("dinamik manifest", dynamic)):
        for key, tool in source.items():
            if key in TOOL_REGISTRY:
                logger.warning(
                    "%s araci '%s' statik TOOL_REGISTRY adiyla cakisiyor - "
                    "yoksayildi (statik kazanir).", label, key,
                )
                continue
            merged[key] = tool
    merged.update(TOOL_REGISTRY)
    return merged


def get_tool(name: str) -> Optional[Tool]:
    """Once statik `TOOL_REGISTRY`, sonra dinamik manifest, en son MCP araclari."""
    tool = TOOL_REGISTRY.get(name)
    if tool is not None:
        return tool
    tool = load_dynamic_tools().get(name)
    if tool is not None:
        return tool
    return get_default_adapter().discover_tools().get(name)
