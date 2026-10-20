import { fireEvent, render, screen } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { SystemInfoPanel } from './SettingsPanels'
import history from '../../release_history.json'

it('shows the source candidate version and expandable historical release lessons', async () => {
  const request = vi.fn(async (path: string) => path === '/api/health' ? { status: 'ok', database: 'ok' } : { app_version: history.current_version, releases: history.releases, roadmap: history.roadmap })
  render(<SystemInfoPanel request={request} />)
  expect(await screen.findByText('0.4.0')).toBeTruthy()
  expect(await screen.findByText('Personal OS v0.3.2 · 已发布')).toBeTruthy()
  const summary = screen.getByText('《这次我们学到了什么》')
  fireEvent.click(summary)
  expect(summary.closest('details')?.open).toBe(true)
  expect(screen.getByText('不是每个故障都需要新架构。')).toBeTruthy()
  expect(history.releases.some(item => item.version === '0.3.3')).toBe(true)
})
