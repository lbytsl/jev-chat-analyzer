import { defineStore } from 'pinia'
import { ref } from 'vue'

import { deleteSessions, getSession, listSessions } from '@/api/client'

/**
 * 会话列表：谁在列表里、当前是哪一条、多选删除。
 *
 * 只负责「列表 + 当前 id」这一层状态；会话内容的载入交给 analysis store 的 applySession，
 * 避免两处都持有同一份聊天数据。
 */
export const useSessionsStore = defineStore('sessions', () => {
  const sessions = ref([])
  const total = ref(0)
  const currentId = ref(null)
  const loading = ref(false)
  const error = ref('')
  const selectMode = ref(false)
  const selected = ref(new Set())

  async function refresh() {
    loading.value = true
    try {
      const data = await listSessions()
      sessions.value = data.sessions || []
      total.value = data.total || 0
      error.value = ''
      // 当前会话被别处删掉了就别再指着它。
      if (currentId.value && !sessions.value.some((item) => item.id === currentId.value)) {
        const stillThere = await hasSession(currentId.value)
        if (!stillThere) currentId.value = null
      }
    } catch (err) {
      error.value = err.message
    } finally {
      loading.value = false
    }
  }

  async function hasSession(sessionId) {
    try {
      await getSession(sessionId)
      return true
    } catch (err) {
      return false
    }
  }

  function markCurrent(sessionId) {
    currentId.value = sessionId
  }

  function find(sessionId) {
    return sessions.value.find((item) => item.id === sessionId) || null
  }

  function toggleSelect(sessionId) {
    const next = new Set(selected.value)
    if (next.has(sessionId)) next.delete(sessionId)
    else next.add(sessionId)
    selected.value = next
  }

  function setSelectMode(on) {
    selectMode.value = on
    if (!on) selected.value = new Set()
  }

  async function remove(ids) {
    const target = [...ids]
    if (!target.length) return { deleted: 0, session_ids: [] }
    const result = await deleteSessions(target)
    selected.value = new Set()
    await refresh()
    return result
  }

  return {
    sessions, total, currentId, loading, error, selectMode, selected,
    refresh, markCurrent, find, toggleSelect, setSelectMode, remove,
  }
})
