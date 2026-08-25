"""Intent -> handler eslemesi (bkz. docs/ROADMAP.md Faz 2.1 "Modul yonlendirme arayuzu").

Dispatcher SADECE siniflandirir (core.dispatcher); bir intent'in gercekte ne yapacagini
burada, ayri bir modulde tutuyoruz (tek sorumluluk) - core.app.py sadece HANDLERS.get(name)
ile bakar, hangi intent'in nasil calistigini bilmesi gerekmez.

Sadece dosya/sistem erisimi GEREKTIRMEYEN intent'ler burada gercek bir handler'a sahip.
`list_files` gibi gercek bir kaynagi (dosya sistemi) okuyan intent'ler BILINCLI olarak
handler'siz birakildi - ROADMAP'in kendi tanimina gore bunlar Faz 3.1'in erisim-kontrollu
tool'udur; handler'i olmayan bir intent core.app'te otomatik olarak normal sohbete duser.
"""

from datetime import datetime
from typing import Callable

from src.jarvis.core.dispatcher import Intent


def _handle_get_time(intent: Intent) -> str:
    # Handler Brain'i (dolayisiyla dil-esleme kuralini) hic devreye sokmuyor, bu yuzden
    # kullanicinin TR mi EN mi sordugunu bilemiyoruz - brain/llm.py'nin hata mesajlariyla
    # ayni desen: iki dilde birden don.
    now = datetime.now().strftime("%H:%M")
    return f"Şu an saat {now}. It's {now} now."


HANDLERS: dict[str, Callable[[Intent], str]] = {
    "get_time": _handle_get_time,
}
