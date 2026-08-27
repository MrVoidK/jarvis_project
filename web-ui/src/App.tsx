import { HologramOrb } from './components/HologramOrb';
import { StatusBar } from './components/StatusBar';
import { SystemMonitor } from './components/SystemMonitor';
import { Terminal } from './components/Terminal';
import { ToolNotification } from './components/ToolNotification';
import { useJarvisSocket } from './hooks/useJarvisSocket';
import './App.css';

function App() {
  const { status, jarvisState, logs, telemetry, staticInfo, activeTools, sendCommand } = useJarvisSocket();

  return (
    <div className="jv-app">
      <div className="jv-crt-overlay" />
      <div className="jv-vignette-overlay" />
      <div className="jv-flicker-overlay" />

      <StatusBar status={status} jarvisState={jarvisState} />

      <main className="jv-layout">
        <SystemMonitor telemetry={telemetry} staticInfo={staticInfo} />
        <HologramOrb state={jarvisState} />
        <Terminal logs={logs} connected={status === 'online'} onSend={sendCommand} />
      </main>

      <ToolNotification tools={activeTools} />
    </div>
  );
}

export default App;
