import axios from 'axios';
import { logLlmSent, logLlmReceived, debugError } from '../lib/pipelineLog';
import { resolveConfiguredUrl } from '../lib/basePath';

// FinGuru talks to its OWN hosted agent backend -- deliberately NOT the
// shared onboarding `client` in ./client.js. Keeping this isolated means the
// onboarding API and FinGuru can point at completely different servers.
// A relative value (the default deployment style, e.g.
// "/agents/finguru/invoke") is mounted under the app's base so it still
// resolves when the app is served from a sub-path. An absolute URL is left
// exactly as configured -- see lib/basePath.js.
const FINGURU_URL = resolveConfiguredUrl(
  import.meta.env.VITE_FINGURU_URL,
  'https://ominous-ripening-droplet.ngrok-free.dev/agents/finguru/invoke',
);
const FINGURU_KEY = import.meta.env.VITE_FINGURU_KEY || 'z_oz7yXmwrkZq64hJHstqzuKNVziPUKa';

const finguruClient = axios.create({
  headers: {
    'Content-Type': 'application/json',
    'X-API-Key': FINGURU_KEY,
    // Skips the ngrok free-tier browser interstitial for good measure.
    'ngrok-skip-browser-warning': 'true',
  },
});

// The streaming counterpart of the same endpoint: same body, same key, but it
// emits each sentence as the agent finishes writing it. Used by
// api/finguruStream.js for spoken turns -- see that file for why.
export const FINGURU_STREAM_URL = `${FINGURU_URL}/stream`;

// Origin of the FinGuru backend, derived from the invoke URL rather than
// configured separately so the two can never point at different servers. The
// dynamic-tools endpoints (/api/tools, /api/tools/execute, /api/tools/save)
// hang off this.
export const FINGURU_API_BASE = FINGURU_URL.replace(/\/agents\/[^/]+\/invoke.*$/, '');

// The same headers as the axios client above, for callers that need plain
// fetch. Streaming cannot go through axios: it buffers the whole response
// before resolving, which is exactly the wait we are trying to remove.
export function finguruHeaders() {
  return {
    'Content-Type': 'application/json',
    'X-API-Key': FINGURU_KEY,
    'ngrok-skip-browser-warning': 'true',
  };
}

/**
 * Ask FinGuru a question. The agent is stateless/single-shot; `history` is
 * sent for forward-compatibility but the current agent does not use it.
 *
 * `options.colloquial` toggles the "Desi mode" register: when true we ask for
 * everyday/street language (e.g. how Hindi/Tamil is actually spoken) rather
 * than formal/literary phrasing. Sent to the backend as the boolean `style`
 * (true = Desi mode ON, false = OFF).
 *
 * `options.language` is a language code (e.g. "ta", "hi", "en") the agent
 * should reply in, sent as `language`.
 *
 * `options.voice` is true when the question came in by voice (mic/live call)
 * rather than typed, sent as the boolean `voice`.
 *
 * `options.name` is the resolved session identity (see lib/finguruIdentity.js)
 * -- included on every request per the name-based-identity spec, whenever
 * a name has been resolved. The agent isn't required to use it.
 *
 * Returns { text, language, confidence, hitl, error }.
 */
export async function askFinGuru(question, history = [], options = {}) {
  const { colloquial = false, language, voice = false, name } = options;
  const evidence = {
    question,
    history,
    style: colloquial,
    voice,
    ...(language ? { language } : {}),
    ...(name ? { name } : {}),
  };

  logLlmSent({ engine: 'FinGuru', question, style: colloquial, voice, language: language || null });

  const startedAt = performance.now();
  try {
    const { data } = await finguruClient.post(FINGURU_URL, { evidence });
    const output = data?.output || {};
    const result = {
      text: output.content || '',
      language: output.language || null,
      confidence: output.confidence,
      hitl: data?.hitl || { triggered: false, reasons: [] },
      error: data?.error || null,
      // Calculators to offer beside this reply, each carrying its own full
      // definition and any values the agent already computed. Usually empty.
      // See api/tools.js and components/ToolCard.jsx.
      tools: Array.isArray(data?.tools) ? data.tools : [],
      // Suggested next questions, now a first-class field of the agent's
      // output schema rather than a marker parsed back out of the prose.
      // The schema is what constrains the model's JSON, so this field is
      // always present -- the ###FOLLOWUPS### instruction was followed only
      // sometimes (measured: present for one English question, absent for
      // another and for Hindi, in the same minute). Already in the user's
      // language, and never inside `content`, so a spoken reply cannot read
      // the suggestion list aloud.
      followUps: Array.isArray(output.follow_ups) ? output.follow_ups : [],
    };
    logLlmReceived({
      engine: 'FinGuru',
      ms: Math.round(performance.now() - startedAt),
      language: result.language,
      textLength: result.text.length,
      textPreview: result.text.slice(0, 120),
    });
    return result;
  } catch (err) {
    logLlmReceived({ engine: 'FinGuru', ms: Math.round(performance.now() - startedAt), error: err?.response?.status || err?.message });
    debugError('[FinGuru] ✗ request failed', {
      status: err?.response?.status,
      detail: err?.response?.data || err?.message,
    });
    throw err;
  }
}
