import { motion } from 'framer-motion';
import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react';
import type { LogKind } from '../types';
import type { DisplayLogEntry } from '../hooks/useJarvisSocket';
import './Terminal.css';

const KIND_META: Record<LogKind, { label: string; className: string }> = {
  info: { label: '[i]', className: 'jv-kind-info' },
  success: { label: '[OK]', className: 'jv-kind-success' },
  pass: { label: '[PASS]', className: 'jv-kind-success' },
  warn: { label: '[!]', className: 'jv-kind-warn' },
  error: { label: '[X]', className: 'jv-kind-error' },
  user: { label: 'SEN >', className: 'jv-kind-user' },
  agent: { label: 'JARVIS >', className: 'jv-kind-agent' },
  prompt: { label: '[ONAY]', className: 'jv-kind-prompt' },
  route: { label: '[ROUTER]', className: 'jv-kind-route' },
  panel: { label: '[PANEL]', className: 'jv-kind-panel' },
};

const TYPEWRITER_MS_PER_CHAR = 8;

function TerminalLine({ entry, skipAnimation }: { entry: DisplayLogEntry; skipAnimation: boolean }) {
  const [revealed, setRevealed] = useState(skipAnimation ? entry.message.length : 0);
  const meta = KIND_META[entry.kind] ?? KIND_META.info;
  const isGlitch = entry.kind === 'error';
  // Coklu-satirli icerik (ör. print_table/print_panel'in tum ciktisi) TEK
  // satirlik "SEN >"/"[i]" duzeniyle AYNI yatay flex-row'a sikistirilinca
  // okunmaz hale geliyordu (kullanici bulgusu: "/help UI'da bozuk cikiyor")
  // - bunun yerine ayri, dikey (blok) bir duzen kullaniliyor.
  const isMultiline = entry.message.includes('\n');

  useEffect(() => {
    if (skipAnimation) return;
    if (entry.message.length === 0) return;
    let frame = 0;
    const step = () => {
      frame += 1;
      setRevealed(frame);
      if (frame < entry.message.length) {
        timer = window.setTimeout(step, TYPEWRITER_MS_PER_CHAR);
      }
    };
    let timer = window.setTimeout(step, TYPEWRITER_MS_PER_CHAR);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const text = entry.message.slice(0, revealed);
  const done = revealed >= entry.message.length;

  if (isMultiline) {
    const lines = text.split('\n');
    // Tum satirlar " | " ile ayrilmis hucrelere sahipse (print_table
    // ciktisi, ilk satir basliklar) gercek bir HTML tablosu olarak
    // render ediliyor - sadece TAM reveal olduktan sonra (yazilirken
    // ara satirlar tutarsiz hucre sayisina sahip olabilir).
    const isTabular = done && lines.length > 1 && lines.every((line) => line.includes(' | '));

    return (
      <motion.div
        className={`jv-term-block ${meta.className} ${isGlitch ? 'jv-term-glitch' : ''}`}
        initial={{ opacity: 0, y: -4 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.2 }}
      >
        <div className="jv-term-block-header">
          <span className="jv-term-kind">{meta.label}</span>
          {entry.title && <span className="jv-term-title">{entry.title}</span>}
        </div>
        {isTabular ? (
          <table className="jv-term-table">
            <thead>
              <tr>
                {lines[0].split(' | ').map((cell, i) => (
                  <th key={i}>{cell}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {lines.slice(1).map((line, i) => (
                <tr key={i}>
                  {line.split(' | ').map((cell, j) => (
                    <td key={j}>{cell}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <pre className="jv-term-block-body">
            {text}
            {!done && <span className="jv-term-cursor">▌</span>}
          </pre>
        )}
      </motion.div>
    );
  }

  return (
    <motion.div
      className={`jv-term-line ${meta.className} ${isGlitch ? 'jv-term-glitch' : ''}`}
      initial={{ opacity: 0, x: -6 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.18 }}
    >
      <span className="jv-term-kind">{meta.label}</span>
      {entry.title && <span className="jv-term-title">{entry.title}</span>}
      <span className="jv-term-msg" data-text={entry.message}>
        {text}
        {!done && <span className="jv-term-cursor">▌</span>}
      </span>
    </motion.div>
  );
}

interface TerminalProps {
  logs: DisplayLogEntry[];
  connected: boolean;
  onSend: (text: string) => void;
}

export function Terminal({ logs, connected, onSend }: TerminalProps) {
  const [input, setInput] = useState('');
  const historyRef = useRef<string[]>([]);
  const historyIndexRef = useRef<number>(-1);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const stickToBottomRef = useRef(true);

  useEffect(() => {
    const el = scrollRef.current;
    if (el && stickToBottomRef.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [logs]);

  function handleScroll() {
    const el = scrollRef.current;
    if (!el) return;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    stickToBottomRef.current = distanceFromBottom < 48;
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    const text = input.trim();
    if (!text) return;
    onSend(text);
    historyRef.current.push(text);
    historyIndexRef.current = historyRef.current.length;
    setInput('');
    stickToBottomRef.current = true;
  }

  function handleKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'ArrowUp') {
      e.preventDefault();
      if (historyRef.current.length === 0) return;
      historyIndexRef.current = Math.max(0, historyIndexRef.current - 1);
      setInput(historyRef.current[historyIndexRef.current] ?? '');
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (historyRef.current.length === 0) return;
      historyIndexRef.current = Math.min(historyRef.current.length, historyIndexRef.current + 1);
      setInput(historyRef.current[historyIndexRef.current] ?? '');
    }
  }

  return (
    <motion.div
      className="jv-glass jv-terminal"
      initial={{ opacity: 0, x: 24 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.5, ease: 'easeOut' }}
    >
      <div className="jv-panel-title jv-terminal-title">Sistem Konsolu</div>
      <div className="jv-terminal-scroll" ref={scrollRef} onScroll={handleScroll}>
        {logs.length === 0 && <div className="jv-term-empty">Bağlantı bekleniyor, veri akışı yok...</div>}
        {logs.map((entry) => (
          <TerminalLine key={entry.id} entry={entry} skipAnimation={entry.fromSnapshot} />
        ))}
      </div>
      <form className="jv-terminal-input-row" onSubmit={submit}>
        <span className="jv-terminal-prompt">{connected ? '>' : '×'}</span>
        <input
          className="jv-terminal-input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={!connected}
          placeholder={connected ? 'komut girin veya /help yazın...' : 'bağlantı bekleniyor...'}
          spellCheck={false}
          autoComplete="off"
        />
      </form>
    </motion.div>
  );
}
