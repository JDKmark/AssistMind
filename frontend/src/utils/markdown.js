import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'

/**
 * 回答的 markdown 渲染（Chat 消息气泡用）。
 *
 * 单例：MarkdownIt 实例构建有成本，且渲染器本身无状态，消息再多也只需一份。
 * 渲染结果必须经 DOMPurify 消毒后再注入 —— LLM/知识库内容不可信，
 * 需防 javascript: 链接、事件属性等 XSS 载荷。
 */
const md = new MarkdownIt({ html: false, linkify: true, breaks: true })

// 块级渲染的启用门槛：低于这个量级，切块与逐块挂载的开销超过省下的渲染
// （实测 29-37 字的单块回答，块级版本反而慢 0-40%）
const BLOCK_MIN_COUNT = 2
const BLOCK_MIN_LENGTH = 300

// 引用式链接定义 `[1]: url` 是文档级作用域：写在后面的定义会影响前面的引用，
// 逐块渲染会漏掉它（实测 markdown-it 的 env.references 只在整篇 parse 时收集）。
// 命中即退回整篇渲染。
const LINK_DEFINITION = /^ {0,3}\[[^\]\n]+\]:/m

// 已完成块的 HTML 缓存：键是块的 markdown 源串。
// 源串由 lines.slice().join() 生成（新字符串，不是原串的 substring 视图），
// 不会让 V8 通过 substring retainer 把整篇回答钉在内存里。
// 按源串缓存是安全的：引用式链接定义已在上游 bail-out，块的渲染结果只取决于自身文本。
const blockCache = new Map()
const BLOCK_CACHE_MAX = 400

// 整篇渲染（不经 token 复用）：bail-out 与短文本路径共用
function renderAnswerWhole(text) {
  return DOMPurify.sanitize(md.render(text))
}

// 复用已 parse 的 token 渲染（省掉一次 parse）
function renderWhole(tokens, env) {
  return DOMPurify.sanitize(md.renderer.render(tokens, md.options, env))
}

/**
 * 把顶层 token 切成块。
 *
 * 用 markdown-it 自己的 token 流分组，而不是按空行切文本——后者会把
 * 「松散列表」（`- a` 空行 `- b`）切成两个列表、把含空行的代码块切断，
 * 渲染结果与整篇不一致。分组依据 nesting：nesting=1 开块、=-1 收块，
 * 自闭合块（fence/hr）一次即止。
 */
function groupTopLevelBlocks(tokens) {
  const groups = []
  let i = 0
  while (i < tokens.length) {
    const start = i
    let depth = 0
    do {
      const t = tokens[i]
      if (t.nesting === 1) depth += 1
      else if (t.nesting === -1) depth -= 1
      i += 1
    } while (i < tokens.length && depth > 0)
    groups.push(tokens.slice(start, i))
  }
  return groups
}

// 块的源码文本（缓存键）：顶层 token 的 map 覆盖整块的行范围
function blockSource(token, lines) {
  if (!token.map) return null
  const [start, end] = token.map
  return lines.slice(start, Math.min(end, lines.length)).join('\n')
}

/**
 * 渲染一条回答。
 *
 * 返回值二选一：
 * - `{ blocks: [{ html }] }`：块级渲染，供模板用 v-for + v-memo 逐块挂载，
 *   已完成的块不再重渲染、也不重写 DOM（流式期间只有尾块变化）
 * - `{ html }`：整篇渲染，短回答 / 有引用式链接定义 / 解析异常时走这条
 *
 * @param {string} text markdown 源串
 * @returns {{ html: string, blocks: Array<{ html: string }> | null }}
 */
export function renderAnswer(text) {
  if (!text) return { html: '', blocks: null }
  // 短文本直接整篇：连定义检测的正则都不跑（62% 的真实回答是单块短文）
  if (text.length < BLOCK_MIN_LENGTH) return { html: renderAnswerWhole(text), blocks: null }
  // 没有空行的文档只可能是单块（标题连排等极少数情况也走整篇，仅放弃优化、不影响结果），
  // 用一次 indexOf 换掉「parse + 分组」的整轮开销，保证单块回答与改造前等价
  if (!text.includes('\n\n')) return { html: renderAnswerWhole(text), blocks: null }
  if (LINK_DEFINITION.test(text)) return { html: renderAnswerWhole(text), blocks: null }

  let tokens
  const env = {}
  try {
    tokens = md.parse(text, env)
  } catch {
    // 解析异常（畸形输入）不阻断回答：退回整篇渲染，行为与改造前一致
    return { html: renderAnswerWhole(text), blocks: null }
  }

  const groups = groupTopLevelBlocks(tokens)
  if (groups.length < BLOCK_MIN_COUNT) {
    return { html: renderWhole(tokens, env), blocks: null }
  }

  const lines = text.split('\n')
  const blocks = []
  for (const group of groups) {
    const key = blockSource(group[0], lines)
    if (key === null) {
      // 拿不到行范围就无法稳定命中缓存，整篇渲染更可靠
      return { html: renderWhole(tokens, env), blocks: null }
    }
    let html = blockCache.get(key)
    if (html === undefined) {
      html = renderWhole(group, env)
      if (blockCache.size >= BLOCK_CACHE_MAX) blockCache.clear()
      blockCache.set(key, html)
    }
    blocks.push({ html })
  }
  return { html: '', blocks }
}
