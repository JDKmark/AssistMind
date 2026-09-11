import { describe, it, expect, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { useBreakpoint } from '@/utils/useBreakpoint'

// jsdom 无真实视口：改写 window.innerWidth 模拟三档宽度
function setWindowWidth(w) {
  Object.defineProperty(window, 'innerWidth', {
    configurable: true,
    writable: true,
    value: w,
  })
}

// 宿主组件：setup 中调用 composable，状态引用存到外部变量供断言
function mountWithBreakpoint() {
  let bp
  const Host = defineComponent({
    setup() {
      bp = useBreakpoint()
      return () => h('div')
    },
  })
  const wrapper = mount(Host)
  return { wrapper, bp }
}

describe('useBreakpoint', () => {
  beforeEach(() => {
    setWindowWidth(1024)
  })

  it('桌面档 ≥1024：isTablet=false、isMobile=false', () => {
    setWindowWidth(1440)
    const { wrapper, bp } = mountWithBreakpoint()
    expect(bp.width.value).toBe(1440)
    expect(bp.isTablet.value).toBe(false)
    expect(bp.isMobile.value).toBe(false)
    wrapper.unmount()
  })

  it('平板档 768-1023：isTablet=true、isMobile=false', () => {
    setWindowWidth(800)
    const { wrapper, bp } = mountWithBreakpoint()
    expect(bp.width.value).toBe(800)
    expect(bp.isTablet.value).toBe(true)
    expect(bp.isMobile.value).toBe(false)
    wrapper.unmount()
  })

  it('手机档 <768：isTablet=true、isMobile=true', () => {
    setWindowWidth(375)
    const { wrapper, bp } = mountWithBreakpoint()
    expect(bp.width.value).toBe(375)
    expect(bp.isTablet.value).toBe(true)
    expect(bp.isMobile.value).toBe(true)
    wrapper.unmount()
  })

  it('边界归属：1024 属桌面、768 属平板（<1024 / <768 半开区间）', () => {
    setWindowWidth(1024)
    const a = mountWithBreakpoint()
    expect(a.bp.isTablet.value).toBe(false)
    expect(a.bp.isMobile.value).toBe(false)
    a.wrapper.unmount()

    setWindowWidth(768)
    const b = mountWithBreakpoint()
    expect(b.bp.isTablet.value).toBe(true)
    expect(b.bp.isMobile.value).toBe(false)
    b.wrapper.unmount()
  })

  it('resize 事件驱动宽度与档位更新', () => {
    setWindowWidth(1440)
    const { wrapper, bp } = mountWithBreakpoint()
    expect(bp.isMobile.value).toBe(false)

    setWindowWidth(700)
    window.dispatchEvent(new Event('resize'))
    expect(bp.width.value).toBe(700)
    expect(bp.isTablet.value).toBe(true)
    expect(bp.isMobile.value).toBe(true)
    wrapper.unmount()
  })

  it('卸载后移除 resize 监听：事件不再更新状态', () => {
    setWindowWidth(1440)
    const { wrapper, bp } = mountWithBreakpoint()
    wrapper.unmount()

    setWindowWidth(500)
    window.dispatchEvent(new Event('resize'))
    expect(bp.width.value).toBe(1440)
    expect(bp.isMobile.value).toBe(false)
  })
})
