/**
 * The look of the installation, painted onto the page.
 *
 * HexDeck ships one look in two brightnesses. An accent colour and a style
 * sheet of the operator's own are enough to make it belong in the room it
 * hangs in, and both come from one setting that everybody may read.
 *
 * The accent is written into the four variables the whole interface builds
 * on, in both brightnesses, so a light board follows the same colour.
 */
/** A theme: the tokens of both brightnesses, as `#rrggbb`, under their names without the `--nd-` prefix. */
export interface Theme {
  name: string
  dark: Record<string, string>
  light: Record<string, string>
}

export interface Appearance {
  preset: string
  accent: string
  css: string
  colour: string
  presets?: Record<string, string>
  theme?: Theme | null
  themes?: Record<string, Theme>
}

const STYLE_ID = 'hexdeck-appearance'
const HEX = /^#[0-9a-f]{6}$/i

/** The three parts of a `#rrggbb` colour as numbers, or null if it is not one. */
export function channels(colour: string): [number, number, number] | null {
  if (!HEX.test(colour)) return null
  return [colour.slice(1, 3), colour.slice(3, 5), colour.slice(5, 7)].map((part) => parseInt(part, 16)) as [number, number, number]
}

/** A darker shade of the accent, for the pressed state and the strong border. */
export function darker(colour: string, by = 0.18): string {
  const parts = channels(colour)
  if (!parts) return colour
  const shaded = parts.map((value) => Math.max(0, Math.round(value * (1 - by))))
  return `#${shaded.map((value) => value.toString(16).padStart(2, '0')).join('')}`
}

/** The variables an accent colour turns into. */
export function accentVariables(colour: string): Record<string, string> {
  const parts = channels(colour)
  if (!parts) return {}
  const [red, green, blue] = parts
  return {
    '--nd-accent': colour,
    '--nd-accent-strong': darker(colour),
    '--nd-accent-soft': `rgba(${red}, ${green}, ${blue}, 0.14)`,
    '--nd-accent-glow': `rgba(${red}, ${green}, ${blue}, 0.35)`,
    '--nd-aurora-1': `rgba(${red}, ${green}, ${blue}, 0.16)`,
  }
}

/**
 * A theme as a style sheet: the dark tokens on the root, the light ones
 * under the light attribute, the same two blocks the shipped sheet has, so
 * a token the theme leaves out keeps its shipped value. An accent brings
 * its derived shades with it.
 */
export function themeCss(theme: Theme | null | undefined): string {
  if (!theme) return ''
  const blocks: string[] = []
  for (const [mode, selector] of [['dark', ':root'], ['light', ":root[data-theme='light']"]] as const) {
    const tokens = theme[mode] ?? {}
    const lines: string[] = []
    for (const [token, value] of Object.entries(tokens)) {
      if (!HEX.test(value)) continue
      lines.push(`  --nd-${token.replace(/^--nd-/, '')}: ${value};`)
    }
    if (tokens.accent && HEX.test(tokens.accent)) {
      for (const [name, value] of Object.entries(accentVariables(tokens.accent))) {
        if (name !== '--nd-accent') lines.push(`  ${name}: ${value};`)
      }
    }
    if (lines.length) blocks.push(`${selector} {\n${lines.join('\n')}\n}`)
  }
  return blocks.join('\n')
}

/** Whether the theme sets the accent itself, in either brightness. */
export function themeHasAccent(theme: Theme | null | undefined): boolean {
  return Boolean(theme && (HEX.test(theme.dark?.accent ?? '') || HEX.test(theme.light?.accent ?? '')))
}

/**
 * Paint it on. The accent goes onto the root element, where it beats the
 * style sheet's own value in both brightnesses; the theme and the operator's
 * style sheet go into one tag of their own at the end of the head, so they
 * win over everything HexDeck ships and nothing else has to move.
 *
 * A theme that brings its own accent is left alone by the chosen preset, so
 * it may differ between the brightnesses; a colour of one's own still wins.
 */
export function applyAppearance(look: Appearance | null | undefined): void {
  const root = document.documentElement
  const own = HEX.test(look?.accent ?? '') ? look!.accent : ''
  const inline = own || (themeHasAccent(look?.theme) ? '' : (look?.colour ?? ''))
  const variables = accentVariables(inline)
  for (const name of ['--nd-accent', '--nd-accent-strong', '--nd-accent-soft', '--nd-accent-glow', '--nd-aurora-1']) {
    if (variables[name]) root.style.setProperty(name, variables[name])
    else root.style.removeProperty(name)
  }

  const css = [themeCss(look?.theme), (look?.css ?? '').trim()].filter(Boolean).join('\n')
  let tag = document.getElementById(STYLE_ID) as HTMLStyleElement | null
  if (!css) {
    tag?.remove()
    return
  }
  if (!tag) {
    tag = document.createElement('style')
    tag.id = STYLE_ID
    document.head.append(tag)
  }
  tag.textContent = css
}
