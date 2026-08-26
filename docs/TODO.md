# Yarın Yapılacaklar — Gerçek Kullanım Testinden Çıkan Sorunlar

Bu liste, 2026-08-26 tarihli iki uzun canlı mikrofon test oturumunun terminal
loglarının analizinden çıkarıldı.

**Durum (2026-08-26 takibi):** Madde 1, 2 ve 4 kodda düzeltildi + regresyon
testi eklendi (`tests/test_tools.py`, `python -m pytest tests/ -v` → 34/34
yeşil). Madde 3 bilinçli tasarım kararı olduğu için dokunulmadı.

## 1. [DÜZELTİLDİ] `run_command` sondaki noktalama işaretini komuta dahil ediyor

**Gözlem:** "Run command ls." dendiğinde dispatcher `content="ls."` yakaladı
(sondaki nokta dahil), komut Windows'ta `ls.` olarak çalıştırılmaya çalışıldı
ve "'ls.' is not recognized..." hatası döndü.

**Kök neden:** `tools/shell.py:RunCommandTool.execute()`, `tools/spotify.py:
_clean_query()`'nin yaptığı sondaki noktalama temizliğini yapmıyor —
`command = (params.get("content") or "").strip()` sadece baştaki/sondaki
boşluğu siliyor, noktalama işaretini değil.

**Uygulanan düzeltme:** `_clean_query`'deki noktalama temizleme mantığı yeni
`core/text.py:strip_trailing_punct()`'a çıkarıldı; hem `tools/spotify.py`
hem `tools/shell.py:RunCommandTool.execute()` bunu kullanıyor.
`tools/notes.py`'ye bilinçli olarak DOKUNULMADI — bir notun içeriğinde
noktalama işareti silinmemeli.

## 2. [ÇÖZÜLDÜ — REGEX'TE BUG YOKTU] EN "pause music" / "skip track" çalışmıyor gibi görünüyor

**Gözlem:** Kullanıcı bildirdi: "pause music ve skip track deyince
çalışmıyor ama türkçelerini deyince çalışıyor." Ancak bu oturumun loglarında
kullanıcı literal "pause music" ya da "skip track" demedi — sadece "Pass
music." denendi (muhtemelen Whisper "pause"u "pass" diye yanlış transkribe
etti) ve regex `\bpause\b...` "pass" ile eşleşmediği için normal sohbete
düştü (beklenen davranış, çünkü gerçekten "pause" değil "pass" transkribe
edildi).

**Doğrulama:** Mikrofon/STT'yi devre dışı bırakıp `Dispatcher.match_rule()`'a
doğrudan `"pause music"`, `"Pause music."`, `"skip track"`, `"Skip track."`
metinleri verildi — hepsi doğru intent'e (`pause_music`/`skip_track`,
`lang="en"`) eşleşti. Yani regex'te bug YOKTU; gerçek test oturumundaki sorun
sadece STT'nin "pause"u "pass" diye yanlış transkribe etmesiydi.

**Uygulanan düzeltme:** `pause_music`'in EN kalıbına, "pass" bare kelime
olarak DEĞİL (çok yaygın kelime, yanlış pozitif riski yüksek — "I'll pass on
that" gibi cümleleri yanlışlıkla tetikler) ama "pass music"/"pass the song"
gibi açıkça müzikle birlikte geçtiğinde eşleşen ayrı bir alternatif eklendi
(`core/dispatcher.py`). Regresyon testi:
`test_dispatcher_pause_music_tolerates_pass_mishearing`.

## 3. [BUG DEĞİL, DAVRANIŞ AÇIKLAMASI] "Dosyaları listele" hep boş dönüyor

**Gözlem:** "Dosyaları listele." dendiğinde "Calisma dizininiz bos." cevabı
geldi.

**Açıklama:** Bu bir hata DEĞİL. `list_files` tool'u bilinçli bir Zero-Trust
tasarım kararıyla (bkz. `docs/ROADMAP.md` Faz 3.1, security-reviewer
incelemesi) proje kökünü ya da rastgele bir sistem dizinini DEĞİL, sadece
izole `jarvis_workspace/` klasörünü listeliyor. O klasör şu an gerçekten boş
olduğu için cevap doğru. Bir dosya konursa (`jarvis_workspace/` içine)
listelenecektir.

**Yarın yapılacak (opsiyonel, davranış değişikliği isteniyorsa):** Eğer
kullanıcı bunun yerine proje dosyalarını ya da başka bir dizini listelemesini
istiyorsa, bu bilinçli bir kapsam genişletme kararı olur (yeni izin verilen
dizin(ler) tanımlanmalı) — mevcut haliyle "bug" değil, "beklenmedik ama
kasıtlı davranış".

## 4. [DÜZELTİLDİ] Spotify bazen bariz yanlış şarkı buluyor

**Gözlem:** "Play Back in Black." → "Iron Man - Black Sabbath" çaldı (AC/DC'nin
"Back in Black"i değil); "Play this should I stay or should I go?" → "This
Charming Man - Nouvelle Vague" çaldı. Aynı istekler "ACDC" eklenince veya
"this" kelimesi olmadan söylenince doğru şarkıyı buldu.

**Olası nedenler:**
- `tools/spotify.py:_clean_query()`'deki `_LEADING_FILLER_RE` listesi
  "this"/"that"/"some" gibi kelimeleri kapsamıyor — bu kelimeler arama
  sorgusuna karışıp alakasız sonuçlara yol açabiliyor.
- `client.search(q=query, type="track", limit=1)` sadece 1 sonuç istiyor ve
  ona kör kör güveniyor; Spotify'ın kendi sıralaması her zaman "en bilinen"
  şarkıyı ilk sıraya koymayabiliyor.

**Uygulanan düzeltme:** `_LEADING_FILLER_RE`'ye "this"/"that"/"some" eklendi;
`client.search(...)` `limit=1` yerine `limit=5` ile çağrılıp dönen adaylar
arasından `popularity` alanı en yüksek olan seçiliyor
(`tools/spotify.py:PlayMusicTool.execute()`). Regresyon testleri:
`test_clean_query_strips_this_that_some_filler`,
`test_play_music_picks_most_popular_result`.
