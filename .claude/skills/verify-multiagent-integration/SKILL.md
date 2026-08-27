---
name: verify-multiagent-integration
description: docs/jarvis-mimari-v2-multiagent-entegrasyon.md'deki Faz A-I planina gore hangi fazin uygulandigini tespit eder, git diff'i o fazin spesifikasyonuyla karsilastirir, ilgili verify-* skill'ini calistirir ve §11'deki sinirlama-cozum eslesmesinin hala dogru olup olmadigini raporlar. Multi-agent/hafiza/execution-mode v2 degisikligi yapildiktan sonra veya "v2 entegrasyonunu dogrula" dendiginde kullan.
disable-model-invocation: false
---

Bu skill, `docs/jarvis-mimari-v2-multiagent-entegrasyon.md` (kisaca **v2
belgesi**) planinin uygulanmasini denetler. Kod DEGISTIRMEZ; sadece
gozlemler ve raporlar. Tum § referanslari v2 belgesine aittir.

## Adim 1 — Hangi faz uygulaniyor? (tespit)

`git diff --stat HEAD` + son commit'i (`git log -1 --stat`) al. Degisen/eklenen
dosyalari v2 §12 "Degisen / Eklenen Dosya Yapisi" ve §13 bagimlilik grafigiyle
esle. Faz -> bolum -> tetikleyici dosya haritasi:

| Faz | Spesifikasyon | Anahtar dosyalar (§12) | Ilgili verify-* skill |
|---|---|---|---|
| **A** | §7.1 `Tool.execute()` stop_event; §7.2 `is_path_safe()` genellestirme | `tools/base.py`, `core/security_config.py`, `core/app.py:_run_tool_pipeline` | yok -> `pytest tests/` + `security-reviewer` subagent |
| **B** | §2.2 model konsolidasyonu (ROLE_MODEL_MAP, Hermes retire); §2.3 router+chat birlesimi; §2.4 `respond_stream()` | `adapters/agent_factory.py`, `agents/base.py`, `brain/llm.py` | **verify-brain-pipeline** (regresyon), sonra **verify-wakeword-pipeline** |
| **C** | §2.5 `ClaudeCodeAdapter` gercek implementasyon; §2.6 `delegate_complex_task`/`delegate_code_task` sentinel'leri | `adapters/agent_factory.py`, `core/dispatcher.py`, `core/app.py:_handle_turn` | **verify-brain-pipeline** (router davranisi + `no_tool_needed` hala gerekli mi) |
| **D** | §3 Agent Registry / manifest (allowlist tabanli) | `core/registry_loader.py` (YENI), `tools/registry.py`, `config/security.yaml` (`enabled_dynamic_agents`) | yok -> `pytest tests/` + `security-reviewer` |
| **E** | §4 Mem0 hafiza katmani (guardrail'den gecmeden yazma/okuma yok) | `core/memory.py` (YENI), `core/app.py` (`recall()`/`remember()`), `requirements.txt` | yok -> `security-reviewer` (§4.3 guardrail) zorunlu |
| **F** | §5 execution modes: scheduler + continuous runner (yalnizca LOW risk otomatik) | `core/scheduler.py`, `core/continuous_runner.py` (YENI), `config/scheduled_tasks.yaml` | yok -> `pytest tests/` + `security-reviewer` (§5.3 kisit) |
| **G** | §6 `CreateProjectTool` (Faz A'daki `is_path_safe` genellestirmesine bagimli) | `tools/project_tool.py`, `tools/subprocess_utils.py` (YENI), `templates/CLAUDE.md.template` | yok -> `security-reviewer` (subprocess spawn + path safety) zorunlu |
| **H** | §8 MCP genisletme: Google Drive (MEDIUM) + Home Assistant (durum okuma MCP, kontrol yerel `iot_tool.py`) | `config/mcp_servers.yaml`, `tools/iot_tool.py` (YENI) | yok -> `security-reviewer` (ilke ayrimi: kontrol MCP'den gecmemeli) |
| **I** | §9 `core/trace.py` — SQLite kalici izleme + `/trace [n]` CLI | `core/trace.py` (YENI), `core/cli_commands.py` | yok -> `pytest tests/` |

Birden fazla faz ayni diff'te varsa hepsini listele ama §13 sirasina gore
en dusuk harfliden basla (A once, cunku B-G ona bagimli). Diff bos veya
sadece belge/test degisikligi ise kullaniciya soyle ve dur.

## Adim 2 — Diff'i faz spesifikasyonuyla karsilastir

Tespit edilen her faz icin v2 belgesinin ilgili bolumunu oku ve diff'i
madde madde denetle. Genel kontrol listesi:

1. **Imza/isim birebir mi** — belge `respond_stream()`, `ROLE_MODEL_MAP`,
   `delegate_complex_task`, `is_path_safe(path, base_dir, *, allow_create=False)`
   gibi somut adlar veriyor; kod bunlarla birebir mi yoksa sapmis mi?
2. **Geriye donuk uyum** — §7.1: `stop_event` varsayilan `None`, mevcut
   tool'lar kirilmamali. §7.2: `allow_create=False` iken yol var olmali.
3. **Guvenlik ilkesi korunmus mu** — §0 tablosu: paylasimli model + rol
   bazli prompt (iki ayri 8B degil); hafiza guardrail'den geciyor;
   scheduled/continuous yalnizca LOW; IoT kontrolu yerel, MCP degil;
   registry allowlist (manifest tek basina aktive etmez).
4. **"Neden" yorumu** — §12'de "YENI" isaretli her dosyada, siradan olmayan
   karar (adapter secimi, fallback, monkeypatch, sentinel) yaninda kisa
   gerekce yorumu var mi (CLAUDE.md kurali).
5. **Kapsam disi sizmasi** — §10'daki bilincli kapsam-disi maddeler (CRITICAL
   risk, AEC, `shell=True` kaldirma, guardrail regex genislemesi) yanlislikla
   bu diff'e girmemeli.
6. **Belge-kod tutarliligi** — `mimari-genel-bakis.md` ile celisen bir sey
   varsa (v2 belgesi: "celisen yer bulunursa ARCHITECTURE.md senkronize
   edilmeli"), hangi belge/bolumun guncellenecegini not et.

Derinlemesine mimari denetim gerekiyorsa `architecture-reviewer` subagent'ini
cagir.

## Adim 3 — Ilgili verify-* skill'ini calistir

Adim 1 tablosunda faz icin bir verify-* skill'i varsa onu calistir:

- **Faz B veya C** -> `verify-brain-pipeline`. Ozellikle §14 devir notu:
  Hermes'e/model konsolidasyonuna gecis router davranisini degistirebilir;
  `no_tool_needed` sentinel'inin **hala gerekli olup olmadigini** bu noktada
  test et (TR ve EN girdiyle, araca ihtiyac duymayan bir cumle router'i
  bir arac cagirmaya zorluyor mu?).
- **Faz B** ek olarak -> `verify-wakeword-pipeline` (tam dongu regresyonu,
  latency loglari).
- verify-* skill'i olmayan fazlar -> `python -m pytest tests/ -v` (bare
  `pytest` degil) ve tablo "zorunlu" diyorsa `security-reviewer` subagent'i.

Skill/test sonucunu (gecti/kaldi + hangi kural) ozete tasi.

## Adim 4 — §11 sinirlama-cozum eslesmesi hala dogru mu?

v2 §11 tablosu, `mimari-genel-bakis.md` §20'deki 12 sinirlamayi v2'nin
nasil ele aldigina esler. Uygulanan fazdan sonra bu tablonun ilgili
satirlari icin **gercek kod durumunu** dogrula:

| # | Sinirlama | §11 iddiasi | Uygulama sonrasi kontrol |
|---|---|---|---|
| 1 | Cift LLM cagrisi gecikmesi | §2.3 mini router -> "Azaltildi" | router+chat birlesimi/mini model gercekten devrede mi? |
| 2 | Chat Agent arayuzunu kullanmiyor | §2.4 `respond_stream()` -> "Cozuldu" | `brain/llm.py` artik `agent.respond_stream()` uzerinden mi? |
| 3 | Multi-agent bagli degil | §2.2/2.5/2.6 -> "Cozuldu" | `ClaudeCodeAdapter` implement edilmis + sentinel'ler dispatcher semasinda mi? |
| 4 | Otonom gorev zinciri yok | §2.6 -> "Kismi" | hala kismi mi, yoksa kapsam disi (§10) mi kaydi? |
| 5 | `Tool.execute()` stop_event yok | §7.1 -> "Cozuldu" | imza + `_run_tool_pipeline` timeout sarmalayici var mi? |
| 6 | `is_path_safe()` genel degil | §7.2 -> "Cozuldu" | UNC/`\\?\` reddi + ad allowlist + `allow_create` var mi? |
| 7-9, 11 | CRITICAL risk / AEC / `shell=True` / guardrail regex | §10 -> "Kapsam disi" | bu diff yanlislikla bunlara dokunmus mu? (dokunmamali) |
| 10 | `no_tool_needed` workaround | §2.2 -> "Azaltildi, dogrulama gerekir" | Adim 3 router testi: sentinel hala gerekli mi? sonucu buraya yaz |
| 12 | VRAM butcesi | §2.2.1 model konsolidasyonu -> "Cozuldu" | tek paylasimli model mi? `agent_factory.py`'de iki ayri 8B yuklenmiyor mu? |

Iddia ile kod uyusmuyorsa (or. §11 "Cozuldu" diyor ama kod hala eski
yolda) bunu **kritik bulgu** olarak isaretle ve v2 §11 tablosunun
guncellenmesi gerektigini soyle.

## Rapor formati

1. **Tespit edilen faz(lar)** — harf + baslik + tetikleyen dosyalar.
2. **Spesifikasyon uyumu** — Adim 2 kontrol listesinden gecti/kaldi
   maddeler, her sapma icin `dosya:satir` + § referansi + onem
   (kritik/uyari/oneri).
3. **verify-* / test sonucu** — hangi skill/test kosuldu, ciktisi.
4. **§11 eslesme durumu** — degisen fazla ilgili satirlar icin
   "hala dogru" / "guncellenmeli (neden)".
5. **Belge senkronu** — `mimari-genel-bakis.md` / `ARCHITECTURE.md` /
   `ROADMAP.md` icinde guncellenmesi gereken bolum var mi.
6. **Sonraki adim** — §13 sirasina gore bir sonraki faz veya eksik is.
