# Hibrit MCP Vizyonu — ROADMAP.md + ARCHITECTURE.md Güncelleme Planı

**Tarih:** 2026-08-26
**Durum:** Onaylanmadı / henüz uygulanmadı — talimat bekleniyor.

Bu dosya, `docs/ROADMAP.md` ve `docs/ARCHITECTURE.md`'ye "Hibrit MCP"
vizyonunu (yerel çekirdek + MCP bilgi katmanı) işlemek için hazırlanmış
detaylı bir uygulama planıdır. Şu an sadece kayıt altına alınmıştır;
uygulanması için bu dosyadaki talimatların ayrıca verilmesi gerekir.

## Context

Faz 3 tamamlandı, Faz 4 (Otonom Ajan Döngüsü) öncesi mimari inceleme
aşamasındayız. Projeye MCP (Model Context Protocol) tabanlı dış bilgi
kaynakları (Obsidian vault, SQLite, GitHub, ileride IoT) eklenmesi
kararlaştırıldı, ancak Zero-Trust felsefesi gereği **hibrit** bir sınır
çiziliyor: işletim sistemi kontrolü (terminal, uygulama başlatma, medya —
YÜKSEK riskli) her zaman mevcut yerel `TOOL_REGISTRY`/`security.yaml`
sandbox'ında kalacak; MCP sadece **bilgi/veri erişimi** için kullanılacak.
Bu görev, bu vizyonu `docs/ARCHITECTURE.md` (nasıl/neden) ve
`docs/ROADMAP.md`'ye (ne zaman/ne, eyleme geçirilebilir checklist) işlemek.

**Not (repo taramasından):** `tools/registry.py:TOOL_REGISTRY` bilinçli
olarak STATİK bir dict — "bir aracın yanlışlıkla kayıtlı olması imkânsız"
ilkesiyle, auto-discovery yok (`registry.py:16-46`). MCP sunucularının
araçları doğası gereği dinamiktir. Plan bu gerilimi görmezden gelmiyor:
MCP-kaynaklı araçlar asla doğrudan `TOOL_REGISTRY`'ye enjekte edilmeyecek,
ayrı bir yol/registry üzerinden, varsayılan en az MEDIUM risk ile
guardrail'den geçecek şekilde tasarlanacak (bkz. aşağıdaki §9.2 taslağı).

**Zaten yapılmış olan (bu plan hazırlanmadan önce, auto mode'dayken):**
`docs/ARCHITECTURE.md`'de şu değişiklikler DAHA ÖNCE uygulandı ve dosyada
duruyor — plan bunların üzerine inşa edilecek, tekrar yapılmayacak:
- §1 ilkelere hibrit çekirdek/bilgi ayrımı bullet'ı eklendi.
- §2 katman mermaid diyagramına "3b. MCP Bilgi Katmanı" subgraph'ı
  (FS/SQLite/GitHub/IoT MCP + `MCPClientAdapter ⬜`) eklendi.
- §3 Adapter class diagram'ına `MCPClientAdapter` eklendi.
- §3 Adapter açıklamalarına `ClaudeCodeAdapter`'ın revize (CLI subcontractor)
  tasarımı ve yeni `MCPClientAdapter` paragrafı eklendi.
- §4 sequence diagram'ına Claude Code'un `terminal_tool` ile tetiklenmesi
  ve yeni bir "geniş bilgi/veri erişimi" dalı (MCP) eklendi.

Bu değişiklikler ileride yazılacak §9'a `(bkz. §9)` referansları bırakıyor
— dolayısıyla §9'un eklenmesi zorunlu, aksi halde dosya kırık referanslar
içerir.

## Yapılacaklar

### 1. `docs/ARCHITECTURE.md` — yeni §9 bölümü ekle (§8'den sonra, dosya sonu)

`### 9. Hibrit MCP Mimarisi (Bilgi Katmanı) ⬜` başlığıyla, alt bölümler:

- **9.1 İlke — Neden Hibrit?** OS kontrolü vs. bilgi erişimi ayrımının
  gerekçesi; MCP tool'larının asla doğrudan sistem/süreç kontrolü
  yapmayacağı açık sınırı.
- **9.2 `MCPClientAdapter` Tasarımı** — `src/jarvis/adapters/
  mcp_client_adapter.py` (⬜); MCP sunucularını keşfedip
  `adapters/tool_schema.py:build_ollama_tools()` ile aynı JSON-Schema
  sözleşmesine çevirme. **Statik `TOOL_REGISTRY` gerilimi** burada açıkça
  belgelenecek: MCP araçları ayrı bir yoldan (örn. çalışma zamanı keşfi,
  ayrı bir `MCP_TOOL_REGISTRY` ya da adapter'ın kendi iç durumu) sunulacak,
  hiçbir zaman sessizce statik registry'ye yazılmayacak; varsayılan risk
  seviyesi en az MEDIUM (dış sunucu verisi = güvenilmeyen girdi).
  Config deseni: `config/mcp_servers.yaml` (gitignore) +
  `config/mcp_servers.example.yaml` (commit'lenir), `core/security_config.py`
  ile aynı fail-loud yükleme prensibi.
- **9.3 Kritik Kullanım Senaryoları** — File System MCP (Obsidian vault,
  `notes_tool.py`'nin mevcut tek-dosya kapsam sınırlamasıyla karşılaştırarak),
  SQLite MCP (Asistan Game Master / FRP), GitHub MCP (repo/PR, salt-okunur
  öncelik — yazma işlemleri HIGH risk), IoT MCP (Faz 5.1 ile ilişki).
- **9.4 Ajan Mimarisi ile İlişkisi** — Orkestratör↔Hermes↔Claude Code
  iletişiminin MCP'ye GEÇMEDİĞİ, kendi `Agent`/ReAct arayüzünde kaldığı;
  `ClaudeCodeAdapter`'ın revize planı (terminal_tool → `claude` CLI,
  "Alt Yüklenici" deseni) — §2.2'deki mevcut stub'ın yerini alacağı not
  edilecek.
- **9.5 Güvenlik Notu** — MCP sunucuları da Zero-Trust dış sınır; her MCP
  tool çağrısı `core/risk.py` + `core/guardrail/` zincirinden geçer.

### 2. `docs/ARCHITECTURE.md` §7 (Genişletilmiş Klasör Yapısı) güncelle

`adapters/` altına `mcp_client_adapter.py ⬜` satırı eklenecek; ayrıca
kök `config/` altına `mcp_servers.yaml`/`mcp_servers.example.yaml ⬜`
referansı eklenecek (mevcut `security.yaml` deseniyle simetrik).

### 3. `docs/ROADMAP.md` — yeni "Faz 4.5" bölümü ekle

`## Faz 4 — Otonom Ajan Döngüsü ⬜` bölümünden sonra, `## Faz 5`'ten önce:

`## Faz 4.5 — Hibrit MCP Entegrasyonu (Bilgi Katmanı) ⬜`

Kısa bir intro paragrafı (`docs/ARCHITECTURE.md` §9'a referans) + checklist:
- [ ] `MCPClientAdapter` (`src/jarvis/adapters/mcp_client_adapter.py`) —
      statik `TOOL_REGISTRY` ile MCP'nin dinamik doğası arasındaki
      gerilimin nasıl çözüleceği (ayrı yol/registry, min. MEDIUM risk).
- [ ] `config/mcp_servers.yaml` + `.example.yaml` — `security_config.py`
      deseniyle tutarlı fail-loud yükleme.
- [ ] File System MCP — Obsidian vault geniş okuma.
- [ ] SQLite MCP — Asistan Game Master modu (FRP zar/stat/kural kitapçığı).
- [ ] GitHub MCP — repo okuma, PR analizi, commit farkları (salt-okunur
      öncelik; yazma işlemleri ayrı, HIGH riskli bir onay gerektirir).
- [ ] Claude Code CLI "Alt Yüklenici" tetikleme deseni — `ClaudeCodeAdapter`
      mevcut `NotImplementedError` stub'ının (bkz. Faz 2.2) yerine,
      `terminal_tool`/`run_command` üzerinden `claude` komutunu çalıştıracak
      şekilde revize edilmesi.
- Not: IoT MCP entegrasyonu Faz 5.1'e ait (aşağıdaki değişikliğe bak).

### 4. `docs/ROADMAP.md` küçük çapraz-referans dokunuşları

- Faz 2.2'deki `ClaudeCodeAdapter` stub bullet'ına, revize planın Faz 4.5'te
  olduğuna dair tek satırlık bir not eklenecek.
- Faz 3.1'deki "bu tool katmanını MCP standardına uygun bir sunucu olarak
  paketleme (opsiyonel)" satırına, bunun (Jarvis'i MCP SUNUCUSU yapmak)
  Faz 4.5'teki MCP CLIENT entegrasyonundan farklı bir yön olduğunu
  netleştiren kısa bir not eklenecek (karışıklığı önlemek için).
- Faz 5.1 (IoT & Uç Nokta) bölümüne, ESP32/mikrodenetleyici sistemlerinin
  IoT MCP sunucuları üzerinden bağlanabileceğine dair bir madde eklenecek
  (mevcut MQTT/VLAN maddeleriyle birlikte, onların yerine değil).

### 5. Doğrulama

- Her iki dosyada da `bkz. §9` / `Faz 4.5` gibi çapraz referansların
  gerçekten karşılığı olduğunu (kırık referans yok) elle kontrol et.
- Mermaid blokları (§2, §3, §4) sözdizimi olarak tutarlı mı diye gözden
  geçir (yeni node/edge'ler mevcut ok yönleriyle çelişmiyor).
- `git diff docs/ROADMAP.md docs/ARCHITECTURE.md` ile son hali gözden
  geçir.
- Kullanıcının orijinal talebi doğrultusunda değişiklikleri commit'le
  (tek commit, "docs:" prefix'i — repo'nun mevcut commit mesajı
  üslubuyla tutarlı, bkz. `git log`). Push YOK — sadece "commit al"
  istendi.

## Değiştirilecek Dosyalar

- `C:\project_JARVIS\docs\ARCHITECTURE.md` (yeni §9 + §7 güncelle)
- `C:\project_JARVIS\docs\ROADMAP.md` (yeni Faz 4.5 + 3 küçük çapraz-referans)

## Uygulama Notu

`docs/ARCHITECTURE.md`'ye yukarıda "zaten yapılmış" olarak listelenen
değişiklikler bu planın hazırlandığı anda dosyada zaten mevcuttu (auto
mode'da yapılmıştı, plan mode'a geçilince durduruldu). Bu plan
uygulanırken önce dosyanın güncel hâli tekrar okunmalı, üzerine
§9/§7 eklemeleri yapılmalı ve `docs/ROADMAP.md` bu plandaki 3. ve 4.
maddelere göre güncellenmeli.
