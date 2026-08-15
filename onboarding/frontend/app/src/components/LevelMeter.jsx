// Lightweight animated mic-level indicator (a handful of bars reacting to
// RMS level 0..1) -- avoids pulling in a waveform/charting dependency.
const BAR_COUNT = 5;

export default function LevelMeter({ level = 0, className = '' }) {
  return (
    <div className={`flex items-end gap-0.5 h-4 ${className}`} aria-hidden="true">
      {Array.from({ length: BAR_COUNT }).map((_, i) => {
        // Stagger each bar's sensitivity so the meter looks alive rather than
        // all bars moving in lockstep.
        const threshold = i / BAR_COUNT;
        const active = Math.min(1, Math.max(0, (level - threshold * 0.15) * 4));
        const height = 4 + active * 12;
        return (
          <span
            key={i}
            className="w-1 rounded-full bg-primary transition-all duration-75"
            style={{ height: `${height}px`, opacity: 0.35 + active * 0.65 }}
          />
        );
      })}
    </div>
  );
}
