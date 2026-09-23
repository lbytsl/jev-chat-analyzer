<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'

import MessageRow from '@/components/MessageRow.vue'

const props = defineProps({
  data: { type: Object, default: null },
  // 分析进行中。注意它只用来决定「还没收到任何东西时」的占位，见模板里的 v-if。
  loading: { type: Boolean, default: false },
  names: { type: Object, required: true },
  // 新结果落地时自增：新消息在底部，滚下去才看得到最后的分析卡片。
  scrollTick: { type: Number, default: 0 },
  pendingIndexes: { type: Object, default: () => new Set() },
  interpretErrors: { type: Object, default: () => ({}) },
  // 流式预览：index → { text, suggestions, kind }（kind 见 useAnalysis.applyPreview）。
  previews: { type: Object, default: () => ({}) },
})

const emit = defineEmits(['open-drawer', 'interpret', 'generate'])

const flowEl = ref(null)
const menu = ref({ open: false, x: 0, y: 0, index: 0, canGenerate: false })
const MENU_WIDTH = 156
const MENU_HEIGHT = 124

// analyse 按 index 建索引：messages 与 analyses 是两份数组，靠 index 对齐。
const byIndex = computed(() => {
  const map = {}
  for (const item of props.data?.analyses || []) map[item.index] = item
  return map
})
// 底部回复面板锚定的那条：它的建议显示在底部，不在消息下面再重复一份。
const replyIndex = computed(() => props.data?.reply_target?.index ?? null)

// 只在「有结果落地」时滚到底；补跑生成层不会触发（不会把正在回看的用户拽走）。
watch(() => props.scrollTick, async () => {
  await nextTick()
  if (flowEl.value) flowEl.value.scrollTop = flowEl.value.scrollHeight
})

function openMenu({ index, x, y }) {
  menu.value = {
    open: true,
    // 别让菜单跑出视口右/下边缘
    x: Math.max(8, Math.min(x, window.innerWidth - MENU_WIDTH - 8)),
    y: Math.max(8, Math.min(y, window.innerHeight - MENU_HEIGHT - 8)),
    index,
    canGenerate: !!byIndex.value[index],
  }
}

function closeMenu() {
  if (menu.value.open) menu.value = { ...menu.value, open: false }
}

function pick(kind) {
  const index = menu.value.index
  const allowed = menu.value.canGenerate
  closeMenu()
  if (allowed) emit('generate', { index, kind })
}

function onKeydown(event) {
  if (event.key === 'Escape') closeMenu()
}

onMounted(() => document.addEventListener('keydown', onKeydown))
onUnmounted(() => document.removeEventListener('keydown', onKeydown))
</script>

<template>
  <div id="results" ref="flowEl" class="wx-flow" aria-live="polite">
    <!-- 只在「什么都还没收到」时铺整块占位：`start` 事件一到骨架就上屏了，
         继续盖着它会把后面逐条落地的卡片、逐字冒出来的潜台词全挡在外面（流式等于白做）。 -->
    <div v-if="loading && !data" class="empty">Jev 正在读取上下文并分析每条消息…</div>
    <div v-else-if="!data" class="empty">
      <strong>还没有聊天记录。</strong>
      <small>点左侧「导入聊天」→ 选关系 / 场景 → 粘贴聊天记录，再点「仅Jev分析」。</small>
      <button type="button" class="cta" @click="emit('open-drawer')">导入聊天</button>
    </div>
    <template v-else>
      <MessageRow
        v-for="message in data.messages || []"
        :key="message.index"
        :message="message"
        :item="byIndex[message.index] || null"
        :names="names"
        :gen-interpretation="!!data.gen_interpretation"
        :pending="pendingIndexes.has(message.index)"
        :error="interpretErrors[message.index] || ''"
        :preview="previews[message.index] || null"
        :dock-anchored="message.index === replyIndex"
        @interpret="emit('interpret', message.index)"
        @menu="openMenu"
      />
    </template>

    <!-- 右键消息气泡：单条生成潜台词 / 推荐回复 / 一键生成。 -->
    <Teleport to="body">
      <div v-if="menu.open" class="ctx-mask" @click="closeMenu" @contextmenu.prevent="closeMenu"></div>
      <div
        v-if="menu.open"
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
