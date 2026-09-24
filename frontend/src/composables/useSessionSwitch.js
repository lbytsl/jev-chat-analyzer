import { getSession } from '@/api/client'
import { useAnalysisStore } from '@/stores/analysis'
import { useFormStore } from '@/stores/form'
import { usePeopleStore } from '@/stores/people'
import { useSessionsStore } from '@/stores/sessions'
import { useUiStore } from '@/stores/ui'

/**
 * 会话的「打开 / 清空 / 新建导入」。
 *
 * 为什么还单独留一块（而不是塞进某个 store）：这三件事都要**同时改五处状态**——列表里的
 * 当前 id、表单上的正文 / 关系 / 两个 AI 勾选、分析结果、说话人选择，外加浮层开关。
 * 它们不属于任何单一 store，放在这里能一眼看清「一次切换都动了谁」。
 *
 * 状态进 store 之后，这里不再需要 App 注入依赖（原来是 list / analysis / people / form
 * 四组参数 + 两个回调），直接取 store；App 那边只剩 UI 事件转发。
 */
export function useSessionSwitch() {
  const form = useFormStore()
  const people = usePeopleStore()
  const sessions = useSessionsStore()
  const analysis = useAnalysisStore()
  const ui = useUiStore()

  /** 切换到某个历史会话：整屏还原，不调用任何模型（零成本）。 */
  async function openSession(session) {
    if (!session) return
    if (session.id === sessions.currentId) {
      ui.drawerOpen = false
      ui.closeSidebarOnMobile()
      return
    }
    try {
      const detail = await getSession(session.id)
      form.finishImport()
      people.finishImport()
      sessions.markCurrent(detail.session_id)
      analysis.applySession(detail)
      form.applySession(detail)
      people.restore({ me: detail.me_label, read: detail.read_labels })
      ui.drawerOpen = false
      ui.closeSidebarOnMobile()
      analysis.notice = ''
    } catch (err) {
      analysis.notice = err.message
      // 列表可能已经过期（会话被别处删了）：重新拉一次，让侧栏回到真实状态。
      sessions.refresh()
    }
  }

  /** 把界面收回空态（当前会话被删掉、或回到没有会话的状态）。 */
  function clearCurrentSession() {
    form.finishImport()
    people.finishImport()
    analysis.reset()
    form.reset()
    people.restore({})
    ui.drawerOpen = false
  }

  /**
   * 「＋ 导入聊天」先打开一份空表单。旧聊天留在主界面，关闭抽屉可恢复原表单；
   * 真正提交时才清掉旧会话绑定，避免打开面板就让左侧内容跳回空态。
   */
  function startImport() {
    if (ui.drawerOpen) return
    people.beginImport()
    form.beginImport()
    people.restore({})
    analysis.notice = ''
    ui.closeAppend()
    ui.drawerOpen = true
    ui.closeSidebarOnMobile()
  }

  /**
   * 删除会话（含「删掉的正好是当前这条」的收尾）。
   *
   * 当前会话被删掉时必须把界面收回空态并关掉继续记录弹窗：否则屏幕上还留着一条已经不存在的
   * 会话内容，再点「继续记录」就是往一个已删除的 id 上追加。
   */
  async function removeSessions(ids) {
    const target = (ids || []).map(String)
    if (!target.length) return null
    const removingCurrent = !!sessions.currentId && target.includes(sessions.currentId)
    try {
      const result = await sessions.remove(target)
      if (removingCurrent) {
        clearCurrentSession()
        ui.closeAppend()
      }
      analysis.setStatus('已删除 ' + result.deleted + ' 个会话。')
      return result
    } catch (err) {
      analysis.notice = err.message
      return null
    }
  }

  return { openSession, clearCurrentSession, startImport, removeSessions }
}
