# Jarvis Projesi için Claude Code Kullanım Rehberi

Bu rehber, Jarvis projesini Claude Code ile en verimli şekilde geliştirmen
için hazırlandı. Ekteki `.claude/` iskeletini gerçek reponun köküne
kopyaladıktan sonra bu dosyayı takip et.

Not: "Clide" ile CLI'yi (terminal `claude` komutunu) kastettiğini
varsayıyorum — zaten Jarvis'in kendisi terminal tabanlı olduğu için bu doğru
tercih; Desktop app veya VS Code eklentisi aynı `.claude/` yapılandırmasını
okur ama sen CLI ile ilerleyeceksin.

---

## 1. İlk kurulum

```bash
cd /yol/jarvis-repo
# Ekteki .claude/, CLAUDE.md dosyalarını buraya kopyala/birleştir
claude
```

Oturum içinde ilk iş: `/init` çalıştır. Claude, kod tabanını tarar ve
CLAUDE.md'ye build/test komutları, gerçek klasör yapısı gibi kendi
keşfedemeyeceği ama koddan çıkarabileceği bilgileri ekler/önerir — ekteki
CLAUDE.md'de bıraktığım `<!-- TODO -->` yerlerini bu şekilde doldurabilirsin.
Zaten bir CLAUDE.md varsa `/init` onu ezmez, iyileştirme önerir.

`/context` komutuyla hangi dosyaların (CLAUDE.md, rules, skills) oturuma
gerçekten yüklendiğini her zaman doğrulayabilirsin.

---

## 2. Hafıza sistemi: CLAUDE.md vs Auto Memory

İki tamamlayıcı mekanizma var, ikisi de her oturumun başında yüklenir:

| | CLAUDE.md | Auto Memory |
|---|---|---|
| Kim yazar | Sen | Claude kendi kendine |
| İçerik | Kural/talimat | Öğrenilen tercih, düzeltme, proje bağlamı |
| Kapsam | proje/kullanıcı/org | repo başına, worktree'ler arası paylaşılır |
| Ne için kullan | Build komutları, kod stili, mimari kararlar | Senin verdiğin düzeltmeler, kod'dan çıkarılamayan bağlam |

**Kurallar:**
- CLAUDE.md'yi **200 satırın altında** tut — uzadıkça Claude talimatları
  "unutmaya" başlar. Her satır için sor: "bu olmasa Claude hata yapar mı?"
  Cevap hayırsa sil.
- Sık değişen veya sadece belirli dosya türleriyle ilgili kurallar için
  `.claude/rules/*.md` kullan (ekteki `python-style.md` gibi) — bu kurallar
  sadece ilgili dosyalarla çalışılırken yüklenir, her oturumda değil.
- Auto memory'yi denetlemek için `/memory` komutunu kullan; Claude'un neyi
  "hatırladığını" gör, gerekirse düzenle/sil.
- Kişisel/gitignore'lanacak notlar için `CLAUDE.local.md` (repo köküne, ama
  `.gitignore`'a ekle).

---

## 3. Skills: tekrar eden prosedürleri paketle

Skill = `SKILL.md` içeren bir klasör. Claude, açıklamasına (`description`)
göre otomatik yükler; sen de `/skill-adı` ile elle çağırabilirsin. Custom
command'ler (`.claude/commands/*.md`) artık skill'lerle birleşti, aynı şekilde
çalışıyorlar.

**Ne zaman skill yaz:** Aynı talimatı/checklist'i ikinci kez sohbete
yapıştırıyorsan, ya da CLAUDE.md'deki bir madde aslında çok adımlı bir
prosedüre dönüştüyse.

**İki tip içerik:**
- *Referans içerik*: "API'lerde şu kuralları izle" gibi bilgi — Claude
  konuşma sırasında uygular.
- *Görev içeriği*: "deploy et", "pipeline'ı doğrula" gibi adım adım işlem —
  genelde `disable-model-invocation: true` ile sadece sen tetikleyesin diye
  işaretlenir (Claude'un kendiliğinden deploy etmesini istemezsin).

Ekte `verify-audio-pipeline` skill'i bir örnek: pipeline değiştikten sonra
nasıl çalıştırılıp doğrulanacağını (dogru komut, gecici .wav kontrolü, VRAM
kontrolü) tek yerde topluyor. Gerçek komutları/dosya yollarını güncelle.

**Jarvis için önerilen sıradaki skill'ler** (modüller ilerledikçe):
- `add-tool-integration` — "Sistem Entegrasyonları" adımına başlarken, yeni
  bir tool-calling entegrasyonu eklerken izlenecek standart adımlar
  (arayüz tanımı, hata yönetimi, testler).
- `intent-parsing-conventions` — Modüler Komut Yöneticisi'nde niyet
  şemasının nasıl tanımlandığı, yeni bir komut eklerken nereye dokunulacağı.

Skill'ler `~/.claude/skills/` (tüm projelerinde) veya `.claude/skills/`
(sadece bu projede) altında yaşar; projeye özgü olanları repoya commit'le.

---

## 4. Subagent'lar: bağlamı temiz tut

Claude'un context penceresi doldukça performans düşer. Subagent, ayrı bir
context penceresinde çalışıp sana sadece özet döndürür — arama sonuçları,
loglar, dosya içerikleri ana konuşmanı şişirmez.

Ekte iki örnek subagent var:
- `pipeline-debugger` — ses/CUDA pipeline hatalarını araştırır (CUDA durumu,
  geçici dosya sızıntısı, blok sınırı sorunları, gecikme).
- `security-reviewer` — Jarvis'in terminal komutu çalıştırma / API
  entegrasyonu yüzeylerini inceler (komut enjeksiyonu, ayrıcalık sınırları,
  sır yönetimi). Jarvis kullanıcının sisteminde komut çalıştırabildiği için
  bunu **"Sistem Entegrasyonları" adımına başlar başlamaz proaktif olarak
  kullanmanı öneririm.**

**Ne zaman kullanmalı:**
```
Modüler komut yöneticisini tasarlamadan önce, subagent kullanarak mevcut
intent-parsing yaklaşımlarını (rule-based vs LLM-based vs hybrid)
araştır ve artı/eksilerini özetle.
```
```
Terminal komutu çalıştırma modülünü bitirdikten sonra security-reviewer
subagent'ını kullanarak komut enjeksiyonu riskleri açısından incele.
```

Claude, `description` alanına bakarak ne zaman devreye sokacağına kendi de
karar verebilir; "proactively" gibi ifadeler otomatik delegasyonu teşvik eder
(security-reviewer'ın açıklamasına bunu bilerek ekledim).

---

## 5. Hooks: garantili otomasyon

CLAUDE.md talimatları *tavsiye niteliğindedir* — Claude'un uyacağının garantisi
yok. Hook'lar ise belirli bir noktada **her zaman** çalışan shell komutlarıdır,
Claude'un kararından bağımsız.

Ekteki `.claude/settings.json` + `.claude/hooks/format-python.sh` örneği: her
`Edit`/`Write` sonrası değişen `.py` dosyasını otomatik formatlıyor
(ruff/black varsa). Jarvis için ekleyebileceğin diğer hook fikirleri:

- **PreToolUse**: `.env` veya `secrets/` altına yazmayı tamamen engelle
  (settings.json'daki `deny` kuralları zaten bunu kapsıyor, ama bir hook ile
  ek log/uyarı da ekleyebilirsin).
- **PostToolUse**: audio pipeline dosyaları değiştiğinde otomatik olarak
  ilgili testi çalıştır, sonucu Claude'a geri bildir.
- **Stop**: "bitti" demeden önce test suite'in geçtiğini deterministik olarak
  doğrulayan bir kapı (bkz. Bölüm 7 — doğrulama döngüsü).

`/hooks` komutuyla o an tanımlı hook'ları görebilirsin.

---

## 6. MCP: harici araçlara bağlanmak

MCP (Model Context Protocol), Claude Code'a yerleşik olmayan araçlar
kazandırır (issue tracker, veritabanı, tarayıcı vb.).

```bash
# Örnek: yerel bir sunucu
claude mcp add playwright -- npx -y @playwright/mcp@latest

# Örnek: barındırılan (hosted) bir sunucu
claude mcp add --transport http sentry https://mcp.sentry.dev/mcp
```

**Jarvis'e özgü bir gözlem:** Projenin 3. MVP adımı ("Sistem Entegrasyonları
ve Araçlar") aslında Jarvis'in kendi tool-calling katmanını inşa etmek. Bu
katmanı MCP standardına uygun bir sunucu olarak da tasarlayabilirsin —
böylece hem Jarvis kendi araçlarını kullanır hem de istersen aynı araçları
Claude Code'a bağlayıp (`claude mcp add`) geliştirme sırasında test
edebilirsin. Ayrıca Claude Code'un kendisi de bir MCP sunucusu gibi
kullanılabiliyor ("Use Claude Code as an MCP server") — ileride Jarvis'in
otonom görev zincirine Claude Code'u bir "yürütücü" olarak bağlamak
istersen bu yol açık.

Harici servislerle (GitHub vb.) çalışırken CLI araçlarını (`gh`, `aws`,
`gcloud`) kurman, MCP'den bile daha context-verimli — Claude bunları zaten
biliyor.

---

## 7. Günlük döngü: Keşfet → Planla → Uygula → Doğrula

Yeni bir modül (örn. Modüler Komut Yöneticisi) eklerken:

1. **Keşfet** — Plan mode'a gir (`Shift+Tab` ile `⏸ plan mode on` görene
   kadar, veya `claude --permission-mode plan`). Claude sadece okur,
   değişiklik yapmaz.
   ```
   src/audio ve mevcut pipeline'ı incele, intent parsing için nasıl bir arayüz
   beklediğini anla.
   ```
2. **Planla** — "Detaylı bir uygulama planı oluştur" de, `Ctrl+G` ile planı
   editöründe düzenle.
3. **Uygula** — Plan modundan çık, Claude kodlasın; testleri kendisi
   çalıştırıp düzeltsin.
4. **Doğrula/Commit** — Bulgu varsa `security-reviewer` veya
   `pipeline-debugger` ile ikinci bir göz attır, sonra commit mesajı yazdır.

**Kapsamı net olmayan/küçük değişiklikler için** (typo, tek satır log) plan
mode'u atla, direkt iste.

### Doğrulama döngüsü kritik

Claude "iş bitti" dediğinde bunun gerçekten doğru olduğunu gösterecek bir
mekanizma ver — yoksa tek sinyal "görünüşte bitti" olur. Jarvis için:
- Audio pipeline: örnek `.wav` + beklenen transkript dosyası (ekteki
  `verify-audio-pipeline` skill'i bunu varsayıyor) — `tests/fixtures/`
  altına gerçek bir örnek koy.
- Komut yöneticisi: birkaç örnek komut + beklenen yönlendirme çıktısı.

### Context yönetimi

- İlgisiz görevler arasında `/clear` kullan.
- Aynı hatayı iki kereden fazla düzeltmen gerekiyorsa, `/clear` at ve
  öğrendiklerini içeren daha net bir prompt ile yeniden başla.
- Araştırma gerektiren ama ana konuşmaya girmesi gerekmeyen işler için
  subagent kullan (Bölüm 4).
- `/rewind` (veya çift `Esc`) ile önceki bir checkpoint'e dön — konuşmayı,
  kodu veya ikisini birden geri alabilirsin.

---

## 8. İzin modları ve güvenlik

Jarvis, tanımı gereği terminal komutu çalıştıran bir sistem olduğu için izin
modlarını bilinçli seçmek önemli:

- **Manual (default) mode**: her dosya yazma/komut için onay ister — "Sistem
  Entegrasyonları" modülünü ilk yazarken güvenli başlangıç noktası.
- **Plan mode**: sadece okur, hiç değişiklik yapmaz.
- **Auto mode**: bir sınıflandırıcı model riskli görünen eylemleri (kapsam
  aşımı, bilinmeyen altyapı) engeller, geri kalanı onaysız geçer — günlük
  rutin işler için hızlandırır.
- **bypassPermissions**: tüm onayları atlar — Jarvis gibi komut çalıştıran
  bir proje için genel oturumda **önerilmez**; en fazla izole bir worktree/
  container içinde, bilinçli olarak kullan.

Ekteki `settings.json`'daki `deny` kuralları (`.env`, `secrets/**`) bir
başlangıç; gerçek sır dosyalarının yollarına göre güncelle.

---

## 9. Worktree'ler ile paralel modül geliştirme

MVP'nin 4 adımı büyük ölçüde birbirinden ayrıştırılabilir. Örneğin "Modüler
Komut Yöneticisi" ile "Sistem Entegrasyonları"nı aynı anda, birbirini
etkilemeden ilerletmek istersen:

```
Bu özellik için ayrı bir git worktree'de çalışmak istiyorum, oluştur.
```

Her worktree kendi izole checkout'una sahip olur, değişiklikler çakışmaz.
Masaüstü uygulaması bunu görsel olarak da yönetir (birden fazla oturumu aynı
anda izleme).

---

## 10. Ölçeklendirme (ileride işine yarayacak)

- **Headless/otomasyon**: `claude -p "prompt" --output-format json` — CI'da
  veya bir script içinde Jarvis'in kendi test setini toplu çalıştırmak gibi
  işler için.
- **Writer/Reviewer deseni**: bir oturum kod yazsın, ayrı bir oturum (temiz
  context ile) gözden geçirsin — subagent'lardan farklı olarak burada iki
  ayrı `claude` süreci kullanılır.
- **Adversarial review**: bir işi "bitti" saymadan önce, farklı bir subagent'a
  sadece diff'i ve kriterleri vererek gözden geçirt (`/code-review` bunu
  hazır sağlıyor).

---

## 11. Hızlı komut tablosu

| Komut | Ne işe yarar |
|---|---|
| `/init` | Kod tabanından CLAUDE.md taslağı/iyileştirmesi üretir |
| `/context` | Bu oturuma ne yüklendiğini gösterir (CLAUDE.md, rules, skills) |
| `/memory` | CLAUDE.md ve auto memory dosyalarını listeler/açar |
| `/clear` | Context'i sıfırlar |
| `/compact [talimat]` | Context'i özetleyerek daraltır |
| `/rewind` | Önceki bir checkpoint'e döner (konuşma/kod/ikisi) |
| `/mcp` | Bağlı MCP sunucularını yönetir |
| `/hooks` | Tanımlı hook'ları listeler |
| `/skills` | Kullanılabilir skill'leri listeler |
| `/agents` (eski) | Artık sihirbaz açmıyor — subagent için Claude'a yazdır veya `.claude/agents/` dosyasını elle düzenle |
| `Shift+Tab` | Plan mode'a geçiş |
| `Ctrl+G` (plan modunda) | Planı editörde düzenle |

---

## 12. Sıradaki somut adımlar

1. Ekteki `.claude/` ve `CLAUDE.md`'yi gerçek Jarvis reposunun köküne kopyala.
2. `claude` başlat, `/init` çalıştır, TODO'ları gerçek komut/klasör bilgisiyle
   doldur.
3. `tests/fixtures/` altına gerçek bir örnek `.wav` + beklenen transkript koy
   (verify-audio-pipeline skill'i bunu kullanacak).
4. Modüler Komut Yöneticisi'ne başlarken: plan mode + Explore subagent ile
   önce mevcut intent-parsing yaklaşımlarını araştır, sonra planı onaylayıp
   uygula.
5. Terminal komutu çalıştırma özelliği eklendiği an `security-reviewer`
   subagent'ını çalıştır — bu proje için erteleme.
