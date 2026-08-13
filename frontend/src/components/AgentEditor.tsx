import { useEffect, useState } from 'react'
import { load } from 'js-yaml'
import { CodeEditor } from './CodeEditor'
import { Playground } from './Playground'
import { ReferencePanel } from './ReferencePanel'
import { YamlPreview } from './YamlPreview'
import { ConfirmDialog } from './ConfirmDialog'
import { AddSkillModal } from './AddSkillModal'
import { IntegratePanel } from './IntegratePanel'
import { Badge, Button, DangerIconButton, SegmentedControl, Skeleton, TextInput, TrashIcon } from './ui'
import { api, ApiRequestError } from '../api'
import type { AgentFiles, AgentSummary, Capability, TemplateSummary } from '../types'

type TopTab = 'files' | 'playground' | 'reference' | 'integrate'
type FileViewMode = 'raw' | 'preview'

interface FileTab {
  key: string
  label: string
  language: 'yaml' | 'json' | 'markdown'
  get: (files: AgentFiles) => string
  set: (files: AgentFiles, value: string) => AgentFiles
}

interface SkillGroup {
  key: string
  label: string
  kind: 'agent' | 'skill'
  hasRules: boolean
  skillId?: string
  tabs: FileTab[]
}

const languageBadge: Record<FileTab['language'], { text: string; className: string }> = {
  yaml: { text: 'Y', className: 'text-brand-700 bg-brand-50 border-brand-200' },
  json: { text: '{}', className: 'text-gold-800 bg-gold-50 border-gold-200' },
  markdown: { text: 'M', className: 'text-emerald-700 bg-emerald-50 border-emerald-200' },
}

function buildSkillGroups(files: AgentFiles): SkillGroup[] {
  const groups: SkillGroup[] = [
    {
      key: 'agent',
      label: 'Agent',
      kind: 'agent',
      hasRules: false,
      tabs: [
        {
          key: 'agent_yaml',
          label: 'agent.yaml',
          language: 'yaml',
          get: (f) => f.agent_yaml,
          set: (f, v) => ({ ...f, agent_yaml: v }),
        },
      ],
    },
  ]

  for (const skillId of Object.keys(files.skills).sort()) {
    const skill = files.skills[skillId]
    const hasRules = Object.keys(skill.rules).length > 0 || skill.output_contract_json.trim().length > 0
    const ruleTabs: FileTab[] = Object.keys(skill.rules).map((ruleName) => ({
      key: `skill:${skillId}:rule:${ruleName}`,
      label: `${ruleName}.yaml`,
      language: 'yaml',
      get: (f) => f.skills[skillId]?.rules[ruleName] ?? '',
      set: (f, v) => ({
        ...f,
        skills: {
          ...f.skills,
          [skillId]: { ...f.skills[skillId], rules: { ...f.skills[skillId].rules, [ruleName]: v } },
        },
      }),
    }))

    const tabs: FileTab[] = [
      {
        key: `skill:${skillId}:skill_yaml`,
        label: 'skill.yaml',
        language: 'yaml',
        get: (f) => f.skills[skillId]?.skill_yaml ?? '',
        set: (f, v) => ({ ...f, skills: { ...f.skills, [skillId]: { ...f.skills[skillId], skill_yaml: v } } }),
      },
      {
        key: `skill:${skillId}:instructions_md`,
        label: 'instructions.md',
        language: 'markdown',
        get: (f) => f.skills[skillId]?.instructions_md ?? '',
        set: (f, v) => ({ ...f, skills: { ...f.skills, [skillId]: { ...f.skills[skillId], instructions_md: v } } }),
      },
    ]

    if (hasRules) {
      tabs.push(
        {
          key: `skill:${skillId}:task_prompt_md`,
          label: 'task_prompt.md',
          language: 'markdown',
          get: (f) => f.skills[skillId]?.task_prompt_md ?? '',
          set: (f, v) => ({ ...f, skills: { ...f.skills, [skillId]: { ...f.skills[skillId], task_prompt_md: v } } }),
        },
        {
          key: `skill:${skillId}:output_contract_json`,
          label: 'output_contract.json',
          language: 'json',
          get: (f) => f.skills[skillId]?.output_contract_json ?? '',
          set: (f, v) => ({
            ...f,
            skills: { ...f.skills, [skillId]: { ...f.skills[skillId], output_contract_json: v } },
          }),
        },
        ...ruleTabs,
      )
    }

    groups.push({ key: `skill:${skillId}`, label: skillId, kind: 'skill', hasRules, skillId, tabs })
  }

  return groups
}

interface AgentEditorProps {
  agentId: string
  agents: AgentSummary[]
  stages: string[]
  capabilities: Capability[]
  templates: TemplateSummary[]
  onSaved: () => void
}

export function AgentEditor({ agentId, agents, stages, capabilities, templates, onSaved }: AgentEditorProps) {
  const [files, setFiles] = useState<AgentFiles | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [topTab, setTopTab] = useState<TopTab>('files')
  const [activeFileKey, setActiveFileKey] = useState('agent_yaml')
  const [fileViewMode, setFileViewMode] = useState<FileViewMode>('raw')
  const [saving, setSaving] = useState(false)
  const [saveMessage, setSaveMessage] = useState<{ kind: 'ok' | 'warn' | 'error'; text: string } | null>(null)
  const [showAddSkillModal, setShowAddSkillModal] = useState(false)
  const [pendingRemoveSkillId, setPendingRemoveSkillId] = useState<string | null>(null)
  const [accepting, setAccepting] = useState(false)
  const [fileFixFeedback, setFileFixFeedback] = useState('')
  const [fileFixing, setFileFixing] = useState(false)
  const [fileFixError, setFileFixError] = useState<string | null>(null)
  const [fileFixMessage, setFileFixMessage] = useState<string | null>(null)

  useEffect(() => {
    setLoading(true)
    setLoadError(null)
    setFiles(null)
    setSaveMessage(null)
    setTopTab('files')
    setActiveFileKey('agent_yaml')
    api
      .getAgentFiles(agentId)
      .then(setFiles)
      .catch((err) => setLoadError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false))
  }, [agentId])

  useEffect(() => {
    setFileFixFeedback('')
    setFileFixError(null)
    setFileFixMessage(null)
  }, [activeFileKey])

  const refetchFiles = async () => {
    const refreshed = await api.getAgentFiles(agentId)
    setFiles(refreshed)
    return refreshed
  }

  const skillGroups = files ? buildSkillGroups(files) : []
  const allFileTabs = skillGroups.flatMap((g) => g.tabs)
  const activeTab = allFileTabs.find((t) => t.key === activeFileKey) ?? allFileTabs[0]

  const handleSave = async () => {
    if (!files) return
    setSaving(true)
    setSaveMessage(null)
    try {
      const result = await api.saveAgentFiles(agentId, {
        agent_yaml: files.agent_yaml,
        skills: files.skills,
      })
      if (result.status === 'ok') {
        setSaveMessage({ kind: 'ok', text: 'Saved and validated — this agent is live.' })
        onSaved()
      } else {
        setSaveMessage({
          kind: 'warn',
          text: `Files saved, but the agent failed to load: ${result.error}`,
        })
      }
    } catch (err) {
      setSaveMessage({
        kind: 'error',
        text: err instanceof ApiRequestError ? err.message : String(err),
      })
    } finally {
      setSaving(false)
    }
  }

  const acceptDraft = async () => {
    if (!files) return
    // A targeted line replace, not a parse+re-dump — re-dumping the whole
    // YAML risks silently reformatting the purpose block/comments elsewhere
    // in the file for a change that's really just two flags.
    const updatedYaml = files.agent_yaml
      .replace(/^draft:\s*true\s*$/m, 'draft: false')
      .replace(/^routable:\s*false\s*$/m, 'routable: true')
    setAccepting(true)
    setSaveMessage(null)
    try {
      const result = await api.saveAgentFiles(agentId, { agent_yaml: updatedYaml, skills: files.skills })
      setFiles({ ...files, agent_yaml: updatedYaml })
      if (result.status === 'ok') {
        setSaveMessage({ kind: 'ok', text: 'Accepted — this agent is live.' })
        onSaved()
      } else {
        setSaveMessage({ kind: 'warn', text: `Saved, but the agent failed to load: ${result.error}` })
      }
    } catch (err) {
      setSaveMessage({ kind: 'error', text: err instanceof ApiRequestError ? err.message : String(err) })
    } finally {
      setAccepting(false)
    }
  }

  const existingSkillIds = files ? Object.keys(files.skills) : []

  const handleAddSkill = async (payload: {
    skill_id: string
    mode: 'scaffold' | 'attach_existing'
    has_rules?: boolean
    template_id?: string
    description?: string
    purpose?: string
  }) => {
    const result = await api.addSkill(agentId, payload)
    await refetchFiles()
    onSaved()
    setShowAddSkillModal(false)
    setSaveMessage(
      result.status === 'ok' ? null : { kind: 'warn', text: `Skill added, but the agent failed to load: ${result.error}` },
    )
    setActiveFileKey(`skill:${result.skill_id}:skill_yaml`)
  }

  const confirmRemoveSkill = async () => {
    if (!pendingRemoveSkillId) return
    const skillId = pendingRemoveSkillId
    setPendingRemoveSkillId(null)
    const result = await api.removeSkill(agentId, skillId)
    await refetchFiles()
    onSaved()
    const prefix = `skill:${skillId}:`
    setActiveFileKey((cur) => (cur.startsWith(prefix) ? 'agent_yaml' : cur))
    setSaveMessage(
      result.status === 'ok' ? null : { kind: 'warn', text: `Skill removed, but the agent failed to load: ${result.error}` },
    )
  }

  const handleFixFile = async () => {
    if (!fileFixFeedback.trim() || !activeTab) return
    setFileFixing(true)
    setFileFixError(null)
    setFileFixMessage(null)
    try {
      const result = await api.editFileWithAI(agentId, activeTab.key, fileFixFeedback.trim())
      await refetchFiles()
      onSaved()
      if (result.status === 'ok') {
        setFileFixFeedback('')
        setFileFixMessage('Applied your feedback.')
      } else {
        setFileFixMessage(null)
        setFileFixError(`Saved, but the agent failed to load: ${result.error}`)
      }
    } catch (err) {
      setFileFixError(err instanceof ApiRequestError ? err.message : String(err))
    } finally {
      setFileFixing(false)
    }
  }

  if (loading) {
    return (
      <div className="flex h-full" aria-busy="true" aria-label="Loading agent">
        <div className="w-56 shrink-0 border-r border-neutral-200 p-4 flex flex-col gap-2 bg-white">
          <Skeleton className="h-3 w-16 mb-1" />
          <Skeleton className="h-6 w-full" />
          <Skeleton className="h-6 w-full" />
          <Skeleton className="h-3 w-16 mt-3 mb-1" />
          <Skeleton className="h-6 w-full" />
        </div>
        <div className="flex-1 p-4 bg-neutral-50">
          <Skeleton className="h-full w-full" />
        </div>
      </div>
    )
  }
  if (loadError) return <p className="text-red-600 text-sm p-6">{loadError}</p>
  if (!files) return null

  let isDraft = false
  try {
    isDraft = (load(files.agent_yaml) as { draft?: boolean } | undefined)?.draft === true
  } catch {
    // mid-edit invalid YAML — just don't show the draft banner until it's valid again
  }

  return (
    <div className="flex flex-col h-full bg-white">
      <div className="flex items-center justify-between border-b border-neutral-200 px-5">
        <div className="flex gap-5">
          {(['files', 'playground', 'integrate', 'reference'] as TopTab[]).map((tab) => (
            <button
              key={tab}
              onClick={() => setTopTab(tab)}
              className={`py-3.5 text-sm font-medium capitalize border-b-2 transition-colors cursor-pointer ${
                topTab === tab
                  ? 'border-brand-600 text-neutral-900'
                  : 'border-transparent text-neutral-400 hover:text-neutral-700'
              }`}
            >
              {tab === 'playground' ? 'Playground' : tab === 'integrate' ? 'Integrate' : tab}
            </button>
          ))}
        </div>

        {topTab === 'files' && (
          <div className="flex items-center gap-3 py-2">
            {saveMessage && (
              <Badge
                tone={saveMessage.kind === 'ok' ? 'success' : saveMessage.kind === 'warn' ? 'warning' : 'danger'}
                className="rounded-full px-2.5 py-1 text-xs"
              >
                {saveMessage.text}
              </Badge>
            )}
            <Button size="sm" onClick={handleSave} disabled={saving}>
              {saving ? 'Saving…' : 'Save'}
            </Button>
          </div>
        )}
      </div>

      <div className="flex-1 overflow-hidden flex flex-col">
        {topTab === 'files' && isDraft && (
          <div className="shrink-0 border-b border-amber-200 bg-amber-50 px-4 py-3">
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2 min-w-0">
                <Badge tone="warning" uppercase>draft — needs review</Badge>
                <span className="text-xs text-amber-800">
                  Generated by AI. Review the files below — open any file to fix it with AI, or edit it directly.
                </span>
              </div>
              <Button variant="success" size="sm" className="shrink-0" onClick={acceptDraft} disabled={accepting}>
                {accepting ? 'Accepting…' : 'Accept draft'}
              </Button>
            </div>
          </div>
        )}

        {topTab === 'files' && (
          <div className="flex flex-1 min-h-0">
            <div className="w-60 shrink-0 border-r border-neutral-200 overflow-y-auto py-4 bg-neutral-50/60">
              {skillGroups.map((group, i) => (
                <div
                  key={group.key}
                  className={`group mb-5 last:mb-0 pb-5 last:pb-0 ${
                    i < skillGroups.length - 1 ? 'border-b border-neutral-200/70' : ''
                  }`}
                >
                  <div className="px-4 flex items-center justify-between gap-1 mb-2">
                    <div className="flex items-center gap-1.5 min-w-0">
                      <span className="text-[10px] font-semibold uppercase tracking-wider text-neutral-400 truncate">
                        {group.label}
                      </span>
                      {group.kind === 'skill' && !group.hasRules && (
                        <Badge tone="success" uppercase className="text-[9px] px-1 py-0.5">guidance only</Badge>
                      )}
                    </div>
                    {group.kind === 'skill' && group.skillId && (
                      <DangerIconButton
                        label={`Remove ${group.skillId} from this agent`}
                        onClick={() => setPendingRemoveSkillId(group.skillId!)}
                        className="opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 shrink-0"
                      >
                        <TrashIcon />
                      </DangerIconButton>
                    )}
                  </div>
                  <div className="px-2.5 flex flex-col gap-0.5">
                    {group.tabs.map((tab) => {
                      const badge = languageBadge[tab.language]
                      const selected = activeTab.key === tab.key
                      return (
                        <button
                          key={tab.key}
                          onClick={() => setActiveFileKey(tab.key)}
                          className={`w-full flex items-center gap-2 text-left px-2.5 py-1.5 rounded-lg text-xs font-mono transition-colors cursor-pointer ${
                            selected
                              ? 'bg-white text-brand-700 shadow-sm ring-1 ring-brand-100'
                              : 'text-neutral-500 hover:bg-white/70 hover:text-neutral-800'
                          }`}
                        >
                          <span
                            className={`shrink-0 w-4 h-4 rounded border flex items-center justify-center text-[9px] font-sans font-semibold ${badge.className}`}
                          >
                            {badge.text}
                          </span>
                          <span className="truncate">{tab.label}</span>
                        </button>
                      )
                    })}
                  </div>
                </div>
              ))}

              <div className="px-2.5 mt-1">
                <button
                  onClick={() => setShowAddSkillModal(true)}
                  className="w-full text-left px-2.5 py-1.5 rounded-lg text-xs text-neutral-500 hover:bg-white hover:text-neutral-800 transition-colors cursor-pointer border border-dashed border-neutral-300"
                >
                  + Add skill
                </button>
              </div>
            </div>

            <div className="flex-1 flex flex-col min-w-0 p-4 bg-neutral-50">
              <div className="flex items-center justify-between gap-3 mb-3 shrink-0">
                <h2 className="text-sm font-mono text-neutral-600 truncate">{activeTab.label}</h2>

                {activeTab.language === 'yaml' && (
                  <div className="shrink-0">
                    <SegmentedControl
                      options={[
                        { value: 'raw', label: 'Raw' },
                        { value: 'preview', label: 'Preview' },
                      ]}
                      value={fileViewMode}
                      onChange={setFileViewMode}
                    />
                  </div>
                )}
              </div>

              <div className="shrink-0 mb-3 border border-neutral-200 rounded-lg bg-white p-3">
                <div className="flex gap-2">
                  <TextInput
                    className="flex-1"
                    placeholder={`e.g. change this feature, add that field…`}
                    value={fileFixFeedback}
                    onChange={(e) => setFileFixFeedback(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && !fileFixing && handleFixFile()}
                  />
                  <Button
                    size="sm"
                    className="shrink-0"
                    onClick={handleFixFile}
                    disabled={fileFixing || !fileFixFeedback.trim()}
                  >
                    {fileFixing ? 'Fixing…' : 'Fix with AI'}
                  </Button>
                </div>
                <p className="text-[11px] text-neutral-400 mt-1.5">
                  Edits only {activeTab.label} — describe the change and it applies just that, preserving
                  everything else in this file exactly as it is. Works on live agents, not just drafts.
                </p>
                {fileFixMessage && <p className="text-xs text-emerald-700 mt-2">{fileFixMessage}</p>}
                {fileFixError && <p className="text-xs text-red-600 mt-2">{fileFixError}</p>}
              </div>

              <div className="flex-1 min-h-0 border border-neutral-200 rounded-lg overflow-hidden shadow-sm bg-white">
                {activeTab.language === 'yaml' && fileViewMode === 'preview' ? (
                  <YamlPreview value={activeTab.get(files)} />
                ) : (
                  <CodeEditor
                    value={activeTab.get(files)}
                    language={activeTab.language}
                    onChange={(value) => setFiles(activeTab.set(files, value))}
                  />
                )}
              </div>
            </div>
          </div>
        )}

        {topTab === 'playground' && (
          <div className="h-full p-4 bg-neutral-50">
            <Playground agents={agents} lockedAgentId={agentId} />
          </div>
        )}

        {topTab === 'integrate' && (
          <div className="h-full p-4 bg-neutral-50 overflow-y-auto">
            <IntegratePanel
              agentId={agentId}
              inputSchema={agents.find((a) => a.agent_id === agentId)?.input_schema}
            />
          </div>
        )}

        {topTab === 'reference' && (
          <div className="h-full p-4 bg-neutral-50">
            <ReferencePanel stages={stages} capabilities={capabilities} />
          </div>
        )}
      </div>

      {showAddSkillModal && (
        <AddSkillModal
          existingSkillIds={existingSkillIds}
          templates={templates}
          onClose={() => setShowAddSkillModal(false)}
          onAdd={handleAddSkill}
        />
      )}

      {pendingRemoveSkillId && (
        <ConfirmDialog
          title={`Remove skill "${pendingRemoveSkillId}"?`}
          message="This detaches it from this agent immediately (not part of the Save flow above) and discards any unsaved edits in other tabs. The skill package itself stays in skills_library, untouched."
          confirmLabel="Remove"
          onConfirm={confirmRemoveSkill}
          onCancel={() => setPendingRemoveSkillId(null)}
        />
      )}
    </div>
  )
}
