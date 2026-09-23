import { defineStore, storeToRefs } from 'pinia'
import { computed, ref, triggerRef } from 'vue'

import { ApiError, appendChat, fetchHealth, isCancelled, streamAPI } from '@/api/client'
import { useFormStore } from '@/stores/form'
import { usePeopleStore } from '@/stores/people'
import { useSessionsStore } from '@/stores/sessions'
import { downloadText, exportFilename, toMarkdown } from '@/utils/export'

/**
 * 分析动作的状态机：整段分析 / 补跑潜台词 / 补跑推荐回复 / 追加新消息。
 *
 * 几条容易被「优化掉」的细节：
 * - 业务错误显示在抽屉里的状态栏，网络错误显示在顶部提示条，两者不混用；
 * - 请求失败不改动已有结果（不会把分析好的对话清空）；
 * - 文本被改动过（dirty）时禁用两个增强按钮，因为它们锚定的是上一版文本的分类结果；
 * - 每次重新渲染对话都把回复面板恢复成第一版建议（旧版是重建 DOM，编辑内容自然丢弃）。
 *
 * 潜台词与推荐回复是两个独立端点（`/interpret-chat`、`/suggest-chat`）：点哪个只付哪个的
 * 模型调用，一边失败不影响另一边。会话（持久记忆）相关：所有写结果的动作都带上 session_id，
 * 带 session_id 时服务端会读库、合并、落库，并回传合并好的 `session`，前端直接渲染它
 * —— 前后端不会出现两份不一致的数据。
 */
export const useAnalysisStore = defineStore('analysis', () => {
  // 依赖直接取自其它 store。原来由 App 注入 8 个 ref（transcript / me / targets / sessionId…），
  // 「这份状态归谁」只体现在 App 的调用参数里；现在 store 自己取用。
  // 下面用到的别名刻意与旧参数同名，所以实现部分逐字未改——迁移保持机械，不顺手改行为。
  const form = useFormStore()
  const peopleStore = usePeopleStore()
  const sessions = useSessionsStore()
  const { transcript, relationship, genInterpretation, genSuggestions } = storeToRefs(form)
  const { me, people, targets } = storeToRefs(peopleStore)
  const sessionId = storeToRefs(sessions).currentId
  // 每次结果落库后刷新侧栏（新会话 / 更新的时间与预览）。
  const onPersisted = () => sessions.refresh()

  const lastData = ref(null)
  const analyzedText = ref('')
  const statusText = ref('')
  const statusErr = ref(false)
  const notice = ref('')
  const health = ref(null)
  const busy = ref(false)
  const augBusy = ref(false)
  const appendBusy = ref(false)
  const appendStatus = ref('')
  const appendStatusErr = ref(false)
  // 每次「重新渲染对话」自增：回复面板用它做 key，回到第一版建议（与旧版重建 DOM 一致）。
  const renderTick = ref(0)
  // 只有「新结果」才自增：用于把对话滚到底，让最后一条分析卡片进视野（补跑生成层不动滚动位置）。
  const scrollTick = ref(0)
  // 单句潜台词：哪几条正在生成、哪几条失败了（错误直接显示在那张卡片里）。
  const pendingIndexes = ref(new Set())
  const interpretErrors = ref({})
  // 流式预览：index → { text, suggestions, kind }。只在生成过程中有值，`done` 后清空
  // （最终内容以服务端返回的结果为准，预览只负责「让人看到它在动」）。
  const previews = ref({})

  const dirty = computed(() => !!lastData.value && transcript.value !== analyzedText.value)
  const analyzed = computed(() => !!lastData.value)
  // 生成中的按钮禁用，以及「文本改过 → 上一版分类结果作废」的禁用，合在一个开关里。
  const augDisabled = computed(() => augBusy.value || dirty.value)
  const replyTarget = computed(() => {
    const data = lastData.value
    if (!data) return null
    if (data.reply_target) return data.reply_target
    // 兼容老数据：没有 reply_target 时回退到 analyses 里 index 最大的一条。
    let fallback = null
    for (const item of data.analyses || []) {
      if (!fallback || item.index > fallback.index) fallback = item
    }
    return fallback
  })
  // 底部回复面板只在「锚定那条真有建议（或明确失败 / 正在流式生成建议）」时出现：
  // 右键给中间某条单独生成时，建议就贴在那条下面，底部不再重复占位。
  const dockVisible = computed(() => {
    const target = replyTarget.value
    if (!target) return false
    const result = target.result || {}
    // 流式生成时先按预览出现：`kind` 说明这条正在生成建议（面板立刻展开显示「正在生成…」），
    // 已经冒出来的那几条则让面板边生成边长。
    const live = previews.value[target.index]
    const streaming = live?.kind === 'suggestions'
    const previewed = (live?.suggestions || []).length > 0
    // 整段分析（busy）期间本来不占位；但这一条的建议已经在流式冒出来时该展开，否则看不到效果。
    if (busy.value && !streaming && !previewed) return false
    return !!(result.suggestions?.length || result.gen_failed || streaming || previewed)
  })

  // 展示用的「我 / 对方」称呼：优先用记录里的原始标签（可能是真实昵称），没有就退回默认。
  // 放在 store 里是因为消息行、回复面板、导出三处都要用同一份，别再各算一遍。
  const names = computed(() => ({
    me: lastData.value?.me_label || '我',
    other: lastData.value?.other_label || '她',
  }))

  /**
   * 导出当前这次分析为 Markdown。
   *
   * 纯前端拼接 + 下载，不经过后端：导出的是界面上已经看到的结论，
   * 没必要为此再加一个端点（也就没有「导出的内容和屏幕不一致」的风险）。
   */
  function exportMarkdown() {
    if (!lastData.value) return
    downloadText(exportFilename(names.value), toMarkdown(lastData.value, names.value))
    setStatus('已导出为 Markdown 文件。')
  }

  /**
   * 在途的流式请求。取消要真取消：fetch 一断，后端才发现「客户端走了」，从而停止提交
   * 后面的消息（否则关掉页面后它还会把剩下几十条 Jev 调用全跑完，白烧额度）。
   *
   * 用 Set 而不是单个 controller：右键「一键生成」会同时开潜台词与建议两条流。
   */
  const streams = new Set()

  function beginStream() {
    const controller = new AbortController()
    streams.add(controller)
    return controller
  }

  function endStream(controller) {
    streams.delete(controller)
  }

  /** 切会话 / 重新导入 / 离开页面时调用：停掉所有在途的流。 */
  function cancelStreams() {
    for (const controller of streams) controller.abort()
    streams.clear()
  }

  function setStatus(text, isErr = false) {
    statusText.value = text
    statusErr.value = isErr
  }

  function setAppendStatus(text, isErr = false) {
    appendStatus.value = text
    appendStatusErr.value = isErr
  }

  function render(data, isNewResult = true) {
    lastData.value = data
    // withItem / applyPreview 都是原地改的：对象引用没变时 ref 的 setter 不会触发，
    // 这里显式通知一次，保证「把同一个对象再渲染一遍」也能生效。
    triggerRef(lastData)
    renderTick.value += 1
    if (isNewResult) scrollTick.value += 1
  }

  function setInterpretError(index, message) {
    interpretErrors.value = { ...interpretErrors.value, [index]: message || '' }
  }

  function isPending(index) {
    return pendingIndexes.value.has(Number(index))
  }

  /** 切到某条历史会话：整屏还原，不调用任何模型。 */
  function applySession(detail) {
    cancelStreams()
    sessionId.value = detail.session_id || null
    analyzedText.value = detail.transcript || ''
    interpretErrors.value = {}
    pendingIndexes.value = new Set()
    previews.value = {}
    render(detail, true)
  }

  /** 当前会话被删掉 / 清空时把界面收回到空态。 */
  function reset() {
    cancelStreams()
    sessionId.value = null
    lastData.value = null
    analyzedText.value = ''
    interpretErrors.value = {}
    pendingIndexes.value = new Set()
    previews.value = {}
    setStatus('')
    renderTick.value += 1
  }

  function slimOf(data) {
    return {
      relationship: data.relationship,
      messages: data.messages,
      reply_target: data.reply_target,
      analyses: data.analyses.map((item) => ({
        index: item.index,
        speaker: item.speaker,
        context: item.context,
        message: item.message,
        result: {
          primary_intent: item.result.primary_intent
            ? { label: item.result.primary_intent.label, score: item.result.primary_intent.score, definition: item.result.primary_intent.definition }
            : null,
          emotion: item.result.emotion
            ? { label: item.result.emotion.label, score: item.result.emotion.score, definition: item.result.emotion.definition }
            : null,
        },
      })),
    }
  }

  async function probe() {
    try {
      const info = await fetchHealth()
      health.value = info
      if (!info.ok) {
        notice.value = '本地服务已启动，但没有读到 Jev API key（' + info.api_key
          + '）。请检查项目根目录 .env 里的 TYPESAFE_API_KEY，改完重启服务。'
      } else if (info.general_llm === '缺失') {
        notice.value = '已读到 Jev key，但生成层还没有可用的 API Key（' + info.general_llm
          + '）。回复建议和潜台词将生成失败——请在「配置 → 生成层」里给当前启用的那套填上密钥。'
      } else {
        notice.value = ''
      }
    } catch (err) {
      health.value = null
      notice.value = err.message
    }
  }

  /**
   * 整段分析（流式）。返回 true 表示成功（调用方据此收起抽屉）。
   *
   * 体验上的关键变化：以前要等整批 Jev 跑完才一次性渲染；现在每条消息一算完就先画出来，
   * 状态栏也会显示「已完成 x/y 条」。Jev 是 2 次调用/条且不支持流式，所以这才是主要体感。
   */
  async function analyze() {
    // 前端一个人都没认出来时不要自己拦：后端是同一套规则，让它再判一次。
    if (people.value.length && !targets.value.length) {
      setStatus('先在第 2 步选一个要解读的人。', true)
      return false
    }
    if (!relationship.value) {
      setStatus('请先选择关系 / 场景，再开始分析。', true)
      return false
    }
    const wantInterpretation = genInterpretation.value
    const wantSuggestions = genSuggestions.value
    const parts = ['正在 Jev 分类']
    if (wantInterpretation) parts.push('DeepSeek 潜台词')
    if (wantSuggestions) parts.push('DeepSeek 推荐回复')
    const total = targets.value.reduce((sum, p) => sum + p.count, 0)
    const headline = parts.join(' + ')
    busy.value = true
    setStatus(headline + '（共 ' + total + ' 条，请稍候…）')
    const sentText = transcript.value
    const controller = beginStream()
    let done = null
    let received = 0
    try {
      await streamAPI('/analyze-chat/stream', {
        relationship: relationship.value,
        transcript: sentText,
        me_label: me.value,
        read_labels: people.value.length ? targets.value.map((p) => p.label) : null,
        gen_interpretation: wantInterpretation,
        gen_suggestions: wantSuggestions,
        session_id: sessionId.value,
      }, (event) => {
        if (event.type === 'start') {
          // 先把消息骨架渲染出来：用户立刻能看到整段对话，卡片随后一条条补上。
          interpretErrors.value = {}
          previews.value = {}
          lastData.value = shellOf(event)
          render(lastData.value, true)
          return
        }
        if (event.type === 'message') {
          received += 1
          setStatus(headline + '（已完成 ' + received + '/' + (event.total || total) + ' 条…）')
          // 卡片是一条条长出来的，落地就跟着滚到底：停在原地的话新卡片全在视口外面，
          // 看着跟「卡住了」没区别。分析期间用户也不会在这时候往上翻历史。
          render(withItem(lastData.value, event.item), true)
          return
        }
        if (event.type === 'done') {
          done = event
          return
        }
        applyPreview(event)
      }, { signal: controller.signal })
      if (!done) throw new ApiError('分析没有正常结束，请重试。', 'app')
      analyzedText.value = sentText
      sessionId.value = done.session_id || sessionId.value
      previews.value = {}
      render(done.data)
      notice.value = ''
      if (done.data.failed_count) {
        setStatus('已完成 ' + done.data.count + ' 条，另有 ' + done.data.failed_count
          + ' 条暂未取得 Jev 结果；可重新点「仅Jev分析（意图+情绪）」补跑。', true)
      } else {
        setStatus('分析完成。')
      }
      onPersisted?.()
      return true
    } catch (err) {
      // 主动取消（切会话 / 重新导入 / 离开页面）：静默收尾，既不报错也不覆盖状态栏。
      if (isCancelled(err)) return false
      // 业务错误（400/500）必须显示出来：之前把提示清空了，看起来就像「点了没反应」。
      if (err.kind === 'app') {
        notice.value = ''
        setStatus(err.message, true)
      } else {
        notice.value = err.message
        setStatus('')
      }
      return false
    } finally {
      endStream(controller)
      busy.value = false
    }
  }

  /** `start` 事件 → 一个完整的骨架 envelope（消息先上屏，analyses 为空）。 */
  function shellOf(event) {
    const others = (event.speakers || []).filter((item) => item.role === 'other')
      .map((item) => item.label)
    return {
      version: '',
      relationship: event.relationship,
      messages: event.messages || [],
      analyses: [],
      count: 0,
      count_other: 0,
      count_me: 0,
      failed_count: 0,
      failed_indexes: [],
      include_me: !!(event.me_label && (event.read_labels || []).includes(event.me_label)),
      speakers: event.speakers || [],
      me_label: event.me_label || null,
      other_label: others[0] || null,
      other_labels: others,
      read_labels: event.read_labels || [],
      gen_interpretation: !!event.gen_interpretation,
      gen_suggestions: !!event.gen_suggestions,
      reply_target: null,
    }
  }

  /**
   * `message` 事件 → 把这一条追加进当前骨架，并把计数与 reply_target 同步上。
   *
   * 原地追加（而不是 `[...analyses, item]` 造新数组）：50 条消息下每次重建整表累计是
   * O(n²) 的拷贝，而且每次都要把整个 lastData 换掉、连带整屏 diff。
   *
   * 这里刻意**不用 shallowRef**：`lastData` 会作为 prop 传给 ChatFlow → MessageRow，
   * 子组件靠「渲染时读到的深层属性」建立依赖；换成 shallowRef 后原地改内容不再触发
   * 子组件更新（prop 引用没变），得反过来每次造新对象——那就把 O(n²) 又请回来了。
   */
  function withItem(data, item) {
    if (!data) return data
    if (!Array.isArray(data.analyses)) data.analyses = []
    data.analyses.push(item)
    const mine = item.speaker === '我'
    const lastIndex = (data.messages || []).reduce((max, message) => (
      message.index > max ? message.index : max), 0)
    data.count = data.analyses.length
    data.count_other = (data.count_other || 0) + (mine ? 0 : 1)
    data.count_me = (data.count_me || 0) + (mine ? 1 : 0)
    data.failed_count = 0
    if (item.index === lastIndex) data.reply_target = item
    return data
  }

  /**
   * 生成层预览事件：潜台词逐字 / 建议逐条；`reset` 表示这一条在重试或被改写，先清空。
   *
   * `delta.text` 是**增量片段**（不是累计全文），所以这里是往后接，而不是整体替换。
   *
   * `kind` 必须原样留着：它标出这片预览属于哪一类，`ReplyDock` 靠它知道「正在生成建议」，
   * 从而在建议还没冒出来时显示「正在生成回复建议…」而不是「暂时没有可用的回复建议」。
   * 右键「一键生成」会同时开两条流（潜台词 + 建议），所以要给两类各留一个槽位：
   * `reset` 只清事件声明的这一类，不然潜台词重试会把已经冒出来的建议抹掉。
   */
  function applyPreview(event) {
    const index = event.index
    if (index === undefined || index === null) return
    // 按 index 原地写（每次都整表浅拷贝的话，逐字 delta 下就是「每个 token 拷一遍全表」）。
    const live = previews.value
    const current = live[index] || { text: '', suggestions: [], kind: '' }
    if (event.type === 'reset') {
      const clearing = event.kind || current.kind
      live[index] = clearing === 'suggestions'
        ? { kind: 'suggestions', text: current.text || '', suggestions: [] }
        : { kind: 'interpretation', text: '', suggestions: current.suggestions || [] }
    } else if (event.type === 'delta') {
      live[index] = { ...current, kind: 'interpretation',
        text: (current.text || '') + (event.text || '') }
    } else if (event.type === 'item') {
      live[index] = { ...current, kind: 'suggestions',
        suggestions: [...(current.suggestions || []), event.suggestion] }
    }
  }

  /**
   * 收掉某几条的某一类预览；并发生成的另一类保持不动（预览只是「让人看到它在动」）。
   *
   * `kind` 标记要一起撤掉：它代表「这类还在流」，留着会让底部面板一直停在「正在生成…」。
   * 另一类还在流的话，kind 顺势让给它。
   */
  function clearPreviews(indexes, kind) {
    const live = previews.value
    for (const raw of indexes) {
      const key = String(raw)
      const current = live[key]
      if (!current) continue
      const other = current.kind === kind ? '' : current.kind
      live[key] = kind === 'interpretation'
        ? { ...current, text: '', kind: other }
        : { ...current, suggestions: [], kind: other }
    }
  }

  /**
   * 补跑一类生成（潜台词或推荐回复），两个端点各跑各的。
   * @param {'interpretation'|'suggestions'} kind
   * @param {number[]|null} indexes 只跑这几条；null = 潜台词全体 / 推荐回复只跑最后一条
   * @param {object} options silent=true 时不改全局状态栏（单条生成用）
   */
  async function runGeneration(kind, indexes = null, { silent = false } = {}) {
    const data = lastData.value
    if (!data) return { ok: false, error: '还没有可用的分析结果。' }
    const single = Array.isArray(indexes) && indexes.length > 0
    const what = kind === 'interpretation' ? '潜台词' : '推荐回复'
    if (!single && !silent) augBusy.value = true
    if (!single && !silent) setStatus('AI 正在生成' + what + '，请稍候…')
    const controller = beginStream()
    let started = []
    try {
      const payload = {}
      if (single) payload.indexes = indexes
      if (sessionId.value) payload.session_id = sessionId.value
      else payload.prev = slimOf(data)
      const path = kind === 'interpretation' ? '/interpret-chat/stream' : '/suggest-chat/stream'
      let done = null
      await streamAPI(path, payload, (event) => {
        if (event.type === 'start') {
          // 先把这几条标成「生成中」：卡片按钮会变成「生成中…」，用户知道在跑哪几条。
          started = event.indexes || []
          if (started.length) {
            pendingIndexes.value = new Set([...pendingIndexes.value, ...started])
          }
          return
        }
        if (event.type === 'done') {
          done = event
          return
        }
        applyPreview(event)
      }, { signal: controller.signal })
      if (!done) throw new ApiError('生成没有正常结束，请重试。', 'app')
      // `done` 只带本次这一类的增量（服务端另有一份加锁合并后落库的权威副本），
      // 前端按同一套「字段是否存在」规则合并：另一类已经生成的内容不会被清掉。
      mergeLocally(data, done, kind)
      render(data, false)
      if (sessionId.value) onPersisted?.()
      if (!single && !silent) {
        setStatus(done.failed_indexes?.length
          ? '「' + what + '」完成，但有 ' + done.failed_indexes.length + ' 条生成失败（可重试）。'
          : what + '完成。')
      }
      return { ok: true, error: '', failed: done.failed_indexes || [] }
    } catch (err) {
      // 取消不是失败：不写状态栏，也不给调用方一个「错误」去弹到卡片上。
      if (isCancelled(err)) return { ok: false, error: '', cancelled: true }
      if (!single && !silent) {
        if (err.kind === 'app') setStatus(err.message, true)
        else {
          setStatus('')
          notice.value = err.message
        }
      }
      return { ok: false, error: err.message || '生成失败，请重试。' }
    } finally {
      if (started.length) {
        const next = new Set(pendingIndexes.value)
        for (const index of started) next.delete(index)
        pendingIndexes.value = next
      }
      // 只收自己这一类的预览：并发跑的另一类（右键「一键生成」）可能还在流。
      clearPreviews(started, kind)
      endStream(controller)
      augBusy.value = false
    }
  }

  /** 工具栏 / 右键的「生成潜台词」：不传 indexes = 覆盖全部已有分析。 */
  function runInterpretation(indexes = null, options) {
    return runGeneration('interpretation', indexes, options)
  }

  /** 工具栏 / 右键的「生成推荐回复」：不传 indexes = 只跑最后一条。 */
  function runSuggestions(indexes = null, options) {
    return runGeneration('suggestions', indexes, options)
  }

  /**
   * 单条生成：只对某一条消息跑生成，加载/失败都显示在那条上。
   * 右键菜单的「生成潜台词 / 生成推荐回复 / 一键生成」和气泡右侧的按钮都走这里。
   * 两类各自一个请求；服务端的会话合并是加锁的，所以并发也不会互相覆盖。
   */
  async function generateFor(index, { interpretation = true, suggestions = false } = {}) {
    const target = Number(index)
    if (!lastData.value || isPending(target)) return { ok: false, error: '' }
    pendingIndexes.value = new Set(pendingIndexes.value).add(target)
    setInterpretError(target, '')
    try {
      const calls = []
      if (interpretation) calls.push(runInterpretation([target], { silent: true }))
      if (suggestions) calls.push(runSuggestions([target], { silent: true }))
      const outcomes = await Promise.all(calls)
      // 被取消的那条不算失败，别在卡片上盖一个「生成失败」的红字。
      const failedCall = outcomes.find((item) => !item.ok && !item.cancelled)
      const failedIndex = outcomes.some((item) => (item.failed || []).includes(target))
      if (failedCall || failedIndex) {
        setInterpretError(target, failedCall?.error || '这条生成失败，可以再点一次重试。')
      }
      return { ok: !failedCall && !failedIndex }
    } finally {
      const next = new Set(pendingIndexes.value)
      next.delete(target)
      pendingIndexes.value = next
    }
  }

  /** 气泡右侧那个按钮：只生成潜台词。 */
  function interpretOne(index) {
    return generateFor(index, { interpretation: true, suggestions: false })
  }

  /** 老路径（没有会话）时的本地合并，规则与服务端 merge_augmentations 一致：按字段是否存在。 */
  function mergeLocally(data, resp, kind) {
    const map = resp.augmentations || {}
    let succeeded = false
    const merge = (item) => {
      const aug = map[String(item.index)]
      if (!aug) return
      if ('intent_detail' in aug) {
        item.result.interpretation = aug.interpretation
        item.result.intent_detail = aug.intent_detail
        item.result.emotion_detail = aug.emotion_detail
        item.result.interpretation_failed = false
        item.result.gen_skipped = false
        succeeded = true
      } else if (kind === 'interpretation' && aug.gen_failed) {
        item.result.interpretation_failed = true
        item.result.gen_error = aug.gen_error
      }
      if ('suggestions' in aug) {
        item.result.suggestions = aug.suggestions
        item.result.gen_failed = false
        item.result.gen_skipped = false
        succeeded = true
      } else if (kind === 'suggestions' && aug.gen_failed) {
        item.result.gen_failed = true
        item.result.gen_error = aug.gen_error
      }
    }
    for (const item of data.analyses || []) merge(item)
    // reply_target 与 analyses 里同序号的那条是两份独立副本，两处都要合并（同服务端规则）。
    if (data.reply_target) merge(data.reply_target)
    // 同服务端：什么都没生成时不动开关，避免把本来藏着的旧数据翻出来。
    if (succeeded && kind === 'interpretation') data.gen_interpretation = true
    if (succeeded && kind === 'suggestions') data.gen_suggestions = true
  }

  /** 追加新消息：只对新增部分跑 Jev，旧结果原样保留，不自动跑 AI。返回 true 表示成功。 */
  async function appendTail(tail) {
    const text = (tail || '').trim()
    if (!text) {
      setAppendStatus('请先粘贴新增的消息。', true)
      return false
    }
    if (!lastData.value || !lastData.value.messages || !lastData.value.messages.length) {
      setAppendStatus('请先做一次「仅Jev分析（意图+情绪）」，再追加。', true)
      return false
    }
    appendBusy.value = true
    setAppendStatus('正在对新增消息做 Jev 分类，请稍候…')
    const before = lastData.value.messages.length
    try {
      // 旧文本在前、新增尾部在后，交给后端一次性重解析：前缀序号不变，新消息接在后面。
      const full = (analyzedText.value || '') + '\n' + text
      const resp = await appendChat({
        prev: sessionId.value ? undefined : lastData.value,
        session_id: sessionId.value,
        transcript: full,
        old_count: before,
      })
      const added = (resp.messages || []).length - before
      if (added <= 0) {
        // 没认出新消息多半是格式问题（缺说话人标记），明确提示，别让人以为是成功了。
        setAppendStatus('没有识别到新增的消息。请确认粘贴的是新内容，且带说话人（微信复制格式，或「我：…」「她：…」）。', true)
        return false
      }
      analyzedText.value = full
      // 主文本框同步成追加后的全文，避免被脏检测判定为「文本已改动」而误禁用增强按钮。
      transcript.value = full
      sessionId.value = resp.session_id || sessionId.value
      render(resp)
      setAppendStatus(resp.failed_count
        ? '已追加 ' + added + ' 条并完成 Jev 分类，另有 ' + resp.failed_count + ' 条未取到结果，可再次追加补跑。'
        : '已追加 ' + added + ' 条并完成 Jev 分类。')
      onPersisted?.()
      return true
    } catch (err) {
      setAppendStatus(err.message || '追加失败，请重试。', true)
      return false
    } finally {
      appendBusy.value = false
    }
  }

  // 只导出「外部真的会用到」的：analyzedText / augBusy / isPending 都只在本文件内部使用，
  // 放进返回值会让人以为外面要读它（曾经就是如此，实际没人用）。
  return {
    lastData, renderTick, scrollTick, health,
    statusText, statusErr, notice, busy, dirty, analyzed, augDisabled,
    dockVisible, replyTarget, names, pendingIndexes, interpretErrors, previews,
    appendBusy, appendStatus, appendStatusErr,
    setStatus, setAppendStatus, analyze, interpretOne, generateFor,
    runInterpretation, runSuggestions, appendTail, applySession, reset, probe,
    cancelStreams, exportMarkdown,
  }
})
