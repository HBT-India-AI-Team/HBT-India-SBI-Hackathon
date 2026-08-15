import { useEffect, useMemo, useState } from 'react';
import { executeTool, saveToolInstance } from '../api/tools';

// The ONE generic tool renderer (finguru-dynamic-tools-frontend-spec.md §1).
//
// It knows nothing about EMI or FIRE. Every field, label, prefix, suffix and
// the result caption come from the `tool` definition the backend sent; adding
// a third calculator is a backend row, not a change here. Nothing in this file
// may branch on tool_id -- if you find yourself wanting to, the thing you want
// belongs in the definition instead.
//
// Both current tools are execution: "server", so the compute path is a POST.
// The spec's client-side branch (§4) is deliberately absent rather than
// stubbed: writing it now would mean writing a formula evaluator with no
// formula to test it against, and eval() is explicitly forbidden.

function formatValue(value, prefix, suffix) {
  if (value === null || value === undefined || value === '') return '—';
  const n = Number(value);
  if (!Number.isFinite(n)) return String(value);
  // Indian digit grouping: 2,12,796.03, not 212,796.03. The agent's own
  // replies use it, and a card beside them showing the Western grouping for
  // the same number reads as a different number.
  const shown = n.toLocaleString('en-IN', { maximumFractionDigits: 2 });
  return `${prefix || ''}${shown}${suffix ? ` ${suffix}` : ''}`;
}

export default function ToolCard({ suggestion, name, onSaved }) {
  const tool = suggestion?.tool || {};
  const inputs = useMemo(() => (Array.isArray(tool.inputs) ? tool.inputs : []), [tool.inputs]);

  // Prefilled from whatever the agent actually computed, so the card opens
  // showing the same figures as the sentence above it rather than blank.
  const [values, setValues] = useState(() => {
    const seed = {};
    for (const field of inputs) {
      const given = suggestion?.prefill?.[field.key];
      seed[field.key] = given === undefined || given === null ? '' : String(given);
    }
    return seed;
  });
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [saved, setSaved] = useState(false);

  const complete = inputs.length > 0 && inputs.every((f) => String(values[f.key] ?? '').trim() !== '');

  const run = async (payload) => {
    setBusy(true);
    setError(null);
    try {
      const numeric = {};
      for (const field of inputs) {
        const raw = payload[field.key];
        numeric[field.key] = field.type === 'number' ? Number(raw) : raw;
      }
      const res = await executeTool(tool.tool_id || suggestion.tool_id, numeric);
      setResult(res);
      setSaved(false);
    } catch {
      setError('Could not calculate that. Check the values and try again.');
    } finally {
      setBusy(false);
    }
  };

  // A card that arrives already filled in should show its answer immediately --
  // making someone press Calculate to see a number the agent has just told them
  // is busywork. An empty card waits for input.
  useEffect(() => {
    if (complete && result === null && !busy) run(values);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const save = async () => {
    const stored = await saveToolInstance(name, tool.tool_id || suggestion.tool_id, values, result?.result);
    if (stored) {
      setSaved(true);
      onSaved?.(stored);
    }
  };

  if (!inputs.length) return null;

  return (
    <div
      style={{
        border: '1px solid var(--color-outline-variant)',
        borderRadius: 14,
        padding: '12px 14px',
        margin: '8px 0 4px',
        background: 'var(--color-surface-container-low, rgba(0,0,0,0.02))',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 8 }}>
        <strong style={{ fontSize: 14 }}>{tool.name || suggestion.tool_id}</strong>
        {suggestion.reason === 'computed' && (
          <span style={{ fontSize: 10, opacity: 0.6 }}>from your question</span>
        )}
      </div>

      <div style={{ display: 'grid', gap: 8, marginTop: 10 }}>
        {inputs.map((field) => (
          <label key={field.key} style={{ display: 'grid', gap: 3 }}>
            <span style={{ fontSize: 11, opacity: 0.75 }}>{field.label || field.key}</span>
            <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              {field.prefix && <span style={{ fontSize: 13, opacity: 0.7 }}>{field.prefix}</span>}
              <input
                type={field.type === 'number' ? 'number' : 'text'}
                inputMode={field.type === 'number' ? 'decimal' : undefined}
                value={values[field.key] ?? ''}
                min={field.min}
                step={field.step}
                onChange={(e) => setValues((prev) => ({ ...prev, [field.key]: e.target.value }))}
                style={{
                  flex: 1, minWidth: 0, padding: '7px 9px', fontSize: 14,
                  border: '1px solid var(--color-outline-variant)', borderRadius: 8,
                  background: 'var(--color-surface, #fff)', color: 'inherit',
                }}
              />
              {field.suffix && <span style={{ fontSize: 11, opacity: 0.7 }}>{field.suffix}</span>}
            </span>
          </label>
        ))}
      </div>

      <div style={{ display: 'flex', gap: 8, marginTop: 10, alignItems: 'center' }}>
        <button
          type="button"
          onClick={() => run(values)}
          disabled={busy || !complete}
          style={{
            padding: '7px 14px', fontSize: 13, borderRadius: 999, border: 'none',
            background: 'var(--color-primary, #1b6ef3)', color: '#fff',
            opacity: busy || !complete ? 0.5 : 1,
          }}
        >
          {busy ? 'Calculating…' : 'Calculate'}
        </button>
        {result && name && (
          <button
            type="button"
            onClick={save}
            disabled={saved}
            style={{
              padding: '7px 12px', fontSize: 12, borderRadius: 999,
              border: '1px solid var(--color-outline-variant)',
              background: 'transparent', color: 'inherit', opacity: saved ? 0.5 : 1,
            }}
          >
            {saved ? 'Saved' : 'Save'}
          </button>
        )}
      </div>

      {error && <div style={{ marginTop: 8, fontSize: 12, color: 'var(--color-error, #b3261e)' }}>{error}</div>}

      {result && !error && (
        <div style={{ marginTop: 10, paddingTop: 9, borderTop: '1px solid var(--color-outline-variant)' }}>
          <div style={{ fontSize: 11, opacity: 0.7 }}>{result.output_label || tool.output_label || 'Result'}</div>
          <div style={{ fontSize: 20, fontWeight: 600, marginTop: 2 }}>
            {formatValue(result.result, tool.output_prefix, tool.output_suffix)}
          </div>
        </div>
      )}
    </div>
  );
}
