/**
 * The Projects page: create one, open it, add what belongs to it. The API
 * is mocked at the client, and the page invalidates its query after every
 * write, so what it shows is what the server has.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi } from 'vitest'

import type { ProjectView } from '../../api/projects'
import { ProjectsSettings } from './ProjectsSettings'

const store = vi.hoisted(() => ({ projects: [] as ProjectView[] }))

vi.mock('../../api/projects', async (importOriginal) => {
  const original = await importOriginal<typeof import('../../api/projects')>()
  return {
    ...original,
    listProjects: vi.fn(async () => store.projects),
    createProject: vi.fn(async (body: { name: string }) => {
      const project: ProjectView = { id: 1, slug: 'hexdeck', name: body.name, description: '', status: 'active', colour: '', position: 0, repos: [], milestones: [], items: [] }
      store.projects.push(project)
      return project
    }),
    addItem: vi.fn(async (projectId: number, body: { title: string }) => {
      const item = { id: 9, milestone_id: null, title: body.title, notes: '', status: 'todo' as const, issue: '', url: '', position: 0 }
      store.projects.find((p) => p.id === projectId)!.items.push(item)
      return item
    }),
    addMilestone: vi.fn(async (projectId: number, body: { title: string; target_date?: string | null }) => {
      const milestone = { id: 4, title: body.title, target_date: body.target_date ?? null, status: 'open' as const, position: 0 }
      store.projects.find((p) => p.id === projectId)!.milestones.push(milestone)
      return milestone
    }),
  }
})

function show() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <ProjectsSettings />
    </QueryClientProvider>,
  )
}

describe('ProjectsSettings', () => {
  beforeEach(() => {
    store.projects = []
  })

  it('creates a project, which opens, and adds a milestone and an item', async () => {
    show()
    const user = userEvent.setup()
    await user.type(await screen.findByLabelText(/Name of the new project/), 'HexDeck')
    await user.click(screen.getByRole('button', { name: /^Create$/ }))
    // A project just created is the open one; nothing to click.
    await user.type(await screen.findByLabelText(/New milestone/), 'M3')
    await user.click(screen.getByRole('button', { name: /Add milestone/ }))
    expect(await screen.findByDisplayValue('M3')).toBeInTheDocument()
    await user.type(await screen.findByLabelText(/New item/), 'Roadmap card')
    await user.click(screen.getByRole('button', { name: /Add item/ }))
    expect(await screen.findByDisplayValue('Roadmap card')).toBeInTheDocument()
  })

  it('says so when there is nothing yet', async () => {
    show()
    expect(await screen.findByText(/No projects yet/)).toBeInTheDocument()
  })
})
