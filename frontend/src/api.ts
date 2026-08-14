import type {
  AddSkillResult,
  AgentFiles,
  AgentRouterResult,
  AgentSummary,
  ApiKeyResult,
  ApiSurface,
  ArchetypeSummary,
  ToolDefinition,
  ToolResult,
  Capability,
  ChatTurnResult,
  InputMode,
  EditFileResult,
  GenerateAgentEvent,
  GenerateAgentResult,
  OllamaCallLog,
  OllamaCallLogSummary,
  SaveResult,
  SkillCatalogEntry,
  TemplateSummary,
  TestRunFileResult,
  TestRunResult,
} from './types'

export class ApiRequestError extends Error {}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }))
    throw new ApiRequestError(body.detail ?? `Request to ${path} failed (${response.status})`)
  }
  return response.json() as Promise<T>
}

export const api = {
  listAgents: () => apiFetch<{ agents: AgentSummary[] }>('/admin/agents'),
  listStages: () => apiFetch<{ stages: string[] }>('/admin/stages'),
  listCapabilities: () => apiFetch<{ capabilities: Capability[] }>('/admin/capabilities'),
  listTemplates: () => apiFetch<{ templates: TemplateSummary[] }>('/admin/templates'),
  listArchetypes: () => apiFetch<{ archetypes: ArchetypeSummary[] }>('/admin/archetypes'),
  getOllamaLogs: (limit = 100) => apiFetch<{ calls: OllamaCallLogSummary[] }>(`/admin/ollama-logs?limit=${limit}`),
  getOllamaLogDetail: (offset: number) => apiFetch<OllamaCallLog>(`/admin/ollama-logs/${offset}`),

  getAgentFiles: (agentId: string) => apiFetch<AgentFiles>(`/admin/agents/${agentId}/files`),

  saveAgentFiles: (agentId: string, files: Omit<AgentFiles, 'agent_id'>) =>
    apiFetch<SaveResult>(`/admin/agents/${agentId}/files`, {
      method: 'PUT',
      body: JSON.stringify(files),
    }),

  listSkillCatalog: () => apiFetch<{ skills: SkillCatalogEntry[] }>('/admin/skills'),

  addSkill: (
    agentId: string,
    payload: {
      skill_id: string
      mode: 'scaffold' | 'attach_existing'
      has_rules?: boolean
      template_id?: string
      description?: string
      purpose?: string
    },
  ) =>
    apiFetch<AddSkillResult>(`/admin/agents/${agentId}/skills`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  removeSkill: (agentId: string, skillId: string) =>
    apiFetch<AddSkillResult>(`/admin/agents/${agentId}/skills/${skillId}`, { method: 'DELETE' }),

  createAgent: (agentId: string, skillId: string, purpose: string, templateId: string) =>
    apiFetch<{ status: string; agent_id: string; skill_id: string; template_id: string }>('/admin/agents', {
      method: 'POST',
      body: JSON.stringify({ agent_id: agentId, skill_id: skillId, purpose, template_id: templateId }),
    }),

  getApiKey: (agentId: string) => apiFetch<ApiKeyResult>(`/admin/agents/${agentId}/api-key`),

  /** Every endpoint this backend serves, plus what has actually been called.
   *  The traffic half is the point: a request against a path we do not serve
   *  shows up as unrecognised, which is how a client calling the wrong URL
   *  becomes visible instead of just failing quietly on their side. */
  getApiSurface: () => apiFetch<ApiSurface>('/admin/api-surface'),

  regenerateApiKey: (agentId: string) =>
    apiFetch<ApiKeyResult>(`/admin/agents/${agentId}/api-key/regenerate`, { method: 'POST' }),

  deleteAgent: (agentId: string) =>
    apiFetch<{ status: string; agent_id: string }>(`/admin/agents/${agentId}`, { method: 'DELETE' }),

  generateAgent: (agentId: string, purpose: string) =>
    apiFetch<GenerateAgentResult>('/admin/agents/generate', {
      method: 'POST',
      body: JSON.stringify({ agent_id: agentId, purpose }),
    }),

  // Streams step-by-step progress over SSE (decompose, each skill's rule
  // generation, save, validate) instead of one opaque call. Calls onEvent
  // as each step arrives and resolves with the final result.
  generateAgentStream: async (
    agentId: string,
    purpose: string,
    archetypeId: string,
    onEvent: (event: GenerateAgentEvent) => void,
  ): Promise<GenerateAgentResult> => {
    const response = await fetch('/admin/agents/generate/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ agent_id: agentId, purpose, archetype_id: archetypeId }),
    })
    if (!response.ok || !response.body) {
      const body = await response.json().catch(() => ({ detail: response.statusText }))
      throw new ApiRequestError(body.detail ?? `Request to generate agent failed (${response.status})`)
    }

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let finalResult: GenerateAgentResult | null = null

    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })

      const chunks = buffer.split('\n\n')
      buffer = chunks.pop() ?? ''
      for (const chunk of chunks) {
        const line = chunk.split('\n').find((l) => l.startsWith('data: '))
        if (!line) continue
        const event = JSON.parse(line.slice('data: '.length)) as GenerateAgentEvent
        onEvent(event)
        if (event.step === 'final' && event.result) finalResult = event.result
      }
    }

    if (!finalResult) throw new ApiRequestError('Agent generation stream ended without a result')
    return finalResult
  },

  // The one "Fix with AI" mechanism — edits exactly the file identified by fileKey (the
  // same tab-key scheme AgentEditor.tsx's buildSkillGroups already uses, e.g. "agent_yaml",
  // "skill:fin_health:rule:factors"), the way a careful human applies a targeted change:
  // full file content in, full corrected file content out, everything untouched preserved.
  // Works on any file, on live agents as much as drafts.
  editFileWithAI: (agentId: string, fileKey: string, feedback: string) =>
    apiFetch<EditFileResult>(`/admin/agents/${agentId}/edit-file`, {
      method: 'POST',
      body: JSON.stringify({ file_key: fileKey, feedback }),
    }),

  setInputMode: (agentId: string, inputMode: InputMode) =>
    apiFetch<{ status: string; agent_id: string; input_mode?: InputMode; error?: string }>(
      `/admin/agents/${agentId}/input-mode`,
      { method: 'POST', body: JSON.stringify({ input_mode: inputMode }) },
    ),

  /** `signal` backs the composer's stop button. Note this aborts the client's
   *  wait, not the run: the backend has no cancellation hook, so the model
   *  call finishes server-side regardless.
   *
   *  `style` and `voice` are sent per turn rather than held on the session, so
   *  the same question can be asked twice in one conversation and the answers
   *  compared. Their defaults here mirror the backend's: style on, voice off. */
  chatWithAgent: (
    agentId: string,
    sessionId: string | null,
    message: string,
    opts: { style?: boolean; voice?: boolean; signal?: AbortSignal } = {},
  ) =>
    apiFetch<ChatTurnResult>(`/admin/agents/${agentId}/chat`, {
      method: 'POST',
      body: JSON.stringify({
        session_id: sessionId,
        message,
        style: opts.style ?? true,
        voice: opts.voice ?? false,
      }),
      signal: opts.signal,
    }),

  /** Same turn as chatWithAgent, but sentences arrive as they are written.
   *
   *  `onSentence` fires per finished sentence; the resolved value is the same
   *  object chatWithAgent returns, and is authoritative — the sentences are a
   *  preview of it, not a separate answer.
   *
   *  Read with fetch + a stream reader rather than EventSource, which cannot
   *  POST. Same approach the agent generator already uses. */
  chatWithAgentStream: async (
    agentId: string,
    sessionId: string | null,
    message: string,
    opts: {
      userId?: string | null
      style?: boolean
      voice?: boolean
      signal?: AbortSignal
      onSentence?: (text: string, index: number) => void
    } = {},
  ): Promise<ChatTurnResult> => {
    const response = await fetch(`/admin/agents/${agentId}/chat/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: sessionId,
        user_id: opts.userId ?? null,
        message,
        style: opts.style ?? true,
        voice: opts.voice ?? false,
      }),
      signal: opts.signal,
    })
    if (!response.ok || !response.body) {
      throw new ApiRequestError(`Request failed (${response.status})`)
    }

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let result: ChatTurnResult | null = null

    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      // SSE frames are separated by a blank line. A chunk can split one in
      // half, so only whole frames are consumed and the remainder is kept.
      const frames = buffer.split('\n\n')
      buffer = frames.pop() ?? ''
      for (const frame of frames) {
        const line = frame.split('\n').find((l) => l.startsWith('data: '))
        if (!line) continue
        const event = JSON.parse(line.slice(6))
        if (event.event === 'sentence') {
          opts.onSentence?.(event.text, event.index)
        } else if (event.event === 'done') {
          result = event as ChatTurnResult
        } else if (event.event === 'error') {
          throw new ApiRequestError(event.message ?? 'Stream failed')
        }
      }
    }
    if (!result) throw new ApiRequestError('Stream ended without a reply')
    return result
  },

  /** Every calculator the backend knows about, with its input fields. */
  listTools: () => apiFetch<ToolDefinition[]>('/api/tools'),

  /** Compute a calculator's result. Runs the same registered capability the
   *  agent calls when it answers in prose, so the widget and the sentence
   *  above it cannot disagree. */
  executeTool: (toolId: string, inputs: Record<string, string | number>) =>
    apiFetch<ToolResult>('/api/tools/execute', {
      method: 'POST',
      body: JSON.stringify({ tool_id: toolId, inputs }),
    }),

  /** Remember a user's inputs so the calculator comes back filled in. Same
   *  `user_id` the chat session is keyed by. */
  saveTool: (
    userId: string,
    toolId: string,
    inputValues: Record<string, string | number>,
    result: Record<string, unknown> | null,
  ) =>
    apiFetch<{ user_id: string; tool_id: string }>('/api/tools/save', {
      method: 'POST',
      body: JSON.stringify({ user_id: userId, tool_id: toolId, input_values: inputValues, result }),
    }),

  testRunAgent: (agentId: string, input: Record<string, unknown>) =>
    apiFetch<TestRunResult>(`/admin/agents/${agentId}/test-run`, {
      method: 'POST',
      body: JSON.stringify({ input }),
    }),

  testRunAgentFile: async (agentId: string, file: File): Promise<TestRunFileResult> => {
    const body = new FormData()
    body.append('file', file)
    // No Content-Type header here — the browser sets multipart/form-data with the right
    // boundary itself; apiFetch always forces application/json, so this can't reuse it.
    const response = await fetch(`/admin/agents/${agentId}/test-run-file`, { method: 'POST', body })
    if (!response.ok) {
      const errBody = await response.json().catch(() => ({ detail: response.statusText }))
      throw new ApiRequestError(errBody.detail ?? `Request failed (${response.status})`)
    }
    return response.json() as Promise<TestRunFileResult>
  },

  runAgentRouter: (input: Record<string, unknown>) =>
    apiFetch<AgentRouterResult>('/workflows/agent_router/invoke', {
      method: 'POST',
      body: JSON.stringify(input),
    }),

  renderExplanationMarkdown: (explanation: Record<string, unknown>) =>
    apiFetch<{ markdown: string }>('/admin/explain/markdown', {
      method: 'POST',
      body: JSON.stringify({ explanation }),
    }),
}
