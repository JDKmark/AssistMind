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

// 已闭合的行内代码段：统计强调标记前要剔除，否则代码里的 ** 会被当成未闭合
const CODE_SPAN = /`[^`]*`/g
// 围栏代码块起始行（0-3 空格缩进 + 至少三个反引号或波浪号）
const FENCE_LINE = /^ {0,3}(```|~~~)/

// 文末是否处于未闭合的围栏代码块内：围栏内不解析行内语法，补标记会变成可见字符
function isInsideFence(text) {
  let open = false
  for (const line of text.split('\n')) {
    if (FENCE_LINE.test(line)) open = !open
  }
  return open
}

const countOf = (text, token) => text.split(token).length - 1

// 开标记后面是否已经有内容：只有收到内容才补闭合。
// 否则 `**` + `**` = `****` 会被 markdown-it 当成 <hr>（实测），`[a](` + `)` 会变成空链接
function hasContentAfter(text, marker) {
  const i = text.lastIndexOf(marker)
  return i !== -1 && text.length > i + marker.length
}

// 表格行：以竖线开头、以竖线结尾（LLM 输出表格几乎都带首尾竖线）。
// 不要求「以 | 开头」会误判正文里带竖线的句子（如 "A | B |"）。
const TABLE_ROW = /^\s*\|[\s\S]*\|\s*$/
// 分隔行：含竖线且含至少一个短横（`| --- | --- |`、`|:--|--:|`）
const TABLE_DELIM = /^\s*\|?[\s:|-]*-[\s:|-]*\|[\s:|-]*$/
// 正在输入的分隔行：只由竖线/短横/冒号/空白组成（`| ---`、`|` 这种半截状态）
const TABLE_DELIM_PARTIAL = /^[\s|:-]+$/

const isTableDelimiter = (line) => TABLE_DELIM.test(line)

// 单元格数：`| a | b |` → 2（去掉首尾竖线后按竖线切）
function columnCount(row) {
  const inner = row.trim().replace(/^\|/, '').replace(/\|$/, '')
  return inner.split('|').length
}

function buildDelimiter(cols) {
  return `| ${Array.from({ length: cols }, () => '---').join(' | ')} |`
}

// 第二行是不是「分隔行」——完整或半截都算（`| ---`、`|` 这种是正在输入）
const isDelimiterLike = (line) => isTableDelimiter(line) || TABLE_DELIM_PARTIAL.test(line)

/**
 * 表格补齐：表头行已完整、但分隔行还没到。
 *
 * 不补的话，这段时间整块会被当成普通段落渲染——用户看到的是带竖线的乱文本，
 * 等 `| --- | --- |` 到达再整体变成表格（一次明显的重排）。补一行合成分隔行后，
 * 表头一到就立刻是表格，后续行正常追加；真分隔行到达时合成的那行被替换掉，
 * 渲染结果不变，所以不会再有跳变。
 *
 * 关键：分隔行的**列数必须与表头一致**，否则 markdown 不认（`| --- |` 配两列表头
 * 依然会被当成段落）。所以半截分隔行要按表头列数重写，而不是简单叠加。
 *
 * 保守起见只在两种状态动手：只有表头一行、表头 + 第二行是分隔行；其余原样返回。
 * 表头行里还有未闭合行内标记时也不动（补的分隔行会被挤到标记后面而失效）。
 */
function completeTable(tail) {
  // 尾部空白（含表头刚输出完的那一个换行）先剥掉再判断，否则会多出空行、
  // 让表头和分隔行之间出现空行（表格立刻失效）
  const core = tail.replace(/\s+$/, '')
  const gap = tail.slice(core.length)
  const lines = core.split('\n')

  const header = lines[0]
  if (!TABLE_ROW.test(header)) return tail
  // 表头里有未闭合标记：先让行内补齐处理，这一帧不补表格
  if (countOf(header, '**') % 2 === 1) return tail
  if (countOf(header, '~~') % 2 === 1) return tail
  if (countOf(header, '`') % 2 === 1) return tail

  const cols = columnCount(header)
  // 单列表头多半是「表头还没输完」的中间态（`| 商品 |` 之后还会来第二列），
  // 这时候补会先渲染成 1 列再跳成 2 列；等列数 ≥2 再补，表格形态一次到位
  if (cols < 2) return tail

  const delimiter = buildDelimiter(cols)
  const second = lines[1]
  if (second === undefined) return `${core}\n${delimiter}${gap}`
  // 第二行已是列数匹配的分隔行 → 表格成立，不动
  if (isTableDelimiter(second) && columnCount(second) === cols) return tail
  // 第二行是半截分隔行（`| ---`、`|`）或列数不对 → 按表头列数重写
  if (isDelimiterLike(second)) return `${header}\n${delimiter}${gap}`
  // 第二行是数据行：模型漏了分隔行。只有整块都长得像表格行时才插入，
  // 否则可能是「以竖线开头的散文」，不猜
  if (lines.every((l) => l.trim() === '' || TABLE_ROW.test(l))) {
    return `${header}\n${delimiter}\n${lines.slice(1).join('\n')}${gap}`
  }
  return tail
}

/**
 * 补齐「没有闭合标记」的行内语法（幂等：完整文本原样返回）。
 *
 * 对齐成熟方案（vercel/streamdown 的 remend）：它是个**每次渲染都跑**的预处理，
 * 不是「只在流式期间跑」的开关（FAQ: parseIncompleteMarkdown 默认 true，
 * 渲染前统一预处理）。这里同样不区分流式/结束——否则最后一帧到 done 之间会
 * 出现「补过 → 退回原文」的跳变（表格缺分隔行时尤其明显）。
 *
 * 解决的是观感问题：LLM 逐 token 输出时，`**加粗` 会先原样显示 `**`，等闭合标记
 * 到了再突然变成加粗（一次跳变）。这里在渲染前把能确定「后面一定会闭合」的标记补上，
 * 补出来的标记本身不会变成可见字符，所以内容不变、只是少了一次跳变。
 *
 * 只补行内代码 / 加粗 / 删除线 / 链接右括号：
 * - 围栏代码块不用补（CommonMark 规定未闭合围栏延续到文末，markdown-it 已正确渲染）
 * - 单个 `*` `_` 斜体不补：与列表符号、下划线命名歧义太大，误判会改内容
 * - 只处理最后一个空行之后的段落：更早的块已定稿，往中间插闭合标记会改已渲染内容
 * - 尾段在围栏代码块内则原样返回（代码块内补标记会多出可见字符）
 *
 * @param {string} text 流式中的 markdown 源串
 * @returns {string} 补好闭合标记的源串（无未闭合语法时原样返回）
 */
export function completeMarkdown(text) {
  if (!text) return text
  // 绝大多数回答没有任何行内标记或表格：先做一次廉价预判，避免每帧都 split 全文
  if (!/[*~`[|]/.test(text)) return text
  // 围栏代码块内不解析行内语法，补标记会变成可见字符
  if ((text.includes('```') || text.includes('~~~')) && isInsideFence(text)) return text

  const idx = text.lastIndexOf('\n\n')
  const head = idx === -1 ? '' : text.slice(0, idx + 2)
  const tail = idx === -1 ? text : text.slice(idx + 2)
  if (!tail) return text

  // 表格：表头已到、分隔行未到 —— 先补合成分隔行，避免整块被当成段落渲染
  const withTable = completeTable(tail)
  if (withTable !== tail) return head + withTable

  // 结尾空白要留在闭合标记之后：`**` 紧跟空格无法闭合（CommonMark 要求右侧不是空白）
  const trailing = tail.match(/\s+$/)
  const gap = trailing ? trailing[0] : ''
  const core = gap ? tail.slice(0, -gap.length) : tail

  let suffix = ''
  // 未闭合的链接目标：[文本](https://exa（至少收到一个 URL 字符才补右括号）
  if (/\[[^\]\n]*\]\([^)\s]+$/.test(core)) suffix += ')'

  // 未闭合的行内代码：反引号奇数个且后面已有内容；代码段内的 ** 不算强调，故先截掉
  let body = core
  if (countOf(core, '`') % 2 === 1 && hasContentAfter(core, '`')) {
    suffix += '`'
    body = core.slice(0, core.lastIndexOf('`'))
  }

  // 加粗 / 删除线：成对计数为奇数、且开标记后已有内容。两种同时未闭合时按「后开的先关」收尾
  const plain = body.replace(CODE_SPAN, '')
  const boldOpen = countOf(plain, '**') % 2 === 1 && hasContentAfter(plain, '**')
  const strikeOpen = countOf(plain, '~~') % 2 === 1 && hasContentAfter(plain, '~~')
  if (boldOpen && strikeOpen) {
    suffix += plain.lastIndexOf('**') > plain.lastIndexOf('~~') ? '**~~' : '~~**'
  } else if (boldOpen) {
    suffix += '**'
  } else if (strikeOpen) {
    suffix += '~~'
  }

  return suffix ? `${head}${core}${suffix}${gap}` : text
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
  // 无条件补齐未闭合语法：补出的标记不产生可见字符，完整文本上是 no-op。
  // 不做「流式才补」的门控——那会让 done 时退回原文，产生最后一帧的跳变。
  const source = completeMarkdown(text)
  // 短文本直接整篇：连定义检测的正则都不跑（62% 的真实回答是单块短文）
  if (source.length < BLOCK_MIN_LENGTH) return { html: renderAnswerWhole(source), blocks: null }
  // 没有空行的文档只可能是单块（标题连排等极少数情况也走整篇，仅放弃优化、不影响结果），
  // 用一次 indexOf 换掉「parse + 分组」的整轮开销，保证单块回答与改造前等价
  if (!source.includes('\n\n')) return { html: renderAnswerWhole(source), blocks: null }
  if (LINK_DEFINITION.test(source)) return { html: renderAnswerWhole(source), blocks: null }

  let tokens
  const env = {}
  try {
    tokens = md.parse(source, env)
  } catch {
    // 解析异常（畸形输入）不阻断回答：退回整篇渲染，行为与改造前一致
    return { html: renderAnswerWhole(source), blocks: null }
  }

  const groups = groupTopLevelBlocks(tokens)
  if (groups.length < BLOCK_MIN_COUNT) {
    return { html: renderWhole(tokens, env), blocks: null }
  }

  const lines = source.split('\n')
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
