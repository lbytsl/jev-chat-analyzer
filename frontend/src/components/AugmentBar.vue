<script setup>
import { computed } from 'vue'

const props = defineProps({
  // 有分析结果时才出现这三个按钮（没有结果时点了也没东西可生成）。
  visible: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  hintVisible: { type: Boolean, default: false },
  relationship: { type: String, default: '' },
})

defineEmits(['interpret', 'suggest', 'open'])

// 「关系/场景」跟着工具栏一起放在标题栏右侧：先看关系，再点生成。
const relationshipText = computed(() => '关系/场景：' + (props.relationship || '未导入'))
</script>

<template>
  <div class="wx-title-tools">
    <small id="wxRel">{{ relationshipText }}</small>
    <template v-if="visible">
      <button type="button" id="augInterpret" class="aug-btn" :disabled="disabled" @click="$emit('interpret')">生成潜台词</button>
      <button type="button" id="augSuggest" class="aug-btn" :disabled="disabled" @click="$emit('suggest')">生成推荐回复</button>
      <button type="button" id="appendOpen" class="aug-btn ghost" @click="$emit('open')">继续记录</button>
    </template>
    <p class="muted" id="augHint" v-show="hintVisible">
      文本已改动，请重新点「仅Jev分析（意图+情绪）」重跑，再决定是否要潜台词 / 推荐回复。
    </p>
  </div>
</template>
