import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { ref, computed, nextTick } from 'vue'
import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'
import { renderAnswer } from '@/utils/markdown'

// 块级渲染的正确性：分组必须与 markdown 语义一致，否则「快」是拿错渲染换来的。
// 关键反例：按空行切文本会把松散列表切成两个列表、把含空行的代码块切断。

const fill = (ch, n = 160) => ch.repeat(n)

function joinBlocks(blocks) {
  return blocks.map((b) => b.html).join('')
}

function root(html) {
  const el = document.createElement('div')
  el.innerHTML = html
  return el
}

function htmlOf(result) {
  return result.blocks ? joinBlocks(result.blocks) : result.html
}

describe('renderAnswer 块级渲染', () => {
  it('短文本整篇渲染（不切块）', () => {
    const r = renderAnswer('退货 **48 小时** 到账')
    expect(r.blocks).toBeNull()
    expect(r.html).toContain('<strong>48 小时</strong>')
  })

  it('空串返回空结果', () => {
    expect(renderAnswer('')).toEqual({ html: '', blocks: null })
  })

  it('长文本按顶层块切分，块数等于段落数', () => {
    const text = [fill('甲'), fill('乙'), fill('丙')].join('\n\n')
    const r = renderAnswer(text)
    expect(r.blocks).not.toBeNull()
    expect(r.blocks.length).toBe(3)
    for (const b of r.blocks) expect(b.html).toContain('<p>')
  })

  it('拼接各块后的可见文本与源文本一致（无丢字）', () => {
    const text = [fill('甲', 150), fill('乙', 150), fill('丙', 150)].join('\n\n')
    const el = root(joinBlocks(renderAnswer(text).blocks))
    expect(el.textContent.replace(/\s+/g, '')).toBe(text.replace(/\s+/g, ''))
  })

  it('松散列表（项之间有空行）不会被切成两个列表', () => {
    const text = `${fill('甲')}\n\n- 项目一\n\n- 项目二\n\n${fill('乙')}`
    const el = root(htmlOf(renderAnswer(text)))
    expect(el.querySelectorAll('ul').length).toBe(1)
    expect(el.querySelectorAll('li').length).toBe(2)
  })

  it('含空行的代码块不会被切断', () => {
    const text = `${fill('甲', 150)}\n\n\`\`\`js\nconst a = 1\n\nconst b = 2\n\`\`\`\n\n${fill('乙', 150)}`
    const el = root(htmlOf(renderAnswer(text)))
    expect(el.querySelectorAll('pre').length).toBe(1)
    expect(el.textContent).toContain('const b = 2')
  })

  it('表格整块保留（不按行切开）', () => {
    const table = ['| 项目 | 时效 |', '| --- | --- |', '| 退货 | 48h |', '| 换货 | 72h |'].join(
      '\n',
    )
    const text = `${fill('甲')}\n\n${table}\n\n${fill('乙')}`
    const el = root(htmlOf(renderAnswer(text)))
    expect(el.querySelectorAll('table').length).toBe(1)
    expect(el.querySelectorAll('tbody tr').length).toBe(2)
  })

  it('引用式链接定义存在时退回整篇渲染，链接仍可解析', () => {
    const text = `${fill('甲', 200)}\n\n详见[售后文档][1]。\n\n${fill('乙', 120)}\n\n[1]: https://example.com/doc`
    const r = renderAnswer(text)
    expect(r.blocks).toBeNull()
    expect(r.html).toContain('href="https://example.com/doc"')
  })

  it('原始 HTML 标签被转义（html:false），不会注入元素', () => {
    const text = `${fill('甲', 320)}\n\n<img src=x onerror="alert(1)">`
    const el = root(htmlOf(renderAnswer(text)))
    expect(el.querySelector('img')).toBeNull()
  })

  it('javascript: 链接被消毒', () => {
    const text = `${fill('甲', 320)}\n\n[点我](javascript:alert(1))`
    const el = root(htmlOf(renderAnswer(text)))
    const href = el.querySelector('a')?.getAttribute('href') || ''
    expect(href).not.toContain('javascript:')
  })

  it('同一内容重复渲染结果稳定（块缓存不串内容）', () => {
    const a = renderAnswer(`${fill('甲', 150)}\n\n${fill('乙', 150)}`)
    const b = renderAnswer(`${fill('甲', 150)}\n\n${fill('乙', 150)}`)
    expect(a.blocks.map((x) => x.html)).toEqual(b.blocks.map((x) => x.html))
  })

  it('无空行的长文本仍走整篇（快速路径不改变结果）', () => {
    const text = fill('甲', 400)
    const r = renderAnswer(text)
    expect(r.blocks).toBeNull()
    expect(r.html).toContain('<p>')
  })

  it('短文本路径与改造前逐字节一致（整篇 md.render + 消毒）', () => {
    const text = '退货 **48 小时** 到账，详见[售后规则](/tickets)。'
    const legacy = DOMPurify.sanitize(
      new MarkdownIt({ html: false, linkify: true, breaks: true }).render(text),
    )
    expect(renderAnswer(text).html).toBe(legacy)
  })
})

// 模板契约：块级渲染靠 v-memo 保证已完成块不被重写。这里直接验证 DOM 节点身份——
// 若 v-memo 失效，节点会被替换，流式期间每帧重建整篇（等于没优化）
describe('块级渲染的模板契约', () => {
  const Host = {
    template: `
      <div>
        <div v-if="answer.blocks" class="md-body">
          <div
            v-for="(b, i) in answer.blocks"
            :key="i"
            v-memo="[b.html]"
            class="md-block"
            v-html="b.html"
          />
        </div>
        <div v-else class="md-body" v-html="answer.html" />
      </div>`,
    setup() {
      const text = ref([fill('甲', 150), fill('乙', 150)].join('\n\n'))
      const answer = computed(() => renderAnswer(text.value))
      return { text, answer }
    },
  }

  it('已完成块在后续增量中保持同一个 DOM 节点（不重写）', async () => {
    const wrapper = mount(Host)
    const blocks = wrapper.findAll('.md-block')
    expect(blocks.length).toBe(2)
    const firstNode = blocks[0].element

    // 模拟流式：只改最后一段
    wrapper.vm.text = [fill('甲', 150), `${fill('乙', 150)}追加`].join('\n\n')
    await nextTick()

    const after = wrapper.findAll('.md-block')
    expect(after.length).toBe(2)
    expect(after[0].element).toBe(firstNode)
    expect(after[1].text()).toContain('追加')
  })

  it('文本跨过阈值后从整篇切到块级，内容不丢', async () => {
    const wrapper = mount(Host)
    wrapper.vm.text = '短回答'
    await nextTick()
    expect(wrapper.findAll('.md-block').length).toBe(0)

    wrapper.vm.text = [fill('甲', 150), fill('乙', 150)].join('\n\n')
    await nextTick()
    expect(wrapper.findAll('.md-block').length).toBe(2)
    expect(wrapper.text().replace(/\s+/g, '')).toBe(
      '甲乙'.repeat(0) + '甲'.repeat(150) + '乙'.repeat(150),
    )
  })
})
