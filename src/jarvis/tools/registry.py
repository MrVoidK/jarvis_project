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
"""

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
