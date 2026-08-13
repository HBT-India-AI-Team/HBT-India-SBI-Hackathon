// Mirrors backend/admin.py and backend/main.py response shapes.

export interface InputSchemaProperty {
  // `boolean` is real — several agent.yaml input schemas declare it (e.g.
  // demo_sme_loan_quickcheck's gst_registered), and SchemaForm renders it as
  // a true/false <select>. Leaving it out of this union made those branches
  // look like dead comparisons and broke `tsc -b`, which is why dist/ went stale.
  type: 'string' | 'number' | 'integer' | 'boolean' | 'object' | 'array'
  description?: string
  /** Only present on `object` properties whose own sub-fields are known (e.g. qualification's
   *  nested `evidence` object) — lets the Playground render them as their own col/val rows
   *  instead of falling back to a raw JSON box. */
  properties?: Record<string, InputSchemaProperty>
}

export interface InputSchema {
  type?: string
  required?: string[]
  properties?: Record<string, InputSchemaProperty>
}

// What a Logs row shows collapsed. The bodies are excluded on purpose —
// they're fetched per-row via getOllamaLogDetail(offset) on expand, since a
// single call's prompt + completion can run to six figures of characters.
export interface OllamaCallLogSummary {
  timestamp: string
  host: string
  model: string
  attempt: number
  total_attempts: number
  duration_ms: number
  ok: boolean
  error: string | null
  /** Byte offset in ollama_calls.jsonl — the record's stable ID. */
  offset: number
}

export interface OllamaCallLog extends OllamaCallLogSummary {
  request: {
    messages?: { role: string; content: string }[]
    format?: Record<string, unknown> | null
    tools?: Record<string, unknown>[] | null
    options?: Record<string, unknown>
  }
  response: Record<string, unknown> | null
}

export interface ChatDecision {
  outcome: string
  reason: string
  composite_score: number | null
}

/** What an LLM stage actually did, straight from the model's own response —
 *  never a narrated reconstruction. `thinking` is the model's real reasoning
 *  trace (reasoning-capable models return it alongside their answer); it's
 *  absent for models that don't reason, and the UI then shows the call stats
 *  alone rather than inventing a story. Non-LLM stages carry an empty detail. */
export interface StageDetail {
  model?: string
  duration_ms?: number
  prompt_tokens?: number
  completion_tokens?: number
  prompt_chars?: number
  done_reason?: string
  thinking?: string
  llm_error?: string
  tool_calls?: { name: string; arguments: Record<string, unknown>; result: unknown }[]
  /** Whether the vernacular wording layer reached this answer, and if not why.
   *  Reported because every way it can produce nothing looks the same from
   *  outside — off, wrong script, or nothing above the retrieval floor. */
  style?: {
    applied: boolean
    language?: string | null
    guide?: boolean
    examples?: number
    reason?: string
  }
}

export interface StageTraceEntry {
  stage: string
  status: 'ok' | 'error' | 'skipped'
  summary: string
  duration_ms: number
  detail?: StageDetail
}

export type ChatContentType = 'text' | 'code' | 'image' | 'video' | 'audio'

export interface ChatTurnResult {
  session_id: string
  reply: string
  evidence: Record<string, unknown>
  decision: ChatDecision | null
  done: boolean
  content_type?: ChatContentType | null
  stage_trace?: StageTraceEntry[] | null
}

export type InputMode = 'chat' | 'form' | 'json' | 'trigger' | 'file'

export interface AgentSummary {
  agent_id: string
  version?: string
  purpose?: string
  skills?: string[]
  pipeline?: string[]
  routable?: boolean
  draft?: boolean
  input_schema?: InputSchema
  input_mode?: InputMode
  demo_sample_input?: Record<string, unknown> | null
  error?: string
}

export interface GeneratedSkillSummary {
  skill_id: string
  description: string
  used_fallback: boolean
}

export interface ApiKeyResult {
  agent_id: string
  api_key: string
}

export interface GenerateAgentResult {
  status: 'ok' | 'saved_with_errors' | 'error'
  agent_id: string
  skill_id?: string
  used_fallback?: boolean
  attempts?: number
  skills?: GeneratedSkillSummary[]
  error?: string
}

export interface GenerateAgentEvent {
  step: 'decompose' | 'generate_skill' | 'save' | 'validate' | 'final'
  status?: 'start' | 'done' | 'error'
  skill_id?: string
  index?: number
  total?: number
  count?: number
  used_fallback?: boolean
  error?: string
  result?: GenerateAgentResult
}

export interface EditFileResult {
  status: 'ok' | 'saved_with_errors'
  agent_id: string
  file_key: string
  error?: string
}

export interface ArchetypeSummary {
  id: string
  label: string
  description: string
}

export interface TemplateSummary {
  id: string
  label: string
  description: string
  pipeline: string[]
}

export interface SkillFiles {
  skill_yaml: string
  instructions_md: string
  task_prompt_md: string
  output_contract_json: string
  rules: Record<string, string>
}

export interface AgentFiles {
  agent_id: string
  agent_yaml: string
  skills: Record<string, SkillFiles>
}

export interface SkillCatalogEntry {
  skill_id: string
  kind: 'deterministic' | 'procedural'
  description: string
}

export interface AddSkillResult {
  status: 'ok' | 'saved_with_errors'
  agent_id: string
  skill_id: string
  error?: string
}

export interface SaveResult {
  status: 'ok' | 'saved_with_errors'
  error?: string
}

export interface Capability {
  name: string
  description: string
}

export interface TestRunResult {
  run_id: string
  decision: Record<string, unknown> | null
  explanation: Record<string, unknown> | null
  error: { stage: string; type: string; message: string } | null
  stage_trace?: StageTraceEntry[]
}

export interface TestRunFileResult extends TestRunResult {
  /** The evidence dict actually extracted from the uploaded file — shown so a
   *  tester can see what was read, not just the resulting decision. */
  parsed_evidence: Record<string, unknown>
}

export interface AgentRouterResult {
  workflow_id: string
  run_id: string
  status: 'COMPLETED' | 'NEEDS_CLARIFICATION' | 'AGENT_FAILED' | 'FAILED'
  chosen_agent_id: string | null
  routing_confidence: number | null
  routing_reasoning: string | null
  agent_run_id: string | null
  decision: Record<string, unknown> | null
  explanation: Record<string, unknown> | null
  error?: { stage: string; type: string; message: string }
}

export interface ApiError {
  detail: string
}
