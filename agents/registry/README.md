# `agents/registry/` — Dinamik Araç/Ajan Manifestleri (Faz 6.4)

Bu dizindeki `*.yaml` dosyaları, **kod değişikliği yapmadan** Jarvis'e yeni
araç eklemeyi sağlar — ama `tools/registry.py:TOOL_REGISTRY`'nin "bir araç
yanlışlıkla kayıtlı olamaz" güvenlik ilkesini bozmadan.

## Bir aracı devreye almak = İKİ elle adım

1. Bu dizine bir manifest dosyası koy: `agents/registry/<ad>.yaml`.
2. `<ad>`'ı (dosya kökü, uzantısız) `config/security.yaml` içindeki
   `enabled_dynamic_agents` listesine ekle.

**İkinci adım olmadan hiçbir şey aktive olmaz.** Otomatik keşif yoktur.
Allowlist'te olmayan veya `*.example.yaml` uzantılı manifestler sessizce
atlanır.

Yükleyici: `src/jarvis/core/registry_loader.py:load_dynamic_tools()`.
`tools/registry.py:all_tools()` üç kaynağı birleştirir — statik `TOOL_REGISTRY`
+ bu manifestler + MCP; ad çakışmasında statik kazanır.

## Manifest şeması

| Alan | Zorunlu | Açıklama |
|------|---------|----------|
| `name` | ✅ | Aracın adı. **`module:class` ile yüklenen `Tool` alt sınıfının `.name`'iyle BİREBİR aynı olmalı** — uyuşmazsa manifest fail-closed atlanır. |
| `description` | — | Router LLM'e giden açıklama. Injection taramasından geçer. |
| `kind` | ✅ | Şimdilik yalnızca `tool`. (`agent` ileride.) |
| `risk_level` | ✅ | `low` / `medium` / `high` / `critical`. **Sınıfın `.risk_level`'ıyla aynı olmalı** — uyuşmazsa atlanır (bir manifest gerçek riski gizleyemez). |
| `execution_mode` | — | `on_demand` (varsayılan) / `scheduled` / `continuous`. `scheduled`/`continuous` + `risk_level` MEDIUM+ → boot'ta reddedilir (v2 §5.3). |
| `module` | ✅ | Import edilecek Python modülü, ör. `src.jarvis.tools.iot_tool`. |
| `class` | ✅ | O modüldeki `Tool` alt sınıfının adı. |
| `parameters_schema` | — | Advisory çapraz-kontrol; asıl otorite sınıfın kendi `parameters_schema`'sı. Anahtarlar uyuşmazsa yalnızca uyarı loglanır. |

Örnek: `home_assistant_lights.example.yaml` (bu dizinde; `.example` uzantısı
olduğu için asla yüklenmez — canlı şablon).

## `kind: toolset` manifest şeması (Faz 6.10 — planlı)

`<set>.toolset.yaml` dosyaları, birden fazla aracı **uzman tool-set** olarak
gruplar; genel orkestrasyon döngüsü (`_run_delegate_complex`) göreve göre
yalnızca ilgili set(ler)in üye şemasını yükler. `load_toolsets()` kendi
`*.toolset.yaml` glob'unu kullanır; allowlist girişi `<set>.toolset` stem'idir
(yani `enabled_dynamic_agents`'a hem üye `<araç>` hem `<set>.toolset` eklenir).

| Alan | Zorunlu | Açıklama |
|------|---------|----------|
| `name` | ✅ | Tool-set kimliği. |
| `kind` | ✅ | `toolset`. |
| `description` | ✅ | Seçim adımının gördüğü tek şey. `InputInjectionCheck` + 500 karakter kırpma. |
| `trigger_hints` | — | TR/EN örnek ifadeler; küçük router modeli için per-set disambiguation'ın TEK yeri. Injection taramasından geçer. |
| `tools` | ✅ | Üye araç **adları** (`all_tools()`'tan çözülür — statik/manifest/MCP). Set davranış tanımlamaz, gruplar. Çözülemeyen üye fail-soft atlanır. |
| `risk_ceiling` | — | Advisory (yükleme + call-time re-check). Otoriter zorlayıcı `_run_tool_pipeline` per-tool risk kapısıdır. Üyeler asla `CRITICAL`; mutasyon-yetkili delegasyon araçları üye olamaz. Yoksa varsayılan = HIGH'a kadar izin. |
| `max_steps` | — | `1 <= max_steps <= _MAX_DELEGATE_STEPS`; eksik/bozuk → global tavana düşer (asla sınırsıza). Global tavanı ASLA yükseltemez. |
| `memory_aware` | — | `true` ise seçim sonrası `recall(task)` (ham task) bağlamı bu set'in adımlarına `role: user` / sınırlandırılmış blokla girer (asla `role: system`). |

## Fail-soft

Bozuk / uyumsuz / import edilemeyen bir manifest yalnızca **atlanır** (uyarı
logu + `print_system`), Jarvis yine başlar. "fail-closed" burada
*yüklenmez/aktive olmaz* demektir, *çökmez* değil.
