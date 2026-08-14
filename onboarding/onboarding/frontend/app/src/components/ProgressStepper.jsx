export default function ProgressStepper({ progress }) {
  if (!progress || !progress.steps || !progress.steps.length) return null;
  return (
    <div className="flex items-center justify-between w-full py-2 mb-4">
      {progress.steps.map((step, idx) => (
        <div key={step.index} className="flex items-center flex-1 last:flex-none">
          <div className="flex flex-col items-center">
            <div
              className={`w-7 h-7 rounded-full flex items-center justify-center text-[11px] font-bold shrink-0 ${
                step.status === 'complete'
                  ? 'bg-primary text-on-primary'
                  : step.status === 'action_needed'
                  ? 'bg-error text-on-error'
                  : step.status === 'in_progress'
                  ? 'border-2 border-primary text-primary bg-surface'
                  : 'border-2 border-outline-variant text-outline bg-surface'
              }`}
              title={step.label}
            >
              {step.status === 'complete' ? '✓' : step.index}
            </div>
          </div>
          {idx < progress.steps.length - 1 && (
            <div
              className={`h-[2px] flex-1 mx-1 ${
                step.status === 'complete' ? 'bg-primary' : 'bg-outline-variant'
              }`}
            />
          )}
        </div>
      ))}
    </div>
  );
}
