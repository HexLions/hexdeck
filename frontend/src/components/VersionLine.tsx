/**
 * Which version this is, where somebody is already looking.
 *
 * The About page has always carried it, three clicks away. The question "what am
 * I running" comes up while reading a changelog or an issue, so the answer sits
 * in the account menu and under both settings navigations as well.
 *
 * ⚠️ Fetched by hand rather than through react-query. This line sits in the bar,
 * which is drawn on the kiosk and in the preview too, and a component that
 * demands a QueryClient would take those pages down with it. One request per
 * mount, failures ignored: there is nothing to say if the answer never comes.
 *
 * ⚠️ `latest_version` is answered for administrators only, and only while the
 * update check is on. Everybody else gets the plain version, which is the honest
 * thing: a viewer cannot be told about an update only somebody else can make.
 */
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { get } from '../api/client'
import type { About } from '../api/types'

export function VersionLine({ className = '' }: { className?: string }) {
  const { t } = useTranslation()
  const [about, setAbout] = useState<About | null>(null)
  useEffect(() => {
    let dropped = false
    get<About>('/about')
      .then((answer) => {
        if (!dropped) setAbout(answer)
      })
      .catch(() => undefined)
    return () => {
      dropped = true
    }
  }, [])
  if (!about?.version) return null
  const newer = Boolean(about.latest_version && about.latest_version !== about.version)
  return (
    <p className={`flex items-center gap-1.5 text-[11px] text-faint ${className}`}>
      <span>
        HexDeck <span className="num">{about.version}</span>
      </span>
      {newer && (
        <a
          href={about.release_url}
          target="_blank"
          rel="noreferrer"
          className="chip !py-0 text-[10px] text-accent"
        >
          {t('settings.system.updateAvailable', { version: about.latest_version })}
        </a>
      )}
    </p>
  )
}
