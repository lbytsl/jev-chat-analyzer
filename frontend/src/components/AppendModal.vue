<script setup>
import { nextTick, ref, watch } from 'vue'

const props = defineProps({
  open: { type: Boolean, default: false },
  draft: { type: String, default: '' },
  statusText: { type: String, default: '' },
  statusErr: { type: Boolean, default: false },
  busy: { type: Boolean, default: false },
})

const emit = defineEmits(['update:draft', 'close', 'submit'])

const inputEl = ref(null)

// 打开即聚焦：省掉一次点击，和旧版 openAppendModal 的行为一致。
watch(() => props.open, async (open) => {
  if (!open) return
  await nextTick()
  inputEl.value?.focus()
})

// 点遮罩空白处关闭（点在弹窗卡片上不关）。
function onMaskClick(event) {
  if (event.target === event.currentTarget) emit('close')
}
</script>

<template>
  <div id="appendModal" v-show="open" class="modal-mask" @click="onMaskClick">
    <div class="modal" role="dialog" aria-modal="true" aria-label="继续记录这段对话">
      <h3>继续记录这段对话</h3>
      <p class="muted">只粘贴新增的消息（接在已有记录后面）。旧结果会自动保留，只对新增部分做 Jev 分类，不自动生成潜台词 / 回复。</p>
      <textarea
        id="appendInput"
        ref="inputEl"
        maxlength="50000"
        placeholder="把新消息贴在这里…"
        :value="draft"
        @input="emit('update:draft', $event.target.value)"
      ></textarea>
      <div class="modal-actions">
        <p id="appendStatus" class="muted" :class="{ err: statusErr }" v-show="statusText">{{ statusText }}</p>
        <button type="button" id="appendCancel" class="aug-btn ghost" @click="emit('close')">取消</button>
        <button type="button" id="appendBtn" class="aug-btn" :disabled="busy" @click="emit('submit')">追加并分析</button>
      </div>
    </div>
  </div>
</template>
