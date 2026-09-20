/**
 * Projects: what is being built, with milestones, items and the
 * repositories a project reads. The cards on the boards show this; here it
 * is managed in full. Every write goes to the server and the list is read
 * again, so the page never shows a state the server does not have.
 */
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowDown, ArrowUp, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError } from '../../api/client'
import {
  addItem,
  addMilestone,
  addRepo,
  createProject,
  deleteItem,
  deleteMilestone,
  deleteProject,
  listProjects,
  orderItems,
  patchItem,
  patchMilestone,
  patchProject,
  removeRepo,
  type ItemStatus,
  type ItemView,
  type MilestoneStatus,
  type MilestoneView,
  type ProjectStatus,
  type ProjectView,
} from '../../api/projects'
import { Field } from '../../components/ui'
import { SettingsCard } from './SettingsCard'

const PROJECT_STATUSES: ProjectStatus[] = ['active', 'paused', 'done']
const MILESTONE_STATUSES: MilestoneStatus[] = ['open', 'done']
const ITEM_STATUSES: ItemStatus[] = ['todo', 'doing', 'done']

export function ProjectsSettings() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const projects = useQuery({ queryKey: ['projects'], queryFn: listProjects })
  const [chosen, setChosen] = useState<number | null>(null)
  const [newName, setNewName] = useState('')
  const [error, setError] = useState('')
  const rows = projects.data ?? []
  const current = rows.find((p) => p.id === chosen) ?? null

  /** One write, then the list again. Failures land in one line under the form. */
  const run = async (write: () => Promise<unknown>) => {
    setError('')
    try {
      await write()
      await queryClient.invalidateQueries({ queryKey: ['projects'] })
    } catch (failure) {
      setError(failure instanceof ApiError ? failure.message : t('errors.network'))
    }
  }

  return (
    <div className="space-y-4">
      <SettingsCard title={t('projects.page.title')} description={t('projects.page.help')}>
        {rows.length === 0 && !projects.isPending && <p className="mb-3 text-sm text-muted">{t('projects.page.none')}</p>}
        <ul className="mb-4 space-y-1.5">
          {rows.map((project) => (
            <li key={project.id}>
              <button
                type="button"
                className={`flex w-full items-center gap-2 rounded-xl border p-2.5 text-left text-sm ${project.id === chosen ? 'border-accent' : 'border-line'}`}
                onClick={() => setChosen(project.id === chosen ? null : project.id)}
              >
                <span className="h-3 w-3 flex-none hex-clip" style={{ background: project.colour || 'var(--nd-accent)' }} />
                <span className="flex-1 truncate">{project.name}</span>
                <span className="chip">{t(`projects.status.${project.status}`)}</span>
                <span className="text-[11px] text-faint">
                  {project.milestones.length} · {project.items.filter((i) => i.status === 'done').length}/{project.items.length}
                </span>
              </button>
            </li>
          ))}
        </ul>
        <Field label={t('projects.page.newName')} htmlFor="p-new">
          <div id="p-new-row" className="flex gap-2">
            <input id="p-new" className="input" value={newName} onChange={(e) => setNewName(e.target.value)} />
            <button
              className="btn btn-accent flex-none"
              disabled={!newName.trim()}
              onClick={() =>
                void run(async () => {
                  const created = await createProject({ name: newName.trim() })
                  setNewName('')
                  setChosen(created.id)
                })
              }
            >
              {t('projects.page.create')}
            </button>
          </div>
        </Field>
        {error && <p className="mt-2 text-sm text-bad">{error}</p>}
      </SettingsCard>

      {current && <ProjectDetail project={current} run={run} onDeleted={() => setChosen(null)} />}
    </div>
  )
}

function ProjectDetail({ project, run, onDeleted }: { project: ProjectView; run: (write: () => Promise<unknown>) => Promise<void>; onDeleted: () => void }) {
  const { t } = useTranslation()
  const [name, setName] = useState(project.name)
  const [description, setDescription] = useState(project.description)
  const [status, setStatus] = useState<ProjectStatus>(project.status)
  const [colour, setColour] = useState(project.colour)
  const [repo, setRepo] = useState('')
  const [milestoneTitle, setMilestoneTitle] = useState('')
  const [milestoneDate, setMilestoneDate] = useState('')
  const [itemTitle, setItemTitle] = useState('')

  return (
    <>
      <SettingsCard title={project.name}>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label={t('projects.page.name')} htmlFor="p-name">
            <input id="p-name" className="input" value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <Field label={t('projects.page.status')} htmlFor="p-status">
            <select id="p-status" className="input" value={status} onChange={(e) => setStatus(e.target.value as ProjectStatus)}>
              {PROJECT_STATUSES.map((s) => (
                <option key={s} value={s}>{t(`projects.status.${s}`)}</option>
              ))}
            </select>
          </Field>
          <Field label={t('projects.page.description')} htmlFor="p-description">
            <textarea id="p-description" className="input" rows={3} value={description} onChange={(e) => setDescription(e.target.value)} />
          </Field>
          <Field label={t('projects.page.colour')} htmlFor="p-colour" help={t('projects.page.colourHelp')}>
            <div id="p-colour-row" className="flex items-center gap-2">
              <input id="p-colour" className="input" value={colour} placeholder="#3aa0ff" onChange={(e) => setColour(e.target.value)} />
              <span className="h-6 w-6 flex-none hex-clip" style={{ background: colour || 'var(--nd-accent)' }} aria-hidden="true" />
            </div>
          </Field>
        </div>
        <div className="mt-3 flex gap-2">
          <button className="btn btn-accent" onClick={() => void run(() => patchProject(project.id, { name: name.trim(), description, status, colour }))}>
            {t('common.save')}
          </button>
          <button
            className="btn btn-danger ml-auto"
            onClick={() => {
              if (!window.confirm(t('projects.page.deleteBody'))) return
              void run(() => deleteProject(project.id)).then(onDeleted)
            }}
          >
            {t('projects.page.delete')}
          </button>
        </div>
      </SettingsCard>

      <SettingsCard title={t('projects.page.repos')} description={t('projects.page.repoHelp')}>
        <ul className="mb-3 space-y-1.5">
          {project.repos.map((r) => (
            <li key={r.id} className="flex items-center gap-2 rounded-xl border border-line p-2 text-sm">
              <a className="flex-1 truncate" href={r.url} target="_blank" rel="noreferrer">{r.repo}</a>
              <button className="btn btn-icon h-7 w-7 btn-danger" onClick={() => void run(() => removeRepo(project.id, r.id))} aria-label={t('common.delete')}>
                <Trash2 size={14} />
              </button>
            </li>
          ))}
        </ul>
        <Field label={t('projects.page.newRepo')} htmlFor="p-repo">
          <div id="p-repo-row" className="flex gap-2">
            <input id="p-repo" className="input" value={repo} placeholder="owner/name" onChange={(e) => setRepo(e.target.value)} />
            <button
              className="btn flex-none"
              disabled={!repo.trim()}
              onClick={() =>
                void run(async () => {
                  await addRepo(project.id, repo.trim())
                  setRepo('')
                })
              }
            >
              {t('projects.page.addRepo')}
            </button>
          </div>
        </Field>
      </SettingsCard>

      <SettingsCard title={t('projects.page.milestones')}>
        <ul className="mb-3 space-y-1.5">
          {project.milestones.map((m, index) => (
            <MilestoneRow key={m.id} milestone={m} first={index === 0} last={index === project.milestones.length - 1} siblings={project.milestones} run={run} />
          ))}
        </ul>
        <div className="grid gap-2 sm:grid-cols-[1fr_auto_auto]">
          <Field label={t('projects.page.newMilestone')} htmlFor="m-new">
            <input id="m-new" className="input" value={milestoneTitle} onChange={(e) => setMilestoneTitle(e.target.value)} />
          </Field>
          <Field label={t('projects.page.date')} htmlFor="m-date">
            <input id="m-date" className="input" type="date" value={milestoneDate} onChange={(e) => setMilestoneDate(e.target.value)} />
          </Field>
          <div className="flex items-end">
            <button
              className="btn"
              disabled={!milestoneTitle.trim()}
              onClick={() =>
                void run(async () => {
                  await addMilestone(project.id, { title: milestoneTitle.trim(), target_date: milestoneDate || null })
                  setMilestoneTitle('')
                  setMilestoneDate('')
                })
              }
            >
              {t('projects.page.addMilestone')}
            </button>
          </div>
        </div>
      </SettingsCard>

      <SettingsCard title={t('projects.page.items')}>
        <ul className="mb-3 space-y-1.5">
          {project.items.map((item, index) => (
            <ItemRow key={item.id} item={item} project={project} first={index === 0} last={index === project.items.length - 1} run={run} />
          ))}
        </ul>
        <Field label={t('projects.page.newItem')} htmlFor="i-new">
          <div id="i-new-row" className="flex gap-2">
            <input id="i-new" className="input" value={itemTitle} onChange={(e) => setItemTitle(e.target.value)} />
            <button
              className="btn flex-none"
              disabled={!itemTitle.trim()}
              onClick={() =>
                void run(async () => {
                  await addItem(project.id, { title: itemTitle.trim() })
                  setItemTitle('')
                })
              }
            >
              {t('projects.page.addItem')}
            </button>
          </div>
        </Field>
      </SettingsCard>
    </>
  )
}

/** Up and down swap positions with the neighbour; the server keeps them. */
function MilestoneRow({ milestone, first, last, siblings, run }: { milestone: MilestoneView; first: boolean; last: boolean; siblings: MilestoneView[]; run: (write: () => Promise<unknown>) => Promise<void> }) {
  const { t } = useTranslation()
  const [title, setTitle] = useState(milestone.title)
  const swap = (direction: -1 | 1) => {
    const index = siblings.findIndex((m) => m.id === milestone.id)
    const other = siblings[index + direction]
    if (!other) return
    void run(async () => {
      await patchMilestone(milestone.id, { position: other.position })
      await patchMilestone(other.id, { position: milestone.position })
    })
  }
  return (
    <li className="grid items-center gap-2 rounded-xl border border-line p-2 text-sm sm:grid-cols-[1fr_auto_auto_auto_auto]">
      <input
        className="input"
        aria-label={t('projects.page.milestoneTitle')}
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        onBlur={() => title.trim() && title !== milestone.title && void run(() => patchMilestone(milestone.id, { title: title.trim() }))}
      />
      <input
        className="input"
        type="date"
        aria-label={t('projects.page.date')}
        value={milestone.target_date ?? ''}
        onChange={(e) => void run(() => (e.target.value ? patchMilestone(milestone.id, { target_date: e.target.value }) : patchMilestone(milestone.id, { clear_date: true })))}
      />
      <select className="input" aria-label={t('projects.page.status')} value={milestone.status} onChange={(e) => void run(() => patchMilestone(milestone.id, { status: e.target.value as MilestoneStatus }))}>
        {MILESTONE_STATUSES.map((s) => (
          <option key={s} value={s}>{t(`projects.milestone.${s}`)}</option>
        ))}
      </select>
      <span className="flex gap-1">
        <button className="btn btn-icon h-7 w-7" disabled={first} onClick={() => swap(-1)} aria-label={t('projects.page.moveUp')}><ArrowUp size={14} /></button>
        <button className="btn btn-icon h-7 w-7" disabled={last} onClick={() => swap(1)} aria-label={t('projects.page.moveDown')}><ArrowDown size={14} /></button>
      </span>
      <button className="btn btn-icon h-7 w-7 btn-danger" onClick={() => void run(() => deleteMilestone(milestone.id))} aria-label={t('common.delete')}>
        <Trash2 size={14} />
      </button>
    </li>
  )
}

function ItemRow({ item, project, first, last, run }: { item: ItemView; project: ProjectView; first: boolean; last: boolean; run: (write: () => Promise<unknown>) => Promise<void> }) {
  const { t } = useTranslation()
  const [title, setTitle] = useState(item.title)
  const [issue, setIssue] = useState(item.issue)
  const move = (direction: -1 | 1) => {
    const ids = project.items.map((i) => i.id)
    const index = ids.indexOf(item.id)
    if (index + direction < 0 || index + direction >= ids.length) return
    ids.splice(index, 1)
    ids.splice(index + direction, 0, item.id)
    void run(() => orderItems(project.id, ids))
  }
  return (
    <li className="grid items-center gap-2 rounded-xl border border-line p-2 text-sm sm:grid-cols-[auto_1fr_auto_auto_auto_auto]">
      <select className="input" aria-label={t('projects.page.status')} value={item.status} onChange={(e) => void run(() => patchItem(item.id, { status: e.target.value as ItemStatus }))}>
        {ITEM_STATUSES.map((s) => (
          <option key={s} value={s}>{t(`projects.item.${s}`)}</option>
        ))}
      </select>
      <input
        className="input"
        aria-label={t('projects.page.itemTitle')}
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        onBlur={() => title.trim() && title !== item.title && void run(() => patchItem(item.id, { title: title.trim() }))}
      />
      <select
        className="input"
        aria-label={t('projects.page.milestone')}
        value={item.milestone_id ?? ''}
        onChange={(e) => void run(() => (e.target.value ? patchItem(item.id, { milestone_id: Number(e.target.value) }) : patchItem(item.id, { clear_milestone: true })))}
      >
        <option value="">{t('projects.page.noMilestone')}</option>
        {project.milestones.map((m) => (
          <option key={m.id} value={m.id}>{m.title}</option>
        ))}
      </select>
      <input
        className="input w-40"
        aria-label={t('projects.page.issue')}
        placeholder="owner/name#123"
        value={issue}
        onChange={(e) => setIssue(e.target.value)}
        onBlur={() => issue !== item.issue && void run(() => patchItem(item.id, { issue: issue.trim() }))}
      />
      <span className="flex gap-1">
        <button className="btn btn-icon h-7 w-7" disabled={first} onClick={() => move(-1)} aria-label={t('projects.page.moveUp')}><ArrowUp size={14} /></button>
        <button className="btn btn-icon h-7 w-7" disabled={last} onClick={() => move(1)} aria-label={t('projects.page.moveDown')}><ArrowDown size={14} /></button>
      </span>
      <button className="btn btn-icon h-7 w-7 btn-danger" onClick={() => void run(() => deleteItem(item.id))} aria-label={t('common.delete')}>
        <Trash2 size={14} />
      </button>
    </li>
  )
}
