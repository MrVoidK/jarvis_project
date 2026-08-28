# JARVIS — Gelişmiş Mimari v2: Multi-Agent + Hafıza + Execution Modes

> Bu doküman `docs/mimari-genel-bakis.md`'de tarif edilen **çalışan mevcut
> sisteme** eklenecek/değiştirilecek kısımları tarif eder. Hedef: mevcut
> §20 "Bilinen Sınırlamalar & İyileştirme Adayları" listesindeki maddeleri
> somut kod değişikliklerine dönüştürmek + önceki planlama sohbetlerinde
> konuşulan multi-agent/hafıza/execution-mode genişlemesini bunlarla
> **aynı** güvenlik felsefesiyle (Zero-Trust, fail-closed, tek merkezi
> güvenlik hattı, fail-soft dış bağlantılar) entegre etmek.
>
> Bu dosya **mevcut `docs/ARCHITECTURE.md`'nin yerine geçmez** — onunla
> çelişen bir yer bulunursa `ARCHITECTURE.md` bu dosyayla senkronize
> edilmeli. Faz numaraları mevcut `docs/ROADMAP.md`'deki Faz 1–6 ile
> **çakışmasın diye kasıtlı olarak harflendirilmiştir** (Faz A, B, C...) —
> ROADMAP.md'ye entegre edilirken oradaki numaralandırmaya taşınmalı.
>
> Referans verilen tüm dosya yolları, sınıf adları ve fonksiyon imzaları
> `mimari-genel-bakis.md` ile birebir tutarlıdır; yeni olanlar açıkça
> "YENİ" olarak işaretlenmiştir.

---

## 0. Üst Düzey Karar Özeti

| Karar | Neden |
|---|---|
| `orchestrator` ve `tool_agent` rolleri **aynı modeli** paylaşır (Hermes-3-Llama-3.1-8B) | §20 madde 12 (VRAM bütçesi) — iki ayrı 8B model aynı anda sığmıyor; tek model + rol bazlı sistem prompt ile çözülür |
| Router şemasına `delegate_complex_task` / `delegate_code_task` sentinel'leri eklenir | Mevcut `no_tool_needed` sentinel deseni zaten kanıtlanmış; multi-agent yönlendirmesi aynı mekanizmayı tekrar kullanır, yeni bir sınıflandırma katmanı icat edilmez |
| `Agent` ABC'ye `respond_stream()` eklenir | §20 madde 2 — Brain'in Factory/Adapter soyutlamasına girmesi |
| Hafıza (Mem0) **guardrail'den geçmeden asla yazılmaz/okunmaz** | Kalıcı hafızaya sızan bir prompt injection tek turluk değil, her gelecek turda tekrar enjekte olur — mevcut sistemden daha yüksek risk |
| Scheduled/continuous ajanlar **yalnızca LOW risk** araç tetikleyebilir (otomatik) | Kullanıcı onay veremeyecek durumdayken (gece, uzakta) MEDIUM+ eylem varsayılan RED ilkesiyle çelişir |
| IoT kontrolü MCP üzerinden **değil**, yerel `tools/iot_tool.py` üzerinden | Mevcut ilke: "OS kontrolü asla MCP üzerinden geçmez" — fiziksel dünya kontrolü aynı kategoriye girer, sadece durum *okuma* MCP'de kalabilir |
| Agent Registry **otomatik keşif değil, allowlist tabanlı** | Mevcut "bir araç yanlışlıkla kayıtlı olamaz" güvenlik ilkesi korunur — manifest dosyası koymak tek başına aktive etmez |

---

## 1. Yeni Üst Düzey Mimari

```
                          ┌───────────────────────────────┐
                          │   HAFIZA (Mem0) — yatay katman │
                          │   core/memory.py · fail-soft    │
                          └───────────────┬─────────────────┘
                                          │ recall() / remember()
   ┌─────────┐  ses/metin   ┌──────────────┐   intent    ┌────────────────────┐
   │  EARS   │ ───────────▶ │  GUARDRAIL   │ ──────────▶ │     DISPATCHER      │
   │ + metin │              │   (girdi)    │             │  (3 kademeli router) │
   └─────────┘              └──────────────┘             └──────────┬──────────┘
   wake-word/VAD                                                     │
   faster-whisper    ┌───────────────┬───────────────┬────────────────┼───────────────────┐
                      ▼               ▼               ▼                ▼                   ▼
              HANDLERS          TOOL_REGISTRY    BRAIN (chat)     HERMES ROLE         CLAUDE CODE ROLE
              (get_time,        + AGENT          orchestrator     tool_agent          deep_reasoning
              LLM'siz)          REGISTRY         (Hermes-3-       (AYNI model,        (Anthropic API,
                                (statik +         Llama-3.1-8B)   farklı sistem       proje/mimari/
                                YENİ manifest                     prompt, çok-adım)   ağır kodlama)
                                allowlist'i)
                      │               │               │                │                   │
                      └───────────────┴───────────────┴────────────────┴───────────────────┘
                                                       ▼
                                              GUARDRAIL (çıktı)
                                                       ▼
                                             ┌──────────────┐
                                             │  MOUTH (TTS) │
                                             │  + JARVIS HUD│
                                             └──────────────┘

  Yatay ek girdi kaynakları (aynı InputHub kuyruğuna, aynı güvenlik hattından geçer):
  ┌────────────────────┐        ┌─────────────────────────┐
  │  core/scheduler.py  │        │ core/continuous_runner.py│
  │  (YENİ, cron)        │        │  (YENİ, arka plan izleme) │
  │  source="scheduled"  │        │  source="continuous"      │
  └────────────────────┘        └─────────────────────────┘
```

**Kritik ilke:** Scheduler ve continuous runner **yeni bir güvenlik yolu
açmaz** — ürettikleri `InputEvent` aynı `InputHub` kuyruğuna girer, aynı
`_handle_turn()` → guardrail → dispatcher → `_run_tool_pipeline()`
zincirinden geçer. Tek fark `source` alanı ve §6.3'teki risk kısıtlaması.

---

## 2. Agent Katmanı — Rol Konsolidasyonu ve Aktivasyon

### 2.1 Mevcut durumun diagramla eşleşmesi

`adapters/agent_factory.py:AgentFactory` tablosundaki üç rol, önceki
planlama sohbetlerindeki üç yollu ayrımın (basit komut / karmaşık görev /
ağır kodlama) doğrudan kod karşılığıdır:

| Rol (mevcut kod) | Karşılık geldiği görev | Şu anki durum |
|---|---|---|
| `orchestrator` (`LlamaOrchestratorAdapter`, `llama3.1:8b`) | Basit/hızlı komut sınıflandırma | Aktif |
| `tool_agent` (`HermesAgentAdapter`, `hermes3:8b`) | Karmaşık/çok adımlı görev | `call_tools()` → `NotImplementedError` |
| `deep_reasoning` (`ClaudeCodeAdapter`) | Mimari/ağır kodlama | Tamamı stub |

Bu bölümün amacı: bu üçünü **VRAM bütçesini aşmadan** ve **mevcut router
mekanizmasını yeniden icat etmeden** çalışır hale getirmek.

### 2.2 Model kararı — VRAM bütçesi çözümü (§20 madde 12)

Mevcut bütçe: Whisper turbo (~1.5–2 GB) + llama3.1:8b (~5 GB) + XTTS-v2
(~2–3 GB) ≈ 8.5–10 GB / 12 GB. Ayrı bir `hermes3:8b`'yi **aynı anda**
yüklemek sınırı zorlar.

#### 2.2.1 İki seçenek ve neden A seçildi

**Seçenek B (reddedildi) — iki ayrı model, gerçekten ayrı görevler:**
`llama3.1:8b` hızlı/sık işler için (router + günlük sohbet) sürekli
yüklü kalır, `hermes3:8b` yalnızca `delegate_complex_task` tetiklendiğinde
Ollama'nın model-swap mekanizmasıyla belleğe alınır. Bu görevsel olarak
daha "temiz" ayrım olurdu ama **iki 8B modeli aynı anda tutacak VRAM yok**
(bkz. bütçe hesabı) — yani Hermes her çağrıldığında önce Llama'nın
bellekten atılıp Hermes'in yüklenmesi gerekir. Bu soğuk yükleme birkaç
saniye sürer ve **tam olarak sistemin şu an şikayet edilen "hantallık"
hissini büyütür**, azaltmaz.

**Seçenek A (seçildi) — tek model, iki rol:** `orchestrator` ve
`tool_agent` rolleri **aynı Ollama modelini** paylaşır — `llama3.1:8b`
yerine **`hermes3:8b` (Hermes-3-Llama-3.1-8B)** tek model olarak
yüklenir, Llama3.1 sahneden çıkar. Gerekçe:

- Hermes zaten Llama 3.1 8B'nin fine-tune'u — temel yetenek kaybı yok,
  üstüne fonksiyon çağırma/JSON çıktı için özel eğitilmiş.
- İki rol arasındaki fark artık **farklı model** değil, **farklı sistem
  prompt + farklı çağrı derinliği**: `orchestrator` tek-adımlı sınıflandırma
  (`temperature=0.1`, kısa context), `tool_agent` çok-adımlı akıl yürütme
  (daha yüksek `temperature`, tool sonuçlarını takip eden döngü).
- VRAM bütçesi **değişmez** (tek 8B model, öncekiyle aynı) ve **soğuk
  yükleme/swap gecikmesi hiç oluşmaz** — model zaten bellekte, sadece
  hangi system prompt ile çağrıldığı değişiyor.
- Yan fayda: §20 madde 10 (`no_tool_needed` sentinel workaround) yeniden
  test edilmeli — Hermes'in native fonksiyon-çağırma eğitimi, mevcut
  "her zaman bir fonksiyon çağır" şablon sorununu azaltabilir.

#### 2.2.2 Model alternatifleri — neden Hermes3, başka ne değerlendirildi

`hermes3:8b` varsayılan seçim ama tek aday değil. Aynı VRAM diliminde
(Q4'te ~5 GB, Whisper+XTTS ile birlikte 12 GB'a rahat sığan) ciddi
rakipler var:

| Model | VRAM (Q4) | Neden aday | Neden birincil seçilmedi |
|---|---|---|---|
| **Hermes3:8b** (seçili) | ~5 GB | Nous'un özel fonksiyon-çağırma/ChatML eğitimi, kanıtlanmış tool-use formatı | — |
| **Qwen3:8b** | ~5 GB | Native tool-calling eğitimi güçlü, güncel (2026) genel kalite Llama 3.1 tabanından yüksek | Henüz A/B test edilmedi — bkz. aşağıdaki not |
| Gemma 4 12B | ~8 GB | Native function calling + yapılandırılmış JSON çıktı | 12 GB kartta Whisper+XTTS ile birlikte bütçeyi zorluyor (~8+2+2.5=12.5 GB, sınırda/aşıyor) |
| Mistral Small 24B | ~14 GB (Q4) | En güçlü ajanik/JSON performansı | Tek başına 12 GB'ı aşıyor, bu donanımda seçenek değil |

**Pratik öneri:** Hermes3:8b'yi varsayılan tut ama geçişten önce (Faz B
uygulanmadan) **Qwen3:8b ile bir günlük A/B testi** yap — aynı 20-30
router komutunu (§20 madde 10'daki gerçek kullanım senaryolarından) her
ikisine sor, hangisi daha az yanlış-araç-seçimi / gereksiz
`no_tool_needed` kaçışı yapıyor karşılaştır. İkisi de aynı VRAM/gecikme
profiline sahip olduğu için bu, mimariyi değiştirmeyen düşük-riskli bir
deney — sadece `ROLE_MODEL_MAP` içindeki string değişir.

`adapters/agent_factory.py` değişikliği:

```python
# AgentFactory.create() içinde
ROLE_MODEL_MAP = {
    "orchestrator": "hermes3:8b",   # önceden llama3.1:8b
    "tool_agent":   "hermes3:8b",   # önceden ayrı yükleme planlanıyordu — artık AYNI model
}
# İki rol de LlamaOrchestratorAdapter'ı kullanır (sınıf farklılaşması
# gereksiz hale gelir), yalnızca system_prompt parametresi farklılaşır.
```

`HermesAgentAdapter` sınıfı bu noktada **retire edilebilir** —
`LlamaOrchestratorAdapter`, constructor'a `role_prompt: str` parametresi
alacak şekilde genelleştirilir. Bu, kod tekrarını da azaltır.

### 2.3 Router + Chat çağrı birleşimi (§20 madde 1)

Mevcut durumda kural eşleşmeyen her turda iki ayrı LLM çağrısı olabilir
(router + chat). İki seçenek var; **ikisi birlikte** önerilir:

1. **Mini router modeli:** Sadece `Intent` sınıflandırması için çok küçük,
   hızlı bir model (örn. Ollama üzerinden `qwen2.5:1.5b` veya `3b`)
   ayrılır. VRAM etkisi ihmal edilebilir (~1 GB, Q4). Bu model **sadece**
   "hangi araç / delegate / no_tool_needed" kararını verir; gerçek
   sohbeti hiç üretmez. `Dispatcher.classify()` bu modele işaret eder.
2. Router kararı `no_tool_needed` çıkarsa, **aynı çağrının** ürettiği
   ilk token'ları chat yanıtına dönüştürmeye çalışmak (tek istekte
   birleştirme) — bu, Ollama'nın function-calling şablon davranışı
   yüzünden (§20 madde 10) kırılgan; bu yüzden öncelik 1'de.

Bu değişiklik `core/dispatcher.py`'de `AgentFactory.create("orchestrator")`
çağrısının yerini `AgentFactory.create("router")` (yeni, ayrı role→model
eşlemesi) alması ile yapılır. `ROLE_MODEL_MAP["router"] = "qwen2.5:1.5b"`.

### 2.4 `Agent` ABC'ye `respond_stream()` eklenmesi (§20 madde 2)

`agents/base.py` değişikliği:

```python
class Agent(ABC):
    def respond(self, prompt, context=None) -> str: ...
    def respond_stream(self, prompt, context=None) -> Iterator[str]: ...  # YENİ
    def supports_tools(self) -> bool: ...
    def call_tools(self, prompt, tools, context=None) -> AgentToolResponse: ...
```

`LlamaOrchestratorAdapter.respond_stream()` mevcut `ollama.chat(...,
stream=True)` mantığını sarar. `brain/llm.py:think_and_respond_stream()`
artık doğrudan `ollama.chat` çağırmaz:

```python
# ÖNCESİ (brain/llm.py, tarihsel — Factory'den önce yazılmış)
response = ollama.chat(model="llama3.1:8b", messages=..., stream=True)

# SONRASI
agent = AgentFactory.create("orchestrator")   # veya aktif rol neyse
for chunk in agent.respond_stream(user_input, context=history):
    ...  # cümle bölme, guardrail, history yönetimi AYNI KALIR — bu kısım
         # sağlayıcıya özel değil, uygulama protokolü, brain/llm.py'de kalmalı
```

Bu değişiklik `brain/llm.py`'nin cümle bölme / history trim / hata
sınıflandırma sorumluluklarını **korur** — sadece "hangi model çağrılıyor"
kısmını Factory'ye devreder. Sağlayıcı değişiminde (örn. ileride farklı
bir yerel model denemek) artık tek nokta yeterli olur.

### 2.5 `ClaudeCodeAdapter` gerçek implementasyonu

```python
class ClaudeCodeAdapter(Agent):
    def __init__(self):
        self._client = anthropic.Anthropic()  # ANTHROPIC_API_KEY ortam değişkeninden
    def respond(self, prompt, context=None) -> str:
        # anthropic SDK messages.create(), context → messages listesine çevrilir
    def call_tools(self, prompt, tools, context=None) -> AgentToolResponse:
        # Claude'un tool_use bloklarını AgentToolResponse'a eşle
    def supports_tools(self) -> bool:
        return True
```

Önkoşul: `requirements.txt`'e `anthropic` eklenir, `config/security.yaml`'a
`anthropic_api_key_env: "ANTHROPIC_API_KEY"` (fail-loud: değişken yoksa
`deep_reasoning` rolü `check_ollama_connection` benzeri bir `check_
anthropic_connection()` ile boot'ta **uyarı verir ama sistemi çökertmez** —
bu rol opsiyonel, yerel roller çalışmaya devam eder).

### 2.6 Router şemasına delegasyon sentinel'leri

Mevcut `_NO_TOOL_SCHEMA` deseni (§7.3) genişletilir — **yeni bir
sınıflandırma katmanı icat edilmez**, aynı router çağrısının şemasına iki
sentinel daha eklenir:

```python
_DELEGATE_COMPLEX_SCHEMA = {
    "name": "delegate_complex_task",
    "description": "Kullanıcının isteği tek bir araçla değil, birden "
                    "fazla adım/araç zinciriyle çözülüyorsa bunu çağır "
                    "(örn: 'şu araştırmayı yap ve notlara ekle')."
}
_DELEGATE_CODE_SCHEMA = {
    "name": "delegate_code_task",
    "description": "İstek yeni bir proje/kod mimarisi/ağır refactor "
                    "gerektiriyorsa bunu çağır (örn: 'yeni bir proje "
                    "başlat', 'şu modülü yeniden yaz')."
}
```

`Dispatcher.classify()` yorum mantığı genişler:

```python
call.name == "delegate_complex_task"  → Intent("delegate_complex", 0.7, source="llm")
call.name == "delegate_code_task"     → Intent("delegate_code", 0.7, source="llm")
```

`core/app.py:_handle_turn()`'e iki yeni dal eklenir (mevcut `intent.name
!= "chat"` bloğunun içine, `HANDLERS`/`get_tool` kontrollerinin yanına):

```python
elif intent.name == "delegate_complex":
    agent = AgentFactory.create("tool_agent")
    # agent.call_tools(...) ile TOOL_REGISTRY üzerinde çok-adımlı döngü
    # (bkz. §20 madde 4 — bu, tam otonom görev zinciri DEĞİL; tek bir
    # tur içinde sınırlı, önceden tanımlı bir maksimum adım sayısıyla
    # (örn. 3) çalışan kontrollü bir zincirdir; sınırsız otonomi ayrı,
    # daha dikkatli tasarlanması gereken bir sonraki faz)
elif intent.name == "delegate_code":
    agent = AgentFactory.create("deep_reasoning")
    response = agent.respond(user_text, context=history)
    # response TTS'e kısa özet olarak, tam çıktı HUD paneline
```

> **Faz 6.10 notu (ROADMAP §6.10):** `delegate_complex` dalı genelleştirildi —
> döngü başına tek ucuz `router` çağrısı task'ı kayıtlı **tool-set** (`kind:
> toolset` manifest) açıklamalarına karşı sınıflandırıp yalnızca ilgili
> set(ler)in üye araç şemasını döngüye sokar (bağlam şişmesini önler). 0
> tool-set kayıtlıyken adım atlanır, döngü yukarıdaki düz `all_tools()`
> davranışıyla çalışır (birebir geri uyum). Global sabit adım tavanı ve
> adım-başı `_run_tool_pipeline` risk kapısı değişmez. `delegate_code` ise
> **Faz 6.10.3**'te dosya DEĞİŞTİREN moda (`ClaudeCodeAdapter` `writable`)
> genişler — ikinci bir açık onaydan sonra.

---

## 3. Agent Registry — Dinamik Ekleme (mevcut güvenlik ilkesini bozmadan)

### 3.1 Problem

Yeni bir araç/ajan eklemek şu an `tools/registry.py:TOOL_REGISTRY`'ye elle
import + dict girişi gerektiriyor. Bu **bilinçli bir güvenlik özelliği**
("bir araç yanlışlıkla kayıtlı olamaz") — bu yüzden çözüm **otomatik
keşif değil**, **açık allowlist ile genişletilebilir manifest** olmalı.

### 3.2 Manifest şeması (YENİ: `agents/registry/*.yaml`)

```yaml
# agents/registry/home_assistant_lights.yaml
name: control_lights
description: "Ev içindeki akıllı ışıkları açar/kapatır/parlaklık ayarlar"
kind: tool
risk_level: MEDIUM
execution_mode: on_demand
module: src.jarvis.tools.iot_tool
class: HomeAssistantLightTool
parameters_schema:
  room: {type: string, required: true}
  action: {type: string, enum: ["on", "off", "dim"]}
```

### 3.3 Yükleme mekanizması (YENİ: `core/registry_loader.py`)

```python
def load_dynamic_tools() -> dict[str, Tool]:
    """config/security.yaml:enabled_dynamic_agents allowlist'inde ismi
    geçen manifest dosyalarını yükler. Allowlist'te olmayan manifest
    dosyaları SESSİZCE ATLANIR (fail-closed — TOOL_REGISTRY ile aynı
    'statik onay' felsefesi, sadece dosya yerine YAML+config girişi)."""
```

`config/security.yaml` eklentisi:

```yaml
enabled_dynamic_agents:
  - home_assistant_lights   # agents/registry/home_assistant_lights.yaml
  # yeni bir ajan eklemek: (1) manifest dosyası yaz, (2) bu listeye
  # ismini ekle. İkinci adım olmadan hiçbir şey aktive olmaz.
```

`tools/registry.py:all_tools()` üç kaynağı birleştirir (önceki iki
kaynağın üstüne):

```python
def all_tools():
    return {
        **TOOL_REGISTRY,                    # statik, kod-içi (en güvenilir)
        **load_dynamic_tools(),             # YENİ — manifest + allowlist
        **MCP_adapter.discover_tools(),      # dış, en az güvenilir
    }
```

Bu tasarım, "yeni ajanları sisteme entegre etmeden çalıştırma" hedefini
karşılar (kod değişikliği yok, sadece YAML + tek satır config) ama
"bir araç yanlışlıkla kayıtlı olamaz" ilkesini de korur (iki açık adım
gerekir, hiçbiri otomatik değildir).

> **Uygulama notu (Faz 6.4, ROADMAP §6.4):** yukarıdaki `all_tools()` kod
> parçacığı sıralamayı yanıltıcı gösteriyor — `{**TOOL_REGISTRY, **dynamic,
> **mcp}` dict-birleşiminde son yazan kazandığı için MCP statiği ezerdi.
> Uygulama bilinçli olarak **statiği en son uygular** (öncelik statik >
> dinamik manifest > MCP, ad çakışmasında statik kazanır + uyarı loglar).
> Ek olarak: manifest `module:class` instantiate edilir ve **sınıf otoriterdir**
> (manifest `name`/`risk_level` sınıfla çelişirse fail-closed atlanır);
> `description` + parametre açıklamaları `InputInjectionCheck`'ten geçer
> (§8.2 MCP "tool poisoning" emsali); `risk_level: critical` reddedilir
> (§10); bozuk/import-zamanında patlayan manifest fail-soft atlanır, boot
> sürer. `enabled_dynamic_agents` anahtarı = manifest **dosya kökü** (stem).

---

## 4. Hafıza Katmanı — Kalıcı Semantic Hafıza (YENİ: `core/memory.py`)

> **Faz 6.5 kararı (ROADMAP §6.5):** Mem0 **kullanılmıyor**. Her `remember()`'da
> fact-extraction için ekstra Ollama LLM turu (VRAM bütçesiyle çakışır),
> `openai` bağımlılığı ve v2.x hızlı değişen API nedeniyle elendi. Yerine
> **DIY minimal**: `sentence-transformers` (`paraphrase-multilingual-MiniLM-L12-v2`,
> CPU — iki-dillilik için `all-MiniLM-L6-v2`'den sapma, §4.4) + merkezi
> `data/jarvis.db` (SQLite/WAL, `core/db.py` — 6.5.1'in de temeli) + in-process
> numpy brute-force cosine. LLM-in-loop yok; embedding'ler tabloda `BLOB`;
> ayrı FAISS index dosyası yok (`>10k` girişte `faiss-cpu` escape hatch,
> arayüz sabit). Aşağıdaki §4.1–4.3 (arayüz + güvenlik ilkeleri) aynen geçerli;
> yalnızca §4.4'teki backend seçimi değişti.

### 4.1 Mevcut kısa-vadeli hafıza ile ilişki

`brain/llm.py`'deki `history` (system dahil son 12 mesaj) **değişmeden
kalır** — bu, tek oturumluk konuşma bağlamı. Kalıcı hafıza **ayrı** bir
katman: oturumlar arası ("geçen hafta bahsettiğin proje", kullanıcı
tercihleri, aktif proje bağlamı) bilgiyi tutar.

### 4.2 Arayüz

```python
def remember(text: str, metadata: dict) -> None:
    """Fail-soft: Mem0/vektör deposu erişilemezse sessizce no-op + log
    (config/mcp_servers.yaml'daki fail-soft ilkesiyle aynı desen)."""

def recall(query: str, k: int = 5) -> list[str]:
    """Fail-soft: erişilemezse boş liste döner, sistem hafızasız
    çalışmaya devam eder."""
```

### 4.3 Güvenlik — neden bu katman diğerlerinden daha dikkatli olmalı

**Kritik fark:** `_OUTPUT_GUARDRAIL` bugün "bu turu" korur. Hafızaya
yazılan bir şey ise **gelecekteki her turda** tekrar `recall()` ile
context'e enjekte olur — yani bir prompt injection burada başarılı
olursa, tek seferlik değil **kalıcı** bir sızıntı/manipülasyon yüzeyi
haline gelir. Bu yüzden:

- `remember()` çağrısından **önce** içerik `_OUTPUT_GUARDRAIL.run()`'dan
  geçer (Brain zaten her cümlede bunu yapıyor — `remember()` çağrısı bu
  guardrail'den geçmiş, TTS'e gitmiş metni kullanmalı, ham LLM çıktısını
  değil).
- `recall()` ile geri gelen metinler context'e eklenmeden önce **girdi**
  guardrail'inden de geçirilir (`_INPUT_GUARDRAIL` benzeri bir kontrol) —
  geçmişte guardrail'i atlatmış bir şey varsa ikinci bir fırsat.
- `core/app.py:_handle_turn()` başına `recall()` çağrısı eklenir (adım 2
  ile 3 arasına, dispatcher'dan önce — çünkü hafıza router kararını da
  etkileyebilir, örn. "az önce bahsettiğim proje" referansı).

### 4.4 Yerel-öncelikli kurulum

Mem0'ın self-hosted modu tercih edilir (hosted API değil) —
"yerel-öncelikli" ilkesiyle tutarlı. Vektör deposu: yerel Qdrant ya da
basit SQLite+embedding (RTX 4070 üzerinde küçük bir embedding modeli
zaten Whisper/XTTS ile birlikte VRAM bütçesine giriyor — bu yüzden CPU
üzerinde çalışan hafif bir embedding modeli (örn. `all-MiniLM-L6-v2`)
tercih edilmeli, GPU bütçesine dokunmamalı).

---

## 5. Execution Modes — Scheduled ve Continuous (YENİ)

### 5.1 `core/scheduler.py`

Zamanlanmış görevler için (örn. günlük özet). `InputHub`'ın `queue.Queue`'
sine `InputEvent(source="scheduled", text=<önceden tanımlı komut>)` koyar
— **aynı** guardrail/dispatcher/tool hattından geçer, ayrı bir güvenlik
yolu yoktur.

```yaml
# config/scheduled_tasks.yaml (YENİ, security.yaml'a benzer fail-loud)
- name: morning_briefing
  cron: "0 8 * * *"
  text: "günlük özetimi hazırla"   # dispatcher normal şekilde işler
```

### 5.2 `core/continuous_runner.py`

`jarvis-mic`/`jarvis-text-input` ile aynı desende yeni bir daemon thread
— bir koşulu izler (dosya değişikliği, MCP kaynağı, IoT sensör durumu) ve
tetiklendiğinde `InputEvent(source="continuous", ...)` üretir. Thread
haritasına (§19) eklenir, `stop_event` ile aynı şekilde kapanır.

### 5.3 Kritik güvenlik kısıtlaması

**Kural:** `source in {"scheduled", "continuous"}` olan bir olay, sadece
`risk_level == RiskLevel.LOW` olan araçları **otomatik onaysız**
çalıştırabilir. `execution_mode: scheduled` veya `continuous` işaretli
bir manifest, `risk_level` MEDIUM+ ise **boot zamanında reddedilir**
(registry_loader bunu doğrular, sessizce yüklemez, `print_system`
uyarısı basar).

MEDIUM+ bir eylem scheduled/continuous yoldan tetiklenmek istenirse:
otomatik çalışmaz, bunun yerine bir **bekleyen onay** kaydı oluşturulur
(HUD'da/`/status`'ta görünür), kullanıcı döndüğünde onaylar/reddeder.
Bu, mevcut "Zero-Trust: varsayılan HER ZAMAN RED" ilkesinin doğal
uzantısıdır — kullanıcı yokken RED, kullanıcı gelince karar onun.

---

## 6. Proje Başlatma Aracı — `tools/project_tool.py` (YENİ)

```python
class CreateProjectTool(Tool):
    name = "create_project"
    risk_level = RiskLevel.HIGH   # dosya sistemi + subprocess + dış CLI
    parameters_schema = {"project_name": {"type": "string", "required": True}}

    def execute(self, params: dict, stop_event=None) -> str:
        # 1. is_path_safe() (SERTLEŞTİRİLMİŞ hali, §7) ile
        #    PROJECT_ROOT/jarvis_workspace/projects/<project_name> doğrula
        # 2. Klasörü oluştur
        # 3. templates/CLAUDE.md.template'i kopyala + proje adını göm
        # 4. spawn_detached(["claude", "code"], cwd=yeni_klasör)  # YENİ yardımcı
        # 5. Kısa TTS cevabı: "{name} projesi oluşturuldu, Claude Code başlatıldı"
```

**Neden `RunCommandTool`'un mevcut `communicate(timeout=15)` modelini
kullanmıyoruz:** Claude Code oturumu dakikalarca/saatlerce sürebilir,
JARVIS'in ana thread'ini bloklaması kabul edilemez. Bu yüzden yeni bir
yardımcı gerekiyor:

```python
# tools/subprocess_utils.py (YENİ)
def spawn_detached(cmd: list[str], cwd: str) -> None:
    """subprocess.Popen, communicate()/wait() ÇAĞRILMAZ — ateşle-ve-unut.
    JARVIS süreci bittiğinde Claude Code oturumu kesilmez (Windows'ta
    DETACHED_PROCESS / CREATE_NEW_PROCESS_GROUP flag'i)."""
```

Bu, doğrudan `project_name`'in bir dosya yoluna dönüştüğü **ilk yer**
olduğu için §7'deki `is_path_safe()` sertleştirmesi bu tool'un **sert
önkoşuludur** — o olmadan bu tool eklenmemeli.

---

## 7. Önkoşul Sertleştirmeleri (§20 madde 5 ve 6)

Bu ikisi diğer her şeyden **önce** yapılmalı — küçük, izole, ama §5 ve §6
onlara bağımlı.

### 7.1 `Tool.execute()` imzasına `stop_event` (§20 madde 5)

```python
# tools/base.py
class Tool(ABC):
    def execute(self, params: dict, stop_event: threading.Event | None = None) -> str: ...
```

Geriye dönük uyumlu (varsayılan `None`) — mevcut tool'ların hiçbiri
imzasını hemen değiştirmek zorunda değil. `core/app.py:_run_tool_pipeline()`
artık `tool.execute(intent.parameters, stop_event=stop_event)` çağırır.
Gerçek zorlayıcı iptal için (tool içeride periyodik kontrol etmezse
`stop_event` tek başına yetmez) `_run_tool_pipeline` bir
`concurrent.futures.ThreadPoolExecutor` + `future.result(timeout=N)`
sarmalayıcısına alınmalı — özellikle MCP/yeni CreateProjectTool gibi
uzun sürebilecek araçlar için.

### 7.2 `is_path_safe()` genelleştirilmesi (§20 madde 6)

```python
# core/security_config.py
def is_path_safe(path: str, base_dir: Path, *, allow_create: bool = False) -> bool:
    # Mevcut Path.resolve() + is_relative_to() korunur, EKLENİR:
    # - UNC yol reddi (\\server\share)
    # - \\?\ ön eki reddi (uzun yol / güvenlik atlatma girişimi)
    # - dosya/klasör adı allowlist'i (regex: yalnızca alfanumerik + - _)
    # - allow_create=False iken yol zaten var olmalı; True iken (CreateProjectTool
    #   gibi) yalnızca base_dir altında YENİ oluşturmaya izin verilir
```

Şu an yalnızca kod-sabit yollarla çağrılıyor; bu genelleme olmadan
`CreateProjectTool` gibi LLM-türetilmiş bir yol parametresi alan hiçbir
tool eklenmemeli.

---

## 8. MCP Genişletmesi — Google Drive + Home Assistant

### 8.1 Google Drive MCP

Mevcut "MCP yalnızca bilgi/veri erişimi" ilkesine tam uyuyor (not
alma/okuma ile aynı kategori). `config/mcp_servers.yaml`'a standart bir
girdi eklenir, risk seviyesi mevcut not tool'larıyla aynı (`MEDIUM`,
zaten MCP araçlarına asla `LOW` verilmiyor).

### 8.2 Home Assistant — İlke çatışması ve çözümü

**Gerilim:** Home Assistant'ın kendi MCP sunucusu var ama mevcut ilke
"OS kontrolü (terminal, uygulama, medya) asla MCP üzerinden geçmez, yerel
sandbox'ta kalır." IoT kontrolü (ışık, kilit, priz) fiziksel dünyayı
etkilediği için kavramsal olarak OS kontrolüne daha yakın, saf bilgi
erişiminden uzak.

**Karar — ilkeyle tutarlı ayrım:**

| Eylem | Yol | Gerekçe |
|---|---|---|
| IoT **durumu okuma** (hangi lambalar açık, sıcaklık) | MCP (Home Assistant MCP sunucusu) | Salt bilgi, ilkeye uygun |
| IoT **kontrolü** (aç/kapat/kilitle) | Yerel `tools/iot_tool.py:HomeAssistantTool` (Home Assistant REST API'sini doğrudan çağırır, `TOOL_REGISTRY`'ye statik kayıtlı) | Fiziksel etki = OS kontrolü kategorisi |

Risk seviyeleri cihaz tipine göre ayrılır: ışık/priz `MEDIUM`, kilit/
güvenlik cihazı `HIGH`. Bu, mevcut `launch_app`/`run_command` risk
ayrımıyla aynı mantığı takip eder.

---

## 9. Gözlemlenebilirlik — `core/trace.py` (YENİ)

`hud_bus`'a benzer ama **kalıcı** (SQLite, `core/trace.db`). Her agent/
tool çağrısı için: timestamp, rol/model adı, girdi özeti (tam metin
değil — hassas veri birikimini önlemek için kısaltılmış/hash'lenmiş),
süre (ms), sonuç (`success` / `error` / `guardrail_blocked` /
`approval_denied`), varsa token sayısı.

Amaç: §20 madde 1'in (çift çağrı gecikmesi) gerçek etkisini ölçmek,
`delegate_complex_task`'ın kaç adımda tamamlandığını görmek, hangi rolün
en çok zaman/token harcadığını anlamak. `/trace [n]` CLI komutu (mevcut
`/status`, `/debug` ile aynı ailede) son N kaydı gösterir.

---

## 10. Kapsam Dışı Bırakılanlar (bilinçli)

Bu belge her şeyi çözmüyor — mevcut §20 listesinden şu maddeler **başka
bir planlama turuna** bırakılmıştır, burada karıştırılmamalı:

- **Madde 4** (otonom görev zinciri) — §2.6'daki `delegate_complex_task`
  **sınırlı, sabit-adım-sayılı** bir zincirdir; gerçek plan→araç→
  değerlendir→devam döngüsü (ROADMAP Faz 6) ayrı ve daha dikkatli bir
  tasarım gerektiriyor (history'ye tool sonucu ekleme = injection yüzeyi
  büyür, bu belge o kararı vermiyor).
- **Madde 7** (CRITICAL/RFID) — donanım/biyometri, bu belgenin kapsamı
  dışında.
- **Madde 8** (AEC) — ses pipeline konusu, multi-agent/hafıza ile
  ilgisiz.
- **Madde 9** (`RunCommandTool shell=True`) — zaten savunması var, bu
  belge ek değişiklik önermiyor.
- **Madde 11** (guardrail regex kapsamı) — sınıflandırma-modeli tabanlı
  bir katman iyi bir gelecek fazı olur ama bu belgenin öncelik listesinde
  değil.

---

## 11. §20 Sınırlamaları — Bu Belgede Nasıl Ele Alındı

| # | Sınırlama (kısa) | Bu belgede | Durum |
|---|---|---|---|
| 1 | Çift LLM çağrısı gecikmesi | §2.3 mini router modeli | Azaltıldı |
| 2 | Chat, Agent arayüzünü kullanmıyor | §2.4 `respond_stream()` | Çözüldü |
| 3 | Multi-agent bağlı değil | §2.2, 2.5, 2.6 | Çözüldü |
| 4 | Otonom görev zinciri yok | §2.6 (sınırlı versiyon), §10 | Kısmi — tam çözüm kapsam dışı |
| 5 | `Tool.execute()` stop_event yok | §7.1 | Çözüldü |
| 6 | `is_path_safe()` genel değil | §7.2 | Çözüldü |
| 7 | CRITICAL risk kullanılmıyor | §10 | Kapsam dışı |
| 8 | AEC yok | §10 | Kapsam dışı |
| 9 | `RunCommandTool shell=True` | §10 | Kapsam dışı |
| 10 | `no_tool_needed` workaround | §2.2 (yeniden test önerisi) | Azaltıldı, doğrulama gerekir |
| 11 | Guardrail regex kapsamı | §10 | Kapsam dışı |
| 12 | VRAM bütçesi | §2.2.1 model konsolidasyonu (Seçenek A) + §2.2.2 alternatif değerlendirmesi | Çözüldü |

---

## 12. Değişen / Eklenen Dosya Yapısı

```
YENİ:
  src/jarvis/core/memory.py            # Mem0 sarmalayıcı: remember()/recall(), fail-soft
  src/jarvis/core/scheduler.py         # cron-tabanlı InputEvent üretici (source="scheduled")
  src/jarvis/core/continuous_runner.py # arka plan izleme thread'i (source="continuous")
  src/jarvis/core/registry_loader.py   # agents/registry/*.yaml → dinamik Tool yükleyici
  src/jarvis/core/trace.py             # SQLite tabanlı çağrı izleme
  src/jarvis/tools/project_tool.py     # CreateProjectTool
  src/jarvis/tools/iot_tool.py         # HomeAssistantTool (yerel, MCP değil)
  src/jarvis/tools/subprocess_utils.py # spawn_detached()
  agents/registry/                     # manifest .yaml dosyaları
  config/scheduled_tasks.yaml(.example)
  templates/CLAUDE.md.template         # CreateProjectTool'un kopyaladığı scaffold

DEĞİŞEN:
  src/jarvis/agents/base.py            # Agent ABC'ye respond_stream() eklenir
  src/jarvis/adapters/agent_factory.py # ROLE_MODEL_MAP; HermesAgentAdapter retire;
                                        # ClaudeCodeAdapter gerçek implementasyon
  src/jarvis/brain/llm.py              # think_and_respond_stream() → agent.respond_stream()
  src/jarvis/core/dispatcher.py        # delegate_complex_task / delegate_code_task sentinel'leri
  src/jarvis/core/app.py               # _handle_turn(): delegate dalları; recall()/remember() çağrıları;
                                        # _run_tool_pipeline(): stop_event iletimi + timeout sarmalayıcı
  src/jarvis/core/security_config.py   # is_path_safe() genelleştirilir
  src/jarvis/tools/base.py             # Tool.execute() imzasına opsiyonel stop_event
  src/jarvis/tools/registry.py         # all_tools() üçüncü kaynağı (dinamik) ekler
  config/security.yaml(.example)       # enabled_dynamic_agents: []
  config/mcp_servers.yaml(.example)    # Google Drive + Home Assistant (yalnızca durum okuma) örnekleri
  requirements.txt                     # + anthropic, + mem0 (veya seçilen self-hosted alternatif)
```

---

## 13. Önerilen Uygulama Sırası (bağımlılık grafiği)

```
Faz A ─┬─ Tool.execute() stop_event imzası (§7.1)
       └─ is_path_safe() sertleştirme (§7.2)
             │  (ikisi de izole, birbirine bağımlı değil, hemen yapılabilir)
             ▼
Faz B ── Model konsolidasyonu + respond_stream() + Brain refactor (§2.2–2.4)
             │  (VRAM + çift-çağrı + Agent-arayüz sorunlarını aynı anda çözer)
             ▼
Faz C ── Multi-agent aktivasyonu: ClaudeCodeAdapter + router sentinel'leri (§2.5–2.6)
             │
             ├──▶ Faz G ── CreateProjectTool (§6)  ── Faz A'ya bağımlı (is_path_safe)
             │
Faz D ── Agent Registry / manifest sistemi (§3)
             │
Faz E ── Hafıza katmanı — Mem0 (§4)
             │
Faz F ── Execution modes — scheduler + continuous runner (§5)
             │  (Faz D'deki risk-kısıtlama kuralına bağımlı)
             ▼
Faz H ── MCP genişletme: Drive + Home Assistant (§8)  ── bağımsız, herhangi bir zaman
Faz I ── Tracing katmanı (§9)  ── tamamen bağımsız, herhangi bir zaman eklenebilir
```

**Not:** Faz A ve B, en yüksek/riziko-düşürücü etkiye sahip ve en az
bağımlılığa sahip olduğu için önce yapılmalı. Faz D–F (registry, hafıza,
execution modes) birbirinden bağımsızdır, paralel/istenen sırada
ilerlenebilir.

---

## 14. Claude Code'a Devir Notu

Bu dosya `docs/` altına `jarvis-mimari-v2-multiagent-entegrasyon.md`
olarak eklenmeli. Uygulama sırasında:

- Her faz kendi commit'i/PR'ı olsun; §13'teki bağımlılık sırasına uyulsun.
- Faz numaraları (`docs/ROADMAP.md`'ye entegre edilirken) oradaki mevcut
  Faz 1–6 numaralandırmasıyla çakışmayacak şekilde yeniden adlandırılmalı
  (örn. bu belgedeki fazlar ROADMAP'in "Faz 7+" bloğu olabilir).
- `security-reviewer` subagent'ı özellikle §6 (CreateProjectTool,
  subprocess spawn), §7.2 (path safety) ve §4.3 (hafıza guardrail'i)
  üzerinde çalıştırılmalı — bunlar bu belgedeki en yüksek risk yüzeyli
  eklemeler.
- `pipeline-debugger` subagent'ı Faz B'deki model konsolidasyonu sonrası
  `verify-brain-pipeline` skill'iyle regresyon kontrolü yapmalı (Hermes'e
  geçiş, mevcut router davranışını değiştirebilir — özellikle `no_tool_
  needed` sentinel'inin hâlâ gerekli olup olmadığı bu noktada test
  edilmeli).
