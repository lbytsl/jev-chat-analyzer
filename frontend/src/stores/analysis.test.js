import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it } from 'vitest'

import { useAnalysisStore } from '@/stores/analysis'
import { useFormStore } from '@/stores/form'

// 这个文件只碰纯状态与 getter，不发请求（发请求的部分归 api/client 与后端测试）。
// 模块顶层会读 location，所以 vitest 的 environment 必须是 jsdom（见 vite.config.js）。

describe('analysis store', () => {
  beforeEach(() => setActivePinia(createPinia()))

  function withSession(detail) {
    const analysis = useAnalysisStore()
    analysis.applySession(detail)
    return analysis
  }

  it('称呼取自结果里的原始标签，缺省退回「我 / 她」', () => {
    const analysis = useAnalysisStore()
    expect(analysis.names).toEqual({ me: '我', other: '她' })

    analysis.applySession({ transcript: 'x', messages: [], analyses: [],
                            me_label: '张三', other_label: '阿珍' })

    expect(analysis.names).toEqual({ me: '张三', other: '阿珍' })
  })

  it('正文改动后标记 dirty，两个增强按钮据此禁用', () => {
    const form = useFormStore()
    form.transcript = '我：在吗'
    const analysis = withSession({ transcript: '我：在吗', messages: [], analyses: [] })

    expect(analysis.dirty).toBe(false)

    form.transcript = '我：在吗\n她：刚开完会'

    expect(analysis.dirty).toBe(true)
    expect(analysis.augDisabled).toBe(true)
  })

  it('老数据没有 reply_target 时回退到 index 最大的一条', () => {
    const analysis = withSession({ transcript: 'x', messages: [],
                                   analyses: [{ index: 1 }, { index: 5 }, { index: 3 }] })

    expect(analysis.replyTarget.index).toBe(5)
  })

  it('只有锚定那条真有建议（或明确失败）时才显示底部回复面板', () => {
    const empty = withSession({ transcript: 'x', messages: [], analyses: [],
                                reply_target: { index: 2, result: {} } })
    expect(empty.dockVisible).toBe(false)

    const withSuggestions = withSession({
      transcript: 'x', messages: [], analyses: [],
      reply_target: { index: 2, result: { suggestions: [{ text: '刚闲下来' }] } } })
    expect(withSuggestions.dockVisible).toBe(true)

    // 整段分析进行中本来不占位：那期间它是「结果还没出来」，不是「没有建议」。
    withSuggestions.busy = true
    expect(withSuggestions.dockVisible).toBe(false)
  })

  it('切换会话会清掉上一次的预览、错误与待生成标记', () => {
    const analysis = useAnalysisStore()
    analysis.previews = { 1: { text: '半截', kind: 'interpretation' } }
    analysis.interpretErrors = { 1: '失败了' }
    analysis.pendingIndexes = new Set([1])

    analysis.applySession({ transcript: 'x', messages: [], analyses: [] })

    expect(analysis.previews).toEqual({})
    expect(analysis.interpretErrors).toEqual({})
    expect(analysis.pendingIndexes.size).toBe(0)
  })

  it('reset 收回空态：结果、会话绑定与状态栏一起清', () => {
    const analysis = withSession({ session_id: 's1', transcript: 'x', messages: [], analyses: [] })
    analysis.setStatus('分析中…')

    analysis.reset()

    expect(analysis.lastData).toBe(null)
    expect(analysis.analyzed).toBe(false)
    expect(analysis.statusText).toBe('')
  })
})
