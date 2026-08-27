import { useId } from 'react';

interface RingGaugeProps {
  label: string;
  value: number | null;
  max: number;
  displayValue: string;
  size?: number;
}

export function RingGauge({ label, value, max, displayValue, size = 84 }: RingGaugeProps) {
  const gradientId = useId();
  const stroke = 6;
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  const pct = value === null ? 0 : Math.max(0, Math.min(1, value / max));
  const offset = circumference * (1 - pct);
  const isUnavailable = value === null;

  return (
    <div className="jv-ring-gauge">
      {/* viewBox sabit, gercek piksel genisligi CSS'e (%100 + max-width) birakiliyor
          - boylece panel daraldiginda gostergeler TASMAK yerine oranli kucculuyor. */}
      <svg viewBox={`0 0 ${size} ${size}`} className="jv-ring-gauge-svg">
        <defs>
          <linearGradient id={gradientId} x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor="var(--jv-amber-dim)" />
            <stop offset="100%" stopColor="var(--jv-amber-bright)" />
          </linearGradient>
        </defs>
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="rgba(255,191,0,0.1)"
          strokeWidth={stroke}
        />
        {!isUnavailable && (
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            stroke={`url(#${gradientId})`}
            strokeWidth={stroke}
            strokeLinecap="round"
            strokeDasharray={circumference}
            strokeDashoffset={offset}
            transform={`rotate(-90 ${size / 2} ${size / 2})`}
            className="jv-ring-gauge-progress"
          />
        )}
        <text
          x="50%"
          y="47%"
          textAnchor="middle"
          dominantBaseline="middle"
          className="jv-ring-gauge-value"
        >
          {isUnavailable ? 'N/A' : displayValue}
        </text>
      </svg>
      <div className="jv-ring-gauge-label">{label}</div>
    </div>
  );
}
