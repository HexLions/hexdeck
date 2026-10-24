/**
 * The buttons that appear over a card on hover, against what its head shows.
 *
 * ⚠️ Seen on the screenshot in issue #29, 06.10.2026: "reload" and "open"
 * floated over the right corner of the card and lay on the status dot and the
 * beta mark, just when somebody was looking at the card. jsdom measures no
 * layout, so only a real browser can say whether two things overlap.
 *
 * Named to sort after `first-start.spec.ts`, which needs the empty
 * installation.
 */
import { expect, test, type Page } from '@playwright/test'

const ACCOUNT = { username: 'e2e-admin', password: 'A-long-enough-password-1' }
const CSRF = { 'X-Nexdeck-Request': '1' }

async function signIn(page: Page) {
  await page.goto('/')
  await expect(page.getByLabel('User name')).toBeVisible()
  await page.getByLabel('User name').fill(ACCOUNT.username)
  await page.getByLabel('Password', { exact: true }).fill(ACCOUNT.password)
  const confirm = page.getByLabel('Confirm password')
  if (await confirm.isVisible()) {
    await confirm.fill(ACCOUNT.password)
    await page.getByRole('button', { name: 'Next' }).click()
    await page.getByRole('button', { name: 'Next' }).click()
    await page.getByRole('button', { name: 'Finish' }).click()
  } else {
    await page.getByRole('button', { name: 'Sign in' }).click()
  }
  await expect(page).toHaveURL(/\/b\//)
}

/** Whether any of the buttons lies over the dot or the beta mark, in the card's own coordinates. */
function overlaps(page: Page, title: string) {
  return page.locator(`section[aria-label="${title}"]`).evaluate((card) => {
    const box = (element: Element) => element.getBoundingClientRect()
    const marks = [card.querySelector('header .dot'), [...card.querySelectorAll('header .chip')].find((chip) => chip.textContent === 'beta')]
    const buttons = [...card.querySelectorAll('.card-controls :is(button, a)')].filter((button) => box(button).width > 0)
    const hits: string[] = []
    for (const mark of marks) {
      if (!mark) return { buttons: buttons.length, marks: -1, hits }
      const m = box(mark)
      for (const button of buttons) {
        const b = box(button)
        if (b.left < m.right && b.right > m.left && b.top < m.bottom && b.bottom > m.top) hits.push(`${button.getAttribute('aria-label')} over ${mark.className}`)
      }
    }
    return { buttons: buttons.length, marks: marks.length, hits }
  })
}

test('the buttons of a hovered card leave the dot and the beta mark in sight', async ({ page }) => {
  await signIn(page)
  const service = await page.request.post('/api/v1/integrations', { data: { kind: 'nomad', name: 'Nomad here', config: {}, demo: true }, headers: CSRF })
  expect(service.status()).toBe(201)
  const made = await page.request.post('/api/v1/boards', { data: { name: 'Heads', slug: 'heads' }, headers: CSRF })
  expect(made.status()).toBe(201)
  try {
    const pageId = (await made.json()).pages[0].id
    const card = await page.request.post(`/api/v1/pages/${pageId}/widgets`, {
      data: { kind: 'nomad.summary', title: 'Nomad jobs', integration_id: (await service.json()).id, link: 'https://example.com/nomad' },
      headers: CSRF,
    })
    expect(card.status()).toBe(201)
    const id = String((await card.json()).widget.id)
    const placed = await page.request.put(`/api/v1/pages/${pageId}/layouts`, { data: { lg: [{ i: id, x: 0, y: 0, w: 6, h: 2 }] }, headers: CSRF })
    expect(placed.ok()).toBe(true)

    await page.goto('/b/heads')
    const section = page.locator('section[aria-label="Nomad jobs"]')
    await expect(section.getByText('beta', { exact: true })).toBeVisible()

    await section.hover()
    // Both buttons in sight once the card is hovered, and the transition run.
    await expect(section.getByRole('button', { name: 'Refresh now' })).toBeVisible()
    await expect.poll(async () => (await overlaps(page, 'Nomad jobs')).buttons).toBe(2)
    await page.waitForTimeout(300)
    const seen = await overlaps(page, 'Nomad jobs')
    expect(seen.marks, 'the card shows no dot or no beta mark').toBe(2)
    expect(seen.hits, 'a button lies over what the head says').toEqual([])

    // The same in edit mode, where the buttons are always there.
    await page.getByRole('button', { name: 'Edit board' }).click()
    await expect(section.getByRole('button', { name: 'Widget settings' })).toBeVisible()
    await page.waitForTimeout(300)
    const editing = await overlaps(page, 'Nomad jobs')
    expect(editing.buttons, 'no buttons while editing').toBeGreaterThan(0)
    expect(editing.hits, 'a button lies over what the head says while editing').toEqual([])
    await page.getByRole('button', { name: 'Edit board' }).click()
  } finally {
    await page.request.delete('/api/v1/boards/heads', { headers: CSRF })
    await page.request.delete(`/api/v1/integrations/${(await service.json()).id}`, { headers: CSRF })
  }
})

/**
 * ⚠️ Reported on 24.10.2026: in edit mode the bar with Add widget and Done lay
 * over the bottom of the cards in the last row, resize corner and all, and
 * the page ended right there, so those cards could not be resized. The page
 * now leaves the bar's room under the board while editing. On a board whose
 * rows fit the screen that was not enough: measured while scrolled, the rows
 * grew by the scroll and the last row followed the window down again.
 */
test('in edit mode the bar leaves the resize corner of the last row within reach', async ({ page }) => {
  await signIn(page)
  const service = await page.request.post('/api/v1/integrations', { data: { kind: 'nomad', name: 'Nomad low', config: {}, demo: true }, headers: CSRF })
  expect(service.status()).toBe(201)
  const made = await page.request.post('/api/v1/boards', { data: { name: 'Low', slug: 'low' }, headers: CSRF })
  expect(made.status()).toBe(201)
  try {
    const board = await made.json()
    // Rows that fill the window put the last row's bottom where the bar is.
    const fit = await page.request.patch('/api/v1/boards/low', { data: { settings: { ...board.settings, fit_screen: true } }, headers: CSRF })
    expect(fit.ok()).toBe(true)
    const columns = Number((await fit.json()).settings?.columns ?? 12)
    const pageId = board.pages[0].id
    const card = await page.request.post(`/api/v1/pages/${pageId}/widgets`, {
      data: { kind: 'nomad.summary', title: 'Nomad low', integration_id: (await service.json()).id },
      headers: CSRF,
    })
    expect(card.status()).toBe(201)
    const id = String((await card.json()).widget.id)
    // Half the width, so the resize corner sits in the middle, under the bar.
    const placed = await page.request.put(`/api/v1/pages/${pageId}/layouts`, { data: { lg: [{ i: id, x: 0, y: 0, w: columns / 2, h: 4 }] }, headers: CSRF })
    expect(placed.ok()).toBe(true)

    await page.goto('/b/low')
    const section = page.locator('section[aria-label="Nomad low"]')
    await expect(section).toBeVisible()
    await page.getByRole('button', { name: 'Edit board' }).click()
    const done = page.getByRole('button', { name: 'Done' })
    await expect(done).toBeVisible()
    // As far down as the page goes: the room under the board is what lets the last row out from under the bar.
    await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight))
    await page.waitForTimeout(300)

    const corner = page.locator('.react-grid-item').filter({ has: section }).locator('.react-resizable-handle').first()
    const reachable = await corner.evaluate((handle) => {
      const box = handle.getBoundingClientRect()
      const hit = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2)
      return { onTop: hit === handle || handle.contains(hit), bottom: Math.round(box.bottom), window: window.innerHeight,
               scrolled: Math.round(window.scrollY), height: document.documentElement.scrollHeight }
    })
    expect(reachable.onTop, `the corner at ${reachable.bottom} of ${reachable.window} px is covered (scrolled ${reachable.scrolled} of ${reachable.height})`).toBe(true)
    await done.click()
  } finally {
    await page.request.delete('/api/v1/boards/low', { headers: CSRF })
    await page.request.delete(`/api/v1/integrations/${(await service.json()).id}`, { headers: CSRF })
  }
})
