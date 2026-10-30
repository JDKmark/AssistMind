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

export function renderMarkdown(text) {
  if (!text) return ''
  return DOMPurify.sanitize(md.render(text))
}
