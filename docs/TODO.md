# Yarın Yapılacaklar — Gerçek Kullanım Testinden Çıkan Sorunlar

Bu liste, 2026-08-26 tarihli iki uzun canlı mikrofon test oturumunun terminal
loglarının analizinden çıkarıldı. Kod değişikliği YAPILMADI — sadece kayıt.

## 1. [DOĞRULANDI] `run_command` sondaki noktalama işaretini komuta dahil ediyor

**Gözlem:** "Run command ls." dendiğinde dispatcher `content="ls."` yakaladı
(sondaki nokta dahil), komut Windows'ta `ls.` olarak çalıştırılmaya çalışıldı
ve "'ls.' is not recognized..." hatası döndü.

**Kök neden:** `tools/shell.py:RunCommandTool.execute()`, `tools/spotify.py:
_clean_query()`'nin yaptığı sondaki noktalama temizliğini yapmıyor —
`command = (params.get("content") or "").strip()` sadece baştaki/sondaki
boşluğu siliyor, noktalama işaretini değil.

**Önerilen düzeltme:** `_clean_query`'deki `_TRAILING_PUNCT_RE` mantığını
paylaşılan bir yere (örn. yeni bir `core/text.py` ya da `core/dispatcher.py`
içinde küçük bir yardımcı fonksiyon) çıkarıp hem `tools/spotify.py` hem
`tools/shell.py`'nin kullanmasını sağla. **Dikkat:** `tools/notes.py`'ye
uygulama — bir notun içeriğinde noktalama işareti silinmemeli, bu sadece
"komut olarak çalıştırılacak" içerikler için gerekli.

## 2. [NET TEST GEREKİYOR] EN "pause music" / "skip track" çalışmıyor gibi görünüyor

**Gözlem:** Kullanıcı bildirdi: "pause music ve skip track deyince
çalışmıyor ama türkçelerini deyince çalışıyor." Ancak bu oturumun loglarında
kullanıcı literal "pause music" ya da "skip track" demedi — sadece "Pass
music." denendi (muhtemelen Whisper "pause"u "pass" diye yanlış transkribe
etti) ve regex `\bpause\b...` "pass" ile eşleşmediği için normal sohbete
düştü (beklenen davranış, çünkü gerçekten "pause" değil "pass" transkribe
edildi).

**Yarın yapılacak:** Net ve yavaş telaffuzla "pause music" ve "skip track"
ayrı ayrı tekrar denenmeli. Eğer bu net denemede de eşleşmiyorsa
`core/dispatcher.py`'deki `pause_music`/`skip_track` EN kalıplarında gerçek
bir regex hatası var demektir — o zaman düzeltilmeli. Eşleşiyorsa sorun
sadece STT (Whisper'ın "pause"u yanlış duyması) idi; bu durumda "pass
music"i de `pause_music`'in TOLERANS'lı bir varyantı olarak kabul etmek
düşünülebilir (STT hatasına karşı esneklik).

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

## 4. [EK GÖZLEM, düşük öncelik] Spotify bazen bariz yanlış şarkı buluyor

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

**Önerilen düzeltme yönü:** `_LEADING_FILLER_RE`'ye "this"/"that"/"some"
ekle; `limit`'i artırıp (örn. 5) dönen sonuçlar arasından `popularity` alanı
en yüksek olanı seçmeyi değerlendir.
