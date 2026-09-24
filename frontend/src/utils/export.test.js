import { describe, expect, it } from 'vitest'

import { exportFilename, nowStamp, toMarkdown } from './export'

const DATE = new Date(2026, 8, 23, 15, 4)   // 2026-09-23 15:04（本地时区）

const DATA = {
  relationship: '恋爱',
  other_label: '她',
  count: 2,
  failed_count: 0,
  messages: [
    { index: 1, speaker: 'other', label: '她', text: '在忙吗', timestamp: '15:01' },
    { index: 2, speaker: 'me', label: '我', text: '刚开完会' },
  ],
  analyses: [
    { index: 2, result: {
      primary_intent: { label: '接住话了' },
      emotion: { label: '开心' },
      relation_direction: { label: '维持' },
      response_need: { label: '等待接话', display: '希望我接着聊' },
      communication_style: { label: '普通陈述' },
      intent_detail: '想接着聊下去',
      suggestions: [{ label: '接住', text: '刚闲下来，你说' }],
    } },
  ],
}

describe('toMarkdown', () => {
  it('带上标题、元信息与每条消息的标签 / 潜台词 / 建议', () => {
    const markdown = toMarkdown(DATA, { other: '她', me: '我' }, DATE)

    expect(markdown).toContain('# 她 · 恋爱')
    expect(markdown).toContain('导出时间：2026-09-23 15:04')
    expect(markdown).toContain('消息 2 条 · 已解读 1 条')
    expect(markdown).toContain('**她**（15:01）：在忙吗')
    expect(markdown).toContain('- 意图：接住话了 · 情绪：开心')
    expect(markdown).toContain('- 关系信号：维持 · 期待回应：希望我接着聊 · 表达：普通陈述')
    expect(markdown).toContain('- 潜台词：想接着聊下去')
    expect(markdown).toContain('  - 【接住】刚闲下来，你说')
  })

  it('只导出解读过的那条：没结果的只有正文', () => {
    const lines = toMarkdown(DATA, {}, DATE).split('\n')
    const index = lines.findIndex((line) => line.includes('在忙吗'))
    expect(lines[index + 1]).toBe('')   // 紧接着就是空行，没有任何分析行
  })

  it('失败如实写出来，不假装有内容', () => {
    const failed = { ...DATA, analyses: [{ index: 2, result: {
      primary_intent: { label: '接住话了' }, interpretation_failed: true, gen_failed: true } }] }
    const markdown = toMarkdown(failed, {}, DATE)

    expect(markdown).toContain('- 潜台词生成失败')
    expect(markdown).toContain('- 回复建议生成失败')
  })

  it('没有结果时返回空串（调用方据此不触发下载）', () => {
    expect(toMarkdown(null)).toBe('')
  })

  it('消息没有 label 时退回「我 / 对方」', () => {
    const bare = { ...DATA, messages: [{ index: 1, speaker: 'me', text: '嗨' }], analyses: [] }
    expect(toMarkdown(bare, { me: '张三', other: '李四' }, DATE)).toContain('**张三**：嗨')
  })
})

describe('exportFilename', () => {
  it('把文件名里不能用的字符换掉', () => {
    expect(exportFilename({ other: 'a/b:c*d' }, DATE)).toBe('聊天分析-a_b_c_d-20260923-1504.md')
  })

  it('没有名字时退回「对话」', () => {
    expect(exportFilename({}, DATE)).toBe('聊天分析-对话-20260923-1504.md')
  })
})

describe('nowStamp', () => {
  it('补零到分钟', () => {
    expect(nowStamp(new Date(2026, 0, 2, 3, 4))).toBe('2026-01-02 03:04')
  })
})
