/**
 * README.preview.html -> README.preview.pdf（A4，背景色保留，页码页脚）
 *
 * 前置：先跑 docs/build-readme-preview.py 生成 HTML。
 * 用法（Windows PowerShell，仓库根目录）：
 *   $env:NODE_PATH = npm root -g
 *   node docs\print-readme-preview.cjs
 *
 * 说明：分页/不撕裂等规则全部写在 HTML 的 @media print 里，本脚本只负责
 * 用 Chromium 打印引擎落地成 PDF（printBackground + preferCSSPageSize）。
 * 浏览器里手动打印（Ctrl+P → 目标"另存为 PDF"→ 边距"默认"→ 勾选"背景图形"）
 * 得到的是同一份版式。
 */
const path = require('node:path')
const fs = require('node:fs')

// playwright 可能装在全局 npm 根（不同 node 版本前缀不同），逐个候选解析
function loadPlaywright() {
  const staticCandidates = [
    'playwright',
    'playwright-core',
    path.join(process.env.APPDATA || '', 'npm/node_modules/playwright'),
    path.join(process.env.LOCALAPPDATA || '', 'npm-cache/_npx/latest/node_modules/playwright'),
  ]
  for (const c of staticCandidates) {
    try {
      return require(c)
    } catch {
      /* 试下一个 */
    }
  }
  throw new Error(
    '未找到 playwright：请执行  npm i -g playwright  然后  npx playwright install chromium',
  )
}

const { chromium } = loadPlaywright()

const ROOT = 'D:/2026/AssistMind'
const SRC = path.join(ROOT, 'README.preview.html')
const OUT = path.join(ROOT, 'README.preview.pdf')

const FOOTER = `
<div style="width:100%;font-family:'Microsoft YaHei',sans-serif;font-size:8px;color:#8b949e;
            padding:0 12mm;display:flex;justify-content:space-between;">
  <span>AssistMind · 电商智能客服系统</span>
  <span>第 <span class="pageNumber"></span> / <span class="totalPages"></span> 页</span>
</div>`

async function main() {
  if (!fs.existsSync(SRC)) throw new Error(`缺少源文件：${SRC}（先跑 docs/build-readme-preview.py）`)

  const browser = await chromium.launch()
  try {
    const page = await browser.newPage()
    await page.goto('file:///' + SRC.replace(/\\/g, '/'), { waitUntil: 'load' })

    // 等所有图片解码完成，否则 PDF 里会出现空白图位
    await page.evaluate(async () => {
      await Promise.all(
        [...document.images].filter((i) => !i.complete).map(
          (i) => new Promise((r) => { i.addEventListener('load', r); i.addEventListener('error', r) }),
        ),
      )
    })

    await page.emulateMedia({ media: 'print' })

    const opts = {
      printBackground: true,
      preferCSSPageSize: true, // 采用 CSS @page（A4 + 14/12/15mm 边距）
      displayHeaderFooter: true,
      headerTemplate: '<div></div>',
      footerTemplate: FOOTER,
      margin: { top: '0', bottom: '0', left: '0', right: '0' },
    }

    // 直接写目标文件会撞上 Windows 文件锁（EBUSY：PDF 阅读器/杀软实时扫描都会占用）
    // ——先写临时文件再替换；仍被占用则另存为 *.new.pdf，不中断整条流水线。
    const tmp = `${OUT}.tmp.pdf`
    await page.pdf({ ...opts, path: tmp })

    let target = OUT
    try {
      await replaceWithRetry(tmp, OUT)
    } catch (e) {
      target = OUT.replace(/\.pdf$/i, '.new.pdf')
      fs.rmSync(target, { force: true })
      fs.renameSync(tmp, target)
      console.warn(`WARN 原文件被占用（${e.cause || e.message}）`)
      console.warn(`     新版已另存为：${target}`)
      console.warn('     关闭占用它的阅读器后重跑本脚本即可覆盖原文件。')
    }

    const { size } = fs.statSync(target)
    console.log(`OK  ${target}  (${(size / 1024).toFixed(1)} KB)`)
  } finally {
    await browser.close()
  }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

/** 用 tmp 覆盖 target，最多重试 8 次（每次间隔 500ms）以绕过瞬时占用 */
async function replaceWithRetry(tmp, target) {
  let lastErr
  for (let i = 1; i <= 8; i += 1) {
    try {
      fs.rmSync(target, { force: true })
      fs.renameSync(tmp, target)
      return
    } catch (e) {
      lastErr = e
      await sleep(500)
    }
  }
  const err = new Error(`无法覆盖 ${target}`)
  err.cause = lastErr.message
  throw err
}

main().catch((e) => {
  console.error(e)
  process.exit(1)
})
