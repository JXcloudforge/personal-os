import { describe, expect, it } from 'vitest'
import { agentToolStatusLabel } from './agentToolLabels'

describe('agent tool result labels', () => {
  it('never describes search snippets as fetched page text', () => {
    expect(agentToolStatusLabel('web_search', 'completed', { result_count: 2 })).toContain('尚未读取正文')
  })

  it('only describes a successful fetch as read page text', () => {
    expect(agentToolStatusLabel('web_fetch', 'completed', { characters: 1234 })).toContain('已读取正文 1234 字符')
    expect(agentToolStatusLabel('web_fetch', 'denied')).toContain('失败/拒绝')
  })
})
