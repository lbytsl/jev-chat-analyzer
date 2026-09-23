<script setup>
import { nextTick, ref, watch } from 'vue'

import { useFocusTrap } from '@/composables/useFocusTrap'
import { useAnalysisStore } from '@/stores/analysis'
import { useUiStore } from '@/stores/ui'

const analysis = useAnalysisStore()
const ui = useUiStore()

const inputEl = ref(null)
const cardEl = ref(null)

// 模态期间把键盘焦点关在卡片里，关掉后归还给打开它的按钮（见 useFocusTrap）。
useFocusTrap(cardEl, () => ui.appendOpen)

// 打开即聚焦：省掉一次点击，和旧版 openAppendModal 的行为一致。
watch(() => ui.appendOpen, async (open) => {
  if (!open) return
  await nextTick()
  inputEl.value?.focus()
})

/** 提交追加：成功才清草稿并收起弹窗（失败时草稿要留着，用户还得改）。 */
async function onSubmit() {
  const ok = await analysis.appendTail(ui.appendDraft)
  if (ok) ui.closeAppend()
}

// 点遮罩空白处关闭（点在弹窗卡片上不关）。
function onMaskClick(event) {
  if (event.target === event.currentTarget) ui.closeAppend()
}
</script>

<template>
  <div id="appendModal" v-show="ui.appendOpen" class="modal-mask" @click="onMaskClick">
    <div ref="cardEl" class="modal" role="dialog" aria-modal="true" aria-label="继续记录这段对话">
      <h3>继续记录这段对话</h3>
      <p class="muted">只粘贴新增的消息（接在已有记录后面）。旧结果会自动保留，只对新增部分做 Jev 分类，不自动生成潜台词 / 回复。</p>
      <textarea
        id="appendInput"
        ref="inputEl"
        maxlength="50000"
        placeholder="把新消息贴在这里…"
        :value="ui.appendDraft"
        @input="ui.appendDraft = $event.target.value"
      ></textarea>
      <div class="modal-actions">
        <p id="appendStatus" class="muted" :class="{ err: analysis.appendStatusErr }" v-show="analysis.appendStatus">{{ analysis.appendStatus }}</p>
        <button type="button" id="appendCancel" class="aug-btn ghost" @click="ui.closeAppend()">取消</button>
        <button type="button" id="appendBtn" class="aug-btn" :disabled="analysis.appendBusy" @click="onSubmit">追加并分析</button>
      </div>
    </div>
  </div>
</template>
