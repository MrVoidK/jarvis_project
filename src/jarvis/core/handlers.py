"""Intent -> handler eslemesi (bkz. docs/ROADMAP.md Faz 2.1 "Modul yonlendirme arayuzu").

Dispatcher SADECE siniflandirir (core.dispatcher); bir intent'in gercekte ne yapacagini
burada, ayri bir modulde tutuyoruz (tek sorumluluk) - core.app.py sadece HANDLERS.get(name)
ile bakar, hangi intent'in nasil calistigini bilmesi gerekmez.

Sadece dosya/sistem erisimi GEREKTIRMEYEN intent'ler burada gercek bir handler'a sahip.
`list_files` gibi gercek bir kaynagi (dosya sistemi) okuyan intent'ler BILINCLI olarak
handler'siz birakildi - ROADMAP'in kendi tanimina gore bunlar Faz 3.1'in erisim-kontrollu
tool'udur; handler'i olmayan bir intent core.app'te otomatik olarak normal sohbete duser.

Her handler `(text, lang)` cifti donduruyor - tek bir dilde metin + o metnin gercekten
hangi dilde oldugu (dispatcher'in tespit ettigi Intent.parameters["lang"]'dan). core.app
bu `lang`'i dogrudan speak(language=...)'e geciriyor; boylece iki dili tek cumlede
birlestirip TEK bir XTTS lang bayragiyla okutmaya calisan eski desen (metnin yarisi hep
yanlis fonetikle okunuyordu) ortadan kalkiyor.
"""

from datetime import datetime
from typing import Callable

from src.jarvis.core.dispatcher import Intent

_TIME_TEMPLATES = {
    "tr": "Şu an saat {now}.",
    "en": "It's {now} now.",
}


def _handle_get_time(intent: Intent) -> tuple[str, str]:
    now = datetime.now().strftime("%H:%M")
    lang = intent.parameters.get("lang", "en")
    template = _TIME_TEMPLATES.get(lang, _TIME_TEMPLATES["en"])
    return template.format(now=now), lang if lang in _TIME_TEMPLATES else "en"


HANDLERS: dict[str, Callable[[Intent], tuple[str, str]]] = {
    "get_time": _handle_get_time,
}
