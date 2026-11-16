/**
 * Which other connection already has the address being entered.
 *
 * ⚠️ A second Proxmox entered with the address of the first one is refused
 * with a token that is correct, and "check the API key" sends somebody to the
 * one thing that is right. Two connections to one service are allowed, with
 * two accounts or two tokens, so this is a remark under the field and never a
 * refusal.
 */
import type { FieldSpec, Integration } from '../api/types'

/** Host, port and path, so `https://NAS:8006/` and `nas:8006` are one place. */
export function place(address: unknown): string {
  return String(address ?? '')
    .trim()
    .toLowerCase()
    .replace(/^[a-z][a-z0-9+.-]*:\/\//, '')
    .replace(/\/+$/, '')
}

/**
 * The connection of the same kind that holds this address, if there is one.
 *
 * An address kept secret comes back as a row of stars and is never compared,
 * and the address a field starts with is a public service that many
 * connections share.
 */
export function sameAddress(field: FieldSpec, value: unknown, kind: string, own: number | null, connections: Integration[]): Integration | undefined {
  if (field.type !== 'url' || field.secret) return undefined
  const here = place(value)
  if (!here || here === place(field.default)) return undefined
  return connections.find((one) => one.kind === kind && one.id !== own && place(one.config[field.name]) === here)
}
