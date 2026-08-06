import { useMemo, useState } from 'react'
import { Button, ChoiceCard, FieldLabel, Modal, TextArea, TextInput } from './ui'
import type { AgentSummary, GenerateAgentEvent, TemplateSummary } from '../types'

interface NewAgentModalProps {
  agents: AgentSummary[]
  templates: TemplateSummary[]
  onClose: () => void
  onCreate: (agentId: string, skillId: string, purpose: string, templateId: string) => Promise<void>
  onGenerate: (agentId: string, purpose: string, onEvent: (event: GenerateAgentEvent) => void) => Promise<void>
}

type SkillMode = 'new' | 'existing'
type BuildMode = 'template' | 'describe'
type StepStatus = 'pending' | 'active' | 'done' | 'error'
interface ProgressStep {
  id: string
  label: string
  status: StepStatus
}

function stepsFromEvent(prev: ProgressStep[], event: GenerateAgentEvent): ProgressStep[] {
  let id: string
  let label: string
  let status: StepStatus

  if (event.step === 'decompose') {
    id = 'decompose'
    label = 'Reading your description'
    status = event.status === 'done' ? 'done' : 'active'
  } else if (event.step === 'generate_skill') {
    id = `skill:${event.skill_id}`
    label = event.total && event.total > 1
      ? `Drafting rules for "${event.skill_id}" (${event.index}/${event.total})`
      : 'Drafting the rules'
    status = event.status === 'done' ? 'done' : 'active'
  } else if (event.step === 'save') {
    id = 'save'
    label = 'Saving the agent'
    status = event.status === 'done' ? 'done' : 'active'
  } else if (event.step === 'validate') {
    id = 'validate'
    label = 'Checking it loads cleanly'
    status = event.status === 'error' ? 'error' : event.status === 'done' ? 'done' : 'active'
  } else {
    return prev
  }

  const idx = prev.findIndex((s) => s.id === id)
  if (idx === -1) return [...prev, { id, label, status }]
  const next = [...prev]
  next[idx] = { ...next[idx], label, status }
  return next
}

function ProgressChecklist({ steps }: { steps: ProgressStep[] }) {
  return (
    <ul className="mb-4 flex flex-col gap-2 rounded-md border border-neutral-200 bg-neutral-50 p-3">
      {steps.map((step) => (
        <li key={step.id} className="flex items-center gap-2.5 text-sm">
          {step.status === 'done' ? (
            <svg viewBox="0 0 24 24" className="w-4 h-4 shrink-0 text-brand-600" fill="none" stroke="currentColor" strokeWidth="2.5">
              <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
            </svg>
          ) : step.status === 'error' ? (
            <svg viewBox="0 0 24 24" className="w-4 h-4 shrink-0 text-red-600" fill="none" stroke="currentColor" strokeWidth="2.5">
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          ) : step.status === 'active' ? (
            <span className="w-4 h-4 shrink-0 rounded-full border-2 border-brand-300 border-t-brand-600 animate-spin" />
          ) : (
            <span className="w-4 h-4 shrink-0 rounded-full border-2 border-neutral-300" />
          )}
          <span
            className={
              step.status === 'done'
                ? 'text-neutral-500 line-through decoration-neutral-300'
                : step.status === 'error'
                  ? 'text-red-700'
                  : step.status === 'active'
                    ? 'text-neutral-900 font-medium'
                    : 'text-neutral-400'
            }
          >
            {step.label}
          </span>
        </li>
      ))}
    </ul>
  )
}

export function NewAgentModal({ agents, templates, onClose, onCreate, onGenerate }: NewAgentModalProps) {
  const existingSkills = useMemo(() => {
    const byId = new Map<string, string[]>()
    for (const agent of agents) {
      for (const skillId of agent.skills ?? []) {
        byId.set(skillId, [...(byId.get(skillId) ?? []), agent.agent_id])
      }
    }
    return Array.from(byId, ([skillId, usedBy]) => ({ skillId, usedBy })).sort((a, b) =>
      a.skillId.localeCompare(b.skillId),
    )
  }, [agents])

  const [buildMode, setBuildMode] = useState<BuildMode>('template')
  const [agentId, setAgentId] = useState('')
  const [purpose, setPurpose] = useState('')
  const [templateId, setTemplateId] = useState('blank')
  const [skillMode, setSkillMode] = useState<SkillMode>('new')
  const [newSkillId, setNewSkillId] = useState('')
  const [existingSkillId, setExistingSkillId] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [progressSteps, setProgressSteps] = useState<ProgressStep[]>([])

  const selectedTemplate = templates.find((t) => t.id === templateId) ?? null

  const resolvedSkillId =
    skillMode === 'existing' ? existingSkillId : newSkillId.trim() || agentId.trim()
  const canSubmit =
    buildMode === 'describe'
      ? agentId.trim().length > 0 && purpose.trim().length > 0 && !submitting
      : agentId.trim().length > 0 && !!resolvedSkillId && !submitting

  const handleSubmit = async () => {
    setSubmitting(true)
    setError(null)
    try {
      if (buildMode === 'describe') {
        setProgressSteps([])
        await onGenerate(agentId.trim(), purpose.trim(), (event) => {
          setProgressSteps((prev) => stepsFromEvent(prev, event))
        })
      } else {
        if (!resolvedSkillId) return
        await onCreate(agentId.trim(), resolvedSkillId, purpose.trim(), templateId)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Modal onClose={onClose}>
        <h2 className="text-neutral-900 font-semibold mb-1">New Agent</h2>
        <p className="text-xs text-neutral-500 mb-4 leading-relaxed">
          {buildMode === 'describe'
            ? 'An AI drafts real gates, scoring, and product rules from your description. It always lands as a draft — review it in the editor before it\'s trusted or routable.'
            : <>Scaffolds agents/&lt;id&gt;/agent.yaml with a starter pipeline
              {selectedTemplate ? ` (${selectedTemplate.pipeline.join(' → ')})` : ''}, plus a skill
              package it runs against.</>}
        </p>

        <FieldLabel>Agent name</FieldLabel>
        <TextInput
          className="mb-4"
          placeholder="e.g. financial_literacy"
          value={agentId}
          onChange={(e) => setAgentId(e.target.value)}
        />

        <label className="block text-xs text-neutral-400 mb-2">How do you want to build it?</label>
        <div className="grid grid-cols-2 gap-2 mb-4">
          <ChoiceCard
            selected={buildMode === 'template'}
            onClick={() => setBuildMode('template')}
            title="From template"
            subtitle="Pick a starting shape, fill in rules yourself"
          />
          <ChoiceCard
            selected={buildMode === 'describe'}
            onClick={() => setBuildMode('describe')}
            title="Describe it"
            subtitle="AI drafts gates/scoring — creates a draft to review"
          />
        </div>

        {buildMode === 'template' ? (
          <>
            {templates.length > 0 && (
              <>
                <label className="block text-xs text-neutral-400 mb-2">Template</label>
                <div className="grid grid-cols-2 gap-2 mb-4">
                  {templates.map((template) => (
                    <ChoiceCard
                      key={template.id}
                      selected={templateId === template.id}
                      onClick={() => setTemplateId(template.id)}
                      title={template.label}
                      subtitle={template.description}
                    />
                  ))}
                </div>
              </>
            )}

            <label className="block text-xs text-neutral-400 mb-2">Skill package</label>
            <div className="grid grid-cols-2 gap-2 mb-3">
              <ChoiceCard
                selected={skillMode === 'new'}
                onClick={() => setSkillMode('new')}
                title="Create new"
                subtitle="Scaffold a fresh skill for this agent"
              />
              <ChoiceCard
                selected={skillMode === 'existing'}
                disabled={existingSkills.length === 0}
                onClick={() => {
                  setSkillMode('existing')
                  setExistingSkillId((cur) => cur ?? existingSkills[0]?.skillId ?? null)
                }}
                title="Reuse existing"
                subtitle={existingSkills.length === 0 ? 'No skills yet' : 'Point this agent at an existing skill'}
              />
            </div>

            {skillMode === 'new' ? (
              <TextInput
                className="mb-4"
                placeholder="Skill id (defaults to agent name)"
                value={newSkillId}
                onChange={(e) => setNewSkillId(e.target.value)}
              />
            ) : (
              <div className="mb-4 max-h-40 overflow-y-auto grid grid-cols-2 gap-2 pr-0.5">
                {existingSkills.map(({ skillId, usedBy }) => (
                  <ChoiceCard
                    key={skillId}
                    mono
                    selected={existingSkillId === skillId}
                    onClick={() => setExistingSkillId(skillId)}
                    title={skillId}
                    subtitle={`used by ${usedBy.length} agent${usedBy.length === 1 ? '' : 's'}`}
                  />
                ))}
              </div>
            )}

            <FieldLabel>Purpose</FieldLabel>
            <TextArea
              className="mb-4"
              rows={3}
              placeholder="One paragraph describing what this agent does."
              value={purpose}
              onChange={(e) => setPurpose(e.target.value)}
            />
          </>
        ) : (
          <>
            <FieldLabel>Describe what this agent should do</FieldLabel>
            <TextArea
              className="mb-4"
              rows={6}
              placeholder="e.g. Qualify SME loan applicants based on debt ratio and credit score. Reject anyone with an active default. Recommend a working capital loan for strong applicants."
              value={purpose}
              onChange={(e) => setPurpose(e.target.value)}
              disabled={submitting}
            />
            {submitting && progressSteps.length > 0 && <ProgressChecklist steps={progressSteps} />}
          </>
        )}

        {error && (
          <p className="text-xs text-red-700 bg-red-50 border border-red-200 rounded p-2.5 mb-3">{error}</p>
        )}

        <div className="flex justify-end gap-2">
          <Button variant="ghost" size="sm" onClick={onClose}>
            Cancel
          </Button>
          <Button size="sm" onClick={handleSubmit} disabled={!canSubmit}>
            {buildMode === 'describe'
              ? submitting ? 'Generating…' : 'Generate'
              : submitting ? 'Creating…' : 'Create'}
          </Button>
        </div>
    </Modal>
  )
}
