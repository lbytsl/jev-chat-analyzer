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

// 模型正在写的那半句：优先显示，写完（done 事件）后换成服务端给的最终值。
const liveText = computed(() => (props.preview?.text || '').trim())
const typing = computed(() => !!liveText.value)

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
  primary_intent: result.value.primary_intent,
  emotion_family: result.value.emotion_family,
  emotion: result.value.emotion,
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
