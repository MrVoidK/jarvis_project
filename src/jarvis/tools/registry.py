"""TOOL_REGISTRY - intent adi -> Tool eslemesi.

core/handlers.py'deki HANDLERS deseniyle ayni: her tool ACIKCA import edilip statik
bir dict'e konuyor. Otomatik kesif/dinamik import bilincli olarak kullanilmiyor -
hangi araclarin sisteme kayitli oldugunun tek bakista, dosya okunarak gorulebilmesi
bir guvenlik ozelligi (bir arac "yanlislikla" kayitli olamaz).

Anahtarlar core/dispatcher.py'deki _RULES intent adlariyla birebir eslesmeli.
"""

from src.jarvis.tools.base import Tool
from src.jarvis.tools.files import ListFilesTool
from src.jarvis.tools.media_tool import (
    MediaNextTrackTool,
    MediaPlayPauseTool,
    MediaPreviousTrackTool,
    MediaVolumeDownTool,
    MediaVolumeUpTool,
)
from src.jarvis.tools.notes import CreateNoteTool, ReadNotesTool
from src.jarvis.tools.shell import RunCommandTool
from src.jarvis.tools.spotify import PauseMusicTool, PlayMusicTool, SkipTrackTool
from src.jarvis.tools.system_info import SystemInfoTool

TOOL_REGISTRY: dict[str, Tool] = {
    tool.name: tool
    for tool in (
        CreateNoteTool(),
        ReadNotesTool(),
        ListFilesTool(),
        RunCommandTool(),
        SystemInfoTool(),
        PlayMusicTool(),
        PauseMusicTool(),
        SkipTrackTool(),
        MediaPlayPauseTool(),
        MediaNextTrackTool(),
        MediaPreviousTrackTool(),
        MediaVolumeUpTool(),
        MediaVolumeDownTool(),
    )
}
