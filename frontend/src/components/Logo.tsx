/**
 * The HexDeck mark: a hexagon inside a hexagon, joined by three spokes. The
 * outer one is the rack, the inner one the machine at the centre of it, the
 * spokes what connects them. Drawn in the accent so the operator's own colour
 * carries into the mark.
 */
export function LogoMark({ size = 28, className = '' }: { size?: number; className?: string }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      className={className}
      aria-hidden="true"
      focusable="false"
    >
      <path d="M16 2.5 27.7 9.25v13.5L16 29.5 4.3 22.75V9.25z" fill="none" stroke="var(--nd-accent)" strokeWidth="1.7" strokeLinejoin="round" />
      <path d="M16 10 21.2 13v6L16 22l-5.2-3v-6z" fill="var(--nd-accent)" fillOpacity="0.22" stroke="var(--nd-accent)" strokeWidth="1.3" strokeLinejoin="round" />
      <path d="M16 2.5V10M27.7 22.75 21.2 19M4.3 22.75 10.8 19" fill="none" stroke="var(--nd-accent)" strokeWidth="1.3" strokeLinecap="round" />
    </svg>
  )
}

export function Logo({ size = 28 }: { size?: number }) {
  return (
    <span className="inline-flex items-center gap-2 select-none">
      <LogoMark size={size} />
      <span className="wordmark" style={{ fontSize: size * 0.62 }}>
        Hex<span className="text-accent">Deck</span>
      </span>
    </span>
  )
}
