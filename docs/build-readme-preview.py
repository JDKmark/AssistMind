#!/usr/bin/env python
"""README.md -> README.preview.html（屏幕预览 + A4 打印/PDF 友好）

用途：把仓库根目录的 README.md 渲染成一份单文件 HTML 预览，屏幕上看是
GitHub 风格排版，Ctrl+P 打印（或导出 PDF）时按 A4 分页、不溢出、不撕裂。

用法（Windows PowerShell，仓库根目录）：
    & "C:\\Users\\sss208\\.workbuddy\\binaries\\python\\envs\\docsbuild\\Scripts\\python.exe" docs\\build-readme-preview.py

依赖：python-markdown（隔离 venv 内已装）。改版式只需改本文件的 CSS 常量。

约定：
  - README.md 是唯一内容来源，本脚本不写内容、只做渲染，避免两处手工同步。
  - 表格若含图片 -> 视为截图网格（shot-grid），去掉表头、去掉斑马纹、双列铺满。
  - 文档中第一张独立大图 -> 加 hero 类，打印时限制高度，防止超出一页。
  - BREAK_BEFORE_IDS 里的二级标题 -> 打印时整节另起一页。
"""

from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "README.md"
OUT = ROOT / "README.preview.html"

# 这些二级标题在打印时强制另起一页（写标题的锚点 id，由 slugify 生成）
# 目前：首页放标题 + 目录 + 架构图，「界面预览」整节（标题 + 8 张截图）独立成第二页
BREAK_BEFORE_IDS = {"界面预览"}

# ---------------------------------------------------------------------------
# CSS：屏幕样式 + @media print 分页规则
# ---------------------------------------------------------------------------
CSS = """
:root {
  --ink: #1f2328;
  --muted: #59636e;
  --line: #d1d9e0;
  --bg: #f6f8fa;
  --accent: #0969da;
  --code-bg: #f0f2f5;
}

* { box-sizing: border-box; }

body {
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font-family: -apple-system, "Segoe UI", "Microsoft YaHei", system-ui, sans-serif;
  font-size: 16px;
  line-height: 1.65;
  -webkit-text-size-adjust: 100%;
}

.page {
  max-width: 980px;
  margin: 0 auto;
  background: #fff;
  padding: 48px 56px 72px;
}

h1 {
  font-size: 2em;
  margin: 0 0 .4em;
  border-bottom: 1px solid var(--line);
  padding-bottom: .3em;
}
h2 {
  font-size: 1.5em;
  margin: 1.8em 0 .6em;
  border-bottom: 1px solid var(--line);
  padding-bottom: .3em;
}
h3 { font-size: 1.2em; margin: 1.4em 0 .5em; }
h4 { font-size: 1.05em; margin: 1.2em 0 .4em; }

p { margin: .8em 0; }

a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }

code {
  background: var(--code-bg);
  padding: .2em .4em;
  border-radius: 6px;
  font-size: 85%;
  font-family: Consolas, "Courier New", monospace;
}

pre {
  background: var(--bg);
  border: 1px solid var(--line);
  padding: 16px;
  border-radius: 6px;
  overflow-x: auto;
  line-height: 1.5;
}
pre code { background: none; padding: 0; font-size: 13px; }

blockquote {
  margin: 1em 0;
  padding: 0 1em;
  color: var(--muted);
  border-left: .25em solid var(--line);
}

table {
  border-collapse: collapse;
  margin: 14px 0;
  width: 100%;
  font-size: 15px;
}
th, td {
  border: 1px solid var(--line);
  padding: 7px 13px;
  text-align: left;
  vertical-align: top;
}
th { background: var(--bg); font-weight: 600; }
tbody tr:nth-child(2n) { background: #fafbfc; }

img { max-width: 100%; height: auto; border-radius: 6px; }
p > img { border: 1px solid var(--line); }

hr { border: none; border-top: 1px solid var(--line); margin: 2em 0; }

ul, ol { padding-left: 1.6em; }
li { margin: .3em 0; }
li > p { margin: .3em 0; }

/* 截图网格：README「界面预览」那种两列图片表 */
table.shot-grid { border: none; }
table.shot-grid thead { display: none; }
table.shot-grid td {
  border: none;
  width: 50%;
  padding: 10px 12px;
}
table.shot-grid img {
  width: 100%;
  border: 1px solid var(--line);
}

/* 首屏架构图：居中，限高 */
.hero-wrap { text-align: center; margin: 1.4em 0; }
img.hero { border: 1px solid var(--line); }

/* =======================================================================
   打印 / 导出 PDF：A4 分页
   - @page 由浏览器打印对话框与 Playwright 共用
   - 表格行、代码块、图片不跨页撕裂；表头在续页重复
   - pre 自动折行，避免横向溢出被裁掉
   ======================================================================= */
@page {
  size: A4;
  margin: 14mm 12mm 15mm;
}

@media print {
  html, body {
    background: #fff;
    color: #000;
    font-size: 10.5pt;
    line-height: 1.55;
    /* 保留底色（表头/代码块/斑马纹），否则打印出来全白 */
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
  }

  .page {
    max-width: none;
    width: auto;
    margin: 0;
    padding: 0;
    background: #fff;
  }

  /* 标题不与正文分离：标题后至少跟着一行内容 */
  h1, h2, h3, h4, h5, h6 {
    break-after: avoid-page;
    page-break-after: avoid;
    break-inside: avoid;
  }
  /* 指定的章节整节另起一页（避免标题独占上一页底部 + 大片空白） */
  .break-before {
    break-before: page;
    page-break-before: always;
  }
  h1 { font-size: 18pt; margin: 0 0 8pt; }
  h2 { font-size: 14pt; margin: 14pt 0 6pt; }
  h3 { font-size: 11.5pt; margin: 11pt 0 4pt; }
  h4 { font-size: 10.5pt; margin: 9pt 0 3pt; }

  p, li, blockquote { orphans: 3; widows: 3; }
  p { margin: 5pt 0; }

  /* 视觉块不跨页 */
  img, pre, blockquote, figure {
    break-inside: avoid;
    page-break-inside: avoid;
  }
  /* 表格整体允许跨页（长表硬留在下一页会留大片空白），但行不撕裂 + 表头重复 */
  table { break-inside: auto; page-break-inside: auto; width: 100%; font-size: 9pt; margin: 8pt 0; }
  tr, th, td { break-inside: avoid; page-break-inside: avoid; }
  thead { display: table-header-group; }
  tbody tr:nth-child(2n) { background: #f4f6f8 !important; }
  th, td { padding: 3.5pt 6pt; }
  th { background: #eef1f4 !important; }

  /* 代码：折行而非横向溢出；URL 不撑破容器 */
  pre {
    white-space: pre-wrap;
    word-break: break-word;
    overflow: visible;
    padding: 7pt 9pt;
    background: #f6f8fa !important;
  }
  pre code { font-size: 8.5pt; white-space: inherit; }
  code { font-size: 92%; word-break: break-word; }

  a { color: #0b4fa8; }
  /* 不在正文里展开 URL，保持排版干净 */

  blockquote { padding: 0 8pt; margin: 7pt 0; }

  img, img.hero, table.shot-grid img {
    max-width: 100% !important;
    height: auto;
    border: 1px solid var(--line) !important;
  }
  img.hero { max-height: 148mm; width: auto; }

  table.shot-grid { font-size: inherit; }
  table.shot-grid td { border: none; padding: 4pt 5pt; }
  /* 截图网格整体不跨页（4 行 × 约 56mm，一页放得下，拆开会很难看） */
  table.shot-grid { break-inside: avoid; page-break-inside: avoid; }

  ul, ol { padding-left: 1.3em; }
  li { margin: 2pt 0; }

  hr { margin: 10pt 0; }
}
"""


def slugify(text: str, separator: str = "-") -> str:
    """生成锚点 id：保留中英文与数字，去标点（与 README 里的 #中文 目录链接一致）。"""
    text = text.strip().lower()
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = re.sub(r"[\s]+", separator, text)
    text = re.sub(r"-{2,}", separator, text)
    return text.strip(separator)


def mark_shot_grids(html: str) -> str:
    """给「只含图片的表格」加 shot-grid 类（README 界面预览的截图双列）。"""
    out: list[str] = []
    pos = 0
    while True:
        start = html.find("<table>", pos)
        if start < 0:
            out.append(html[pos:])
            break
        end = html.find("</table>", start)
        if end < 0:
            out.append(html[pos:])
            break
        end += len("</table>")
        block = html[start:end]
        if "<img" in block:
            block = block.replace("<table>", '<table class="shot-grid">', 1)
        out.append(html[pos:start])
        out.append(block)
        pos = end
    return "".join(out)


def mark_hero_image(html: str) -> str:
    """首屏架构图加 hero 类并居中（打印时限高，避免撑破一页）。"""
    img = re.compile(r'<img\b([^>]*?)src="(docs/architecture[^"]*)"\s*/?>')

    def add_class(m: re.Match[str]) -> str:
        attrs = m.group(1)
        if "class=" in attrs:
            return m.group(0)
        return f'<img class="hero"{attrs}src="{m.group(2)}">'

    html = img.sub(add_class, html)
    # 顺带把承载它的一段标记为居中容器
    html = html.replace('<p><img class="hero"', '<p class="hero-wrap"><img class="hero"')
    return html


def apply_page_breaks(html: str) -> str:
    """给 BREAK_BEFORE_IDS 里的二级标题加 break-before 类（打印时另起一页）。"""
    for anchor in BREAK_BEFORE_IDS:
        html = re.sub(
            rf'<h2 id="{re.escape(anchor)}">',
            f'<h2 class="break-before" id="{anchor}">',
            html,
        )
    return html


def build() -> Path:
    if not SRC.exists():
        sys.exit(f"找不到源文件：{SRC}")

    text = SRC.read_text(encoding="utf-8")

    md = markdown.Markdown(
        extensions=["extra", "toc", "sane_lists", "nl2br"],
        extension_configs={"toc": {"slugify": slugify, "permalink": False}},
        output_format="html5",
    )
    body = md.convert(text)
    body = mark_shot_grids(body)
    body = mark_hero_image(body)
    body = apply_page_breaks(body)

    title = "AssistMind —— 电商智能客服系统"
    generated = date.today().isoformat()

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}（README 预览）</title>
<!-- 自动生成：由 README.md 经 docs/build-readme-preview.py 渲染，请勿手改本文件 -->
<meta name="generator" content="docs/build-readme-preview.py">
<meta name="source" content="README.md">
<meta name="generated" content="{generated}">
<style>{CSS}</style>
</head>
<body>
<div class="page">
{body}</div>
</body>
</html>
"""

    OUT.write_text(html, encoding="utf-8")
    return OUT


if __name__ == "__main__":
    path = build()
    size_kb = path.stat().st_size / 1024
    print(f"OK  {path}  ({size_kb:.1f} KB)")
