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

## Fail-soft

Bozuk / uyumsuz / import edilemeyen bir manifest yalnızca **atlanır** (uyarı
logu + `print_system`), Jarvis yine başlar. "fail-closed" burada
*yüklenmez/aktive olmaz* demektir, *çökmez* değil.
