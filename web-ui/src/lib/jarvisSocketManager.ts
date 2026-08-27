import { WS_URL } from '../config';
import type { JarvisEvent } from '../types';

export type ConnectionStatus = 'connecting' | 'online' | 'offline';

const RECONNECT_DELAY_MS = 2000;

/**
 * MODUL-SEVIYESI TEKIL (singleton) WebSocket yoneticisi.
 *
 * NEDEN GEREKLI (kullanici bulgusu: ayni log/tool olayi UI'da ust uste
 * onlarca kez tekrarlaniyordu - "/help" ciktisinin cok kez basilmasi
 * dahil): baglanti yonetimi eskiden dogrudan `useJarvisSocket()` hook'unun
 * `useEffect`'i icindeydi. React 18 `StrictMode` (main.tsx) gelistirme
 * modunda HER effect'i BIR KEZ FAZLADAN calistirir (mount -> cleanup ->
 * yeniden mount) - `WebSocket.close()` cagrisi ANINDA degil ASENKRON
 * tamamlandigi icin, ilk soket sunucuya kayit olmayi (bir hud_bus
 * abonesi haline gelmeyi) bitirebiliyor, ikinci soket de ayrica kayit
 * oluyor; sonuc: TEK sayfa yuklemesinde birden fazla aktif WebSocket
 * abonesi, her `publish()` N kez UI'a ulasiyor. Ayrica component
 * mount/unmount dongusunden TAMAMEN bagimsiz, sayfa yasam suresi boyunca
 * TEK bir baglanti kurulmasi gerekiyor (birden fazla bilesen ayni HUD
 * verisine ihtiyac duysa bile).
 *
 * Bu sinif, baglanti YASAM DONGUSUNU React'in render/effect dongusunden
 * tamamen AYIRIYOR: `start()` idempotent (`_started` bayragiyla), zaten
 * baglanmis/baglanmaya calisiyorsa hicbir sey yapmaz - StrictMode'un
 * cift-mount'u burada sadece dinleyici ekleyip cikarir (zararsiz),
 * WebSocket'in kendisine hic dokunmaz.
 */
class JarvisSocketManager {
  private socket: WebSocket | null = null;
  private status: ConnectionStatus = 'connecting';
  private reconnectTimer: number | undefined;
  private started = false;
  private eventListeners = new Set<(event: JarvisEvent) => void>();
  private statusListeners = new Set<(status: ConnectionStatus) => void>();

  start(): void {
    if (this.started) return;
    this.started = true;
    this.connect();
  }

  private connect(): void {
    const socket = new WebSocket(WS_URL);
    this.socket = socket;

    socket.onopen = () => this.setStatus('online');

    socket.onmessage = (message: MessageEvent<string>) => {
      try {
        const parsed = JSON.parse(message.data) as JarvisEvent;
        this.eventListeners.forEach((listener) => listener(parsed));
      } catch {
        // Bozuk/parse edilemeyen bir frame sessizce atlanir.
      }
    };

    socket.onclose = () => {
      this.setStatus('offline');
      window.clearTimeout(this.reconnectTimer);
      this.reconnectTimer = window.setTimeout(() => this.connect(), RECONNECT_DELAY_MS);
    };

    socket.onerror = () => socket.close();
  }

  private setStatus(status: ConnectionStatus): void {
    this.status = status;
    this.statusListeners.forEach((listener) => listener(status));
  }

  getStatus(): ConnectionStatus {
    return this.status;
  }

  onEvent(listener: (event: JarvisEvent) => void): () => void {
    this.eventListeners.add(listener);
    return () => this.eventListeners.delete(listener);
  }

  onStatus(listener: (status: ConnectionStatus) => void): () => void {
    this.statusListeners.add(listener);
    return () => this.statusListeners.delete(listener);
  }

  send(text: string): void {
    if (this.socket && this.socket.readyState === WebSocket.OPEN) {
      this.socket.send(text);
    }
  }
}

// Modul-seviyesinde TEK bir ornek - import eden her yer (bugun sadece
// useJarvisSocket) AYNI baglantiyi paylasir.
export const jarvisSocket = new JarvisSocketManager();
