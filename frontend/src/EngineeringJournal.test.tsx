import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { EngineeringJournal } from './EngineeringJournal'

describe('Engineering Journal', () => {
  it('shows the source candidate boundary and seven mechanisms', () => {
    render(<EngineeringJournal />)
    expect(screen.getByText('v0.4.0 · Source Release Candidate')).toBeTruthy()
    expect(screen.getByText(/源码候选等待发布审批；v0.3.3 历史发布记录保留/)).toBeTruthy()
    for (const name of ['ID', 'State Machine', 'Service', 'Transaction', 'Idempotency', 'Authorization', 'Audit Trail']) {
      expect(screen.getByText(name)).toBeTruthy()
    }
  })

  it('expands a stage with problem, cause, modules, evidence, and boundaries', () => {
    render(<EngineeringJournal />)
    fireEvent.click(screen.getByText('P0.4b-1'))
    expect(screen.getByText('Invitation、原子领取与 Alembic')).toBeTruthy()
    expect(screen.getByText('只在 Python 里先查询再修改会留下并发竞争窗口。')).toBeTruthy()
    expect(screen.getByText(/邀请专项 7 passed/)).toBeTruthy()
    expect(screen.getByText(/共享限流和 React 注册页面待 P0\.4b-2/)).toBeTruthy()
    fireEvent.click(screen.getByText('P0.4b-2b'))
    expect(screen.getByText('React 邀请注册与双用户 Task 浏览器闭环')).toBeTruthy()
    expect(screen.getByText(/Playwright 全量 5 passed/)).toBeTruthy()
    fireEvent.click(screen.getByText('P0.4c-1'))
    expect(screen.getByText('核心资源双用户授权隔离验收')).toBeTruthy()
    expect(screen.getByText(/文件系统双用户 E2E/)).toBeTruthy()
    fireEvent.click(screen.getByText('P0.4c-2'))
    expect(screen.getByText(/迁移失败旧库仍能启动并返回健康 200/)).toBeTruthy()
    fireEvent.click(screen.getByText('P0.4c-2a'))
    expect(screen.getByText('Fail-Closed Schema Startup Guard')).toBeTruthy()
    expect(screen.getByText(/production Lifespan 在备份调度器前只读检查/)).toBeTruthy()
  })
})
