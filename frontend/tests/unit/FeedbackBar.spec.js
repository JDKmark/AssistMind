import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import FeedbackBar from '@/components/FeedbackBar.vue'
import { submitFeedback } from '@/api/feedback'

const ElMessageMock = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
}))

vi.mock('element-plus', () => ({ ElMessage: ElMessageMock }))
vi.mock('@/api/feedback', () => ({
  submitFeedback: vi.fn(() => Promise.resolve({ feedback_id: 'fb-1', created: true })),
}))

const stubs = {
  'el-rate': { template: '<div class="el-rate-stub" />' },
  'el-input': { template: '<textarea class="el-input-stub" />' },
  'el-button': {
    template: '<button class="el-button-stub" @click="$emit(\'click\')"><slot /></button>',
  },
  'el-form': { template: '<form class="el-form-stub"><slot /></form>' },
  'el-form-item': { template: '<div class="el-form-item-stub"><slot /></div>' },
}

describe('FeedbackBar', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('渲染评分、文本框与提交按钮', () => {
    const wrapper = mount(FeedbackBar, { global: { components: stubs } })
    expect(wrapper.find('.el-rate-stub').exists()).toBe(true)
    expect(wrapper.find('.el-input-stub').exists()).toBe(true)
    expect(wrapper.find('.el-button-stub').text()).toContain('提交')
  })

  it('提交反馈调用 API 并清空表单', async () => {
    const wrapper = mount(FeedbackBar, {
      props: { ticketId: 'TK-123' },
      global: { components: stubs },
    })
    wrapper.vm.form.score = 4
    wrapper.vm.form.comment = '很好用'
    await wrapper.vm.handleSubmit()
    expect(submitFeedback).toHaveBeenCalledWith({
      score: 4,
      comment: '很好用',
      ticket_id: 'TK-123',
    })
    expect(ElMessageMock.success).toHaveBeenCalled()
    expect(wrapper.vm.form.score).toBe(0)
    expect(wrapper.vm.form.comment).toBe('')
  })

  it('未评分时不提交', async () => {
    const wrapper = mount(FeedbackBar, { global: { components: stubs } })
    wrapper.vm.form.score = 0
    await wrapper.vm.handleSubmit()
    expect(submitFeedback).not.toHaveBeenCalled()
  })
})
