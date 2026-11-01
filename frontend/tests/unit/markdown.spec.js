import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { ref, computed, nextTick } from 'vue'
import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'
import { completeStreamingMarkdown, renderAnswer } from '@/utils/markdown'

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

// 流式补齐：目标不是「更好看」，而是「补出来的标记不能变成可见字符、也不能改变语义」。
// 断言方式一律是「补齐后的渲染 == 真收到闭合标记后的渲染」。
describe('completeStreamingMarkdown 未闭合语法补齐', () => {
  const stream = (t) => renderAnswer(t, { streaming: true }).html
  const final = (t) => renderAnswer(t).html

  it('未闭合加粗：补齐后与闭合后的渲染完全一致', () => {
    expect(completeStreamingMarkdown('abc**def')).toBe('abc**def**')
    expect(stream('abc**def')).toBe(final('abc**def**'))
    expect(stream('abc**def')).toContain('<strong>def</strong>')
  })

  it('结尾带空白时，闭合标记插在空白之前（否则 CommonMark 不认）', () => {
    expect(completeStreamingMarkdown('abc**def ')).toBe('abc**def** ')
    expect(stream('abc**def ')).toBe(final('abc**def** '))
  })

  it('未闭合行内代码', () => {
    expect(completeStreamingMarkdown('a`code')).toBe('a`code`')
    expect(stream('a`code')).toContain('<code>code</code>')
  })

  it('未闭合代码段里的 ** 不被当成加粗（只补反引号）', () => {
    expect(completeStreamingMarkdown('`a**b')).toBe('`a**b`')
    expect(stream('`a**b')).toBe(final('`a**b`'))
  })

  it('已闭合代码段里的 ** 不触发补齐', () => {
    expect(completeStreamingMarkdown('`a**b`')).toBe('`a**b`')
  })

  it('未闭合删除线', () => {
    expect(completeStreamingMarkdown('~~del')).toBe('~~del~~')
    expect(stream('~~del')).toContain('<s>del</s>')
  })

  it('未闭合链接目标补右括号', () => {
    expect(completeStreamingMarkdown('[文档](https://example.com/a')).toBe(
      '[文档](https://example.com/a)',
    )
    expect(stream('[文档](https://example.com/a')).toBe(final('[文档](https://example.com/a)'))
  })

  it('列表项里的加粗照常补齐', () => {
    expect(completeStreamingMarkdown('- **重点')).toBe('- **重点**')
    expect(stream('- **重点')).toContain('<strong>重点</strong>')
  })

  it('围栏代码块内不补（否则会多出可见字符）', () => {
    const text = '```js\nconst a = "**x"'
    expect(completeStreamingMarkdown(text)).toBe(text)
    expect(stream(text)).toBe(final(text))
  })

  it('已闭合的文本原样返回', () => {
    for (const t of ['**a**', '`a`', '~~a~~', '普通文本', '[a](b)']) {
      expect(completeStreamingMarkdown(t)).toBe(t)
    }
  })

  it('只补最后一段，不改动已定稿的前文', () => {
    expect(completeStreamingMarkdown('a**b\n\nc**d')).toBe('a**b\n\nc**d**')
  })

  it('加粗与删除线同时未闭合时按后开的先关收尾', () => {
    expect(completeStreamingMarkdown('**a~~b')).toBe('**a~~b~~**')
    expect(completeStreamingMarkdown('~~a**b')).toBe('~~a**b**~~')
  })

  it('非流式（done 之后）不补齐：按模型原文渲染', () => {
    expect(renderAnswer('abc**def').html).not.toContain('<strong>')
    expect(renderAnswer('abc**def').html).toContain('**')
  })
})

describe('流式补齐与块级渲染的配合', () => {
  it('块级路径下尾块也补齐未闭合语法，且不污染已完成块', () => {
    const done = fill('甲', 160)
    const text = `${done}\n\n${fill('乙', 160)}\n\n**重点`
    const r = renderAnswer(text, { streaming: true })
    expect(r.blocks).not.toBeNull()
    expect(r.blocks.at(-1).html).toContain('<strong>重点</strong>')
    expect(r.blocks[0].html).toBe(renderAnswer(done).html)
  })
})

describe('流式补齐的边界与收尾', () => {
  const stream = (t) => renderAnswer(t, { streaming: true }).html
  const final = (t) => renderAnswer(t).html

  it('只收到开标记时不补（补成 **** 会被渲染成 <hr>）', () => {
    for (const t of ['**', 'abc**', '~~', '`', 'a`', '[a](']) {
      expect(completeStreamingMarkdown(t)).toBe(t)
    }
    expect(stream('**')).not.toContain('<hr')
  })

  it('流式全程不把 ** 原样显示（只收到开标记的那一帧除外）', () => {
    const full = '退货申请通过后 **48 小时内** 原路退回，**运费**由商家承担。'
    let checked = 0
    for (let i = 1; i <= full.length; i++) {
      const prefix = full.slice(0, i)
      if (completeStreamingMarkdown(prefix) === prefix) continue
      checked += 1
      expect(root(stream(prefix)).textContent).not.toContain('**')
    }
    expect(checked).toBeGreaterThan(5)
  })

  it('done 之后与流式中最后一次渲染一致，收尾不跳变', () => {
    const full = '退货申请通过后 **48 小时内** 原路退回，`运费`由商家承担。'
    expect(stream(full)).toBe(final(full))
  })
})

// 表格：分隔行（|---|）到达前，整块会被 markdown 当成普通段落 → 用户先看到带竖线的
// 乱文本，再整体重排成表格。成熟方案（Vercel remend）没有 table handler，这块得自己补。
describe('表格补齐', () => {
  const stream = (t) => renderAnswer(t, { streaming: true }).html
  const final = (t) => renderAnswer(t).html
  const hasDelimiter = (t) => t.split('\n').some((l, i) => i > 0 && /^\|[\s:|-]+\|$/.test(l.trim()))

  it('表头到达、分隔行未到：直接渲染成表格，而不是带竖线的段落', () => {
    const html = stream('| 商品 | 价格 |')
    expect(html).toContain('<table>')
    expect(html).toContain('<th>商品</th>')
    expect(html).not.toContain('| 商品')
  })

  it('真分隔行到达后渲染结果不变（不跳变）', () => {
    expect(stream('| 商品 | 价格 |')).toBe(stream('| 商品 | 价格 |\n| --- | --- |'))
  })

  it('半截分隔行被替换而不是叠加', () => {
    expect(completeStreamingMarkdown('| a | b |\n|')).toBe('| a | b |\n| --- | --- |')
    expect(completeStreamingMarkdown('| a | b |\n| ---')).toBe('| a | b |\n| --- | --- |')
  })

  it('列数按表头单元格数生成', () => {
    expect(completeStreamingMarkdown('| a | b | c |')).toBe('| a | b | c |\n| --- | --- | --- |')
  })

  it('已完整的表格原样返回，且流式与 done 渲染一致', () => {
    const t = '| a | b |\n| --- | --- |\n| 1 | 2 |'
    expect(completeStreamingMarkdown(t)).toBe(t)
    expect(stream(t)).toBe(final(t))
  })

  it('不以竖线开头的行不被误判成表格', () => {
    const t = '见下表 A | B |'
    expect(completeStreamingMarkdown(t)).toBe(t)
    expect(stream(t)).not.toContain('<table>')
  })

  it('表头里有未闭合加粗时不补表格（否则分隔行会被挤到标记之后）', () => {
    const t = '| **商品 | 价格 |'
    expect(completeStreamingMarkdown(t)).not.toContain('---')
  })

  it('流式全程：只要分隔行已在，渲染结果一定是表格', () => {
    const full = '| 商品 | 价格 |\n| --- | --- |\n| 华为 Mate 70 | 5499 |'
    let checked = 0
    for (let i = 1; i <= full.length; i++) {
      const prefix = full.slice(0, i)
      if (!hasDelimiter(completeStreamingMarkdown(prefix))) continue
      checked += 1
      expect(stream(prefix)).toContain('<table>')
    }
    expect(checked).toBeGreaterThan(10)
  })
})
