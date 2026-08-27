# JARVIS HUD (web-ui)

Retro-fütüristik, kehribar (amber) temalı web arayüzü - `src/jarvis/core/api.py`
(FastAPI + WebSocket) üzerinden Jarvis'in canlı durumuna (idle/listening/
processing/speaking), sistem konsoluna, telemetriye (CPU/RAM/GPU) ve
araç bildirimlerine bağlanır. Detaylı mimari için kök `CLAUDE.md` ve
`docs/ARCHITECTURE.md`'ye bakın.

## Çalıştırma

1. Backend: proje kökünden `python main.py` (HUD API'yi `127.0.0.1:8000`'de
   otomatik başlatır - ayrıca çalıştırmaya gerek yok).
2. Frontend (bu klasörden):

   ```bash
   npm install
   npm run dev
   ```

   `http://127.0.0.1:5173` adresini açın - `vite.config.ts`'de host/port
   sabit (`strictPort`), çünkü backend'in WebSocket Origin izin listesi
   (`core/api.py:_ALLOWED_ORIGINS`) bu adresle eşleşiyor.

## Yapı

- `src/hooks/useJarvisSocket.ts` - WebSocket bağlantısı, otomatik yeniden
  bağlanma, olay durumu (log/state/telemetry/tool).
- `src/components/HologramOrb.tsx` - three.js ile 3B holografik durum küresi.
- `src/components/Terminal.tsx` - daktilo efektli sistem konsolu + komut girişi.
- `src/components/SystemMonitor.tsx` - CPU/RAM/GPU halka göstergeleri.
- `src/components/ToolNotification.tsx` - geçici araç-kullanım bildirimleri.
