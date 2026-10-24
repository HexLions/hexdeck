/**
 * The installation's own mark and name where HexDeck's stand, and HexDeck's
 * where it has none. "About HexDeck" and the setup wizard keep HexDeck's.
 * Taken from nexdeck 0.47.0.
 */
import { useTranslation } from 'react-i18next'

import { serverUrl } from '../api/client'
import { useBrand } from '../lib/brand'
import { LogoMark } from './Logo'

export function BrandMark({ size = 28, className = '' }: { size?: number; className?: string }) {
  const logo = useBrand((brand) => brand.logo)
  if (!logo) return <LogoMark size={size} className={className} />
  return <img src={serverUrl(logo)} alt="" width={size} height={size} className={`object-contain shrink-0 ${className}`} style={{ width: size, height: size }} data-brand-logo />
}

/** The name in the bar: the installation's own, else HexDeck's word mark. */
export function BrandName({ className = '' }: { className?: string }) {
  const name = useBrand((brand) => brand.name)
  if (!name) {
    return (
      <span className={`wordmark ${className}`}>
        Hex<span className="text-accent">Deck</span>
      </span>
    )
  }
  return <span className={`font-semibold tracking-tight truncate max-w-48 ${className}`} data-brand-name>{name}</span>
}

/** Mark and name over a sign-in form; with a name of its own, a quiet line says what runs underneath. */
export function BrandLogo({ size = 34 }: { size?: number }) {
  const { t } = useTranslation()
  const name = useBrand((brand) => brand.name)
  if (!name) {
    return (
      <span className="inline-flex items-center gap-2 select-none">
        <BrandMark size={size} />
        <span className="wordmark" style={{ fontSize: size * 0.62 }}>
          Hex<span className="text-accent">Deck</span>
        </span>
      </span>
    )
  }
  return (
    <span className="grid justify-items-center gap-1.5 select-none text-center">
      <BrandMark size={size * 1.4} />
      <span className="font-semibold tracking-tight" style={{ fontSize: size * 0.62 }} data-brand-name>{name}</span>
      <span className="text-[11px] text-faint">{t('brand.withHexdeck')}</span>
    </span>
  )
}
