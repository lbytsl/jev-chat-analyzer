import { nextTick, onUnmounted, watch } from 'vue'

const FOCUSABLE = [
  'a[href]', 'button:not([disabled])', 'input:not([disabled])',
  'select:not([disabled])', 'textarea:not([disabled])', '[tabindex]:not([tabindex="-1"])',
].join(',')

/**
 * 把键盘焦点关进一个容器里（模态 / 抽屉用）。
 *
 * 不做的后果很具体：打开「配置」面板后按 Tab，焦点会跑到面板**背后**的页面上，
 * 键盘用户会在看不见的地方操作，Esc 关掉之后焦点还留在原地——回到哪都得自己再找一遍。
 *
 * @param {import('vue').Ref<HTMLElement|null>} containerRef 容器根元素
 * @param {import('vue').Ref<boolean>|(() => boolean)} active 是否启用
 */
export function useFocusTrap(containerRef, active) {
  const isActive = typeof active === 'function' ? active : () => active.value
  let lastFocused = null

  function focusables() {
    const root = containerRef.value
    if (!root) return []
    return [...root.querySelectorAll(FOCUSABLE)].filter((el) => el.offsetParent !== null)
  }

  function onKeydown(event) {
    if (event.key !== 'Tab') return
    const items = focusables()
    if (!items.length) return
    const first = items[0]
    const last = items[items.length - 1]
    const current = document.activeElement
    // 焦点已经在容器外（比如刚打开、或用户点了别处）时，也把它拉回容器内。
    if (!containerRef.value?.contains(current)) {
      event.preventDefault()
      ;(event.shiftKey ? last : first).focus()
      return
    }
    if (event.shiftKey && current === first) {
      event.preventDefault()
      last.focus()
    } else if (!event.shiftKey && current === last) {
      event.preventDefault()
      first.focus()
    }
  }

  async function activate() {
    lastFocused = document.activeElement
    document.addEventListener('keydown', onKeydown, true)
    document.body.style.overflow = 'hidden'
    await nextTick()
    // 容器自己可能已经带 autofocus（比如追加弹窗的输入框），优先尊重它。
    const alreadyInside = containerRef.value?.contains(document.activeElement)
    if (!alreadyInside) focusables()[0]?.focus()
  }

  function deactivate() {
    document.removeEventListener('keydown', onKeydown, true)
    document.body.style.overflow = ''
    // 焦点归还给打开它的那个元素：键盘用户关掉面板后应该回到原处，而不是被丢到页首。
    if (lastFocused && document.contains(lastFocused)) lastFocused.focus()
    lastFocused = null
  }

  watch(isActive, (open) => { if (open) activate(); else deactivate() }, { immediate: true })
  onUnmounted(deactivate)

  return { focusables }
}
