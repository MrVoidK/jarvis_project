import { motion } from 'framer-motion';
import { useEffect, useState } from 'react';
import type { ConnectionStatus } from '../hooks/useJarvisSocket';
import type { JarvisState } from '../types';
import './StatusBar.css';

const STATUS_LABEL: Record<ConnectionStatus, string> = {
  online: 'ÇEVRİMİÇİ',
  connecting: 'BAĞLANIYOR',
  offline: 'ÇEVRİMDIŞI',
};

function useClock(): string {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(id);
  }, []);
  return now.toLocaleTimeString('tr-TR', { hour12: false });
}

interface StatusBarProps {
  status: ConnectionStatus;
  jarvisState: JarvisState;
}

export function StatusBar({ status, jarvisState }: StatusBarProps) {
  const clock = useClock();

  return (
    <motion.header
      className="jv-glass jv-statusbar"
      initial={{ opacity: 0, y: -16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.45, ease: 'easeOut' }}
    >
      <div className="jv-statusbar-brand jv-glow-text">J.A.R.V.I.S.</div>
      <div className="jv-statusbar-mid">Personal Autonomous Assistant System</div>
      <div className="jv-statusbar-right">
        <span className="jv-statusbar-state">{jarvisState.toUpperCase()}</span>
        <span className={`jv-statusbar-pill jv-statusbar-pill-${status}`}>
          <span className="jv-statusbar-pill-dot" />
          {STATUS_LABEL[status]}
        </span>
        <span className="jv-statusbar-clock">{clock}</span>
      </div>
    </motion.header>
  );
}
