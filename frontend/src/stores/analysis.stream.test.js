import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/api/client', async (importOriginal) => ({
  ...await importOriginal(),
  streamAPI: vi.fn(),
  listSessions: vi.fn(async () => ({ sessions: [], total: 0 })),
}))

import { streamAPI } from '@/api/client'
import { useAnalysisStore } from '@/stores/analysis'
import { useFormStore } from '@/stores/form'
import { usePeopleStore } from '@/stores/people'

describe('逐句流式状态', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('只在这一句收到预览到结果完成之间标记为正在输出', async () => {
    const analysis = useAnalysisStore()
    analysis.applySession({
      transcript: '她：没事',
      messages: [{ index: 1, text: '没事', speaker: 'other' }],
      analyses: [{ index: 1, result: {} }],
    })
    streamAPI.mockImplementation(async (_path, _payload, onEvent) => {
      onEvent({ type: 'start', indexes: [1] })
      expect(analysis.previews[1]).toBeUndefined()

      onEvent({ type: 'delta', kind: 'interpretation', index: 1, text: '其实我' })
      expect(analysis.previews[1].activeKinds).toEqual(['interpretation'])
      expect(analysis.previews[1].text).toBe('其实我')

      onEvent({ type: 'result', index: 1, augmentation: { intent_detail: '其实我在等你' } })
      expect(analysis.previews[1].activeKinds).toEqual([])
      onEvent({ type: 'done', augmentations: {}, failed_indexes: [] })
    })

    const outcome = await analysis.runInterpretation([1])
    expect(outcome.ok).toBe(true)
    expect(analysis.previews[1].activeKinds).toEqual([])
  })

  it('整段分析的骨架、逐句结果和完成事件都不要求滚到末尾', async () => {
    const form = useFormStore()
    form.transcript = '她：没事'
    form.relationship = '恋爱'
    usePeopleStore().sync()
    const analysis = useAnalysisStore()
    const message = { index: 1, text: '没事', speaker: 'other', label: '她' }
    const item = { index: 1, speaker: '对方', result: {} }
    streamAPI.mockImplementation(async (_path, _payload, onEvent) => {
      onEvent({ type: 'start', messages: [message], relationship: '恋爱' })
      expect(analysis.scrollTick).toBe(0)
      onEvent({ type: 'message', item, total: 1 })
      expect(analysis.scrollTick).toBe(0)
      onEvent({ type: 'done', data: {
        messages: [message], analyses: [item], count: 1, failed_count: 0,
      } })
    })

    expect(await analysis.analyze()).toBe(true)
    expect(analysis.scrollTick).toBe(0)
  })
})
