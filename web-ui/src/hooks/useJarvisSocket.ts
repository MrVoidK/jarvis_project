import { useCallback, useEffect, useRef, useState } from 'react';
import { MAX_LOG_LINES, TOOL_NOTIFICATION_TTL_MS, WS_URL } from '../config';
import type {
  ActiveTool,
  JarvisEvent,
  JarvisState,
  LogEvent,
  StaticSystemInfo,
  TelemetryEvent,
} from '../types';

export type ConnectionStatus = 'connecting' | 'online' | 'offline';

// `LogEvent` + istemci-yerel alanlar: `id` (React key + typewriter'in "bu
// satiri once gordum mu" takibi icin, backend'in ms-cozunurluklu `ts`'i
// COLLISION'a acik oldugundan tekil degil), `fromSnapshot` (baglanti aninda
// gelen GECMIS loglar TYPEWRITER animasyonu OLMADAN aninda basilir - sadece
// bundan SONRA canli gelen satirlar daktilo efekti alir).
export interface DisplayLogEntry extends LogEvent {
  id: number;
  fromSnapshot: boolean;
}

const RECONNECT_DELAY_MS = 2000;

export function useJarvisSocket() {
  const [status, setStatus] = useState<ConnectionStatus>('connecting');
  const [jarvisState, setJarvisState] = useState<JarvisState>('idle');
  const [logs, setLogs] = useState<DisplayLogEntry[]>([]);
  const [telemetry, setTelemetry] = useState<TelemetryEvent | null>(null);
  const [staticInfo, setStaticInfo] = useState<StaticSystemInfo | null>(null);
  const [activeTools, setActiveTools] = useState<ActiveTool[]>([]);

  const socketRef = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<number | undefined>(undefined);
  const nextIdRef = useRef(0);
  // Bilesen unmount olduktan sonra (StrictMode'un cift-mount'u dahil) bir
  // sonraki reconnect denemesinin zombi bir socket acmamasi icin.
  const stoppedRef = useRef(false);

  const appendLog = useCallback((entry: LogEvent, fromSnapshot: boolean) => {
    setLogs((prev) => {
      const withId: DisplayLogEntry = { ...entry, id: nextIdRef.current++, fromSnapshot };
      const next = [...prev, withId];
      return next.length > MAX_LOG_LINES ? next.slice(next.length - MAX_LOG_LINES) : next;
    });
  }, []);

  const handleEvent = useCallback(
    (event: JarvisEvent) => {
      switch (event.type) {
        case 'snapshot': {
          setJarvisState(event.state);
          const snapshotLogs: DisplayLogEntry[] = event.logs
            .slice(-MAX_LOG_LINES)
            .map((log) => ({ ...log, id: nextIdRef.current++, fromSnapshot: true }));
          setLogs(snapshotLogs);
          setStaticInfo(event.static_info);
          break;
        }
        case 'log':
          appendLog(event, false);
          break;
        case 'state':
          setJarvisState(event.state);
          break;
        case 'telemetry':
          setTelemetry(event);
          break;
        case 'tool':
          setActiveTools((prev) => {
            if (event.phase === 'start') {
              return [
                ...prev,
                { name: event.name, params: event.params ?? {}, startedAt: event.ts },
              ];
            }
            // Ayni isimde en son BASLAYAN (henuz bitmemis) girisi kapat -
            // ayni arac ust uste (nadir ama olanakli) cagrilirsa en eski
            // acik cagriyla eslesmesin diye sondan araniyor.
            const idx = [...prev].reverse().findIndex((t) => t.name === event.name && t.finishedAt === undefined);
            if (idx === -1) return prev;
            const realIdx = prev.length - 1 - idx;
            const startedAt = prev[realIdx].startedAt;
            const next = [...prev];
            next[realIdx] = { ...next[realIdx], finishedAt: event.ts, result: event.result };

            // Bittikten TOOL_NOTIFICATION_TTL_MS sonra listeden otomatik
            // dus - aksi halde `activeTools` sinirsiz buyur (her tool
            // cagrisi kalici birikir) ve bildirim widget'i asla kapanmayan
            // eski toast'larla dolardi.
            window.setTimeout(() => {
              setActiveTools((current) =>
                current.filter((t) => !(t.name === event.name && t.startedAt === startedAt)),
              );
            }, TOOL_NOTIFICATION_TTL_MS);

            return next;
          });
          break;
      }
    },
    [appendLog],
  );

  useEffect(() => {
    stoppedRef.current = false;

    function connect() {
      if (stoppedRef.current) return;
      setStatus((prev) => (prev === 'online' ? prev : 'connecting'));
      const socket = new WebSocket(WS_URL);
      socketRef.current = socket;

      socket.onopen = () => setStatus('online');

      socket.onmessage = (message) => {
        try {
          const parsed = JSON.parse(message.data) as JarvisEvent;
          handleEvent(parsed);
        } catch {
          // Bozuk/parse edilemeyen bir frame sessizce atlanir - UI'nin
          // tek bir kotu mesaj yuzunden cokmesindense bir sonraki olayi
          // beklemesi tercih edildi.
        }
      };

      socket.onclose = () => {
        setStatus('offline');
        if (!stoppedRef.current) {
          reconnectTimer.current = window.setTimeout(connect, RECONNECT_DELAY_MS);
        }
      };

      socket.onerror = () => {
        socket.close();
      };
    }

    connect();

    return () => {
      stoppedRef.current = true;
      window.clearTimeout(reconnectTimer.current);
      socketRef.current?.close();
    };
  }, [handleEvent]);

  const sendCommand = useCallback((text: string) => {
    const socket = socketRef.current;
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(text);
    }
  }, []);

  return { status, jarvisState, logs, telemetry, staticInfo, activeTools, sendCommand };
}
