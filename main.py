from src.jarvis.core.console import print_boot_sequence, print_system, setup_logging, status_spinner

# Root logger'i EN ERKEN noktada rich'e baglaniyor - hemen asagidaki Ears/Mouth
# import'lari modul-seviyesinde GERCEK model yuklemesi tetikliyor (bkz. asagidaki
# yorum) ve o esnada logger.info(...) cagrilari calisiyor; bu satir onlardan
# once calismazsa o loglar RichHandler'siz (varsayilan/formatlar) basilirdi.
setup_logging()

print_boot_sequence()
print_system("J.A.R.V.I.S. başlatılıyor...", level="info")

# --- Sinematik acilis ekrani + gercek alt-sistem yuklemesi ---
# Ears (faster-whisper + openWakeWord) ve Mouth (XTTS-v2), kendi modullerinin
# EN USTUNDE (import zamaninda, fonksiyon cagrisini beklemeden) yukleniyor -
# bkz. src/jarvis/ears/listener.py ve src/jarvis/mouth/tts.py. Eskiden bu importlar
# core.app'in KENDI import zincirinin icinde, run_jarvis()'in "=== ONLINE ==="
# banner'indan SONRA yaziliyormus gibi gorunen ama aslinda cok daha once, sessizce
# gerceklesiyordu - banner hicbir sey yuklenmeden basiliyordu (yanıltıcı). Burada
# ONCE Ears'i, SONRA Mouth'u ACIKCA (ayri ayri, kendi spinner'lariyla) import
# ederek gercek yukleme sirasini gorunur kiliyoruz; core.app daha sonra bu ayni
# modulleri import ettiginde Python sys.modules cache'i sayesinde YENIDEN
# yuklenmezler (bedelsiz).
with status_spinner("[EARS] faster-whisper + openWakeWord yükleniyor..."):
    import src.jarvis.ears.listener  # noqa: F401 - side effect: model yukleme

with status_spinner("[MOUTH] XTTS-v2 yükleniyor..."):
    import src.jarvis.mouth.tts  # noqa: F401 - side effect: model yukleme

with status_spinner("[BRAIN] Ollama bağlantısı kontrol ediliyor..."):
    from src.jarvis.adapters.agent_factory import check_ollama_connection

    _brain_ok, _brain_message = check_ollama_connection()
print_system(_brain_message, level="success" if _brain_ok else "warning")

with status_spinner("[HUD] Web arayüzü köprüsü (FastAPI) başlatılıyor..."):
    from src.jarvis.core.api import API_HOST, API_PORT, start_api_server_thread

    # Daemon thread: uvicorn'un KENDI asyncio event loop'u burada, ana
    # thread'den (Ears/Mouth/Brain'in senkron/bloklayici cagrilarinin
    # calistigi yer) TAMAMEN AYRI calisir - bkz. core/api.py modul
    # docstring'i "NEDEN ASYNC/FASTAPI TEK BURADA".
    start_api_server_thread()
print_system(f"HUD API http://{API_HOST}:{API_PORT}/ws adresinde dinliyor.", level="success")

# Guardrail icin gercek bir "yukleme" adimi yok (model degil, saf Python kural
# zinciri - bkz. core/guardrail/base.py) - simetri icin sadece durum bildirimi.
print_system("Guardrail sistemleri aktif.", level="success")
print_system("Tüm sistemler hazır, Jarvis dinliyor.", level="success")

from src.jarvis.core.app import run_jarvis

if __name__ == "__main__":
    run_jarvis()
