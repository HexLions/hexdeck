import { describe, expect, it } from 'vitest'

import type { FieldSpec, Integration } from '../api/types'
import { place, sameAddress } from './sameAddress'

const URL_FIELD: FieldSpec = { name: 'url', label: 'URL', type: 'url', required: true, secret: false, default: null, help: '', placeholder: '', options: [] }

function connection(id: number, kind: string, url: string): Integration {
  return { id, kind, label: kind, icon: '', beta: false, name: `${kind} ${id}`, config: { url }, enabled: true, demo: false, admin_only: false, last_ok_at: null, last_error: '', widget_count: 0, created_at: '' }
}

const SAVED = [connection(11, 'proxmox', 'https://192.0.2.199:8006'), connection(12, 'radarr', 'http://192.0.2.50:7878')]

describe('place', () => {
  it('reads one place out of the ways an address is written', () => {
    expect(place(' https://NAS.example.com:8006/ ')).toBe('nas.example.com:8006')
    expect(place('nas.example.com:8006')).toBe('nas.example.com:8006')
    expect(place('http://nas.example.com/base//')).toBe('nas.example.com/base')
    expect(place(undefined)).toBe('')
  })
})

describe('sameAddress', () => {
  it('names the connection of the same kind that has the address', () => {
    expect(sameAddress(URL_FIELD, 'https://192.0.2.199:8006/', 'proxmox', null, SAVED)?.id).toBe(11)
  })

  it('says nothing for another address, or for another kind at the same one', () => {
    expect(sameAddress(URL_FIELD, 'https://192.0.2.198:8006', 'proxmox', null, SAVED)).toBeUndefined()
    expect(sameAddress(URL_FIELD, 'https://192.0.2.199:8006', 'pbs', null, SAVED)).toBeUndefined()
  })

  it('does not find a connection in itself', () => {
    expect(sameAddress(URL_FIELD, 'https://192.0.2.199:8006', 'proxmox', 11, SAVED)).toBeUndefined()
  })

  it('says nothing while the field is empty', () => {
    expect(sameAddress(URL_FIELD, '', 'proxmox', null, [connection(13, 'proxmox', '')])).toBeUndefined()
  })

  it('leaves the address a field starts with alone: a public service is shared', () => {
    const field = { ...URL_FIELD, default: 'https://gitlab.com' }
    expect(sameAddress(field, 'https://gitlab.com/', 'gitlab', null, [connection(14, 'gitlab', 'https://gitlab.com')])).toBeUndefined()
    expect(sameAddress(field, 'https://git.example.com', 'gitlab', null, [connection(15, 'gitlab', 'https://git.example.com')])?.id).toBe(15)
  })

  it('never compares an address that is kept secret', () => {
    const field = { ...URL_FIELD, secret: true }
    expect(sameAddress(field, '********', 'ical', null, [connection(16, 'ical', '********')])).toBeUndefined()
  })

  it('looks at addresses only', () => {
    const field: FieldSpec = { ...URL_FIELD, name: 'username', type: 'text' }
    const saved = [{ ...connection(17, 'proxmox', ''), config: { username: 'root' } }]
    expect(sameAddress(field, 'root', 'proxmox', null, saved)).toBeUndefined()
  })
})
