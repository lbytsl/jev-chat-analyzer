import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it } from 'vitest'

import { useUiStore } from '@/stores/ui'

describe('ui store', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('Esc 一次收起全部浮层', () => {
    const ui = useUiStore()
    ui.drawerOpen = true
    ui.settingsOpen = true
    ui.appendOpen = true

    ui.closeOverlays()

    expect([ui.drawerOpen, ui.settingsOpen, ui.appendOpen]).toEqual([false, false, false])
  })

  it('关掉「继续记录」时草稿一起清掉', () => {
    const ui = useUiStore()
    ui.openAppend()
    ui.appendDraft = '她：在吗'

    ui.closeAppend()

    expect(ui.appendOpen).toBe(false)
    expect(ui.appendDraft).toBe('')
  })

  it('右键提示条只关一次就够（同一个页面生命周期内不再出现）', () => {
    const ui = useUiStore()
    expect(ui.showRightClickHint).toBe(true)

    ui.dismissHint()

    expect(ui.showRightClickHint).toBe(false)
  })

  it('手机端默认收起会话栏，Esc 也能关闭展开的会话栏', () => {
    const original = window.matchMedia
    window.matchMedia = () => ({ matches: true })
    try {
      const ui = useUiStore()
      expect(ui.sidebarOpen).toBe(false)
      ui.sidebarOpen = true
      ui.closeOverlays()
      expect(ui.sidebarOpen).toBe(false)
    } finally {
      window.matchMedia = original
    }
  })
})
