import { useCallback, useEffect, useRef, useState } from 'react';
import { MAX_LOG_LINES, TOOL_NOTIFICATION_TTL_MS } from '../config';
import { jarvisSocket, type ConnectionStatus } from '../lib/jarvisSocketManager';
import type {
  ActiveTool,
  JarvisEvent,
  JarvisState,
  LogEvent,
  StaticSystemInfo,
  TelemetryEvent,
} from '../types';

export type { ConnectionStatus };

// `LogEvent` + istemci-yerel alanlar: `id` (React key + typewriter'in "bu
// satiri once gordum mu" takibi icin, backend'in ms-cozunurluklu `ts`'i
// COLLISION'a acik oldugundan tekil degil), `fromSnapshot` (baglanti aninda
// gelen GECMIS loglar TYPEWRITER animasyonu OLMADAN aninda basilir - sadece
// bundan SONRA canli gelen satirlar daktilo efekti alir).
export interface DisplayLogEntry extends LogEvent {
  id: number;
  fromSnapshot: boolean;
}

export function useJarvisSocket() {
  const [status, setStatus] = useState<ConnectionStatus>(jarvisSocket.getStatus());
  const [jarvisState, setJarvisState] = useState<JarvisState>('idle');
  const [logs, setLogs] = useState<DisplayLogEntry[]>([]);
  const [telemetry, setTelemetry] = useState<TelemetryEvent | null>(null);
  const [staticInfo, setStaticInfo] = useState<StaticSystemInfo | null>(null);
  const [activeTools, setActiveTools] = useState<ActiveTool[]>([]);

  const nextIdRef = useRef(0);

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
    // Idempotent - StrictMode'un cift-mount'u burada zararsiz: singleton
    // zaten baglanmissa `start()` no-op, sadece dinleyici ekleyip cikariyoruz
    // (bkz. lib/jarvisSocketManager.ts docstring'i).
    jarvisSocket.start();
    const unsubEvent = jarvisSocket.onEvent(handleEvent);
    const unsubStatus = jarvisSocket.onStatus(setStatus);
    setStatus(jarvisSocket.getStatus());
    return () => {
      unsubEvent();
      unsubStatus();
    };
  }, [handleEvent]);

  const sendCommand = useCallback((text: string) => jarvisSocket.send(text), []);

  return { status, jarvisState, logs, telemetry, staticInfo, activeTools, sendCommand };
}
