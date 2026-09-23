<script setup>
import { computed, nextTick, ref, watch } from 'vue'

import { useAnalysisStore } from '@/stores/analysis'
import { useFormStore } from '@/stores/form'
import { usePeopleStore } from '@/stores/people'
import { useUiStore } from '@/stores/ui'

// 这个抽屉原来是全仓 props 最多的组件（11 个 props + 8 个 emits）：正文、关系、两个勾选、
// 说话人、分析状态全从 App 传进来，改动一处要在两个文件间对齐字段名。状态进 store 之后
// 它自己读写，App 只负责把它挂上去。
const form = useFormStore()
const people = usePeopleStore()
const analysis = useAnalysisStore()
const ui = useUiStore()

const readLabels = computed(() => [...people.read])

/**
 * 按钮文案与禁用只描述**抽屉自己**该不该灰、写什么字，所以就地算，不往 store 或 App 抛。
 * 文字与旧版一字不差。
 */
const targets = computed(() => people.people.filter((p) => readLabels.value.includes(p.label)))
const showAiOptions = computed(() => people.people.length > 0 && targets.value.length > 0)
const submitLabel = computed(() => (people.people.length > 0 && targets.value.length === 0
  ? '先选要解读的人'
  : '仅Jev分析（意图+情绪）'))
const submitDisabled = computed(() => analysis.busy
  || (people.people.length > 0 && targets.value.length === 0))

// 关系分组与旧版一致：情感（暧昧/恋爱）在前，职场（上下级/同事）在后。
const REL_GROUPS = [
  { title: '情感', options: ['暧昧', '恋爱'] },
  { title: '职场', options: ['上下级', '同事'] },
]

const transcriptEl = ref(null)
const relGroupsEl = ref(null)

watch(() => ui.drawerOpen, async (open) => {
  if (!open) return
  await nextTick()
  transcriptEl.value?.focus()
})

function pickRelationship(value) {
  form.relationship = value
  analysis.setStatus('')
}

async function onSubmit() {
  // 还没选关系时先把焦点送到第一个关系 chip 上（旧版行为）；校验本身仍交给 analyze()，
  // 它给的「请选择关系或场景。」会落在下面那条状态栏里，提示与文案都不变。
  if (!form.relationship) relGroupsEl.value?.querySelector('button')?.focus()
  // 说话人推断是防抖的（打字时不重算），提交前先按最新正文 flush 一次，
  // 否则「刚粘完就点提交」会带着上一次的解读对象发出去。
  people.sync()
  // 先收抽屉再等结果：分析是流式的，结果区会边跑边把消息一条条长出来，
  // 抽屉压在上面会把这个过程整个挡住 —— 看起来还是「点了没反应」，流式就白做了。
  ui.drawerOpen = false
  const ok = await analysis.analyze()
  // 失败时再把抽屉收回来：业务错误的提示写在抽屉的状态栏里，关着就看不见了。
  // （网络错误走顶部的 notice 条，不受影响。）
  if (!ok) ui.drawerOpen = true
}
</script>

<template>
  <div id="drawer" class="drawer" :class="{ open: ui.drawerOpen }">
    <div class="drawer-head">
      <button type="button" class="close" id="drawerClose" aria-label="收起" @click="ui.drawerOpen = false">×</button>
    </div>
    <form id="chatForm" style="margin-top:0" @submit.prevent="onSubmit">
      <span class="field-label" id="relLabel">关系 / 场景（必选）</span>
      <input type="hidden" id="relationship" :value="form.relationship">
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
              :class="{ 'is-onsel': form.relationship === value }"
              role="radio"
              :aria-checked="form.relationship === value ? 'true' : 'false'"
              @click="pickRelationship(value)"
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
        :value="form.transcript"
        @input="form.transcript = $event.target.value"
      ></textarea>
      <div id="people" v-show="people.people.length">
        <!-- 只有一个说话人时第 1 步没有意义，直接隐藏。 -->
        <div v-show="!people.solo" class="step" id="meStep">
          <span class="stepl">第 1 步 · 我是谁</span>
          <div id="meChips" class="chips">
            <button
              v-for="person in people.people"
              :key="person.label"
              type="button"
              class="chip"
              :class="{ 'is-me': person.label === people.me }"
              :aria-pressed="person.label === people.me ? 'true' : 'false'"
              @click="people.setMe(person.label)"
            >{{ person.label }}<small>{{ person.count }} 条</small></button>
          </div>
        </div>
        <!-- 一个人也要出第 2 步：否则按钮会禁用、页面上却没有可点的东西（死路）。 -->
        <div class="step" id="readStep">
          <span class="stepl">第 2 步 · 解读谁的消息</span>
          <div id="readChips" class="chips">
            <button
              v-for="person in people.people"
              :key="person.label"
              type="button"
              class="chip"
              :class="{ 'is-read': readLabels.includes(person.label) }"
              :aria-pressed="readLabels.includes(person.label) ? 'true' : 'false'"
              @click="people.toggleRead(person.label)"
            >{{ readLabels.includes(person.label) ? '✓ ' : '' }}{{ person.label }}{{ !people.solo && person.label === people.me ? ' · 我' : '' }}<small>{{ person.count }} 条</small></button>
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
            :checked="form.genInterpretation"
            @change="form.genInterpretation = $event.target.checked"
          >
          <span><strong>生成潜台词</strong>（调用AI等待时间会变长）</span>
        </div>
        <div class="toggle" :class="{ off: !showAiOptions }">
          <input
            id="genSuggestions"
            type="checkbox"
            :disabled="!showAiOptions"
            :checked="form.genSuggestions"
            @change="form.genSuggestions = $event.target.checked"
          >
          <span><strong>生成推荐回复</strong>（调用AI等待时间会变长）</span>
        </div>
        <p v-if="!showAiOptions" class="ai-hint">先在第 2 步选一个要解读的人，这两个 AI 增强才会启用。</p>
      </div>
      <button class="primary" id="submit" type="submit" :disabled="submitDisabled">{{ submitLabel }}</button>
      <div id="status" role="status" aria-live="polite" :class="{ err: analysis.statusErr }">{{ analysis.statusText }}</div>
    </form>
  </div>
</template>
