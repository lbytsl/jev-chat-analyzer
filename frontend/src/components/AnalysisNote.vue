<script setup>
import { computed } from 'vue'

const props = defineProps({
  item: { type: Object, required: true },
  name: { type: String, default: '' },
  genInterpretation: { type: Boolean, default: false },
  error: { type: String, default: '' },
  // 流式预览：{ text, suggestions, kind }，只在生成过程中有值（kind 见 useAnalysis.applyPreview）。
  preview: { type: Object, default: null },
})

const result = computed(() => props.item.result || {})
const intent = computed(() => result.value.primary_intent || {})
const emotion = computed(() => result.value.emotion || {})
const relationDirection = computed(() => result.value.relation_direction || {})
const responseNeed = computed(() => result.value.response_need || {})
const communicationStyle = computed(() => result.value.communication_style || {})

// 模型正在写的那半句：优先显示，写完（done 事件）后换成服务端给的最终值。
const liveText = computed(() => (props.preview?.text || '').trim())
const typing = computed(() => (props.preview?.activeKinds || []).includes('interpretation'))

const intentText = computed(() => {
  const label = intent.value.label || ''
  return label ? label + ' ' + Math.round((intent.value.score || 0) * 100) + '%' : '没看出来'
})

// v008：情绪回到分类层兜底。识别不到情绪信号 / 分类层没把握时显示「无明显情绪」，
// 不再让生成层硬编一个二级情绪标签（宁可承认看不出来，也不瞎猜）。
const emotionText = computed(() => {
  const name = emotion.value.display || emotion.value.label || ''
  if (!name) return '无明显情绪'
  // 细分没把握时只给大类标签本身，不加任何解释句。
  if (emotion.value.coarse) return name
  return name + ' ' + Math.round((emotion.value.score || 0) * 100) + '%'
})

const dimensionText = computed(() => {
  const items = []
  const relation = relationDirection.value.display || relationDirection.value.label
  const response = responseNeed.value.display || responseNeed.value.label
  const style = communicationStyle.value.display || communicationStyle.value.label
  if (relation) items.push('关系信号：' + relation)
  if (response) items.push('期待回应：' + response)
  if (style) items.push('表达：' + style)
  return items
})

const uncertaintyText = computed(() => {
  const uncertainty = result.value.uncertainty || {}
  if (uncertainty.level !== 'high') return ''
  return '这条信息存在较高不确定性，可展开查看候选与原始结果。'
})

// 只有勾选「生成潜台词」且这条确实生成过时才显示，未勾选 / 生成失败都不占位置。
const detail = computed(() => {
  if (typing.value) return liveText.value
  if (result.value.gen_skipped || !props.genInterpretation) return ''
  return result.value.intent_detail || ''
})

// 生成成功但内容为空（模型判定这句是纯事务性应答）时也要说一句，
// 否则「点重新生成 → 界面毫无变化」看起来像坏了。
// 判据是 intent_detail === ''（空串 = 这次真的问过模型、模型给了空）；
// 没问过时这个字段是 null，不会有提示。
const detailEmpty = computed(() => !typing.value && result.value.intent_detail === ''
  && !props.error)

const who = computed(() => props.item.label || props.name || '对方')

const raw = computed(() => JSON.stringify({
  model: result.value.model,
  analysis_schema: result.value.analysis_schema,
  prompt_version: result.value.prompt_version,
  label_version: result.value.label_version,
  intent_family: result.value.intent_family,
  primary_intent: result.value.primary_intent,
  emotion_family: result.value.emotion_family,
  emotion: result.value.emotion,
  relation_direction: result.value.relation_direction,
  response_need: result.value.response_need,
  communication_style: result.value.communication_style,
  uncertainty: result.value.uncertainty,
  answers: result.value.answers,
  usage: result.value.usage,
}, null, 2))
</script>

<template>
  <div class="note">
    <div class="intent-emotion-line">
      <span class="metric">
        <span class="metric-label">意图：</span><span class="metric-value">{{ intentText }}</span>
      </span>
      <span class="divider">｜</span>
      <span class="metric">
        <span class="metric-label">情绪：</span><span class="metric-value">{{ emotionText }}</span>
      </span>
    </div>
    <div v-if="dimensionText.length" class="intent-emotion-line analysis-dimensions">
      <template v-for="(text, index) in dimensionText" :key="text">
        <span v-if="index" class="divider">｜</span>
        <span class="metric"><span class="metric-value">{{ text }}</span></span>
      </template>
    </div>
    <p v-if="uncertaintyText" class="analysis-uncertainty">{{ uncertaintyText }}</p>
    <div v-if="detail" class="intent-detail">
      <strong>潜台词：</strong><em>{{ detail }}<span v-if="typing" class="typing-caret">▍</span></em>
    </div>
    <div v-else-if="detailEmpty" class="intent-detail intent-detail-empty">
      <strong>潜台词：</strong><span>模型判断这句是纯事务性应答，没有潜台词</span>
    </div>
    <!-- 生成失败只影响这一条：提示就显示在这张卡片里，按钮在气泡右侧可再点一次。 -->
    <p v-if="error" class="note-error">{{ error }}</p>
    <details>
      <summary>{{ who }} · 查看 Jev 原始输出</summary>
      <pre class="raw">{{ raw }}</pre>
    </details>
  </div>
</template>
