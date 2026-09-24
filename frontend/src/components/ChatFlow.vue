<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'

import MessageRow from '@/components/MessageRow.vue'
import NewSessionWelcome from '@/components/NewSessionWelcome.vue'
import { useSessionSwitch } from '@/composables/useSessionSwitch'
import { useAnalysisStore } from '@/stores/analysis'

// 流里的每一样东西（结果、进度、预览、历史会话定位信号）都是分析 store 的状态，
// 原来是 App 一个不落地传下来的 7 个 props；直连之后这里只留「消息行怎么排」这件事。
const analysis = useAnalysisStore()
const { startImport } = useSessionSwitch()

const flowEl = ref(null)
const menuEl = ref(null)
const menu = ref({ open: false, x: 0, y: 0, index: 0, canGenerate: false })
const MENU_WIDTH = 156
const MENU_HEIGHT = 124

// analyse 按 index 建索引：messages 与 analyses 是两份数组，靠 index 对齐。
const byIndex = computed(() => {
  const map = {}
  for (const item of analysis.lastData?.analyses || []) map[item.index] = item
  return map
})
// 底部回复面板锚定的那条：它的建议显示在底部，不在消息下面再重复一份。
const replyIndex = computed(() => analysis.lastData?.reply_target?.index ?? null)

// 只有主动打开历史会话才定位到末尾；流式更新不会触发 scrollTick。
watch(() => analysis.scrollTick, async () => {
  await nextTick()
  if (flowEl.value) flowEl.value.scrollTop = flowEl.value.scrollHeight
})

/**
 * 右键菜单（生成潜台词 / 推荐回复 / 一键生成）都落到同一条消息上。
 *
 * `kind` 的映射原来在 App.vue，现在跟着菜单走：谁弹出菜单、谁负责把选项翻译成调用。
 */
function pick(kind) {
  const index = menu.value.index
  const allowed = menu.value.canGenerate
  closeMenu()
  if (!allowed) return
  const both = kind === 'both'
  analysis.generateFor(index, {
    interpretation: kind === 'interpret' || both,
    suggestions: kind === 'suggest' || both,
  })
}

// 菜单是键盘/鼠标两种方式打开的，关掉时要把焦点还给「打开它的那一刻焦点在哪」。
let menuOpener = null

async function openMenu({ index, x, y, fromKeyboard = false }) {
  menuOpener = document.activeElement
  menu.value = {
    open: true,
    // 别让菜单跑出视口右/下边缘
    x: Math.max(8, Math.min(x, window.innerWidth - MENU_WIDTH - 8)),
    y: Math.max(8, Math.min(y, window.innerHeight - MENU_HEIGHT - 8)),
    index,
    canGenerate: !!byIndex.value[index],
  }
  if (!fromKeyboard) return
  // 键盘（Shift+F10 / 菜单键）打开时焦点必须跟着进菜单，否则「按了没反应」。
  await nextTick()
  focusMenuItem(0)
}

function focusMenuItem(offset) {
  const items = [...(menuEl.value?.querySelectorAll('button:not([disabled])') || [])]
  if (!items.length) return
  const current = items.indexOf(document.activeElement)
  const next = current < 0 ? 0 : (current + offset + items.length) % items.length
  items[next].focus()
}

function closeMenu() {
  const wasOpen = menu.value.open
  if (wasOpen) menu.value = { ...menu.value, open: false }
  // 焦点归还：键盘用户关掉菜单后应该回到那条消息上，而不是被丢回页首。
  if (wasOpen && menuOpener && document.contains(menuOpener)) menuOpener.focus()
  menuOpener = null
}

function onKeydown(event) {
  if (!menu.value.open) return
  if (event.key === 'Escape') {
    closeMenu()
  } else if (event.key === 'ArrowDown') {
    event.preventDefault()
    focusMenuItem(1)
  } else if (event.key === 'ArrowUp') {
    event.preventDefault()
    focusMenuItem(-1)
  }
}

onMounted(() => document.addEventListener('keydown', onKeydown))
onUnmounted(() => document.removeEventListener('keydown', onKeydown))
</script>

<template>
  <div id="results" ref="flowEl" class="wx-flow" aria-live="polite">
    <!-- 只在「什么都还没收到」时铺整块占位：`start` 事件一到骨架就上屏了，
         继续盖着它会把后面逐条落地的卡片、逐字冒出来的潜台词全挡在外面（流式等于白做）。 -->
    <div v-if="analysis.busy && !analysis.lastData" class="empty">Jev 正在读取上下文并分析每条消息…</div>
    <NewSessionWelcome v-else-if="!analysis.lastData" @import="startImport()" />
    <template v-else>
      <MessageRow
        v-for="message in analysis.lastData.messages || []"
        :key="message.index"
        :message="message"
        :item="byIndex[message.index] || null"
        :names="analysis.names"
        :gen-interpretation="!!analysis.lastData.gen_interpretation"
        :pending="analysis.pendingIndexes.has(message.index)"
        :error="analysis.interpretErrors[message.index] || ''"
        :preview="analysis.previews[message.index] || null"
        :dock-anchored="message.index === replyIndex"
        @interpret="analysis.interpretOne(message.index)"
        @menu="openMenu"
      />
    </template>

    <!-- 右键消息气泡：单条生成潜台词 / 推荐回复 / 一键生成。
         键盘同样可达：焦点落在气泡那行的按钮上时按 Shift+F10（或菜单键），
         之后用 ↑/↓ 选、Enter 确认、Esc 关闭（焦点会回到原处）。 -->
    <Teleport to="body">
      <div v-if="menu.open" class="ctx-mask" @click="closeMenu()" @contextmenu.prevent="closeMenu()"></div>
      <div
        v-if="menu.open"
        ref="menuEl"
        class="ctx-menu"
        role="menu"
        :style="{ left: menu.x + 'px', top: menu.y + 'px' }"
      >
        <button type="button" role="menuitem" :disabled="!menu.canGenerate" @click="pick('interpret')">
          生成潜台词
        </button>
        <button type="button" role="menuitem" :disabled="!menu.canGenerate" @click="pick('suggest')">
          生成推荐回复
        </button>
        <button type="button" role="menuitem" :disabled="!menu.canGenerate" @click="pick('both')">
          一键生成（潜台词 + 推荐回复）
        </button>
        <p v-if="!menu.canGenerate" class="ctx-hint">这条不在解读范围内（第 2 步没勾选它的说话人）。</p>
      </div>
    </Teleport>
  </div>
</template>
