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
"""

from typing import Optional

from src.jarvis.adapters.mcp_client_adapter import get_default_adapter
from src.jarvis.tools.base import Tool
from src.jarvis.tools.files import ListFilesTool
from src.jarvis.tools.media_tool import (
    MediaNextTrackTool,
    MediaPlayPauseTool,
    MediaPreviousTrackTool,
    MediaVolumeDownTool,
    MediaVolumeUpTool,
    SearchMusicTool,
)
from src.jarvis.tools.notes_tool import CreateNoteTool, ReadNotesTool
from src.jarvis.tools.system_info import SystemInfoTool
from src.jarvis.tools.terminal_tool import LaunchAppTool, RunCommandTool

TOOL_REGISTRY: dict[str, Tool] = {
    tool.name: tool
    for tool in (
        CreateNoteTool(),
        ReadNotesTool(),
        ListFilesTool(),
        RunCommandTool(),
        LaunchAppTool(),
        SystemInfoTool(),
        MediaPlayPauseTool(),
        MediaNextTrackTool(),
        MediaPreviousTrackTool(),
        MediaVolumeUpTool(),
        MediaVolumeDownTool(),
        SearchMusicTool(),
    )
}


def all_tools() -> dict[str, Tool]:
    """`TOOL_REGISTRY` (statik, yerel) + MCP-kesfedilen araclarin BIRLESIK view'i.

    `TOOL_REGISTRY`'nin KENDISI degismez (bkz. modul docstring'i) - bu
    fonksiyon SADECE cagiran kodun (core/dispatcher.py, core/app.py) tek bir
    yerden hem yerel hem MCP araclarini gorebilmesi icin bir birlestirme
    katmani. MCP tarafi kesif sonucunu kendi icinde cache'ledigi icin
    (bkz. adapters/mcp_client_adapter.py:discover_tools()) her turda
    yeniden sunuculara baglanilmaz.
    """
    return {**TOOL_REGISTRY, **get_default_adapter().discover_tools()}


def get_tool(name: str) -> Optional[Tool]:
    """Once yerel `TOOL_REGISTRY`'ye, sonra MCP-kesfedilen araclara bakar."""
    tool = TOOL_REGISTRY.get(name)
    if tool is not None:
        return tool
    return get_default_adapter().discover_tools().get(name)
