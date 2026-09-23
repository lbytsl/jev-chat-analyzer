<script setup>
import { nextTick, ref, watch } from 'vue'

const props = defineProps({
  open: { type: Boolean, default: false },
  relationship: { type: String, default: '' },
  transcript: { type: String, default: '' },
  people: { type: Array, default: () => [] },
  me: { type: String, default: null },
  readLabels: { type: Array, default: () => [] },
  solo: { type: Boolean, default: false },
  showAiOptions: { type: Boolean, default: false },
  submitDisabled: { type: Boolean, default: false },
  submitLabel: { type: String, default: '仅Jev分析（意图+情绪）' },
  statusText: { type: String, default: '' },
  statusErr: { type: Boolean, default: false },
  genInterpretation: { type: Boolean, default: false },
  genSuggestions: { type: Boolean, default: false },
})

const emit = defineEmits([
  'update:relationship', 'update:transcript', 'update:genInterpretation', 'update:genSuggestions',
  'set-me', 'toggle-read', 'submit', 'close',
])

// 关系分组与旧版一致：情感（暧昧/恋爱）在前，职场（上下级/同事）在后。
const REL_GROUPS = [
  { title: '情感', options: ['暧昧', '恋爱'] },
  { title: '职场', options: ['上下级', '同事'] },
]

const transcriptEl = ref(null)
const relGroupsEl = ref(null)

watch(() => props.open, async (open) => {
  if (!open) return
  await nextTick()
  transcriptEl.value?.focus()
})

function onSubmit() {
  // 还没选关系时把焦点送到第一个关系 chip 上（旧版行为）。
  if (!props.relationship) relGroupsEl.value?.querySelector('button')?.focus()
  emit('submit')
}

function onTranscriptInput(event) {
  emit('update:transcript', event.target.value)
}
</script>

<template>
  <div id="drawer" class="drawer" :class="{ open }">
    <div class="drawer-head">
      <button type="button" class="close" id="drawerClose" aria-label="收起" @click="emit('close')">×</button>
    </div>
    <form id="chatForm" style="margin-top:0" @submit.prevent="onSubmit">
      <span class="field-label" id="relLabel">关系 / 场景（必选）</span>
      <input type="hidden" id="relationship" :value="relationship">
      <!-- 关系用 chips 单选：点中即选中（互斥），值写进隐藏字段，提交时统一读。 -->
      <div id="relGroups" ref="relGroupsEl" role="radiogroup" aria-labelledby="relLabel">
        <div v-for="group in REL_GROUPS" :key="group.title" class="rel-group">
          <span class="rel-title">{{ group.title }}</span>
          <div class="chips">
            <button
              v-for="value in group.options"
              :key="value"
              type="button"
              class="chip rel-chip"
              :class="{ 'is-onsel': relationship === value }"
              role="radio"
              :aria-checked="relationship === value ? 'true' : 'false'"
              @click="emit('update:relationship', value)"
            >{{ value }}</button>
          </div>
        </div>
      </div>
      <label for="transcript">聊天记录</label>
      <textarea
        id="transcript"
        ref="transcriptEl"
        maxlength="50000"
        required
        style="min-height:220px"
        :value="transcript"
        @input="onTranscriptInput"
      ></textarea>
      <div id="people" v-show="people.length">
        <!-- 只有一个说话人时第 1 步没有意义，直接隐藏。 -->
        <div v-show="!solo" class="step" id="meStep">
          <span class="stepl">第 1 步 · 我是谁</span>
          <div id="meChips" class="chips">
            <button
              v-for="person in people"
              :key="person.label"
              type="button"
              class="chip"
              :class="{ 'is-me': person.label === me }"
              :aria-pressed="person.label === me ? 'true' : 'false'"
              @click="emit('set-me', person.label)"
            >{{ person.label }}<small>{{ person.count }} 条</small></button>
          </div>
        </div>
        <!-- 一个人也要出第 2 步：否则按钮会禁用、页面上却没有可点的东西（死路）。 -->
        <div class="step" id="readStep">
          <span class="stepl">第 2 步 · 解读谁的消息</span>
          <div id="readChips" class="chips">
            <button
              v-for="person in people"
              :key="person.label"
              type="button"
              class="chip"
              :class="{ 'is-read': readLabels.includes(person.label) }"
              :aria-pressed="readLabels.includes(person.label) ? 'true' : 'false'"
              @click="emit('toggle-read', person.label)"
            >{{ readLabels.includes(person.label) ? '✓ ' : '' }}{{ person.label }}{{ !solo && person.label === me ? ' · 我' : '' }}<small>{{ person.count }} 条</small></button>
          </div>
        </div>
      </div>
      <!-- 两个开关常驻显示：以前是「没选解读对象就整块隐藏」，用户会以为功能没了。
           现在保留位置、置灰并说明原因，找不到的情况就不会再发生。 -->
      <div id="aiOptions">
        <div class="toggle" :class="{ off: !showAiOptions }">
          <input
            id="genInterpretation"
            type="checkbox"
            :disabled="!showAiOptions"
            :checked="genInterpretation"
            @change="emit('update:genInterpretation', $event.target.checked)"
          >
          <span><strong>生成潜台词</strong>（调用AI等待时间会变长）</span>
        </div>
        <div class="toggle" :class="{ off: !showAiOptions }">
          <input
            id="genSuggestions"
            type="checkbox"
            :disabled="!showAiOptions"
            :checked="genSuggestions"
            @change="emit('update:genSuggestions', $event.target.checked)"
          >
          <span><strong>生成推荐回复</strong>（调用AI等待时间会变长）</span>
        </div>
        <p v-if="!showAiOptions" class="ai-hint">先在第 2 步选一个要解读的人，这两个 AI 增强才会启用。</p>
      </div>
      <button class="primary" id="submit" type="submit" :disabled="submitDisabled">{{ submitLabel }}</button>
      <div id="status" role="status" aria-live="polite" :class="{ err: statusErr }">{{ statusText }}</div>
    </form>
  </div>
</template>
