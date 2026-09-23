<script setup>
import { computed } from 'vue'

import AnalysisNote from '@/components/AnalysisNote.vue'
import ReplyDock from '@/components/ReplyDock.vue'

const props = defineProps({
  message: { type: Object, required: true },
  item: { type: Object, default: null },
  names: { type: Object, required: true },
  genInterpretation: { type: Boolean, default: false },
  pending: { type: Boolean, default: false },
  error: { type: String, default: '' },
  // 流式预览：{ text, suggestions, kind }，生成过程中才有（kind 见 useAnalysis.applyPreview）。
  preview: { type: Object, default: null },
  // 这条就是底部回复面板锚定的那条吗？是的话建议显示在底部，不在这里重复一份。
  dockAnchored: { type: Boolean, default: false },
})

const emit = defineEmits(['interpret', 'menu'])

const mine = computed(() => props.message.speaker === 'me')
// 气泡上方的用户名：优先用记录里的原始标签（可能是真实昵称），没有就退回「我 / 对方」。
const displayName = computed(() => props.message.label
  || (mine.value ? props.names.me : props.names.other))
// 已经生成过潜台词时按钮换成「重新生成」，避免每次点都长一样。
const hasDetail = computed(() => !!props.item?.result?.intent_detail)
const actLabel = computed(() => {
  if (props.pending) return '生成中…'
  return hasDetail.value ? '重新生成' : '生成潜台词'
})
// 流式写出来的潜台词（这一条的卡片还没落地时先就地显示）。
const liveText = computed(() => (props.preview?.text || '').trim())
// 右键「生成推荐回复」的结果贴在这条下面（底部面板只负责最后一条）。
// 流式期间只要有建议预览就先展开——让建议一条条冒出来，而不是等 `done` 才整块出现。
const inlineSuggestions = computed(() => {
  if (props.dockAnchored || !props.item) return false
  const result = props.item.result || {}
  return !!(result.suggestions?.length || result.gen_failed
    || (props.preview?.suggestions || []).length)
})

function onContextMenu(event) {
  emit('menu', { index: props.message.index, x: event.clientX, y: event.clientY })
}
</script>

<template>
  <div class="row" :class="{ me: mine }" @contextmenu.prevent="onContextMenu">
    <div class="col">
      <span class="bub-name">{{ displayName }}</span>
      <div class="bub-line">
        <div class="bub">{{ message.text }}</div>
        <!-- 单句潜台词：按钮就贴在气泡右边，哪一条想生成点哪一条（右键还有更多动作）。 -->
        <button
          v-if="item"
          type="button"
          class="bub-act"
          :class="{ 'is-done': hasDetail }"
          :disabled="pending"
          :title="hasDetail ? '重新生成这一条的潜台词' : '只给这一条生成潜台词'"
          @click="emit('interpret')"
        >{{ actLabel }}</button>
      </div>
      <AnalysisNote
        v-if="item"
        :item="item"
        :name="displayName"
        :gen-interpretation="genInterpretation"
        :error="error"
        :preview="preview"
      />
      <!-- 卡片还没落地、但潜台词已经在流式写出来时：先就地显示这句，别让用户干等。 -->
      <div v-else-if="liveText" class="note note-live">
        <div class="intent-detail">
          <strong>潜台词：</strong><em>{{ liveText }}<span class="typing-caret">▍</span></em>
        </div>
      </div>
      <ReplyDock
        v-if="inlineSuggestions"
        :item="item"
        :names="names"
        :preview="preview"
        inline
      />
    </div>
  </div>
</template>
