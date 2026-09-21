/**
 * The way out of demo mode, with the invented data taken along.
 *
 * Switching the flag off alone leaves the demo connections behind, still
 * inventing numbers. This button removes them, the cards that read them and
 * the boards that were nothing but those, after saying so once. A board with
 * a real connection on it keeps everything but its demo cards.
 */
import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'

import { ApiError, post } from '../api/client'
import { Confirm, Toast } from './ui'

interface Left {
  integrations_removed: number
  widgets_removed: number
  boards_removed: number
}

export function LeaveDemo({ className = 'btn' }: { className?: string }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const client = useQueryClient()
  const [asking, setAsking] = useState(false)
  const [busy, setBusy] = useState(false)
  const [toast, setToast] = useState<{ text: string; level: 'ok' | 'error' } | null>(null)
  const leave = async () => {
    setBusy(true)
    try {
      const left = await post<Left>('/settings/demo/leave')
      setAsking(false)
      await client.invalidateQueries()
      setToast({ text: t('demo.left', { ...left }), level: 'ok' })
      navigate('/')
    } catch (failure) {
      setToast({ text: failure instanceof ApiError ? failure.message : t('errors.network'), level: 'error' })
    } finally {
      setBusy(false)
    }
  }
  return (
    <>
      <button type="button" className={className} onClick={() => setAsking(true)}>
        {t('demo.leave')}
      </button>
      <Confirm open={asking} title={t('demo.leaveTitle')} body={t('demo.leaveBody')} danger confirmLabel={t('demo.leave')} confirmDisabled={busy} onCancel={() => setAsking(false)} onConfirm={() => void leave()} />
      {toast && (
        <Toast level={toast.level} onClose={() => setToast(null)}>
          {toast.text}
        </Toast>
      )}
    </>
  )
}
