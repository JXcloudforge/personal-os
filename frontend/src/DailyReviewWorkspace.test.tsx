import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { StrictMode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { DailyReviewWorkspace } from './DailyReviewWorkspace'

const today = '2026-10-14'
const savedReview = {
  id: 9,
  review_date: today,
  planned_summary: 'Read the implementation notes.',
  actual_summary: 'Completed the review flow.',
  blockers: 'No blocker.',
  next_actions: 'Add one follow-up test.',
  task_entries: [{ id: 3, task_id: 42, task_title: 'Review task', status_at_settlement: 'DOING', report_category: 'completed_today', actual_progress: 'Reviewed API boundary.', evidence_note: 'Test output saved.', revision: 1 }],
}

function response(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => { resolve = done })
  return { promise, resolve }
}

describe('daily and weekly review workspace', () => {
  it('does not let a late refresh read overwrite text typed after the form opens', async () => {
    const lateRead = deferred<Response>()
    let dailyReads = 0
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url === '/api/dashboard') return response({ today_date: today })
      if (url === '/api/tasks') return response([])
      if (url === `/api/reviews/daily/${today}`) {
        dailyReads += 1
        return dailyReads === 2 ? lateRead.promise : response({ review_date: today, actual_summary: 'saved baseline', task_entries: [] })
      }
      return response({ detail: 'unexpected request' })
    })
    globalThis.fetch = fetchMock as typeof fetch

    render(<StrictMode><DailyReviewWorkspace /></StrictMode>)
    const input = await screen.findByLabelText('实际完成了什么')
    fireEvent.click(screen.getByRole('button', { name: '刷新' }))
    await waitFor(() => expect(dailyReads).toBe(2))
    fireEvent.change(input, { target: { value: 'user typed this' } })

    await act(async () => {
      lateRead.resolve(response({ review_date: today, actual_summary: 'late stale response', task_entries: [] }))
      await lateRead.promise
    })
    expect((input as HTMLTextAreaElement).value).toBe('user typed this')
  })

  it('keeps the newest refresh result when daily reads finish out of order', async () => {
    const older = deferred<Response>()
    const newer = deferred<Response>()
    let dailyReads = 0
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url === '/api/dashboard') return response({ today_date: today })
      if (url === '/api/tasks') return response([])
      if (url === `/api/reviews/daily/${today}`) {
        dailyReads += 1
        if (dailyReads === 1) return response({ review_date: today, actual_summary: 'baseline', task_entries: [] })
        return dailyReads === 2 ? older.promise : newer.promise
      }
      return response({ detail: 'unexpected request' })
    })
    globalThis.fetch = fetchMock as typeof fetch
    render(<DailyReviewWorkspace />)
    const input = await screen.findByLabelText('实际完成了什么')

    fireEvent.click(screen.getByRole('button', { name: '刷新' }))
    await waitFor(() => expect(dailyReads).toBe(2))
    fireEvent.click(screen.getByRole('button', { name: '刷新' }))
    await waitFor(() => expect(dailyReads).toBe(3))
    await act(async () => { newer.resolve(response({ review_date: today, actual_summary: 'newest response', task_entries: [] })); await newer.promise })
    await act(async () => { older.resolve(response({ review_date: today, actual_summary: 'stale response', task_entries: [] })); await older.promise })
    expect((input as HTMLTextAreaElement).value).toBe('newest response')
  })

  it('shows a read failure and allows a successful retry', async () => {
    let failed = false
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url === '/api/dashboard') return response({ today_date: today })
      if (url === '/api/tasks') return response([])
      if (url === `/api/reviews/daily/${today}`) {
        if (!failed) { failed = true; return new Response(JSON.stringify({ detail: 'temporary read failure' }), { status: 503 }) }
        return response({ review_date: today, actual_summary: 'recovered', task_entries: [] })
      }
      return response({ detail: 'unexpected request' })
    })
    globalThis.fetch = fetchMock as typeof fetch
    render(<DailyReviewWorkspace />)
    expect(await screen.findByText('temporary read failure')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '刷新' }))
    expect((await screen.findByLabelText('实际完成了什么') as HTMLTextAreaElement).value).toBe('recovered')
  })

  it('ignores a refresh response that returns after a successful save', async () => {
    const pendingRead = deferred<Response>()
    let dailyReads = 0
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/dashboard') return response({ today_date: today })
      if (url === '/api/tasks') return response([])
      if (url === `/api/reviews/daily/${today}`) {
        dailyReads += 1
        return dailyReads === 1 ? response({ review_date: today, task_entries: [] }) : pendingRead.promise
      }
      if (url === '/api/reviews/daily' && init?.method === 'POST') {
        const body = JSON.parse(String(init.body))
        return response({ review_date: today, ...body, task_entries: [] })
      }
      return response({ detail: 'unexpected request' })
    })
    globalThis.fetch = fetchMock as typeof fetch
    render(<DailyReviewWorkspace />)
    const input = await screen.findByLabelText('实际完成了什么')
    fireEvent.click(screen.getByRole('button', { name: '刷新' }))
    await waitFor(() => expect(dailyReads).toBe(2))
    fireEvent.change(input, { target: { value: 'save wins' } })
    fireEvent.click(screen.getByRole('button', { name: /保存 \/ 更新本日结算/ }))
    await screen.findByText(`已保存 ${today} 的复盘。任务状态未被自动更改。`)
    await act(async () => { pendingRead.resolve(response({ review_date: today, actual_summary: 'stale read', task_entries: [] })); await pendingRead.promise })
    expect((input as HTMLTextAreaElement).value).toBe('save wins')
  })

  it('opens an editable knowledge draft from saved human review without saving a card', async () => {
    const onSaveKnowledge = vi.fn()
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/dashboard') return response({ today_date: today })
      if (url === '/api/tasks') return response([{ id: 42, title: 'Review task', status: 'DOING', due_date: null }])
      if (url === `/api/reviews/daily/${today}`) return response(savedReview)
      return response({ detail: 'unexpected request', method: init?.method })
    })
    globalThis.fetch = fetchMock as typeof fetch

    render(<DailyReviewWorkspace onSaveKnowledge={onSaveKnowledge} />)
    await screen.findByText('每日执行结算')
    fireEvent.click(screen.getByRole('button', { name: '从已保存复盘带入知识草稿' }))

    expect(onSaveKnowledge).toHaveBeenCalledWith(expect.objectContaining({ domain: 'reviews', content: expect.stringContaining('Completed the review flow.') }))
    expect(onSaveKnowledge.mock.calls[0][0].content).toContain('任务 #42')
    expect(fetchMock.mock.calls.some(([url, init]) => String(url) === '/api/knowledge' && init?.method === 'POST')).toBe(false)
  })

  it('shows weekly totals from the scoped review endpoint', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url === '/api/dashboard') return response({ today_date: today })
      if (url === '/api/tasks') return response([])
      if (url === `/api/reviews/daily/${today}`) return response({ review_date: today, task_entries: [] })
      if (url.startsWith('/api/reviews/weekly?')) return response({
        week_start: '2026-10-12', week_end: '2026-10-18', days_with_reviews: 1, task_entry_count: 1,
        reported_categories: { completed_today: 0, in_progress: 0, blocked: 1, overdue: 0 }, evidence_note_count: 1,
        days: [{ review_date: '2026-10-12', reviewed: true, actual_summary: 'Reviewed the design.', blockers: 'Waiting on input.', next_actions: null, task_entries: [{ task_id: 42, task_title: 'Review task', status_at_settlement: 'DOING', report_category: 'blocked', evidence_note: 'An issue was captured.' }] }],
      })
      return response({ detail: 'unexpected request' })
    })
    globalThis.fetch = fetchMock as typeof fetch

    render(<DailyReviewWorkspace />)
    await screen.findByText('每日执行结算')
    fireEvent.click(screen.getByRole('button', { name: '查看每周总结' }))

    expect(await screen.findByText('每周执行总结')).toBeTruthy()
    expect(await screen.findByText('1/7 天有复盘 · 1 项任务记录 · 1 条自报证据')).toBeTruthy()
    expect(screen.getByText('Reviewed the design.')).toBeTruthy()
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).startsWith('/api/reviews/weekly?week_start=2026-10-12'))).toBe(true))
  })
})
