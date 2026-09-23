import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'

import { useFormStore } from '@/stores/form'
import { usePeopleStore } from '@/stores/people'

const TRANSCRIPT = '我：在忙吗\n她：刚开完会\n我：那晚点说'
const OTHER = '甲：在吗\n乙：在'

describe('people store', () => {
  beforeEach(() => {
    // 每个用例一套全新的 pinia：store 是按 pinia 实例缓存的，不重置会串状态。
    setActivePinia(createPinia())
    vi.useFakeTimers()
  })
  afterEach(() => vi.useRealTimers())

  /** 正文要在建 people store 之前写好：它在创建时就会 sync() 一次。 */
  function setup(transcript = TRANSCRIPT) {
    const form = useFormStore()
    form.transcript = transcript
    return { form, people: usePeopleStore() }
  }

  it('初始化就把说话人、默认「我是谁」与解读对象算出来', () => {
    const { people } = setup()
    expect(people.people.map((p) => p.label)).toEqual(['我', '她'])
    expect(people.me).toBe('我')
    // 两个说话人时默认解读「对方」，不解读自己。
    expect(people.targets.map((p) => p.label)).toEqual(['她'])
  })

  it('正文变化要等防抖到点才重算（不能每敲一个字就全量扫一遍）', async () => {
    const { form, people } = setup()

    form.transcript = OTHER
    await nextTick()   // 让 watch 回调挂上定时器
    expect(people.people.map((p) => p.label)).toEqual(['我', '她'])

    vi.advanceTimersByTime(250)
    expect(people.people.map((p) => p.label)).toEqual(['甲', '乙'])
  })

  it('sync() 可以立刻重算（提交前 flush 用）', () => {
    const { form, people } = setup()

    form.transcript = OTHER
    people.sync()

    expect(people.people.map((p) => p.label)).toEqual(['甲', '乙'])
  })

  it('dispose() 之后待触发的防抖不再执行（卸载时的清理）', async () => {
    const { form, people } = setup()

    form.transcript = OTHER
    await nextTick()
    people.dispose()
    vi.advanceTimersByTime(1000)

    expect(people.people.map((p) => p.label)).toEqual(['我', '她'])
  })

  it('restore 用会话里存的「我是谁 / 解读谁」覆盖自动推断', () => {
    const { people } = setup()

    people.restore({ me: '她', read: ['我'] })

    expect(people.me).toBe('她')
    expect([...people.read]).toEqual(['我'])
  })

  it('restore 拿到空值时交回默认推断（老会话没存这两个字段）', () => {
    const { people } = setup()

    people.restore({})

    expect(people.me).toBe('我')
    expect([...people.read]).toEqual(['她'])
  })
})
