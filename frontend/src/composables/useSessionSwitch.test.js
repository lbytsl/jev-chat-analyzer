import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

// 只替换要断言的两个端点，其余（API 基址、streamAPI…）保持真实：
// analysis store 也从这个模块导入，整体替换会让它在导入期拿到 undefined。
vi.mock('@/api/client', async (importOriginal) => {
  const actual = await importOriginal()
  return { ...actual, getSession: vi.fn(), deleteSessions: vi.fn() }
})

import { deleteSessions, getSession } from '@/api/client'
import { useSessionSwitch } from '@/composables/useSessionSwitch'
import { useAnalysisStore } from '@/stores/analysis'
import { useFormStore } from '@/stores/form'
import { usePeopleStore } from '@/stores/people'
import { useSessionsStore } from '@/stores/sessions'
import { useUiStore } from '@/stores/ui'

const DETAIL = {
  session_id: 's1',
  transcript: '我：在忙吗\n她：刚开完会',
  relationship: '同事',
  gen_interpretation: false,
  gen_suggestions: true,
  me_label: '我',
  read_labels: ['她'],
}

describe('useSessionSwitch', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('打开会话：整屏还原（会话 id / 正文 / 关系 / 两个勾选 / 说话人选择 / 浮层）', async () => {
    getSession.mockResolvedValue(DETAIL)
    const form = useFormStore()
    form.transcript = '旧正文'
    form.relationship = '恋爱'
    form.genInterpretation = true
    const ui = useUiStore()
    ui.drawerOpen = true
    const sessions = useSessionsStore()
    const people = usePeopleStore()
    people.restore({ me: '甲', read: ['乙'] })

    await useSessionSwitch().openSession({ id: 's1' })

    expect(getSession).toHaveBeenCalledWith('s1')
    expect(sessions.currentId).toBe('s1')
    expect(form.transcript).toBe(DETAIL.transcript)
    expect(form.relationship).toBe('同事')
    expect(form.genInterpretation).toBe(false)
    expect(form.genSuggestions).toBe(true)
    expect(people.me).toBe('我')
    expect([...people.read]).toEqual(['她'])
    expect(ui.drawerOpen).toBe(false)
    expect(useAnalysisStore().notice).toBe('')
  })

  it('点的已经是当前会话：不重复请求（切换是零模型成本的整屏还原）', async () => {
    const sessions = useSessionsStore()
    sessions.currentId = 's1'

    await useSessionSwitch().openSession({ id: 's1' })

    expect(getSession).not.toHaveBeenCalled()
  })

  it('打开失败：提示写进顶部提示条并刷新列表，且不动屏幕上的内容', async () => {
    getSession.mockRejectedValue(new Error('这个会话不存在，可能已经被删除。'))
    const form = useFormStore()
    form.transcript = '旧正文'
    const sessions = useSessionsStore()
    const refresh = vi.spyOn(sessions, 'refresh').mockResolvedValue(undefined)

    await useSessionSwitch().openSession({ id: 'gone' })

    expect(useAnalysisStore().notice).toBe('这个会话不存在，可能已经被删除。')
    expect(refresh).toHaveBeenCalled()
    expect(form.transcript).toBe('旧正文')
  })

  it('清空当前会话：表单归零 + 分析结果 reset + 说话人选择交回默认', () => {
    const form = useFormStore()
    form.transcript = '旧正文'
    form.relationship = '恋爱'
    const analysis = useAnalysisStore()
    analysis.applySession(DETAIL)

    useSessionSwitch().clearCurrentSession()

    expect(form.transcript).toBe('')
    expect(form.relationship).toBe('')
    expect(analysis.lastData).toBe(null)
    expect(usePeopleStore().me).toBe(null)
  })

  it('「＋ 导入聊天」先清空再开抽屉，并收起继续记录弹窗', () => {
    const form = useFormStore()
    form.transcript = '旧正文'
    const ui = useUiStore()
    ui.appendOpen = true
    ui.appendDraft = '她：在吗'

    useSessionSwitch().startImport()

    expect(form.transcript).toBe('')
    expect(ui.drawerOpen).toBe(true)
    expect(ui.appendOpen).toBe(false)
    expect(ui.appendDraft).toBe('')
  })

  it('删掉的正好是当前会话时，界面收回空态并关掉继续记录弹窗', async () => {
    deleteSessions.mockResolvedValue({ deleted: 1, session_ids: ['s1'] })
    const form = useFormStore()
    form.transcript = '旧正文'
    const sessions = useSessionsStore()
    sessions.currentId = 's1'
    vi.spyOn(sessions, 'remove').mockResolvedValue({ deleted: 1, session_ids: ['s1'] })
    const ui = useUiStore()
    ui.appendOpen = true

    await useSessionSwitch().removeSessions(['s1'])

    expect(form.transcript).toBe('')
    expect(sessions.currentId).toBe(null)
    expect(ui.appendOpen).toBe(false)
    expect(useAnalysisStore().statusText).toBe('已删除 1 个会话。')
  })
})
