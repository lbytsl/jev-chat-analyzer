import { defineStore } from 'pinia'
import { computed, ref, watch } from 'vue'

import { useFormStore } from '@/stores/form'
import { detectPeople, guessMe } from '@/utils/transcript'

/**
 * 「我是谁 / 解读谁」的自动推断。
 *
 * 规则与旧版逐行等价，关键点有三个：
 * 1) 只在说话人集合发生变化（label+count 变化）时才重算，否则用户的选择不会被打断；
 * 2) 用户手动改过（userTouched）后，优先保留他的选择，只剔除这次记录里已经没有的人；
 * 3) 只剩一个说话人时补勾上、避免「按钮禁用但页面上没有可点的东西」这种死路。
 *
 * 正文直接取自 form store（原来是 App 通过参数喂进来的 ref）：这样它不必等别人注入
 * 状态，也不会有「App 传了旧 ref」这类错配。
 */
export const usePeopleStore = defineStore('people', () => {
  const form = useFormStore()

  const people = ref([])
  const me = ref(null)
  const read = ref(new Set())
  const userTouched = ref(false)
  let lastKey = ''

  function sync() {
    const detected = detectPeople(form.transcript)
    people.value = detected
    const key = detected.map((p) => p.label + ':' + p.count).join('|')
    if (key === lastKey) return
    lastKey = key
    const rememberedRead = [...read.value]
    if (!detected.length) {
      me.value = null
      read.value = new Set()
      userTouched.value = false
      return
    }
    if (userTouched.value) {
      if (detected.length < 2) me.value = null
      else if (!(me.value && detected.some((p) => p.label === me.value))) me.value = guessMe(detected)
      read.value = new Set(detected.filter((p) => rememberedRead.includes(p.label)).map((p) => p.label))
      // 之前勾的人在新记录里一个都不剩（换了一整段聊天，比如直接在抽屉里粘新记录）：
      // 那份选择已经没意义，按默认规则重新给一套——否则会留下「解读对象为空 →
      // 分析按钮禁用 → 点了没反应」的死路。用户主动取消全部勾选时 rememberedRead
      // 是空的，这种情况不覆盖他的选择。
      if (rememberedRead.length && !read.value.size && detected.length <= 2) {
        read.value = defaultRead(detected, me.value)
      }
      return
    }
    // 只有一个说话人时分不出「我是谁」，me 留空（服务端全部按对方处理）。
    me.value = detected.length >= 2 ? guessMe(detected) : null
    read.value = defaultRead(detected, me.value)
  }

  // 1 人：唯一可解读的就是他，直接勾上；2 人：默认勾对方；≥3 人不替用户做主。
  function defaultRead(detected, meLabel) {
    if (detected.length === 1) return new Set(detected.map((p) => p.label))
    if (detected.length === 2) {
      return new Set(detected.filter((p) => p.label !== meLabel).map((p) => p.label))
    }
    return new Set()
  }

  /** 切回某个历史会话：用会话里存的「我是谁 / 解读谁」覆盖自动推断的结果。 */
  function restore({ me: meLabel, read: labels } = {}) {
    const valid = (Array.isArray(labels) ? labels : []).filter(Boolean)
    if (!valid.length) {
      // 老会话没存（或存空了）：交回默认推断规则重新猜一次。
      userTouched.value = false
      lastKey = ''
      sync()
      return
    }
    userTouched.value = true
    me.value = meLabel || null
    read.value = new Set(valid)
  }

  function setMe(label) {
    if (me.value === label) return
    userTouched.value = true
    me.value = label
  }

  function toggleRead(label) {
    userTouched.value = true
    const next = new Set(read.value)
    if (next.has(label)) next.delete(label)
    else next.add(label)
    read.value = next
  }

  // 输入时防抖：detectPeople 会把整段文本逐行跑正则（输入框允许 5 万字），每个输入事件
  // 都全量扫一遍会明显卡顿。`sync()` 本身仍可直接调——切会话的 restore 与提交前的 flush
  // 都走它，防抖只作用于「一边打字一边重算」这条路径。
  const SYNC_DEBOUNCE_MS = 250
  let syncTimer = null
  let disposed = false

  watch(() => form.transcript, () => {
    // store 是长生命周期的，dispose() 之后不该再自己被唤醒（测试里尤其明显）。
    if (disposed) return
    if (syncTimer) clearTimeout(syncTimer)
    syncTimer = setTimeout(() => {
      syncTimer = null
      sync()
    }, SYNC_DEBOUNCE_MS)
  })
  sync()

  /** 清掉待触发的防抖定时器（组件卸载时调用）。 */
  function dispose() {
    disposed = true
    if (syncTimer) {
      clearTimeout(syncTimer)
      syncTimer = null
    }
  }

  const targets = computed(() => people.value.filter((p) => read.value.has(p.label)))
  const solo = computed(() => people.value.length < 2)

  return { people, me, read, targets, solo, setMe, toggleRead, restore, sync, dispose }
})
