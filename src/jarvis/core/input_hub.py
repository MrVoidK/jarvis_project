"""Hibrit girdi merkezi - mikrofon (Ears) ve terminal metnini TEK bir sıralı
kuyruğa toplayan üretici/tüketici (producer/consumer) altyapısı.

Neden bu tasarım (bkz. core/app.py:run_jarvis() yeniden yazımı):
`ears/listener.py:listen_loop()` kendi çalışan thread'i olmayan, çağıran
thread'i `stream.read()` ile bloke eden düz bir generator. Terminalden
`input()` almayı da mikrofonu BEKLETMEDEN istediğimiz için mikrofon
dinlemeyi ARKA PLAN thread'ine taşıdık. Metin girişi de KENDİ arka plan
thread'inde okunuyor (aşağıda "STDIN SAHİPLİĞİ" başlığında açıklanıyor) -
böylece ana thread SADECE kuyruktan okuma (`queue.Queue.get()`, thread-safe,
stdin'e hiç dokunmuyor) yapar ve tüm tur işleme (guardrail/dispatcher/tool/
onay) TEK bir thread'de, sırayla kalır.

STDIN SAHİPLİĞİ (kritik tasarım kararı): `core/risk.py:request_approval()`
normalde KENDİ blocking `console.input()` çağrısını yapar. Hibrit modda
BUNU YAPMIYORUZ - eğer hem "onay bekleyen ana thread" hem "sürekli input()
döngüsündeki metin thread'i" AYNI ANDA stdin okumaya çalışırsa, hangi
thread'in hangi satırı alacağı TANIMSIZ/yarışa açık olurdu (iki ayrı
`input()` çağrısı aynı stdin arabelleğini güvenle paylaşamaz). Çözüm:
stdin'i HER ZAMAN SADECE metin-girdi thread'i okur; onay bekleyen ana
thread kendi `input()` çağırmaz, bunun yerine PAYLAŞILAN KUYRUKTAN bir
sonraki "text" olayını onay cevabı olarak BEKLER (bkz.
`wait_for_text_answer()`, `core/app.py:_execute_tool()`'un hibrit-onay
yolu, `core/risk.py:evaluate_approval_answer()`). Böylece stdin'e hiçbir
zaman birden fazla thread aynı anda erişemez.

Sesli onay BİLİNÇLİ OLARAK devre dışı (mevcut `core/risk.py` ilkesiyle
aynı gerekçe - STT'nin yanlış-algılama payı güvenlik-kritik bir yolda kabul
edilemez): onay beklenirken gelen "voice" kuyruk öğeleri cevap olarak
KULLANILMAZ, `pending`'e eklenip onay sonuçlandıktan SONRA normal bir tur
olarak işlenir - kullanıcının o sırada söylediği söz kaybolmaz.

GÜVENLİK DÜZELTMELERİ (security-reviewer bulguları, hibrit girdi turu):
(1) `wait_for_text_answer()` eskiden kuyruktaki bir sonraki `"text"` öğesini
KOŞULSUZ cevap sayıyordu - onay paneli gösterilmeden ÖNCE (örn. Brain'in
router çağrısı/TTS "onayınızı bekliyorum" anonsu sürerken) kullanıcının
yazmış olabileceği TAMAMEN ALAKASIZ bir metin, kullanıcı onay panelini hiç
görmeden "cevap" olarak tüketilebilirdi (human-in-the-loop'un asıl amacını
boşa çıkarır). Artık `wait_for_text_answer()` çağrıldığı anda kuyrukta
ZATEN bekleyen her şeyi önce `pending`'e boşaltıyor - SADECE bundan SONRA
gelen bir `"text"` öğesi gerçek bir cevap sayılıyor.
(2) stdin kapanırsa (`_text_producer` `EOFError` alıp sessizce sonlanırsa)
onay bekleme eskiden SONSUZA kadar askıda kalırdı - bu hem tam bir DoS hem
de `core/risk.py`'nin "kapalı stdin = ret" ilkesinin (bkz. `request_approval()`
docstring'i) ihlaliydi. Artık `wait_for_text_answer()` her `poll_interval`'da
metin thread'inin hâlâ canlı olup olmadığını da kontrol ediyor; thread
ölmüşse boş string (`evaluate_approval_answer("")` → `False`, yani RET)
döner.
"""

import logging
import queue
import threading
from dataclasses import dataclass
from typing import Literal, Optional

from src.jarvis.core import hud_bus
from src.jarvis.core.console import console

logger = logging.getLogger("jarvis.core.input_hub")

InputSource = Literal["voice", "text", "scheduled", "continuous"]

# Ters-renkli (arka plan vurgulu) stil BILINCLI: dongu boyunca konsola akan
# diger duz metinlerden (Jarvis cevaplari, loglar, guardrail/router panelleri)
# istemin gozle net ayrisabilmesi icin - sadece renk/kalinlik (eski hali) uzun
# ciktilar arasinda kaybolabiliyordu. Sadece ASCII (core/console.py'nin
# belgeledigi Windows kod sayfasi kisiti, bkz. o dosyanin UnicodeEncodeError notu).
TEXT_PROMPT = "\n[bold black on yellow] SEN [/bold black on yellow][bold yellow] >>> [/bold yellow]"


@dataclass
class InputEvent:
    """Kaynağı ne olursa olsun (ses/metin), pipeline'a aynı standart formatta
    giren tek bir kullanıcı turu."""

    source: InputSource
    text: str


def _mic_producer(
    input_queue: "queue.Queue[InputEvent]",
    stop_event: threading.Event,
    speaking_event: threading.Event,
) -> None:
    """`listen_loop()`u OLDUĞU GİBİ tüketir - davranışı hiç değişmedi, sadece
    çağrıldığı thread değişti (ana thread yerine arka plan).

    `ears.listener` importu BİLİNÇLİ OLARAK burada (fonksiyon içinde,
    gecikmeli) - o modülün üst seviyesi gerçek faster-whisper/openWakeWord
    modellerini YÜKLER (bkz. listener.py). Modül üstünde olsaydı, sadece
    `InputHub`'ın kuyruk/pending mantığını (mic/stdin'e hiç dokunmadan) test
    etmek isteyen `tests/test_input_hub.py` bile bu ağır yüklemeyi
    tetiklerdi - mevcut test paketinin hızlı/hermetik kalma ilkesiyle
    (bkz. Faz 4.5 MCP entegrasyonunda aynı gerekçeyle yapılan düzeltme)
    tutarlı bir gecikmeli import.

    `speaking_event`, `mouth/tts.py:speak()`'in set/clear ettiği AYNI event -
    `listen_loop()`'a `mute_event=` olarak aynen iletiliyor (bkz. o fonksiyonun
    docstring'i) ki Jarvis kendi TTS çıktısını mikrofonundan duyup yeni bir
    kullanıcı turu sanmasın (bkz. bu event'in eklenme gerekçesi için
    `core/app.py:run_jarvis()` docstring'i).

    `listen_loop()`'un `on_state_change=hud_bus.publish_state` ile doğrudan
    bağlanması (JARVIS HUD - web-ui entegrasyonu): `hud_bus.publish_state`
    zaten `Callable[[str], None]` imzasıyla eşleşiyor, ayrı bir sarmalayıcı
    closure'a gerek yok.
    """
    from src.jarvis.ears.listener import listen_loop

    for transcript in listen_loop(
        stop_event=stop_event, mute_event=speaking_event, on_state_change=hud_bus.publish_state
    ):
        input_queue.put(InputEvent(source="voice", text=transcript))


def _text_producer(input_queue: "queue.Queue[InputEvent]", stop_event: threading.Event) -> None:
    """stdin'in TEK sahibi - bkz. modül docstring'i. `EOFError` (stdin kapalı/
    yönlendirilmiş, örn. arka planda çalışan bir süreç) sessizce thread'i
    bitirir, `stop_event` görüldüğünde de aynı şekilde çıkılır."""
    while not stop_event.is_set():
        try:
            line = console.input(TEXT_PROMPT)
        except EOFError:
            logger.info("Metin girdisi kapandı (stdin yok) - metin thread'i sonlanıyor.")
            return
        if line.strip():
            input_queue.put(InputEvent(source="text", text=line))


class InputHub:
    """Mic + metin üretici thread'lerini başlatır, tüketiciye (ana thread)
    TEK bir sıralı kuyruk sunar."""

    def __init__(self, stop_event: threading.Event, speaking_event: Optional[threading.Event] = None) -> None:
        # Sinirsiz queue.Queue() BILINCLI OLARAK (security-reviewer bulgusu,
        # degerlendirilip ERTELENDI): bir maxsize, kuyruk dolduysa
        # producer thread'lerin (mic/metin) `put()`te BLOKE olmasi demek -
        # tek-kullanicili/yerel bir arac icin bu, sinirsiz bellek buyumesinden
        # (dusuk olasilik: patolojik girdi hacmi) muhtemelen DAHA KOTU bir
        # basarisizlik modu (bir producer'in sessizce donmasi). Bilincli
        # bir tercih olarak kaydedildi, kod degisikligi yok.
        self._queue: "queue.Queue[InputEvent]" = queue.Queue()
        self._stop_event = stop_event
        # `speaking_event=None` (varsayilan) geriye donuk uyumluluk icin -
        # verilmezse mikrofon HIC susturulmaz (eski davranis, ör. testler).
        self._speaking_event = speaking_event if speaking_event is not None else threading.Event()
        self._mic_thread = threading.Thread(
            target=_mic_producer,
            args=(self._queue, stop_event, self._speaking_event),
            name="jarvis-mic",
            daemon=True,
        )
        self._text_thread = threading.Thread(
            target=_text_producer, args=(self._queue, stop_event), name="jarvis-text-input", daemon=True
        )

    def start(self) -> None:
        self._mic_thread.start()
        self._text_thread.start()

    def submit_event(self, event: InputEvent) -> None:
        """Herhangi bir uretici thread'inden (Faz 6.6 scheduler / continuous
        runner, ya da HUD WebSocket thread'i) dogrudan cagrilabilir - `self._queue`
        bir `queue.Queue` (stdlib kendi kilidiyle korur). Bos/whitespace metinli
        olaylar sessizce atilir (gurultu kaynakli tetiklemeler kuyruga girmesin).
        """
        if event.text.strip():
            self._queue.put(event)

    def submit_external_text(self, text: str) -> None:
        """JARVIS HUD (web-ui) WebSocket thread'inden (`core/api.py`) gelen
        yazili bir komutu ana kuyruga ekler - `_text_producer`'in yaptigi AYNI
        sey. Web'den yazilan metin, terminalden yazilanla AYNI
        `InputEvent(source="text", ...)` olarak girer; `run_jarvis()` `/slash`
        komutlarini ve dogal dili aralarinda fark gozetmeden isler, onay bekleme
        sirasinda gelen bir "evet"/"y" de ayni yoldan akar (bkz.
        wait_for_text_answer()).
        """
        self.submit_event(InputEvent(source="text", text=text))

    def discard_pending_voice(self) -> int:
        """Bir tur bittikten SONRA kuyrukta biriken `source == "voice"` olaylarini
        atar; `"text"`/`"scheduled"`/`"continuous"` olaylari sirasi bozulmadan
        korunur. Atilan sayiyi doner.

        NEDEN (2026-08-29 optimizasyon turu, Cluster A3): Jarvis konusurken/hemen
        sonrasindaki cooldown penceresinde mikrofonun yakaladigi ses neredeyse
        kesin hoparlorden sizan kendi TTS'idir (projede akustik yanki bastirma
        yok). `core/app.py:run_jarvis()` her turun SONUNDA (mute kaldirildiktan
        sonra) bunu cagirir - boylece bir yanit sirasinda birikmis yanki turlari
        kuyrukta kalip pes pese islenmez (canli testte gorulen "cevaplar 20-30 sn
        gecikmeli" + "Jarvis kendi cumlesine cevap veriyor" kok nedeni).

        `self._queue` bir `queue.Queue` (stdlib kendi kilidiyle korur); bu metod
        tuketici (ana) thread'den cagrilir, uretici thread'ler `put()` ile
        eszamanli calisabilir - bu yuzden "bosalt, filtrele, geri koy" arasinda
        ARAYA yeni bir uretici olayi girebilir (kabul edilen: o olay bir sonraki
        `discard`/`next_event`'te ele alinir, kaybolmaz)."""
        kept: list[InputEvent] = []
        discarded = 0
        while True:
            try:
                event = self._queue.get_nowait()
            except queue.Empty:
                break
            if event.source == "voice":
                discarded += 1
            else:
                kept.append(event)
        for event in kept:
            self._queue.put(event)
        if discarded:
            logger.info("Tur sonrasi %d yanki/feedback ses olayi kuyruktan atildi.", discarded)
        return discarded

    def next_event(self, poll_interval: float = 0.5) -> InputEvent:
        """Bir sonraki girdi olayını bekler - kaynağı fark etmeksizin.

        SÜRESİZ (timeout'suz) `queue.Queue.get()` BİLİNÇLİ OLARAK KULLANILMIYOR:
        bu, iyi bilinen bir CPython tuzağı - içeride kullanılan
        `threading.Condition.wait()` süresiz modda, sinyal (Ctrl+C/
        KeyboardInterrupt) kontrolü için ana döngüye asla geri dönmeyebilir,
        yani Ctrl+C ANINDA değil (ya da hiç) işlenmeyebilir. Kısa `timeout`'lu
        bir anket döngüsü (her `poll_interval` saniyede bir `queue.Empty`
        yakalayıp tekrar denemek) bunun standart/güvenilir çözümü - ana
        thread en geç `poll_interval` saniyede bir Python'un sinyal kontrol
        noktasına döner.
        """
        while True:
            try:
                return self._queue.get(timeout=poll_interval)
            except queue.Empty:
                continue

    def wait_for_text_answer(self, pending: list[InputEvent], poll_interval: float = 0.5) -> str:
        """Onay bekleme yolu - SADECE bu çağrıdan SONRA gelen `"text"`
        kaynaklı bir olayı cevap sayar. Arada gelen `"voice"` olayları
        KAYBOLMAZ - `pending`'e eklenir, çağıran taraf
        (`core/app.py:run_jarvis()`) bunları onay sonuçlandıktan SONRA
        normal bir tur olarak işler.

        Boş string dönmesi (stdin kapandığı için) `evaluate_approval_answer("")`
        tarafından `False` (RET) olarak yorumlanır - bkz. modül docstring'i
        "(2)".
        """
        # (1) Bu cagridan ONCE kuyrukta biriken HER SEYI (onay paneli
        # gosterilmeden once yazilmis olabilecek alakasiz metin dahil)
        # once pending'e aktar - SADECE bundan sonraki bir "text" olayi
        # gercek bir cevap sayilir (bkz. modul docstring'i "(1)").
        while True:
            try:
                pending.append(self._queue.get_nowait())
            except queue.Empty:
                break

        while True:
            try:
                event = self._queue.get(timeout=poll_interval)
            except queue.Empty:
                # (2) Metin thread'i (stdin'in TEK sahibi) olmusse bir daha
                # asla yeni bir "text" olayi gelmeyecek - sonsuza kadar
                # beklemek yerine varsayilan RED'e dusuyoruz.
                if not self._text_thread.is_alive():
                    logger.warning(
                        "Onay bekleniyordu ama metin girdisi kapanmış (stdin yok) - varsayılan RED."
                    )
                    return ""
                continue
            if event.source == "text":
                return event.text
            pending.append(event)
