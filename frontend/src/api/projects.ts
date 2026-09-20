/**
 * Projects, milestones and items: what is being built, kept in HexDeck's
 * own database. The settings page manages them; the cards read them and
 * may tick an item off or reorder the list.
 */
import { del, get, patch, post, put } from './client'

export type ProjectStatus = 'active' | 'paused' | 'done'
export type MilestoneStatus = 'open' | 'done'
export type ItemStatus = 'todo' | 'doing' | 'done'

export interface RepoView {
  id: number
  repo: string
  url: string
  position: number
}

export interface MilestoneView {
  id: number
  title: string
  target_date: string | null
  status: MilestoneStatus
  position: number
}

export interface ItemView {
  id: number
  milestone_id: number | null
  title: string
  notes: string
  status: ItemStatus
  issue: string
  url: string
  position: number
  /** When it is due, or null. */
  due_on: string | null
  /** Every how many days it comes back once done; 0 is a one-off. */
  repeat_days: number
  last_done: string | null
}

export interface ProjectView {
  id: number
  name: string
  slug: string
  description: string
  status: ProjectStatus
  colour: string
  position: number
  repos: RepoView[]
  milestones: MilestoneView[]
  items: ItemView[]
}

export const listProjects = () => get<ProjectView[]>('/projects')
export const createProject = (body: { name: string; description?: string; status?: ProjectStatus; colour?: string }) => post<ProjectView>('/projects', body)
export const patchProject = (id: number, body: Partial<Pick<ProjectView, 'name' | 'description' | 'status' | 'colour' | 'position'>>) => patch<ProjectView>(`/projects/${id}`, body)
export const deleteProject = (id: number) => del(`/projects/${id}`)
export const addRepo = (projectId: number, repo: string) => post<RepoView>(`/projects/${projectId}/repos`, { repo })
export const removeRepo = (projectId: number, repoId: number) => del(`/projects/${projectId}/repos/${repoId}`)
export const addMilestone = (projectId: number, body: { title: string; target_date?: string | null; status?: MilestoneStatus }) => post<MilestoneView>(`/projects/${projectId}/milestones`, body)
export const patchMilestone = (id: number, body: { title?: string; target_date?: string; clear_date?: boolean; status?: MilestoneStatus; position?: number }) => patch<MilestoneView>(`/milestones/${id}`, body)
export const deleteMilestone = (id: number) => del(`/milestones/${id}`)
export const addItem = (projectId: number, body: { title: string; notes?: string; status?: ItemStatus; milestone_id?: number | null; issue?: string; due_on?: string; repeat_days?: number }) => post<ItemView>(`/projects/${projectId}/items`, body)
export const patchItem = (id: number, body: { title?: string; notes?: string; status?: ItemStatus; milestone_id?: number; clear_milestone?: boolean; issue?: string; due_on?: string; clear_due?: boolean; repeat_days?: number }) => patch<ItemView>(`/items/${id}`, body)
export const deleteItem = (id: number) => del(`/items/${id}`)
export const orderItems = (projectId: number, ids: number[]) => put<ProjectView>(`/projects/${projectId}/items/order`, { ids })
