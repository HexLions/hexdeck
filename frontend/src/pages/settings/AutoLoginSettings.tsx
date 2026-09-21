/**
 * Signing in by itself on the networks named here, as one account. Off by
 * default. It is said plainly what it means: everybody on those networks is
 * that account, so a guest or a user is the sensible choice, and the
 * warning is loudest when the account chosen is an administrator.
 */
import { useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError, get, put } from '../../api/client'
import type { User } from '../../api/types'
import { Field, Select, Switch, Toast } from '../../components/ui'
import { SettingsCard } from './SettingsCard'

interface AutoLogin {
  enabled: boolean
  user_id: number | null
  networks: string[]
}

export function AutoLoginSettings() {
  const { t } = useTranslation()
  const saved = useQuery({ queryKey: ['auto-login'], queryFn: () => get<AutoLogin>('/settings/auto-login') })
  const users = useQuery({ queryKey: ['users-admin'], queryFn: () => get<User[]>('/users') })
  const [form, setForm] = useState<AutoLogin>({ enabled: false, user_id: null, networks: [] })
  const [networks, setNetworks] = useState('')
  const [toast, setToast] = useState<{ text: string; level: 'ok' | 'error' } | null>(null)
  useEffect(() => {
    if (saved.data) {
      setForm(saved.data)
      setNetworks(saved.data.networks.join('\n'))
    }
  }, [saved.data])
  const chosen = users.data?.find((u) => u.id === form.user_id)
  const store = async () => {
    try {
      const answer = await put<AutoLogin>('/settings/auto-login', { ...form, networks: networks.split(/\r?\n/).map((n) => n.trim()).filter(Boolean) })
      setForm(answer)
      setNetworks(answer.networks.join('\n'))
      setToast({ text: t('common.saved'), level: 'ok' })
    } catch (failure) {
      setToast({ text: failure instanceof ApiError ? failure.message : t('errors.network'), level: 'error' })
    }
  }
  return (
    <>
      <SettingsCard title={t('settings.autoLogin.title')} description={t('settings.autoLogin.help')}>
        <Switch checked={form.enabled} onChange={(enabled) => setForm({ ...form, enabled })} label={t('settings.autoLogin.enabled')} description={t('settings.autoLogin.enabledHelp')} />
        <div className="mt-3 grid gap-3 md:grid-cols-2">
          <Field label={t('settings.autoLogin.account')} htmlFor="al-user" help={chosen?.role === 'admin' ? t('settings.autoLogin.adminWarning') : t('settings.autoLogin.accountHelp')}>
            <Select
              id="al-user"
              value={form.user_id === null ? '' : String(form.user_id)}
              onChange={(value) => setForm({ ...form, user_id: value ? Number(value) : null })}
              options={[{ value: '', label: t('settings.autoLogin.noAccount') }, ...(users.data ?? []).filter((u) => !u.disabled).map((u) => ({ value: String(u.id), label: `${u.display_name || u.username} · ${t(`users.role.${u.role}`)}` }))]}
            />
          </Field>
          <Field label={t('settings.autoLogin.networks')} htmlFor="al-networks" help={t('settings.autoLogin.networksHelp')}>
            <textarea id="al-networks" className="input min-h-20 font-mono text-[12px]" spellCheck={false} placeholder={'192.168.1.0/24\n10.0.0.0/8'} value={networks} onChange={(e) => setNetworks(e.target.value)} />
          </Field>
        </div>
        <p className="mt-1 text-xs text-faint">{t('settings.autoLogin.proxyNote')}</p>
        <button type="button" className="btn btn-accent mt-3" onClick={() => void store()}>
          {t('common.save')}
        </button>
      </SettingsCard>
      {toast && (
        <Toast level={toast.level} onClose={() => setToast(null)}>
          {toast.text}
        </Toast>
      )}
    </>
  )
}
