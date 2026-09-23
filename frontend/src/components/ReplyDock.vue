<script setup>
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'

import { copyText } from '@/utils/clipboard'

const props = defineProps({
  item: { type: Object, required: true },
  names: { type: Object, default: () => ({}) },
  // inline：贴在某条消息下面的小面板（右键「生成推荐回复」用）；默认是底部发送行那块。
  inline: { type: Boolean, default: false },
  // 流式预览：{ suggestions: [...] }，生成过程中有值（建议逐条到位）。
  preview: { type: Object, default: null },
})

const emit = defineEmits(['update:text'])

const activeIndex = ref(0)
const textEl = ref(null)
const copied = ref(false)

// 标题随最后一条消息的说话人切换：对方发的问「不知道怎么回复？」，自己发的问「还想说点什么？」。
// reply_target 的 speaker 是中文「我」/「对方」；为稳妥同时兼容代码值 'me' / 'other'。
const isMe = computed(() => props.item.speaker === '我' || props.item.speaker === 'me')
const result = computed(() => props.item.result || {})
const stored = computed(() => (Array.isArray(result.value.suggestions) ? result.value.suggestions : []))
// 流式过程中先显示已经写好的那几条；`done` 之后预览清空，回到服务端给的最终值。
// 判据不能只看 kind：右键「一键生成」时潜台词那条流会把它抢过去；但只要已经流出过
// 一条建议，就说明这条正在生成建议——那时也该展开面板，而不是显示「暂无建议」。
const live = computed(() => props.preview?.suggestions || [])
const generating = computed(() => props.preview?.kind === 'suggestions' || live.value.length > 0)
const suggestions = computed(() => (live.value.length ? live.value : stored.value))
const streaming = computed(() => generating.value && !live.value.length)
const failed = computed(() => !!result.value.gen_failed)
const empty = computed(() => !failed.value && !streaming.value && !suggestions.value.length)

async function syncText() {
  await nextTick()
  if (!textEl.value) return
  const option = suggestions.value[activeIndex.value]
  textEl.value.textContent = option ? option.text : ''
  emit('update:text', (textEl.value.textContent || '').trim())
}

function pick(index) {
  activeIndex.value = index
  syncText()
}

function onInput() {
  emit('update:text', (textEl.value?.textContent || '').trim())
}

// 「已复制」的复位定时器要留句柄：面板会随 renderTick 反复重建，留着野定时器会在
// 组件已经卸载之后再去改状态。
let copiedTimer = null

async function onCopy() {
  const ok = await copyText((textEl.value?.textContent || '').trim())
  copied.value = ok
  if (copiedTimer) clearTimeout(copiedTimer)
  copiedTimer = setTimeout(() => {
    copiedTimer = null
    copied.value = false
  }, 1500)
}

onUnmounted(() => {
  if (copiedTimer) clearTimeout(copiedTimer)
})

watch(() => [props.item, suggestions.value.length], syncText, { immediate: true })
</script>

<template>
  <div class="reply-suggest" :class="{ 'is-inline': inline }">
    <div class="reply-title">{{ isMe ? '还想说点什么？' : '不知道怎么回复？' }}</div>
    <!-- 大模型生成失败时不回退死模板：直接说失败，让用户重试。 -->
    <div v-if="failed" class="reply-failed">
      回复建议生成失败（大模型调用异常）。可重新右键这条消息重试。
    </div>
    <div v-else-if="streaming" class="reply-typing">正在生成回复建议…</div>
    <div v-else-if="empty" class="reply-failed">暂时没有可用的回复建议。</div>
    <template v-else>
      <div class="reply-modes">
        <button
          v-for="(option, index) in suggestions"
          :key="index"
          type="button"
          class="reply-mode"
          :class="{ active: index === activeIndex }"
          @click="pick(index)"
        >{{ option.label }}</button>
      </div>
      <div
        ref="textEl"
        class="reply-text"
        contenteditable="true"
        role="textbox"
        aria-label="建议回复，可直接编辑"
        @input="onInput"
      ></div>
      <!-- 内联面板没有底部发送行，复制按钮就放在自己这里。 -->
      <div v-if="inline" class="reply-inline-actions">
        <button
          type="button"
          class="reply-copy"
          :class="{ done: copied }"
          @click="onCopy"
        >{{ copied ? '已复制 ✓' : '复制' }}</button>
      </div>
    </template>
  </div>
</template>
