/**
 * The stage behind every board: a soft aurora by default, an uploaded image
 * with blur and dim when the board has one. Fixed, behind everything, and
 * never a network request unless the owner uploaded something.
 */
export interface Background {
  kind: string
  value?: string
  blur?: number
  dim?: number
}

/** One hexagon cell of the tessellation, 56 px across, as a mask: white where a line is. */
const HEX_CELL =
  "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='56' height='97' viewBox='0 0 56 97'%3E%3Cpath d='M28 1 55 16.5v31L28 63 1 47.5v-31zM28 63v34M55 47.5l-27 15.5M1 47.5l27 15.5' fill='none' stroke='white' stroke-width='1'/%3E%3C/svg%3E\")"

export const BUNDLED: Record<string, string> = {
  aurora:
    'radial-gradient(1200px 600px at 8% -10%, var(--nd-aurora-1), transparent 60%), radial-gradient(900px 520px at 92% 8%, var(--nd-aurora-2), transparent 60%), radial-gradient(900px 600px at 50% 115%, var(--nd-aurora-3), transparent 60%)',
  dusk: 'radial-gradient(1000px 600px at 20% 0%, rgba(244,114,182,0.26), transparent 60%), radial-gradient(900px 600px at 90% 100%, rgba(129,140,248,0.26), transparent 60%)',
  ember: 'radial-gradient(1000px 600px at 90% -10%, rgba(251,146,60,0.28), transparent 60%), radial-gradient(900px 600px at 0% 100%, rgba(244,63,94,0.2), transparent 60%)',
  ocean: 'radial-gradient(1100px 600px at 50% -20%, rgba(14,165,233,0.3), transparent 60%), radial-gradient(900px 600px at 100% 100%, rgba(20,184,166,0.22), transparent 60%)',
  forest: 'radial-gradient(1100px 600px at 0% 0%, rgba(34,197,94,0.24), transparent 60%), radial-gradient(900px 600px at 100% 90%, rgba(132,204,22,0.18), transparent 60%)',
  violet: 'radial-gradient(1100px 600px at 80% -10%, rgba(168,85,247,0.3), transparent 60%), radial-gradient(900px 600px at 10% 100%, rgba(99,102,241,0.24), transparent 60%)',
  mono: 'radial-gradient(1100px 600px at 50% -20%, rgba(148,163,184,0.12), transparent 60%)',
  none: 'none',
}

export function BackgroundLayer({ background }: { background?: Background }) {
  const kind = background?.kind ?? 'bundled'
  const blur = background?.blur ?? 18
  const dim = background?.dim ?? 45
  const isImage = kind === 'upload' || kind === 'url'
  const gradient = kind === 'bundled' || kind === 'gradient' ? BUNDLED[background?.value ?? 'aurora'] ?? BUNDLED.aurora : BUNDLED.aurora
  return (
    <div className="fixed inset-0 -z-10 overflow-hidden" aria-hidden="true">
      <div className="absolute inset-0" style={{ background: 'var(--nd-bg)' }} />
      {isImage && background?.value ? (
        <div
          className="absolute -inset-6 bg-cover bg-center"
          style={{
            backgroundImage: `url(${background.value})`,
            filter: `blur(${blur}px)`,
            transform: 'scale(1.05)',
          }}
        />
      ) : (
        <div className="absolute inset-0" style={{ background: gradient }} />
      )}
      {isImage && <div className="absolute inset-0" style={{ background: `rgba(6,9,14,${dim / 100})` }} />}
      {/* The hexagonal tessellation: the cell drawn once as a mask, the line
          colour taken from the theme so it holds in both brightnesses. */}
      <div
        className="absolute inset-0"
        style={{
          backgroundColor: 'color-mix(in srgb, var(--nd-text) 11%, transparent)',
          maskImage: `${HEX_CELL}, radial-gradient(ellipse at 50% 0%, black 25%, transparent 78%)`,
          maskComposite: 'intersect',
          WebkitMaskImage: `${HEX_CELL}, radial-gradient(ellipse at 50% 0%, black 25%, transparent 78%)`,
          WebkitMaskComposite: 'source-in',
          maskSize: '56px 97px, 100% 100%',
          WebkitMaskSize: '56px 97px, 100% 100%',
          maskRepeat: 'repeat, no-repeat',
          WebkitMaskRepeat: 'repeat, no-repeat',
        }}
      />
    </div>
  )
}
