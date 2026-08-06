import { useEffect, useState } from 'react'
import { api, ApiRequestError } from '../api'
import { Button, ChoiceCard, FieldLabel, Modal, TextArea, TextInput } from './ui'
import type { SkillCatalogEntry, TemplateSummary } from '../types'

interface AddSkillModalProps {
  existingSkillIds: string[]
  templates: TemplateSummary[]
  onClose: () => void
  onAdd: (payload: {
    skill_id: string
    mode: 'scaffold' | 'attach_existing'
    has_rules?: boolean
    template_id?: string
    description?: string
    purpose?: string
  }) => Promise<void>
}

type Mode = 'scaffold' | 'attach_existing'

export function AddSkillModal({ existingSkillIds, templates, onClose, onAdd }: AddSkillModalProps) {
  const [mode, setMode] = useState<Mode>('scaffold')
  const [hasRules, setHasRules] = useState(true)
  const [skillId, setSkillId] = useState('')
  const [templateId, setTemplateId] = useState('blank')
  const [purpose, setPurpose] = useState('')
  const [description, setDescription] = useState('')
  const [catalog, setCatalog] = useState<SkillCatalogEntry[]>([])
  const [attachSkillId, setAttachSkillId] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.listSkillCatalog().then((res) =>
      setCatalog(res.skills.filter((s) => !existingSkillIds.includes(s.skill_id))),
    )
  }, [existingSkillIds])

  const canSubmit =
    !submitting && (mode === 'scaffold' ? skillId.trim().length > 0 : attachSkillId !== null)

  const handleSubmit = async () => {
    setSubmitting(true)
    setError(null)
    try {
      if (mode === 'scaffold') {
        await onAdd({
          skill_id: skillId.trim(),
          mode: 'scaffold',
          has_rules: hasRules,
          template_id: hasRules ? templateId : undefined,
          purpose: hasRules ? purpose : undefined,
          description: hasRules ? undefined : description,
        })
      } else {
        await onAdd({ skill_id: attachSkillId as string, mode: 'attach_existing' })
      }
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : String(err))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Modal onClose={onClose}>
        <h2 className="text-neutral-900 font-semibold mb-1">Add Skill</h2>
        <p className="text-xs text-neutral-500 mb-4 leading-relaxed">
          Adds a skill this agent can load — on its own or alongside others — when a request matches it.
        </p>

        <div className="grid grid-cols-2 gap-2 mb-4">
          <ChoiceCard
            selected={mode === 'scaffold'}
            onClick={() => setMode('scaffold')}
            title="Scaffold new"
            subtitle="Create a fresh skill"
          />
          <ChoiceCard
            selected={mode === 'attach_existing'}
            disabled={catalog.length === 0}
            onClick={() => {
              setMode('attach_existing')
              setAttachSkillId((cur) => cur ?? catalog[0]?.skill_id ?? null)
            }}
            title="Attach existing"
            subtitle={catalog.length === 0 ? 'No other skills yet' : 'Point at a skill already in skills_library'}
          />
        </div>

        {mode === 'scaffold' ? (
          <>
            <FieldLabel>Skill id</FieldLabel>
            <TextInput
              className="mb-4"
              placeholder="e.g. advisor_growth"
              value={skillId}
              onChange={(e) => setSkillId(e.target.value)}
            />

            <label className="block text-xs text-neutral-400 mb-2">Rules</label>
            <div className="grid grid-cols-2 gap-2 mb-4">
              <ChoiceCard
                selected={hasRules}
                onClick={() => setHasRules(true)}
                title="Give it rules"
                subtitle="Gates, scoring, product-fit — a real deterministic rule-set"
              />
              <ChoiceCard
                selected={!hasRules}
                onClick={() => setHasRules(false)}
                title="Guidance only"
                subtitle="No rules — just instructions the model reads when loaded"
              />
            </div>

            {hasRules ? (
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

                <FieldLabel>Purpose</FieldLabel>
                <TextArea
                  className="mb-4"
                  rows={2}
                  placeholder="What does this skill's rule-set evaluate?"
                  value={purpose}
                  onChange={(e) => setPurpose(e.target.value)}
                />
              </>
            ) : (
              <>
                <FieldLabel>Description</FieldLabel>
                <TextArea
                  className="mb-4"
                  rows={2}
                  placeholder="When is this guidance relevant? This is what the model reads to decide whether to load it."
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                />
              </>
            )}
          </>
        ) : (
          <div className="mb-4 max-h-52 overflow-y-auto grid grid-cols-1 gap-2 pr-0.5">
            {catalog.map((entry) => (
              <ChoiceCard
                key={entry.skill_id}
                mono
                selected={attachSkillId === entry.skill_id}
                onClick={() => setAttachSkillId(entry.skill_id)}
                title={entry.skill_id}
                subtitle={`${entry.kind === 'deterministic' ? 'has rules' : 'guidance only'} — ${entry.description}`}
              />
            ))}
          </div>
        )}

        {error && (
          <p className="text-xs text-red-700 bg-red-50 border border-red-200 rounded p-2.5 mb-3">{error}</p>
        )}

        <div className="flex justify-end gap-2">
          <Button variant="ghost" size="sm" onClick={onClose}>
            Cancel
          </Button>
          <Button size="sm" onClick={handleSubmit} disabled={!canSubmit}>
            {submitting ? 'Adding…' : 'Add'}
          </Button>
        </div>
    </Modal>
  )
}
