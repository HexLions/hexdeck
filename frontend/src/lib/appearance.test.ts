import { beforeEach, describe, expect, it } from 'vitest'

import { accentVariables, applyAppearance, channels, darker, themeCss, themeHasAccent } from './appearance'

describe('the accent colour', () => {
  it('reads the three parts of a colour', () => {
    expect(channels('#22d3ee')).toEqual([34, 211, 238])
  })

  it('refuses anything that is not a colour', () => {
    for (const text of ['', 'red', 'rgb(1,2,3)', '#fff', '#22d3e', 'javascript:1']) {
      expect(channels(text)).toBeNull()
    }
  })

  it('makes a darker shade for the pressed state', () => {
    expect(darker('#22d3ee')).toBe('#1cadc3')
    expect(darker('#000000')).toBe('#000000')
  })

  it('turns one colour into the four variables the interface uses', () => {
    expect(accentVariables('#22d3ee')).toEqual({
      '--nd-accent': '#22d3ee',
      '--nd-accent-strong': '#1cadc3',
      '--nd-accent-soft': 'rgba(34, 211, 238, 0.14)',
      '--nd-accent-glow': 'rgba(34, 211, 238, 0.35)',
      '--nd-aurora-1': 'rgba(34, 211, 238, 0.16)',
    })
  })

  it('hands back nothing for a colour it cannot read, so the shipped one stays', () => {
    expect(accentVariables('nonsense')).toEqual({})
  })
})

describe('painting it on', () => {
  beforeEach(() => {
    document.documentElement.removeAttribute('style')
    document.getElementById('hexdeck-appearance')?.remove()
  })

  it('sets the variables and adds the style sheet', () => {
    applyAppearance({ preset: 'violet', accent: '', colour: '#a78bfa', css: '.card { border-radius: 4px; }' })
    expect(document.documentElement.style.getPropertyValue('--nd-accent')).toBe('#a78bfa')
    expect(document.getElementById('hexdeck-appearance')?.textContent).toBe('.card { border-radius: 4px; }')
  })

  it('takes the style sheet away again when it is emptied', () => {
    applyAppearance({ preset: 'cyan', accent: '', colour: '#22d3ee', css: '.card { border-radius: 4px; }' })
    applyAppearance({ preset: 'cyan', accent: '', colour: '#22d3ee', css: '   ' })
    expect(document.getElementById('hexdeck-appearance')).toBeNull()
  })

  it('keeps only one tag however often it is painted', () => {
    for (let round = 0; round < 3; round += 1) applyAppearance({ preset: 'cyan', accent: '', colour: '#22d3ee', css: `.a${round} {}` })
    expect(document.querySelectorAll('#hexdeck-appearance')).toHaveLength(1)
    expect(document.getElementById('hexdeck-appearance')?.textContent).toBe('.a2 {}')
  })

  it('gives the shipped colour back when there is nothing to apply', () => {
    applyAppearance({ preset: 'violet', accent: '', colour: '#a78bfa', css: '' })
    applyAppearance(null)
    expect(document.documentElement.style.getPropertyValue('--nd-accent')).toBe('')
  })
})

describe('a theme', () => {
  const nord = { name: 'Nord', dark: { bg: '#2e3440', accent: '#88c0d0' }, light: { bg: '#eceff4', 'text-muted': '#4c566a' } }

  beforeEach(() => {
    document.documentElement.removeAttribute('style')
    document.getElementById('hexdeck-appearance')?.remove()
  })

  it('becomes the two blocks of the shipped sheet, with the accent shades derived', () => {
    const css = themeCss(nord)
    expect(css).toContain(':root {\n  --nd-bg: #2e3440;\n  --nd-accent: #88c0d0;\n  --nd-accent-strong: #709dab;')
    expect(css).toContain("--nd-accent-soft: rgba(136, 192, 208, 0.14)")
    expect(css).toContain(":root[data-theme='light'] {\n  --nd-bg: #eceff4;\n  --nd-text-muted: #4c566a;\n}")
    expect(themeCss(null)).toBe('')
    expect(themeCss({ name: 'x', dark: { bg: 'red' }, light: {} })).toBe('')
  })

  it('leaves the accent to the theme unless a colour of one\'s own is set', () => {
    applyAppearance({ preset: 'hex', accent: '', colour: '#3aa0ff', css: '', theme: nord })
    expect(document.documentElement.style.getPropertyValue('--nd-accent')).toBe('')
    expect(document.getElementById('hexdeck-appearance')?.textContent).toContain('--nd-bg: #2e3440')
    applyAppearance({ preset: 'hex', accent: '#ff8800', colour: '#ff8800', css: '', theme: nord })
    expect(document.documentElement.style.getPropertyValue('--nd-accent')).toBe('#ff8800')
    expect(themeHasAccent(nord)).toBe(true)
    expect(themeHasAccent({ name: 'x', dark: { bg: '#000000' }, light: {} })).toBe(false)
  })

  it('goes into the same tag as the style sheet, the sheet last', () => {
    applyAppearance({ preset: 'hex', accent: '', colour: '#3aa0ff', css: '.card { border-radius: 4px; }', theme: nord })
    const text = document.getElementById('hexdeck-appearance')?.textContent ?? ''
    expect(text.indexOf('--nd-bg')).toBeLessThan(text.indexOf('.card'))
  })
})
