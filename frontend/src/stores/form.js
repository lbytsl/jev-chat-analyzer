import { defineStore } from 'pinia'
import { ref } from 'vue'

/**
 * 导入 / 分析表单：正文、关系（场景）、两个 AI 勾选。
 *
 * 为什么进 store 而不是留在 App.vue：这四个字段被导入抽屉、结果区（脏检测）、
 * 会话切换同时读写。原来靠 props/emits 在 App 与抽屉之间来回传 8 次，
 * 漏接一个就是「改了没生效」这种最难查的 bug。
 */
export const useFormStore = defineStore('form', () => {
  const transcript = ref('')
  const relationship = ref('')
  const genInterpretation = ref(false)
  const genSuggestions = ref(false)

  /** 清空表单（收回空态 / 开始新一段导入）。 */
  function reset() {
    transcript.value = ''
    relationship.value = ''
    genInterpretation.value = false
    genSuggestions.value = false
  }

  /** 用会话里存的字段整屏还原（其余状态见 composables/useSessionSwitch.js）。 */
  function applySession(detail) {
    transcript.value = detail.transcript || ''
    relationship.value = detail.relationship || ''
    genInterpretation.value = !!detail.gen_interpretation
    genSuggestions.value = !!detail.gen_suggestions
  }

  return { transcript, relationship, genInterpretation, genSuggestions, reset, applySession }
})
