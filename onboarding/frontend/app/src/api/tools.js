import axios from 'axios';
import { FINGURU_API_BASE, finguruHeaders } from './finguru';
import { debugLog, debugError } from '../lib/pipelineLog';

// Dynamic financial tools (finguru-dynamic-tools-frontend-spec.md).
//
// These live on the FinGuru agent backend, NOT the onboarding API -- same
// reasoning as api/finguru.js, and the same base URL, so the two cannot drift
// apart. VITE_FINGURU_NAME_API_BASE is deliberately not used here: it is empty
// by default, which would resolve these to this app's own origin and 404.
//
// Both tools currently ship as execution: "server", so §4's formula evaluation
// (and its mathjs dependency) is not needed yet. If a "client" tool ever
// appears, add a real expression parser -- never eval() or Function().

const toolsClient = axios.create({ headers: finguruHeaders() });

/** Every tool the backend knows about. §7: a new row here needs no frontend change. */
export async function fetchTools() {
  const { data } = await toolsClient.get(`${FINGURU_API_BASE}/api/tools`);
  return Array.isArray(data) ? data : [];
}

/**
 * Run a server-executed tool. §5: the frontend packages whatever the user
 * filled in against the generic `inputs` schema and posts it -- no
 * tool-specific logic, because the backend owns the computation.
 *
 * Returns { result, output_label, breakdown? }.
 */
export async function executeTool(toolId, inputs) {
  debugLog('[Tools] execute', { toolId, inputs });
  try {
    const { data } = await toolsClient.post(`${FINGURU_API_BASE}/api/tools/execute`, {
      tool_id: toolId,
      inputs,
    });
    return data;
  } catch (err) {
    debugError('[Tools] ✗ execute failed', {
      toolId,
      status: err?.response?.status,
      detail: err?.response?.data || err?.message,
    });
    throw err;
  }
}

/** §6: persist this instance's inputs against the user's name so the Tools tab
 *  can re-render it in a later session. Best-effort -- a failed save must never
 *  cost the user the result they are looking at. */
export async function saveToolInstance(name, toolId, inputs, result) {
  if (!name) return null;
  try {
    const { data } = await toolsClient.post(`${FINGURU_API_BASE}/api/tools/save`, {
      name,
      tool_id: toolId,
      input_values: inputs,
      result,
    });
    return data;
  } catch (err) {
    debugError('[Tools] ✗ save failed', { toolId, detail: err?.message });
    return null;
  }
}
