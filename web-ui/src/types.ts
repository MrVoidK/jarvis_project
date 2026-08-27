// Backend sozlesmesi: src/jarvis/core/hud_bus.py'nin yayinladigi dict'lerin
// AYNISI - alan adlari kasitli olarak Python tarafiyla birebir (ek bir
// donusum/mapping katmani cikarmamak icin, tek gercek kaynak backend'de).

export type JarvisState = 'idle' | 'listening' | 'processing' | 'speaking';

export type LogKind =
  | 'info'
  | 'success'
  | 'pass'
  | 'warn'
  | 'error'
  | 'user'
  | 'agent'
  | 'prompt'
  | 'route'
  | 'panel';

export interface LogEvent {
  type: 'log';
  ts: number;
  kind: LogKind;
  message: string;
  title?: string;
}

export interface StateEvent {
  type: 'state';
  ts: number;
  state: JarvisState;
}

export interface TelemetryEvent {
  type: 'telemetry';
  ts: number;
  cpu_percent: number;
  ram_percent: number;
  gpu_util_percent: number | null;
  gpu_vram_used_mb: number | null;
  net_up_kbps: number | null;
  net_down_kbps: number | null;
}

export interface ToolEvent {
  type: 'tool';
  ts: number;
  phase: 'start' | 'end';
  name: string;
  params?: Record<string, string>;
  result?: string;
}

export interface StaticSystemInfo {
  cpu_model: string;
  cpu_cores_physical: number | null;
  cpu_cores_logical: number | null;
  ram_total_gb: number;
}

export interface SnapshotEvent {
  type: 'snapshot';
  ts: number;
  state: JarvisState;
  logs: LogEvent[];
  static_info: StaticSystemInfo;
}

export type JarvisEvent = LogEvent | StateEvent | TelemetryEvent | ToolEvent | SnapshotEvent;

export interface ActiveTool {
  name: string;
  params: Record<string, string>;
  startedAt: number;
  finishedAt?: number;
  result?: string;
}
