/** The stats card: a sentence about the whole card, and a hint behind a row that has no room for one. */
import { render, screen } from '@testing-library/react'

import type { WidgetData, WidgetView } from '../lib/types'
import { StatsCard } from './renderers'

const widget = { id: 3, kind: 'core.host', title: 'Host', renderer: 'stats' } as unknown as WidgetView
const data = {
  status: 'ok',
  primary: { label: 'CPU', value: 14.7, unit: '%' },
  secondary: [{ label: 'Memory', value: 41, unit: '%', metric: 'memory', hint: '12.9 GB of 31.3 GB' }],
  metrics: { cpu: 14.7, memory: 41 },
  meta: { host: false, notice: "These are the container's numbers." },
} as unknown as WidgetData

it('says when the card is reading the container, and keeps the detail on the row', () => {
  const { container } = render(<StatsCard widget={widget} data={data} />)
  expect(screen.getByText("These are the container's numbers.")).toBeInTheDocument()
  expect(container.querySelector('[title="12.9 GB of 31.3 GB"]')).not.toBeNull()
})

it('draws no sentence when there is nothing to say', () => {
  const quiet = { ...data, meta: { host: true, notice: '' } } as unknown as WidgetData
  render(<StatsCard widget={widget} data={quiet} />)
  expect(screen.queryByText("These are the container's numbers.")).toBeNull()
})
