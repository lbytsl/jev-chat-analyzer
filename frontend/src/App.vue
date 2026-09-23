<script setup>
import { storeToRefs } from 'pinia'
import { computed, onMounted, onUnmounted, watch } from 'vue'

import AppendModal from '@/components/AppendModal.vue'
import AugmentBar from '@/components/AugmentBar.vue'
import ChatFlow from '@/components/ChatFlow.vue'
import ChatSidebar from '@/components/ChatSidebar.vue'
import ImportDrawer from '@/components/ImportDrawer.vue'
import ReplyDock from '@/components/ReplyDock.vue'
import SettingsDrawer from '@/components/SettingsDrawer.vue'
import { useSessionSwitch } from '@/composables/useSessionSwitch'
import { useAnalysisStore } from '@/stores/analysis'
import { usePeopleStore } from '@/stores/people'
import { useSessionsStore } from '@/stores/sessions'
import { useUiStore } from '@/stores/ui'
import { copyText } from '@/utils/clipboard'

/**
 * App 现在是「布局 + 页面级行为」。
 *
 * 状态全部由 store 持有（见 src/stores/*），各组件自己直连，不再层层传 props。
 * 这里只剩下三类东西，它们的共同点是「要么跨 store 合成，要么属于整个页面」：
 * 1) 顶栏的徽标与提示条（要同时看会话列表错误、分析提示、健康检查三个来源）；
 * 2) Esc 收起全部浮层、页面卸载时取消在途请求；
 * 3) 底部回复面板的复制按钮（它同时依赖分析结果与 ui store 的编辑态）。
 */
const sessions = useSessionsStore()
const analysis = useAnalysisStore()
const people = usePeopleStore()
const ui = useUiStore()
const { openSession } = useSessionSwitch()

const { notice, statusText, statusErr, busy, analyzed, dockVisible, replyTarget,
        previews, renderTick, names } = storeToRefs(analysis)
const { copyState, replyText, showRightClickHint } = storeToRefs(ui)

/**
 * 顶部提示条的内容。
 *
 * 会话列表加载失败原本被 sessions store 记进 `error` 却没有任何人读——用户的观感就是
 * 「历史会话全没了」，而且完全没有提示。这里让它优先显示（与健康检查共用同一条）。
 */
const noticeText = computed(() => (sessions.error
  ? '会话列表加载失败：' + sessions.error
  : notice.value))

// 顶栏状态徽标：一眼看出服务与两个 key 的状态，不用等提示条出现。
const badge = computed(() => {
  const info = analysis.health
  if (!info) return { text: '服务未连接', cls: 'bad' }
  if (!info.ok) return { text: 'Jev 未配置 · ' + info.version, cls: 'bad' }
  if (info.general_llm === '缺失') return { text: 'LLM层未配置 · ' + info.version, cls: 'warn' }
  return { text: 'Jev + LLM层已连接 · ' + info.version, cls: 'ok' }
})

// 按钮文案与旧版一致：默认「复制」，成功后「已复制 ✓」，失败「复制失败」，1.5 秒后复原。
const copyLabel = computed(() => (copyState.value === 'done' ? '已复制 ✓' : copyState.value === 'fail' ? '复制失败' : '复制'))
const copyVisible = computed(() => dockVisible.value && !!replyText.value)

// 每次重新渲染对话都让复制按钮回到初始态（旧版是重建按钮 DOM）。
watch(renderTick, () => { copyState.value = '' })
// 回复面板重建时清空上一次的编辑内容。
watch(dockVisible, (visible) => { if (!visible) replyText.value = '' })

// 「已复制 ✓」1.5 秒后复位；定时器留句柄并在卸载时清掉（别让它在页面走了之后还去改状态）。
let copyTimer = null

async function onCopy() {
  const ok = await copyText((replyText.value || '').trim())
  copyState.value = ok ? 'done' : 'fail'
  if (copyTimer) clearTimeout(copyTimer)
  copyTimer = setTimeout(() => {
    copyTimer = null
    copyState.value = ''
  }, 1500)
}

// Esc：一次收起追加弹窗 / 配置面板 / 导入抽屉（与旧版两个监听器的效果一致）。
function onKeydown(event) {
  if (event.key === 'Escape') ui.closeOverlays()
}

onMounted(async () => {
  document.addEventListener('keydown', onKeydown)
  analysis.probe()
  // 持久记忆的直接收益：刷新页面后自动回到最近聊过的那条会话。
  await sessions.refresh()
  if (sessions.sessions.length) openSession(sessions.sessions[0])
})
onUnmounted(() => {
  document.removeEventListener('keydown', onKeydown)
  // 离开页面时停掉在途的流：否则后端会继续把剩下几十条 Jev 调用跑完（白烧额度）。
  analysis.cancelStreams()
  people.dispose()
  if (copyTimer) clearTimeout(copyTimer)
})
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
        href="https://dream.mindweave.top/about"
        target="_blank"
        rel="noopener noreferrer"
        title="个人博客"
        aria-label="个人博客"
      >
        <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false">
          <g fill="none" stroke="currentColor" stroke-width="1.5">
            <circle cx="8" cy="8" r="6.4"/>
            <ellipse cx="8" cy="8" rx="2.9" ry="6.4"/>
            <path d="M1.8 8h12.4"/>
          </g>
        </svg>
      </a>
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
      <button type="button" id="settingsBtn" @click="ui.settingsOpen = true">配置</button>
    </div>
  </header>
  <main>
    <div id="notice" class="notice" role="status" aria-live="polite" v-show="noticeText">{{ noticeText }}</div>
    <div class="wx-wrap">
      <div class="wx">
        <ChatSidebar />
        <div class="wx-main">
          <div class="wx-title">
            <strong id="wxTitle">{{ names.other }}</strong>
            <AugmentBar />
          </div>
          <!-- 右键提示：让首次用的人知道气泡可以右键单独生成。可关闭，关掉后本次会话不再出现。 -->
          <div v-if="analyzed && showRightClickHint" class="flow-hint" role="note">
            <span class="flow-hint-text">提示：右键任意一条消息气泡，可单独生成「潜台词 / 推荐回复 / 一键生成」；键盘用户把焦点放到那条消息的按钮上，按 Shift+F10 是同一个菜单。</span>
            <button type="button" class="flow-hint-close" aria-label="关闭提示" @click="ui.dismissHint()">×</button>
          </div>
          <!-- 分析进行中的进度。抽屉已经收起，不在这里显示就完全看不到跑到哪一步了。 -->
          <div v-if="busy && statusText" class="stream-bar" :class="{ err: statusErr }">
            <span class="stream-pulse"></span>{{ statusText }}
          </div>
          <ChatFlow />
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
                @update:text="ui.replyText = $event"
              />
            </div>
          </div>
        </div>
        <ImportDrawer />
      </div>
    </div>
  </main>
  <AppendModal />
  <SettingsDrawer />
</template>
