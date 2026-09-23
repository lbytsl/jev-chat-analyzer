import { createPinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, nextTick } from 'vue'

// 只挡掉两个真会发请求的端点，其余保持真实（store 也从这里导入）。
vi.mock('@/api/client', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    fetchHealth: vi.fn(async () => ({ ok: true, version: 'test', general_llm: '已配置' })),
    listSessions: vi.fn(async () => ({ sessions: [], total: 0 })),
  }
})

import App from '@/App.vue'

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
  })

  it('空状态下能挂载并渲染出引导（store 接线正确的兜底）', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = createApp(App).use(createPinia())
    app.mount(host)
    await nextTick()

    expect(host.querySelector('#results')).toBeTruthy()
    expect(host.textContent).toContain('还没有聊天记录')
    // 顶栏徽标走的是 analysis store 里的健康检查结果，而 probe() 是 onMounted 里的异步调用：
    // 等它落地再断言，别把「还没回来」当成「接线错了」。
    await vi.waitFor(() => expect(host.textContent).toContain('Jev + LLM层已连接'))

    app.unmount()
  })

  it('Esc 一次收起全部浮层（页面级快捷键）', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)
    const app = createApp(App).use(createPinia())
    app.mount(host)
    await nextTick()

    document.getElementById('settingsBtn').click()
    await nextTick()
    expect(host.querySelector('#settingsModal').style.display).not.toBe('none')

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await nextTick()

    expect(host.querySelector('#settingsModal').style.display).toBe('none')
    app.unmount()
  })
})
