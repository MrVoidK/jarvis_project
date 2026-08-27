import { AnimatePresence, motion } from 'framer-motion';
import type { ActiveTool } from '../types';
import './ToolNotification.css';

interface ToolNotificationProps {
  tools: ActiveTool[];
}

function formatParams(params: Record<string, string>): string {
  const entries = Object.entries(params);
  if (entries.length === 0) return '';
  return entries.map(([k, v]) => `${k}=${v}`).join(' · ');
}

export function ToolNotification({ tools }: ToolNotificationProps) {
  // En yeni en ustte - kullanicinin tetikledigi son eylem her zaman gorunur kalsin.
  const ordered = [...tools].slice(-4).reverse();

  return (
    <div className="jv-tool-toasts">
      <AnimatePresence initial={false}>
        {ordered.map((tool) => {
          const running = tool.finishedAt === undefined;
          const key = `${tool.name}-${tool.startedAt}`;
          return (
            <motion.div
              key={key}
              className={`jv-glass jv-tool-toast ${running ? 'jv-tool-running' : 'jv-tool-done'}`}
              initial={{ opacity: 0, y: -14, scale: 0.96 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, x: 40, scale: 0.94 }}
              transition={{ duration: 0.25, ease: 'easeOut' }}
              layout
            >
              <div className="jv-tool-toast-header">
                <span className={`jv-tool-dot ${running ? 'jv-tool-dot-running' : 'jv-tool-dot-done'}`} />
                <span className="jv-tool-name">{tool.name}</span>
                <span className="jv-tool-phase">{running ? 'ÇALIŞIYOR' : 'TAMAM'}</span>
              </div>
              {formatParams(tool.params) && (
                <div className="jv-tool-params">{formatParams(tool.params)}</div>
              )}
            </motion.div>
          );
        })}
      </AnimatePresence>
    </div>
  );
}
