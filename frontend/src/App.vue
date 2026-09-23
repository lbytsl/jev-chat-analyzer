<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'

import { getSession } from '@/api/client'
import AppendModal from '@/components/AppendModal.vue'
import AugmentBar from '@/components/AugmentBar.vue'
import ChatFlow from '@/components/ChatFlow.vue'
import ChatSidebar from '@/components/ChatSidebar.vue'
import ImportDrawer from '@/components/ImportDrawer.vue'
import ReplyDock from '@/components/ReplyDock.vue'
import SettingsDrawer from '@/components/SettingsDrawer.vue'
import { useAnalysis } from '@/composables/useAnalysis'
import { usePeople } from '@/composables/usePeople'
import { useSessions } from '@/composables/useSessions'
import { copyText } from '@/utils/clipboard'

const transcript = ref('')
const relationship = ref('')
const genInterpretation = ref(false)
const genSuggestions = ref(false)

const { people, me, targets, solo, setMe, toggleRead, restore, read } = usePeople(transcript)
const readLabels = computed(() => [...read.value])

// 会话列表负责「有哪些会话、当前是哪条」，内容本身放在 useAnalysis 里，避免两份状态。
const {
  sessions, currentId, selectMode, selected, loading: sessionsLoading,
  refresh: refreshSessions, markCurrent, toggleSelect, setSelectMode, remove: removeSessions,
} = useSessions()

const {
  lastData, renderTick, scrollTick, health, statusText, statusErr, notice, busy, augDisabled, dirty,
  analyzed, dockVisible, replyTarget, appendBusy, appendStatus, appendStatusErr,
  pendingIndexes, interpretErrors, previews,
  analyze, interpretOne, generateFor, runInterpretation, runSuggestions,
  appendTail, applySession, reset, probe, setStatus, setAppendStatus,
} = useAnalysis({
  transcript, relationship, me, people, targets, genInterpretation, genSuggestions,
  sessionId: currentId,
  // 每次结果落库后刷新侧栏（新会话 / 更新的时间与预览）
  onPersisted: () => refreshSessions(),
})

// 顶栏状态徽标：一眼看出服务与两个 key 的状态，不用等提示条出现。
const badge = computed(() => {
  const info = health.value
  if (!info) return { text: '服务未连接', cls: 'bad' }
  if (!info.ok) return { text: 'Jev 未配置 · ' + info.version, cls: 'bad' }
  if (info.general_llm === '缺失') return { text: '生成层未配置 · ' + info.version, cls: 'warn' }
  return { text: 'Jev + 生成层已连接 · ' + info.version, cls: 'ok' }
})

const drawerOpen = ref(false)
const settingsOpen = ref(false)
const appendOpen = ref(false)
const appendDraft = ref('')
const replyText = ref('')
const copyState = ref('') // '' | 'done' | 'fail'

// 顶栏下方的右键提示条：首次用的人不知道气泡能右键，给个可关闭的引导（本次会话内隐藏）。
const showRightClickHint = ref(true)
function dismissHint() { showRightClickHint.value = false }

const names = computed(() => ({
  me: lastData.value?.me_label || '我',
  other: lastData.value?.other_label || '她',
}))
/** 右键菜单（生成潜台词 / 生成推荐回复 / 一键生成）都落到同一条消息上。 */
function onGenerate({ index, kind }) {
  const both = kind === 'both'
  generateFor(index, {
    interpretation: kind === 'interpret' || both,
    suggestions: kind === 'suggest' || both,
  })
}
// 按钮文案与旧版一致：默认「复制」，成功后「已复制 ✓」，失败「复制失败」，1.5 秒后复原。
const copyLabel = computed(() => (copyState.value === 'done' ? '已复制 ✓' : copyState.value === 'fail' ? '复制失败' : '复制'))
const copyVisible = computed(() => dockVisible.value && !!replyText.value)

// 每次重新渲染对话都让复制按钮回到初始态（旧版是重建按钮 DOM）。
watch(renderTick, () => { copyState.value = '' })
// 回复面板重建时清空上一次的编辑内容。
watch(dockVisible, (visible) => { if (!visible) replyText.value = '' })

const showAiOptions = computed(() => people.value.length > 0 && targets.value.length > 0)
const submitLabel = computed(() => (people.value.length > 0 && targets.value.length === 0
  ? '先选要解读的人'
  : '仅Jev分析（意图+情绪）'))
const submitDisabled = computed(() => busy.value || (people.value.length > 0 && targets.value.length === 0))

function pickRelationship(value) {
  relationship.value = value
  setStatus('')
}

async function onSubmit() {
  // 先收抽屉再等结果：分析已经从「等整批」改成流式，结果区会边跑边把消息一条条长出来。
  // 抽屉压在上面会把这个过程整个挡住 —— 看起来还是「点了没反应」，流式就白做了。
  drawerOpen.value = false
  const ok = await analyze()
  // 失败时再把抽屉收回来：业务错误的提示写在抽屉的状态栏里，关着就看不见了。
  // （网络错误走顶部的 notice 条，不受影响。）
  if (!ok) drawerOpen.value = true
}

/**
 * 「＋ 导入聊天」= 新起一段：先把界面和表单清空再打开抽屉。
 *
 * 不清空的话会把上一条会话的东西带进来：文本框里还留着上次的记录、关系和 AI 勾选也还在，
 * 点提交容易误以为是在"继续"或"追加"。旧的会话本身不动，仍在左侧列表里（点一下就能回去）；
 * 这里清的是屏幕上的当前状态 + 会话绑定（sessionId 置空，新正文会存成新会话）。
 */
function onImport() {
  clearCurrentSession()
  notice.value = ''
  appendOpen.value = false
  appendDraft.value = ''
  drawerOpen.value = true
}

function openAppend() {
  setAppendStatus('')
  appendOpen.value = true
}

async function onAppendSubmit() {
  const ok = await appendTail(appendDraft.value)
  if (ok) {
    appendDraft.value = ''
    appendOpen.value = false
  }
}

async function onCopy() {
  const ok = await copyText((replyText.value || '').trim())
  copyState.value = ok ? 'done' : 'fail'
  setTimeout(() => { copyState.value = '' }, 1500)
}

// ---------- 会话：切换 / 删除 ----------

/** 切换到某个历史会话：整屏还原，不调用任何模型。 */
async function openSession(session) {
  if (!session || session.id === currentId.value) return
  try {
    const detail = await getSession(session.id)
    markCurrent(detail.session_id)
    applySession(detail)
    transcript.value = detail.transcript || ''
    relationship.value = detail.relationship || ''
    genInterpretation.value = !!detail.gen_interpretation
    genSuggestions.value = !!detail.gen_suggestions
    restore({ me: detail.me_label, read: detail.read_labels })
    drawerOpen.value = false
    notice.value = ''
  } catch (err) {
    notice.value = err.message
    refreshSessions()
  }
}

/** 当前会话被删掉后把界面收回空态（否则输入框还留着已删除会话的内容）。 */
function clearCurrentSession() {
  reset()
  transcript.value = ''
  relationship.value = ''
  genInterpretation.value = false
  genSuggestions.value = false
  restore({})
}

async function onDelete(ids) {
  const targetsIds = (ids || []).map(String)
  if (!targetsIds.length) return
  const removingCurrent = !!currentId.value && targetsIds.includes(currentId.value)
  try {
    const result = await removeSessions(targetsIds)
    if (removingCurrent) {
      clearCurrentSession()
      closeAppend()
    }
    setStatus('已删除 ' + result.deleted + ' 个会话。')
  } catch (err) {
    notice.value = err.message
  }
}

function closeAppend() {
  appendOpen.value = false
}

/** 配置保存后：顶栏徽标依赖 /health，重新探一次才知道新配置是否可用。 */
async function onSettingsSaved() {
  await probe()
  setStatus('模型配置已保存，立即生效。')
}

// Esc：关掉追加弹窗 / 配置面板，同时收起导入抽屉（与旧版两个监听器的效果一致）。
function onKeydown(event) {
  if (event.key !== 'Escape') return
  if (appendOpen.value) appendOpen.value = false
  if (settingsOpen.value) settingsOpen.value = false
  drawerOpen.value = false
}

onMounted(async () => {
  document.addEventListener('keydown', onKeydown)
  probe()
  // 持久记忆的直接收益：刷新页面后自动回到最近聊过的那条会话。
  await refreshSessions()
  if (sessions.value.length) openSession(sessions.value[0])
})
onUnmounted(() => document.removeEventListener('keydown', onKeydown))
</script>

<template>
  <header>
    <div class="brand-row">
      <img src="/favicon.svg" class="brand-logo" alt="恋爱·职场聊天神器" />
      <b>恋爱·职场聊天神器</b>
      <span class="badge" :class="badge.cls">{{ badge.text }}</span>
    </div>
    <div class="header-right">
      <a
        class="gh-link"
        href="https://github.com/lbytsl/jev-chat-analyzer"
        target="_blank"
        rel="noopener noreferrer"
        title="GitHub 仓库"
        aria-label="GitHub 仓库"
      >
        <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false">
          <path fill="currentColor" d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z"/>
        </svg>
      </a>
      <button type="button" id="settingsBtn" @click="settingsOpen = true">配置</button>
    </div>
  </header>
  <main>
    <div id="notice" class="notice" v-show="notice">{{ notice }}</div>
    <div class="wx-wrap">
      <div class="wx">
        <ChatSidebar
          :sessions="sessions"
          :current-id="currentId"
          :select-mode="selectMode"
          :selected="selected"
          :loading="sessionsLoading"
          @import="onImport"
          @open="openSession"
          @delete="onDelete"
          @toggle-select="toggleSelect"
          @toggle-mode="setSelectMode"
        />
        <div class="wx-main">
          <div class="wx-title">
            <strong id="wxTitle">{{ names.other }}</strong>
            <AugmentBar
              :visible="analyzed"
              :disabled="augDisabled"
              :hint-visible="dirty"
              :relationship="lastData?.relationship"
              @interpret="runInterpretation()"
              @suggest="runSuggestions()"
              @open="openAppend"
            />
          </div>
          <!-- 右键提示：让首次用的人知道气泡可以右键单独生成。可关闭，关掉后本次会话不再出现。 -->
          <div v-if="analyzed && showRightClickHint" class="flow-hint" role="note">
            <span class="flow-hint-text">提示：右键任意一条消息气泡，可单独生成「潜台词 / 推荐回复 / 一键生成」。</span>
            <button type="button" class="flow-hint-close" aria-label="关闭提示" @click="dismissHint">×</button>
          </div>
          <!-- 分析进行中的进度。抽屉已经收起，不在这里显示就完全看不到跑到哪一步了。 -->
          <div v-if="busy && statusText" class="stream-bar" :class="{ err: statusErr }">
            <span class="stream-pulse"></span>{{ statusText }}
          </div>
          <ChatFlow
            :data="lastData"
            :loading="busy"
            :names="names"
            :scroll-tick="scrollTick"
            :pending-indexes="pendingIndexes"
            :interpret-errors="interpretErrors"
            :previews="previews"
            @open-drawer="onImport"
            @interpret="interpretOne"
            @generate="onGenerate"
          />
          <div class="wx-bar">
            <div class="send-row">
              <button
                v-show="copyVisible"
                type="button"
                id="barCopy"
                class="reply-copy"
                :class="{ done: copyState === 'done' }"
                @click="onCopy"
              >{{ copyLabel }}</button>
              <span>发送(S)</span>
            </div>
            <div id="replyDock" class="reply-dock" v-show="dockVisible">
              <ReplyDock
                v-if="dockVisible && replyTarget"
                :key="renderTick"
                :item="replyTarget"
                :names="names"
                :preview="previews[replyTarget.index] || null"
                @update:text="replyText = $event"
              />
            </div>
          </div>
        </div>
        <ImportDrawer
          :open="drawerOpen"
          :relationship="relationship"
          :transcript="transcript"
          :people="people"
          :me="me"
          :read-labels="readLabels"
          :solo="solo"
          :show-ai-options="showAiOptions"
          :submit-disabled="submitDisabled"
          :submit-label="submitLabel"
          :status-text="statusText"
          :status-err="statusErr"
          :gen-interpretation="genInterpretation"
          :gen-suggestions="genSuggestions"
          @update:relationship="pickRelationship"
          @update:transcript="transcript = $event"
          @update:gen-interpretation="genInterpretation = $event"
          @update:gen-suggestions="genSuggestions = $event"
          @set-me="setMe"
          @toggle-read="toggleRead"
          @submit="onSubmit"
          @close="drawerOpen = false"
        />
      </div>
    </div>
  </main>
  <AppendModal
    :open="appendOpen"
    :draft="appendDraft"
    :status-text="appendStatus"
    :status-err="appendStatusErr"
    :busy="appendBusy"
    @update:draft="appendDraft = $event"
    @close="appendOpen = false"
    @submit="onAppendSubmit"
  />
  <SettingsDrawer
    :open="settingsOpen"
    @close="settingsOpen = false"
    @saved="onSettingsSaved"
  />
</template>
