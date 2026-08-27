import { motion } from 'framer-motion';
import type { StaticSystemInfo, TelemetryEvent } from '../types';
import { RingGauge } from './RingGauge';
import './SystemMonitor.css';

interface SystemMonitorProps {
  telemetry: TelemetryEvent | null;
  staticInfo: StaticSystemInfo | null;
}

function formatKbps(value: number | null): string {
  if (value === null) return '--';
  if (value >= 1024) return `${(value / 1024).toFixed(1)} MB/s`;
  return `${value.toFixed(0)} KB/s`;
}

export function SystemMonitor({ telemetry, staticInfo }: SystemMonitorProps) {
  return (
    <motion.div
      className="jv-glass jv-sysmon"
      initial={{ opacity: 0, x: -24 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.5, ease: 'easeOut' }}
    >
      <div className="jv-panel-title">Sistem Telemetrisi</div>

      <div className="jv-sysmon-gauges">
        <RingGauge
          label="CPU"
          value={telemetry?.cpu_percent ?? null}
          max={100}
          displayValue={`${Math.round(telemetry?.cpu_percent ?? 0)}%`}
        />
        <RingGauge
          label="RAM"
          value={telemetry?.ram_percent ?? null}
          max={100}
          displayValue={`${Math.round(telemetry?.ram_percent ?? 0)}%`}
        />
        <RingGauge
          label="GPU"
          value={telemetry?.gpu_util_percent ?? null}
          max={100}
          displayValue={`${Math.round(telemetry?.gpu_util_percent ?? 0)}%`}
        />
      </div>

      <div className="jv-sysmon-net">
        <div className="jv-sysmon-net-row">
          <span className="jv-sysmon-net-arrow">▲</span>
          <span>{formatKbps(telemetry?.net_up_kbps ?? null)}</span>
        </div>
        <div className="jv-sysmon-net-row">
          <span className="jv-sysmon-net-arrow jv-sysmon-net-down">▼</span>
          <span>{formatKbps(telemetry?.net_down_kbps ?? null)}</span>
        </div>
        {telemetry?.gpu_vram_used_mb != null && (
          <div className="jv-sysmon-net-row">
            <span className="jv-sysmon-net-arrow">VRAM</span>
            <span>{(telemetry.gpu_vram_used_mb / 1024).toFixed(2)} GB</span>
          </div>
        )}
      </div>

      <div className="jv-sysmon-static">
        <div className="jv-sysmon-static-row">
          <span>CPU</span>
          <span title={staticInfo?.cpu_model}>{staticInfo?.cpu_model ?? '--'}</span>
        </div>
        <div className="jv-sysmon-static-row">
          <span>Çekirdek</span>
          <span>
            {staticInfo?.cpu_cores_physical ?? '--'} / {staticInfo?.cpu_cores_logical ?? '--'}
          </span>
        </div>
        <div className="jv-sysmon-static-row">
          <span>RAM Toplam</span>
          <span>{staticInfo?.ram_total_gb ?? '--'} GB</span>
        </div>
      </div>
    </motion.div>
  );
}
