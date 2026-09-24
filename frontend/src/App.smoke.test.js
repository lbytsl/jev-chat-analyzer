import { createPinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, nextTick } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'

// 只挡掉两个真会发请求的端点，其余保持真实（store 也从这里导入）。
vi.mock('@/api/client', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    fetchHealth: vi.fn(async () => ({ ok: true, version: 'test', general_llm: '已配置' })),
    listSessions: vi.fn(async () => ({ sessions: [], total: 0 })),
    getSession: vi.fn(),
  }
})

import { getSession, listSessions } from '@/api/client'
import App from '@/App.vue'
import JevGuideView from '@/view/JevGuideView.vue'

function mountApp(host) {
  const router = createRouter({ history: createMemoryHistory(),
    routes: [{ path: '/', component: App }, { path: '/jev-guide', component: JevGuideView }] })
  const app = createApp(App).use(createPinia()).use(router)
  app.mount(host)
  return app
}

/**
 * 整页冒烟：真的把 App 挂到 jsdom 上。
 *
 * 单测覆盖不到「接线」这类问题——store 写在 setup 之外、模板引用了已经搬走的 prop、
 * 组件忘了 import 某个 store……这些都是**挂载时才炸**，而构建只看语法。
 * 这个文件就是那层兜底：只要能渲染出空态，说明五个 store 的依赖方向没拧。
 */
describe('App 冒烟', () => {
  beforeEach(() => {
    document.body.innerHTML = ''
    vi.clearAllMocks()
  })

  it('有历史会话时仍从新会话进入，历史记录只显示在侧栏', async () => {
    listSessions.mockResolvedValueOnce({ sessions: [{ id: 'recent', title: '阿珍', preview: '晚安' }], total: 1 })
    const host = document.createElement('div')
    document.body.appendChild(host)
    const app = mountApp(host)

    await vi.waitFor(() => expect(host.querySelector('.session-item')).toBeTruthy())
    expect(host.querySelector('#wxTitle').textContent).toBe('新会话')
    expect(host.textContent).toContain('看见聊天里的')
    expect(host.querySelector('.session-item.on')).toBeNull()
    expect(getSession).not.toHaveBeenCalled()
    app.unmount()
  })

  it('从历史会话打开导入面板时保留原聊天，取消后还原表单', async () => {
    listSessions.mockResolvedValueOnce({ sessions: [{ id: 'recent', title: '阿珍', preview: '晚安' }], total: 1 })
    getSession.mockResolvedValueOnce({
      session_id: 'recent', transcript: '阿珍：晚安', relationship: '恋爱',
      other_label: '阿珍', me_label: '我', read_labels: ['阿珍'],
      messages: [], analyses: [], count: 0,
    })
    const host = document.createElement('div')
    document.body.appendChild(host)
    const app = mountApp(host)
    await vi.waitFor(() => expect(host.querySelector('.session-item')).toBeTruthy())
    host.querySelector('.session-item').click()
    await vi.waitFor(() => expect(host.querySelector('#wxTitle').textContent).toBe('阿珍'))

    host.querySelector('.session-new').click()
    await nextTick()
    expect(host.querySelector('#drawer').classList.contains('open')).toBe(true)
    expect(host.querySelector('.import-scrim')).toBeTruthy()
    expect(host.querySelector('#wxTitle').textContent).toBe('阿珍')
    expect(host.querySelector('#transcript').value).toBe('')

    host.querySelector('.import-scrim').click()
    await nextTick()
    expect(host.querySelector('#transcript').value).toBe('阿珍：晚安')
    expect(host.querySelector('#wxTitle').textContent).toBe('阿珍')
    app.unmount()
  })

  it('空状态下能挂载并渲染出引导（store 接线正确的兜底）', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountApp(host)
    await nextTick()

    expect(host.querySelector('#results')).toBeTruthy()
    expect(host.textContent).toContain('看见聊天里的')
    // 顶栏徽标走的是 analysis store 里的健康检查结果，而 probe() 是 onMounted 里的异步调用：
    // 等它落地再断言，别把「还没回来」当成「接线错了」。
    await vi.waitFor(() => expect(host.textContent).toContain('Jev + LLM层已连接'))

    app.unmount()
  })

  it('Esc 一次收起全部浮层（页面级快捷键）', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)
    const app = mountApp(host)
    await nextTick()

    document.getElementById('settingsBtn').click()
    await nextTick()
    expect(host.querySelector('#settingsModal').style.display).not.toBe('none')

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await nextTick()

    expect(host.querySelector('#settingsModal').style.display).toBe('none')
    app.unmount()
  })

  it('标题栏按钮可以展开和收起会话管理', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)
    const app = mountApp(host)
    await nextTick()

    const toggle = host.querySelector('.sidebar-toggle')
    const sidebar = host.querySelector('#sessionSidebar')
    expect(toggle.getAttribute('aria-expanded')).toBe('true')
    toggle.click()
    await nextTick()
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    expect(sidebar.hasAttribute('inert')).toBe(true)
    toggle.click()
    await nextTick()
    expect(toggle.getAttribute('aria-expanded')).toBe('true')
    app.unmount()
  })
})
