/**
 * AssistMind README 运行时前端截图生成脚本（Playwright, CommonJS）
 *
 * 前置：本地服务已启动（start-demo.ps1 或 start-dev.ps1）
 *   - 前端 http://localhost:5173（Vite，/api 代理到 8002）
 *   - 后端 http://localhost:8002（含 .env 的 DEEPSEEK_API_KEY）
 * 用法：
 *   PS: $env:NODE_PATH = npm root -g; node <脚本所在目录>\shoot-assistmind-screenshots.cjs
 * 产出 D:\2026\AssistMind\docs\screenshots\{01..07}.png
 */
const { chromium } = require('playwright')
const fs = require('node:fs')

const BASE = 'http://localhost:5173'
const OUT_DIR = 'D:/2026/AssistMind/docs/screenshots'

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const log = (...a) => console.log(...a)

async function main() {
  // 等待最近一条客服消息流式结束（done：出现反馈条；error：出现错误条）
  async function waitDone(page, timeout = 120000) {
    await page.waitForFunction(
      () => {
        const bubbles = [...document.querySelectorAll('.chat-row.assistant')]
        if (!bubbles.length) return false
        const last = bubbles[bubbles.length - 1]
        return !!(last.querySelector('.feedback-row') || last.querySelector('.error-alert'))
      },
      { timeout },
    )
  }

  // 折叠所有「诊断信息」面板（保留标题行与 chips，隐藏详情体，画面更干净）
  async function collapseDiag(page) {
    await page.evaluate(() => {
      document.querySelectorAll('.diag-header.open').forEach((el) => el.click())
    })
    await sleep(400)
  }

  // 发送问题并等待完成；LLM 偶发失败时自动重试（最多 3 次）
  async function sendQuestion(page, q) {
    for (let attempt = 1; attempt <= 3; attempt += 1) {
      await page.fill('.ai-input textarea', q)
      await page.keyboard.press('Enter')
      log(`[send] "${q}" attempt ${attempt}`)
      try {
        await waitDone(page)
      } catch (e) {
        log(`[send] "${q}" wait timeout, retry...`)
        continue
      }
      const errCount = await page.locator('.chat-row.assistant .error-alert').count()
      if (errCount === 0) return true
      log(`[send] "${q}" 服务繁忙，重试...`)
      await sleep(1500)
    }
    return false
  }

  fs.mkdirSync(OUT_DIR, { recursive: true })

  const browser = await chromium.launch()
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })

    // ---------- 01 登录页 ----------
    log('[01] login')
    await page.goto(`${BASE}/login`)
    await sleep(3000) // 等待 GSAP 入场动画完成
    await page.screenshot({ path: `${OUT_DIR}/01-login.png` })

    await page.fill('input[placeholder="用户名"]', 'admin')
    await page.fill('input[placeholder="密码"]', 'admin123')
    await page.click('button.login-btn')
    await page.waitForURL('**/chat', { timeout: 15000 })
    await sleep(1500)

    // ---------- 02 Chat：RAG 问答（faq 意图，L1 语义缓存命中） ----------
    log('[02] chat faq')
    await page.reload()
    await page.waitForSelector('.ai-input textarea')
    await sleep(1500)
    await sendQuestion(page, '运费谁出？')
    await sleep(1500)
    await collapseDiag(page)
    await page.screenshot({ path: `${OUT_DIR}/02-chat-faq.png` })

    // ---------- 03 Chat：Agent 工具调用（订单退货全链：query_order → apply_refund）
    // LLM（商汤网关）间歇故障：整流重试，直到出现工具结果卡片 + 干净成功文案为止
    log('[03] chat agent')
    const agentOk = await (async () => {
      for (let attempt = 1; attempt <= 6; attempt += 1) {
        await page.reload()
        await page.waitForSelector('.ai-input textarea')
        await sleep(1200)
        await sendQuestion(page, '订单 20260801002，商品有质量问题，申请退货退款')
        await sleep(1200)
        const errCount = await page.locator('.chat-row.assistant .error-alert').count()
        const cardCount = await page.locator('.chat-row.assistant .tool-result-card').count()
        const answerText = await page.evaluate(() => {
          const last = [...document.querySelectorAll('.chat-row.assistant')].pop()
          return last ? last.innerText : ''
        })
        if (errCount === 0 && cardCount > 0 && !answerText.includes('重复') && !answerText.includes('繁忙')) {
          log(`[03] 工具链成功（attempt ${attempt}），结果卡片 ${cardCount} 张`)
          return true
        }
        log(`[03] attempt ${attempt} 未出干净全链（error=${errCount} cards=${cardCount}），重试...`)
      }
      return false
    })()
    await collapseDiag(page)
    if (!agentOk) log('[03] 工具链多次重试仍未成功（LLM 网关持续故障），截图当前状态')
    await page.screenshot({ path: `${OUT_DIR}/03-chat-agent.png` })

    // ---------- 04-07 其他页面 ----------
    const ACCOUNTS = { user1: ['user1', 'user1123'], admin: ['admin', 'admin123'] }
    async function ensureLogin(user) {
      const [uname, pwd] = ACCOUNTS[user]
      await page.goto(`${BASE}/login`)
      await page.waitForSelector('input[placeholder="用户名"]')
      await sleep(800)
      await page.fill('input[placeholder="用户名"]', uname)
      await page.fill('input[placeholder="密码"]', pwd)
      await page.click('button.login-btn')
      await page.waitForURL('**/chat', { timeout: 15000 })
      await sleep(1200)
    }
    const otherPages = [
      ['04-knowledge', '/knowledge', 'admin'],
      ['05-tickets', '/tickets', 'admin'],
      ['06-orders', '/orders', 'user1'],
      ['07-admin', '/admin', 'admin'],
    ]
    for (const [name, route, user] of otherPages) {
      log(`[${name.slice(0, 2)}] ${route} as ${user}`)
      await ensureLogin(user)
      await page.goto(`${BASE}${route}`)
      await sleep(4000) // 等待异步数据（表格/卡片）渲染
      await page.screenshot({ path: `${OUT_DIR}/${name}.png` })
    }

    log('DONE all screenshots ->', OUT_DIR)
  } finally {
    await browser.close()
  }
}

main().catch((e) => {
  console.error(e)
  process.exit(1)
})