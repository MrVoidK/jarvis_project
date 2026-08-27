interface RingGaugeProps {
  label: string;
  value: number | null;
  max: number;
  displayValue: string;
  size?: number;
}

export function RingGauge({ label, value, max, displayValue, size = 92 }: RingGaugeProps) {
  const stroke = 6;
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  const pct = value === null ? 0 : Math.max(0, Math.min(1, value / max));
  const offset = circumference * (1 - pct);
  const isUnavailable = value === null;

  return (
    <div className="jv-ring-gauge">
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="rgba(255,191,0,0.12)"
          strokeWidth={stroke}
        />
        {!isUnavailable && (
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            stroke="var(--jv-amber)"
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
