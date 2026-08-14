import axios from 'axios';
import { logLlmSent, logLlmReceived, debugError } from '../lib/pipelineLog';

// FinGuru talks to its OWN hosted agent backend -- deliberately NOT the
// shared onboarding `client` in ./client.js. Keeping this isolated means the
// onboarding API and FinGuru can point at completely different servers.
const FINGURU_URL =
  import.meta.env.VITE_FINGURU_URL ||
  'https://ominous-ripening-droplet.ngrok-free.dev/agents/finguru/invoke';
const FINGURU_KEY = import.meta.env.VITE_FINGURU_KEY || 'z_oz7yXmwrkZq64hJHstqzuKNVziPUKa';

const finguruClient = axios.create({
  headers: {
    'Content-Type': 'application/json',
    'X-API-Key': FINGURU_KEY,
    // Skips the ngrok free-tier browser interstitial for good measure.
    'ngrok-skip-browser-warning': 'true',
  },
});

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
