<script setup>
import { computed } from 'vue'

import { useAnalysisStore } from '@/stores/analysis'
import { useUiStore } from '@/stores/ui'

// 工具栏显示的全是分析结果的一部分（有没有结果、要不要禁用、文本改没改过、关系是什么），
// 原来靠 4 个 props + 4 个 emit 在 App 与它之间来回传；直连 store 后没有中间人了。
const analysis = useAnalysisStore()
const ui = useUiStore()

// 「关系/场景」跟着工具栏一起放在标题栏右侧：先看关系，再点生成。
const relationshipText = computed(() => '关系/场景：' + (analysis.lastData?.relationship || '未导入'))
</script>

<template>
  <div class="wx-title-tools">
    <small id="wxRel">{{ relationshipText }}</small>
    <template v-if="analysis.analyzed">
      <button type="button" id="augInterpret" class="aug-btn" :disabled="analysis.augDisabled"
              @click="analysis.runInterpretation()">生成潜台词</button>
      <button type="button" id="augSuggest" class="aug-btn" :disabled="analysis.augDisabled"
              @click="analysis.runSuggestions()">生成推荐回复</button>
      <button type="button" id="appendOpen" class="aug-btn ghost" @click="ui.openAppend()">继续记录</button>
      <button type="button" id="exportBtn" class="aug-btn ghost" @click="analysis.exportMarkdown()">导出</button>
    </template>
    <p class="muted" id="augHint" v-show="analysis.dirty">
      文本已改动，请重新点「仅Jev分析（意图+情绪）」重跑，再决定是否要潜台词 / 推荐回复。
    </p>
  </div>
</template>
