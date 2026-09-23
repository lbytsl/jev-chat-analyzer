import { defineStore } from 'pinia'
import { ref } from 'vue'

/**
 * 界面浮层与面板上的临时状态。
 *
 * 这些值没有任何一个需要跨会话保留，但都需要被「离得挺远」的组件读写：
 * Esc 要一次关掉三个浮层、顶栏要开配置面板、结果区要清掉回复草稿。
 * 放进 store 之后不用再为了传递它们在各层之间接一堆 props。
 */
export const useUiStore = defineStore('ui', () => {
  const drawerOpen = ref(false)      // 导入聊天抽屉
  const settingsOpen = ref(false)    // 配置面板
  const appendOpen = ref(false)      // 「继续记录」弹窗
  const appendDraft = ref('')        // 上面弹窗里待追加的正文
  const replyText = ref('')          // 回复面板里编辑中的文本（复制用）
  const copyState = ref('')          // '' | 'done' | 'fail'
  // 顶栏下方的右键提示条：首次用的人不知道气泡能右键，给个可关闭的引导（本次会话内隐藏）。
  const showRightClickHint = ref(true)

  function dismissHint() {
    showRightClickHint.value = false
  }

  /** Esc：一次收起所有浮层。 */
  function closeOverlays() {
    appendOpen.value = false
    settingsOpen.value = false
    drawerOpen.value = false
  }

  function openAppend() {
    appendOpen.value = true
  }

  function closeAppend() {
    appendOpen.value = false
    appendDraft.value = ''
  }

  return {
    drawerOpen, settingsOpen, appendOpen, appendDraft, replyText, copyState, showRightClickHint,
    dismissHint, closeOverlays, openAppend, closeAppend,
  }
})
