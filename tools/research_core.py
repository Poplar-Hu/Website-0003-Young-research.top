#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
research.who-young.top —— 生成核心
============================================================================
把 content/ 里的「项目元数据 + Markdown 正文」编译成可以直接发布的静态站点：

    content/_site.json         站点级设置（外链、目录预览条数……）
    content/<slug>/meta.json   项目元数据（标题 / 日期 / 状态 / 标签 / 摘要……）
    content/<slug>/article.md  正文（Markdown）
    content/<slug>/images/     插图

    → /<slug>/index.html       项目页（长文 + 侧栏目录）
    → /index.html              首页（卡片 + 目录预览，全部自动产出）
    → /404.html  /sitemap.xml  /robots.txt

设计约束
1. **只用标准库**。写字板要在没装任何第三方包的机器上直接跑起来，
   所以 Markdown 渲染器是自己写的，不依赖 markdown / pygments。
2. **不引用外部 CDN**。代码高亮用本地 assets/vendor/highlight.min.js，
   图表用本地 mermaid.min.js。离线可打开。
3. **HTML 里不写行内样式**，一切视觉状态靠类名（样式在 assets/research.css）。
4. 生成物全部提交进仓库 —— 部署方式是「从分支部署」，线上不需要构建。

命令行用法（写字板是主要入口，这里主要是给批量和排错用）：

    python tools/research_core.py build            重新生成整站
    python tools/research_core.py build <slug>     只重新生成某个项目 + 首页
    python tools/research_core.py check            自检（链接 / i18n / 外链 / 行内样式）
    python tools/research_core.py new <slug> [标题] 新建一个项目骨架
    python tools/research_core.py list             列出所有项目
"""

from __future__ import annotations

import argparse
import datetime as _dt
import html
import json
import re
import shutil
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

# ══════════════════════════════════════════════════════════════════════════
#  路径与常量
# ══════════════════════════════════════════════════════════════════════════

ROOT = Path(__file__).resolve().parent.parent
CONTENT_DIR = ROOT / "content"
SITE_JSON = CONTENT_DIR / "_site.json"
TRASH_DIR = ROOT / ".writepad-trash"
TOOLS_DIR = ROOT / "tools"

ARTICLES = {"cn": "article.md", "en": "article.en.md", "jp": "article.jp.md"}
#: 项目页的落地路径：中文是根，其它语言进子目录
LANG_SUBDIR = {"cn": "", "en": "en/", "jp": "jp/"}
LANG_LABEL_KEY = {"cn": "lang-cn", "en": "lang-en", "jp": "lang-jp"}
#: 语言名是无 JS 时的兜底原文，必须与 lang/cn.json 里同名键一致
LANG_FALLBACK = {"cn": "中文", "en": "English", "jp": "日本語"}

STATUSES = ("active", "done", "paused", "planned", "archived")
COLORS = ("blue", "pink", "green", "lav", "amber", "gray")

#: 写字板里可选的图标（Font Awesome 4.7 里有、且跟科研沾边的）
ICON_CHOICES = (
    "fa-flask", "fa-microscope", "fa-eyedropper", "fa-thermometer-half",
    "fa-bolt", "fa-cube", "fa-cogs", "fa-gears", "fa-magic", "fa-puzzle-piece",
    "fa-line-chart", "fa-area-chart", "fa-bar-chart", "fa-pie-chart",
    "fa-table", "fa-calculator", "fa-superscript", "fa-percent",
    "fa-database", "fa-server", "fa-cloud", "fa-hdd-o",
    "fa-code", "fa-terminal", "fa-laptop", "fa-desktop", "fa-mobile",
    "fa-file-text-o", "fa-book", "fa-bookmark", "fa-sticky-note", "fa-clipboard",
    "fa-camera", "fa-picture-o", "fa-video-camera", "fa-eye",
    "fa-rocket", "fa-plane", "fa-globe", "fa-map-o", "fa-compass",
    "fa-leaf", "fa-tint", "fa-fire", "fa-snowflake-o", "fa-sun-o",
    "fa-heartbeat", "fa-stethoscope", "fa-user-md", "fa-medkit",
    "fa-lightbulb-o", "fa-star", "fa-flag", "fa-check-circle", "fa-question-circle",
)

#: 根目录下不能被项目 slug 占用的名字（它们各有用途）
RESERVED_SLUGS = {
    "assets", "lang", "content", "tools", "images", "index.html", "404.html",
    "sitemap.xml", "robots.txt", "cname", "readme.md", "en", "jp",
}

#: 每个项目目录里允许出现的文件（写 README 时用，也是自检的依据）
RECORD_FILES = ("meta.json", "article.md", "article.en.md", "article.jp.md")

SITE_DEFAULTS = {
    "base_url": "https://research.who-young.top",
    "main_site": "https://who-young.top",
    "tools_site": "https://tools.who-young.top",
    "contact_url": "https://who-young.top/#contact",
    "repo_url": "https://github.com/Poplar-Hu/Website-0003-Young-research.top",
    # 首页卡片上最多列几节目录
    "toc_preview_limit": 6,
    "show_stats": True,
    "show_catalog": True,
    "default_icon": "fa-flask",
    "default_color": "blue",
    "default_status": "active",
}

META_DEFAULTS = {
    "title": "",
    "slug": "",
    "date": "",
    "updated": "",
    "status": "active",
    "tags": [],
    "summary": "",
    "icon": "fa-flask",
    "color": "blue",
    "cover": "",
    "order": 100,
    "pinned": False,
}


# ══════════════════════════════════════════════════════════════════════════
#  小工具
# ══════════════════════════════════════════════════════════════════════════

def today() -> str:
    return _dt.date.today().isoformat()


def esc(text) -> str:
    """转义文本节点"""
    return html.escape(str(text), quote=False)


def attr(text) -> str:
    """转义属性值"""
    return html.escape(str(text), quote=True)


def read_text(path: Path) -> str:
    """读文本，强制 UTF-8 并把行尾统一成 \\n"""
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")


def write_text(path: Path, text: str) -> None:
    """写文本，强制 UTF-8 + LF（仓库里 .gitattributes 统一按 LF 存）"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def read_json(path: Path, default=None):
    if not path.exists():
        return {} if default is None else default
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")


def load_site_config() -> dict:
    cfg = dict(SITE_DEFAULTS)
    cfg.update(read_json(SITE_JSON) or {})
    return cfg


def valid_slug(slug: str) -> bool:
    """slug 只允许小写字母、数字和连字符，且不能是保留字。

    刻意不允许中文：GitHub Pages 能把中文路径服务出去，但地址会变成
    一长串 %E8%96%84%E8%86%9C… 的百分号编码，分享和记笔记时都很难用。
    中文标题写在 meta.json 的 title 里，展示不受影响。
    """
    if not slug or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        return False
    return slug not in RESERVED_SLUGS


def slugify(text: str, fallback: str = "record") -> str:
    """把一段文本压成合法 slug（中文会被丢掉，只剩 ASCII 部分）"""
    # 去掉变音符号，让 é → e
    text = unicodedata.normalize("NFKD", str(text))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    if not text:
        text = fallback
    return text[:60].strip("-")


def safe_url(url: str) -> str:
    """只放行常见协议，挡掉 javascript: 之类的注入"""
    url = (url or "").strip()
    if not url:
        return "#"
    if url.startswith("#") or url.startswith("/"):
        return url
    if re.match(r"^(https?:|mailto:|tel:|\.\.?/)", url, re.I):
        return url
    # 没写协议的相对路径（images/x.png）也算合法
    if re.match(r"^[\w.\-]+/", url) or re.match(r"^[\w.\-]+\.(png|jpe?g|gif|webp|svg|pdf)$", url, re.I):
        return url
    return "#"


def human_date(value: str) -> str:
    return value or "—"


def char_stats(body: str) -> tuple[int, int]:
    """(字数, 预计阅读分钟数)

    中文按「字」算、英文按「词」算，两者相加当作字数。
    围栏代码块在统计前先剔除 —— 整段 Python 代码算进「阅读时长」没有意义。
    """
    text = re.sub(r"^[ \t]*(?:```|~~~).*?^[ \t]*(?:```|~~~)[ \t]*$", "", body, flags=re.S | re.M)
    text = re.sub(r"`[^`]*`", "", text)                 # 行内代码
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)  # 链接 / 图片只留文字
    cjk = len(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]", text))
    words = len(re.findall(r"[A-Za-z0-9][A-Za-z0-9'\-]*", text))
    chars = cjk + words
    minutes = max(1, round(chars / 400)) if chars else 0
    return chars, minutes


# ══════════════════════════════════════════════════════════════════════════
#  Markdown 渲染器
# ══════════════════════════════════════════════════════════════════════════

RE_FENCE = re.compile(r"^[ \t]*(`{3,}|~{3,})[ \t]*([\w+#.\-]*)[ \t]*$")
RE_HEADING = re.compile(r"^(#{1,6})[ \t]+(.*?)[ \t]*#*[ \t]*$")
RE_HR = re.compile(r"^[ \t]*([-*_])[ \t]*(?:\1[ \t]*){2,}$")
RE_QUOTE = re.compile(r"^[ \t]*>[ \t]?(.*)$")
RE_ITEM = re.compile(r"^([ \t]*)([-*+]|\d{1,9}[.)])[ \t]+(.*)$")
RE_DELIM = re.compile(r"^[ \t]*\|?[ \t]*:?-{1,}:?[ \t]*(\|[ \t]*:?-{1,}:?[ \t]*)*\|?[ \t]*$")
RE_IMG = re.compile(r"!\[([^\]]*)\]\(\s*([^\s)]+)(?:\s+[\"']([^\"']*)[\"'])?\s*\)")
RE_LINK = re.compile(r"\[([^\]]*)\]\(\s*([^\s)]+)(?:\s+[\"']([^\"']*)[\"'])?\s*\)")
RE_AUTOLINK = re.compile(r"<((?:https?|mailto):[^>\s]+)>")
RE_BARE_URL = re.compile(r"(?<![\w\"'=(/])(https?://[^\s<>\"']+)")

#: 这些字符出现在末尾时属于「句子的标点」，不是 URL 的一部分
URL_TRAILING = ".,;:!?。，；：！？、）]}>》」』”’"
RE_HTML_BLOCK = re.compile(r"^</?[a-zA-Z][\w-]*(?:\s|>|/>|$)")


class Markdown:
    """Markdown 子集 → HTML

    支持：标题（自动生成锚点）、段落、粗体/斜体/删除线、行内代码、
    链接与图片、围栏代码块（含 mermaid）、引用、有序/无序/任务列表、
    表格（含对齐）、分隔线、独立成段的图片自动变成带编号的 <figure>。

    刻意不支持：行内 HTML 之外的 HTML 块会原样透传（自己写的，风险自负）、
    数学公式、脚注、定义列表 —— 科研记录用不到，写进来只会让渲染器变复杂。
    """

    def __init__(self):
        self.headings: list[dict] = []
        self.used_anchors: set[str] = set()
        self.has_code = False
        self.has_mermaid = False
        self.figure_count = 0

    # ── 对外入口 ─────────────────────────────────────────────────────────
    def render(self, text: str) -> str:
        self.headings = []
        self.used_anchors = set()
        self.has_code = False
        self.has_mermaid = False
        self.figure_count = 0
        return "\n".join(self._blocks(text.split("\n")))

    def toc_tree(self) -> list[dict]:
        """把标题列表折成两级树，供侧栏目录使用（只认 2、3 级）"""
        tree: list[dict] = []
        for head in self.headings:
            if head["level"] == 2:
                tree.append({**head, "children": []})
            elif head["level"] == 3 and tree:
                tree[-1]["children"].append(head)
        return tree

    def sections(self) -> list[dict]:
        """首页卡片与完整目录用的二级标题清单"""
        return [h for h in self.headings if h["level"] == 2]

    # ── 块级 ─────────────────────────────────────────────────────────────
    def _blocks(self, lines: list[str]) -> list[str]:
        out: list[str] = []
        i = 0
        total = len(lines)
        while i < total:
            line = lines[i]

            # 空行
            if not line.strip():
                i += 1
                continue

            # 围栏代码
            fence = RE_FENCE.match(line)
            if fence:
                marker, lang = fence.group(1), fence.group(2).strip()
                i += 1
                buf: list[str] = []
                while i < total and not re.match(
                    r"^[ \t]*" + re.escape(marker[0]) + r"{" + str(len(marker)) + r",}[ \t]*$", lines[i]
                ):
                    buf.append(lines[i])
                    i += 1
                i += 1  # 跳过收尾围栏（没写也不报错，按到文件末尾处理）
                out.append(self._code_block("\n".join(buf), lang))
                continue

            # 标题
            head = RE_HEADING.match(line)
            if head:
                out.append(self._heading(len(head.group(1)), head.group(2).strip()))
                i += 1
                continue

            # 分隔线
            if RE_HR.match(line):
                out.append("<hr>")
                i += 1
                continue

            # 引用（连续的 > 行）
            if RE_QUOTE.match(line):
                buf = []
                while i < total and (RE_QUOTE.match(lines[i]) or (buf and lines[i].strip() and not self._starts_block(lines[i]))):
                    m = RE_QUOTE.match(lines[i])
                    buf.append(m.group(1) if m else lines[i])
                    i += 1
                inner = "\n".join(self._blocks(buf))
                out.append(f"<blockquote>\n{inner}\n</blockquote>")
                continue

            # 表格：本行有 | 且下一行是分隔行
            if "|" in line and i + 1 < total and RE_DELIM.match(lines[i + 1]) and "-" in lines[i + 1]:
                html_table, i = self._table(lines, i)
                out.append(html_table)
                continue

            # 列表
            if RE_ITEM.match(line):
                html_list, i = self._list(lines, i)
                out.append(html_list)
                continue

            # 原始 HTML 块：整段原样透传，直到空行
            if RE_HTML_BLOCK.match(line):
                buf = []
                while i < total and lines[i].strip():
                    buf.append(lines[i])
                    i += 1
                out.append("\n".join(buf))
                continue

            # 段落
            buf = []
            while i < total and lines[i].strip() and not self._starts_block(lines[i]):
                buf.append(lines[i])
                i += 1
            out.append(self._paragraph(buf))
        return out

    def _starts_block(self, line: str) -> bool:
        """判断这一行是否开启了一个新的块（段落遇到它就该收尾）"""
        if RE_FENCE.match(line) or RE_HEADING.match(line) or RE_HR.match(line):
            return True
        if RE_QUOTE.match(line) or RE_ITEM.match(line):
            return True
        if RE_HTML_BLOCK.match(line):
            return True
        return False

    def _paragraph(self, lines: list[str]) -> str:
        """段落。单独一行且只有一张图片时升级成 <figure>"""
        raw = "\n".join(lines)
        # 行尾两个空格 = 硬换行
        raw = re.sub(r"[ \t]{2,}\n", "<br>\n", raw)
        raw = re.sub(r"\\\n", "<br>\n", raw)

        joined = raw.strip()
        img = RE_IMG.fullmatch(joined)
        if img and "\n" not in joined:
            return self._figure(img.group(2), img.group(1), img.group(3) or "")

        # <br> 是刚刚塞进去的，别被转义掉
        parts = re.split(r"(<br>)", joined)
        body = "".join(part if part == "<br>" else self._inline(part) for part in parts)
        return f"<p>{body}</p>"

    def _figure(self, src: str, alt: str, caption: str) -> str:
        self.figure_count += 1
        cap = caption or alt or ""
        # 作者自己写了「图 1」就不再重复编号
        if re.match(r"^\s*图\s*\d+", cap):
            caption_html = self._inline(cap)
        elif cap:
            caption_html = f'<span class="fig-n">图 {self.figure_count}</span>{self._inline(cap)}'
        else:
            caption_html = f'<span class="fig-n">图 {self.figure_count}</span>'
        url = attr(safe_url(src))
        img = f'<img src="{url}" alt="{attr(alt)}" loading="lazy">'
        return (
            '<figure class="fig">\n'
            f'<a class="fig-zoom" href="{url}">{img}</a>\n'
            f"<figcaption>{caption_html}</figcaption>\n"
            "</figure>"
        )

    def _heading(self, level: int, text: str) -> str:
        anchor = self._anchor(text)
        self.headings.append({"level": level, "title": self._plain(text), "anchor": anchor})
        inner = self._inline(text)
        link = f'<a class="h-anchor" href="#{attr(anchor)}" aria-hidden="true"><i class="fa fa-link"></i></a>'
        return f'<h{level} id="{attr(anchor)}">{inner}{link}</h{level}>'

    def _code_block(self, code: str, lang: str) -> str:
        code = code.rstrip("\n")
        if lang.lower() == "mermaid":
            self.has_mermaid = True
            return (
                '<div class="mermaid-wrap is-loading">\n'
                f'<div class="mermaid">{esc(code)}</div>\n'
                "</div>"
            )
        self.has_code = True
        cls = f' class="language-{attr(lang)}"' if lang else ""
        label = lang or "text"
        plain = "" if lang else " is-plain"
        copy_btn = (
            '<button class="code-copy" type="button" data-i18n="code-copy">复制</button>'
        )
        return (
            f'<div class="code-block{plain}">\n'
            '<div class="code-bar">'
            f'<span class="code-lang">{esc(label)}</span>'
            '<span class="spacer"></span>'
            f"{copy_btn}"
            "</div>\n"
            f"<pre><code{cls}>{esc(code)}</code></pre>\n"
            "</div>"
        )

    def _table(self, lines: list[str], start: int) -> tuple[str, int]:
        def cells(row: str) -> list[str]:
            row = row.strip()
            if row.startswith("|"):
                row = row[1:]
            if row.endswith("|") and not row.endswith("\\|"):
                row = row[:-1]
            # 支持 \| 转义
            row = row.replace("\\|", "\x00PIPE\x00")
            return [c.strip().replace("\x00PIPE\x00", "|") for c in row.split("|")]

        header = cells(lines[start])
        aligns: list[str] = []
        for spec in cells(lines[start + 1]):
            left, right = spec.startswith(":"), spec.endswith(":")
            if left and right:
                aligns.append("center")
            elif right:
                aligns.append("right")
            else:
                aligns.append("left")

        rows: list[list[str]] = []
        i = start + 2
        while i < len(lines) and "|" in lines[i] and lines[i].strip():
            rows.append(cells(lines[i]))
            i += 1

        def cell(tag: str, content: str, index: int) -> str:
            align = aligns[index] if index < len(aligns) else "left"
            cls = f' class="align-{align}"' if align != "left" else ""
            return f"<{tag}{cls}>{self._inline(content)}</{tag}>"

        parts = ["<div class=\"table-scroll\">", "<table>", "<thead>", "<tr>"]
        for idx, celltext in enumerate(header):
            parts.append(cell("th", celltext, idx))
        parts += ["</tr>", "</thead>", "<tbody>"]
        for row in rows:
            parts.append("<tr>")
            for idx in range(len(header)):
                parts.append(cell("td", row[idx] if idx < len(row) else "", idx))
            parts.append("</tr>")
        parts += ["</tbody>", "</table>", "</div>"]
        return "".join(parts), i

    def _list(self, lines: list[str], start: int) -> tuple[str, int]:
        base = len(lines[start]) - len(lines[start].lstrip())
        first = RE_ITEM.match(lines[start])
        ordered = bool(first and first.group(2)[0].isdigit())

        items: list[list[str]] = []
        i = start
        total = len(lines)
        while i < total:
            line = lines[i]
            if not line.strip():
                # 空行之后若还是同级列表项，说明是「松散列表」，继续
                j = i
                while j < total and not lines[j].strip():
                    j += 1
                if j < total and RE_ITEM.match(lines[j]) and self._indent(lines[j]) >= base:
                    i = j
                    continue
                break
            m = RE_ITEM.match(line)
            if not m:
                if self._indent(line) > base and items:
                    items[-1].append(line)
                    i += 1
                    continue
                break
            indent = len(m.group(1).expandtabs(4))
            if indent < base:
                break
            if indent > base and items:
                items[-1].append(line)
                i += 1
                continue
            items.append([m.group(3)])
            i += 1

        task = all(re.match(r"^\[[ xX]\]\s", it[0]) for it in items) if items else False
        rendered: list[str] = []
        for item in items:
            first_line = item[0]
            checked = None
            tm = re.match(r"^\[([ xX])\]\s+(.*)$", first_line)
            if tm:
                checked = tm.group(1).lower() == "x"
                first_line = tm.group(2)
            inner_lines = [first_line] + [self._dedent(x, base + 2) for x in item[1:]]
            inner = self._blocks(inner_lines)
            body = "\n".join(inner).strip()
            # 紧凑列表：只有一个段落就不套 <p>
            single = re.fullmatch(r"<p>(.*)</p>", body, flags=re.S)
            if single:
                body = single.group(1)
            if checked is not None:
                box = '<input type="checkbox" disabled' + (" checked" if checked else "") + ">"
                body = f"{box}<span>{body}</span>"
            rendered.append(f"<li>{body}</li>")

        tag = "ol" if ordered else "ul"
        cls = ' class="task-list"' if task else ""
        start_attr = ""
        if ordered:
            sm = re.match(r"^(\d+)", first.group(2))
            if sm and sm.group(1) != "1":
                start_attr = f' start="{int(sm.group(1))}"'
        items_html = "\n".join(rendered)
        return f"<{tag}{cls}{start_attr}>\n{items_html}\n</{tag}>", i

    @staticmethod
    def _indent(line: str) -> int:
        return len(line) - len(line.lstrip())

    @staticmethod
    def _dedent(line: str, width: int) -> str:
        """按显示宽度去掉行首缩进（Tab 记 4 格），嵌套列表才会落在正确的层级上"""
        cut = 0
        count = 0
        while cut < len(line) and count < width and line[cut] in " \t":
            count += 4 if line[cut] == "\t" else 1
            cut += 1
        return line[cut:]

    # ── 行内 ─────────────────────────────────────────────────────────────
    def _inline(self, text: str, depth: int = 0) -> str:
        if depth > 6:
            return esc(text)
        stash: dict[str, str] = {}
        counter = [0]

        def keep(fragment: str) -> str:
            key = f"\x00{counter[0]}\x00"
            counter[0] += 1
            stash[key] = fragment
            return key

        # 1. 反斜杠转义
        text = re.sub(
            r"\\([\\`*_{}\[\]()#+\-.!~|>])",
            lambda m: keep(esc(m.group(1))),
            text,
        )
        # 2. 行内代码
        text = re.sub(
            r"(`+)(.+?)\1",
            lambda m: keep(f"<code>{esc(m.group(2).strip())}</code>"),
            text,
            flags=re.S,
        )
        # 3. 图片
        text = RE_IMG.sub(
            lambda m: keep(
                f'<img src="{attr(safe_url(m.group(2)))}" alt="{attr(m.group(1))}"'
                + (f' title="{attr(m.group(3))}"' if m.group(3) else "")
                + " loading=\"lazy\">"
            ),
            text,
        )
        # 4. 链接
        text = RE_LINK.sub(lambda m: self._link(m, depth, keep), text)
        # 5. 尖括号自动链接
        text = RE_AUTOLINK.sub(
            lambda m: keep(f'<a href="{attr(safe_url(m.group(1)))}">{esc(m.group(1))}</a>'),
            text,
        )
        # 6. 裸 URL（放在最后，避免把已经处理过的链接再啃一遍）
        text = RE_BARE_URL.sub(lambda m: self._bare_url(m, keep), text)

        # 7. 剩下的纯文本整体转义
        text = esc(text)

        # 8. 强调（在转义后的文本上做，标记符不受影响）
        text = re.sub(r"\*\*\*(.+?)\*\*\*", r"<strong><em>\1</em></strong>", text, flags=re.S)
        text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text, flags=re.S)
        text = re.sub(r"~~(.+?)~~", r"<del>\1</del>", text, flags=re.S)
        text = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", text, flags=re.S)
        text = re.sub(r"(?<![\w_])_(?!\s)(.+?)(?<!\s)_(?![\w_])", r"<em>\1</em>", text, flags=re.S)

        # 9. 还原占位符
        for key, fragment in stash.items():
            text = text.replace(key, fragment)
        return text

    def _bare_url(self, m: re.Match, keep) -> str:
        """裸 URL。先把尾部的标点摘下来再判断 —— 中文句号「。」经常紧跟在
        网址后面，直接贪婪匹配会把句号也变成链接的一部分，点开就 404。
        右括号要额外看一眼配对：维基百科那种 /wiki/Foo_(bar) 的尾巴是 URL 本身的。
        """
        url = m.group(1)
        trailing = ""
        while url:
            ch = url[-1]
            if ch == ")" and url.count("(") >= url.count(")"):
                break
            if ch in URL_TRAILING:
                trailing = ch + trailing
                url = url[:-1]
                continue
            break
        if len(url) < 9:      # 至少 https://a.b
            return m.group(0)
        return keep(f'<a href="{attr(safe_url(url))}">{esc(url)}</a>') + esc(trailing)

    def _link(self, m: re.Match, depth: int, keep) -> str:
        label = self._inline(m.group(1), depth + 1)
        url = attr(safe_url(m.group(2)))
        title = f' title="{attr(m.group(3))}"' if m.group(3) else ""
        external = ' target="_blank" rel="noopener"' if re.match(r"^https?://", m.group(2)) else ""
        return keep(f'<a href="{url}"{title}{external}>{label}</a>')

    def _plain(self, text: str) -> str:
        """把标题里的行内标记剥掉，只留纯文本（做锚点和目录用）"""
        text = re.sub(r"`([^`]*)`", r"\1", text)
        text = RE_IMG.sub(lambda m: m.group(1), text)
        text = RE_LINK.sub(lambda m: m.group(1), text)
        text = re.sub(r"[*_~]", "", text)
        text = re.sub(r"<[^>]+>", "", text)
        return html.unescape(text).strip()

    def _anchor(self, text: str) -> str:
        plain = self._plain(text)
        slug = re.sub(r"[^\w\-]+", "-", plain, flags=re.U).strip("-").lower()
        if not slug:
            slug = f"sec-{len(self.headings) + 1}"
        base = slug
        n = 2
        while slug in self.used_anchors:
            slug = f"{base}-{n}"
            n += 1
        self.used_anchors.add(slug)
        return slug


# ══════════════════════════════════════════════════════════════════════════
#  数据模型
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class Record:
    """一个实验项目"""
    slug: str
    directory: Path
    meta: dict = field(default_factory=dict)
    body: str = ""

    @property
    def title(self) -> str:
        return self.meta.get("title") or self.slug

    @property
    def status(self) -> str:
        value = self.meta.get("status") or "active"
        return value if value in STATUSES else "active"

    @property
    def tags(self) -> list[str]:
        tags = self.meta.get("tags") or []
        if isinstance(tags, str):
            tags = [t.strip() for t in re.split(r"[,，、]", tags) if t.strip()]
        return [str(t).strip() for t in tags if str(t).strip()]

    @property
    def color(self) -> str:
        value = self.meta.get("color") or "blue"
        return value if value in COLORS else "blue"

    @property
    def icon(self) -> str:
        return self.meta.get("icon") or "fa-flask"

    @property
    def date(self) -> str:
        return self.meta.get("date") or ""

    @property
    def updated(self) -> str:
        return self.meta.get("updated") or self.date

    @property
    def pinned(self) -> bool:
        return bool(self.meta.get("pinned"))

    @property
    def order(self) -> int:
        try:
            return int(self.meta.get("order", 100))
        except (TypeError, ValueError):
            return 100

    @property
    def summary(self) -> str:
        return self.meta.get("summary") or ""

    def sort_key(self):
        # 置顶 → order 小的靠前 → 日期新的靠前 → 标题
        return (0 if self.pinned else 1, self.order, _neg_date(self.updated), self.title)

    def article_path(self, lang: str = "cn") -> Path:
        return self.directory / ARTICLES[lang]

    def translations(self) -> list[str]:
        """这个项目实际存在哪些语言版本（中文一定有）"""
        found = []
        for lang in ("cn", "en", "jp"):
            if self.article_path(lang).exists():
                found.append(lang)
        if "cn" not in found:
            found.insert(0, "cn")
        return found


def _neg_date(value: str):
    """把日期转成可倒序排序的字符串：2026-10-05 → '0000-00-00' 反排"""
    value = (value or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return "".join(chr(ord("9") - (ord(c) - ord("0"))) if c.isdigit() else c for c in value)
    return "9999-99-99"


# ══════════════════════════════════════════════════════════════════════════
#  读写项目
# ══════════════════════════════════════════════════════════════════════════

def list_slugs() -> list[str]:
    if not CONTENT_DIR.exists():
        return []
    out = []
    for entry in sorted(CONTENT_DIR.iterdir()):
        if entry.is_dir() and not entry.name.startswith(("_", ".")):
            out.append(entry.name)
    return out


def load_record(slug: str, lang: str = "cn") -> Record:
    directory = CONTENT_DIR / slug
    meta = dict(META_DEFAULTS)
    meta.update(read_json(directory / "meta.json") or {})
    meta["slug"] = slug
    body = read_text(directory / ARTICLES[lang])
    return Record(slug=slug, directory=directory, meta=meta, body=body)


def load_records() -> list[Record]:
    records = [load_record(slug) for slug in list_slugs()]
    return sorted(records, key=lambda r: r.sort_key())


def save_record(slug: str, meta: dict, body: str, lang: str = "cn") -> Path:
    """写回元数据与正文。meta 里的 slug 以参数为准。"""
    if not valid_slug(slug):
        raise ValueError(f"不合法的 slug：{slug!r}（只允许小写字母、数字和连字符，且不能重名保留字）")

    clean = dict(META_DEFAULTS)
    clean.update(meta or {})
    clean["slug"] = slug
    clean["title"] = (clean.get("title") or "").strip() or slug
    clean["status"] = clean.get("status") if clean.get("status") in STATUSES else "active"
    clean["color"] = clean.get("color") if clean.get("color") in COLORS else "blue"
    clean["tags"] = [str(t).strip() for t in (clean.get("tags") or []) if str(t).strip()]
    try:
        clean["order"] = int(clean.get("order", 100))
    except (TypeError, ValueError):
        clean["order"] = 100
    clean["pinned"] = bool(clean.get("pinned"))
    if not clean.get("date"):
        clean["date"] = today()

    directory = CONTENT_DIR / slug
    directory.mkdir(parents=True, exist_ok=True)
    write_json(directory / "meta.json", clean)
    write_text(directory / ARTICLES[lang], body if body.endswith("\n") or not body else body + "\n")
    return directory


def create_record(slug: str, title: str = "", status: str = "active", date: str = "") -> Record:
    """新建项目骨架（正文带一份科研记录的常用小节模板）"""
    if not valid_slug(slug):
        raise ValueError(f"不合法的 slug：{slug!r}")
    if (CONTENT_DIR / slug).exists():
        raise FileExistsError(f"项目已存在：{slug}")

    meta = dict(META_DEFAULTS)
    meta.update({
        "title": title or slug,
        "slug": slug,
        "date": date or today(),
        "updated": date or today(),
        "status": status if status in STATUSES else "active",
        "tags": [],
        "summary": "",
    })
    save_record(slug, meta, ARTICLE_TEMPLATE.format(title=title or slug))
    return load_record(slug)


def delete_record(slug: str) -> Path:
    """删除项目：挪进回收站，不真删。

    回收站放在仓库根目录的 .writepad-trash/ 且已写进 .gitignore，
    因为它没被 Git 跟踪，也就不会随「从分支部署」被发布出去。
    """
    source = CONTENT_DIR / slug
    if not source.exists():
        raise FileNotFoundError(f"项目不存在：{slug}")
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    target = TRASH_DIR / f"{slug}-{stamp}"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(target))
    # 生成出来的页面一起清掉，否则线上会留一个孤儿页
    page = ROOT / slug
    if page.exists():
        shutil.rmtree(page)
    return target


def rename_record(old_slug: str, new_slug: str) -> Record:
    """改 slug：目录改名 + 清掉旧的生成页"""
    if old_slug == new_slug:
        return load_record(old_slug)
    if not valid_slug(new_slug):
        raise ValueError(f"不合法的 slug：{new_slug!r}")
    source = CONTENT_DIR / old_slug
    target = CONTENT_DIR / new_slug
    if not source.exists():
        raise FileNotFoundError(f"项目不存在：{old_slug}")
    if target.exists():
        raise FileExistsError(f"目标已存在：{new_slug}")
    shutil.move(str(source), str(target))
    meta = read_json(target / "meta.json") or {}
    meta["slug"] = new_slug
    write_json(target / "meta.json", meta)
    old_page = ROOT / old_slug
    if old_page.exists():
        shutil.rmtree(old_page)
    return load_record(new_slug)


ARTICLE_TEMPLATE = """\
> 一句话说明这个项目要解决什么问题、现在到哪一步了。写完可以删掉这段。

## 目的与假设

要验证什么？预期的结果是什么？

## 装置与参数

| 参数 | 取值 | 说明 |
|---|---|---|
|  |  |  |

## 步骤

1. 
2. 

## 原始数据

```text
把测量数据贴在这里，或者放图表。
```

## 分析与讨论

## 结论与下一步

- [ ] 待办
"""


# ══════════════════════════════════════════════════════════════════════════
#  页面模板
# ══════════════════════════════════════════════════════════════════════════

HEAD = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">

<title data-i18n="{title_key}">{title_text}</title>
<meta name="description" content="{desc}" data-i18n="{desc_key}">

<link rel="icon" href="/assets/icon.ico" sizes="32x32">
<link rel="stylesheet" href="/assets/font-awesome.min.css">
<link rel="stylesheet" href="/assets/research.css">{vendor_css}
<script defer src="/assets/lang.js"></script>
<script defer src="/assets/research.js"></script>{vendor_js}
</head>
<body>
"""


def navbar(cfg: dict, active: str = "records") -> str:
    def cls(name: str) -> str:
        return ' class="is-active"' if active == name else ""

    return f"""<header class="navbar" id="navbar">
  <div class="nav-decoration"></div>
  <div class="nav-inner">

    <div class="nav-left">
      <a class="brand" href="/">
        <i class="fa fa-connectdevelop"></i>Populus Hu<small data-i18n="brand-tag">科研记录</small>
      </a>
      <div class="lang-desktop">
        <select class="language-selector" aria-label="语言 / Language">
          <option value="cn" selected>中文</option>
          <option value="en">English</option>
          <option value="jp">日本語</option>
        </select>
      </div>
    </div>

    <button class="menu-toggle" id="menu-toggle" aria-label="菜单" aria-expanded="false" aria-controls="nav-links">
      <i class="fa fa-bars"></i>
    </button>

    <nav class="nav-links" id="nav-links">
      <a href="/"{cls('records')} data-i18n="nav-records">全部记录</a>
      <a href="{attr(cfg['tools_site'])}" data-i18n="nav-tools">在线工具</a>
      <a href="{attr(cfg['main_site'])}" class="back"><i class="fa fa-home"></i><span data-i18n="nav-main">主站</span></a>
      <div class="lang-mobile">
        <select class="language-selector" aria-label="语言 / Language">
          <option value="cn" selected>中文</option>
          <option value="en">English</option>
          <option value="jp">日本語</option>
        </select>
      </div>
    </nav>

  </div>
</header>
"""


def footer(cfg: dict) -> str:
    return f"""<footer class="site-footer">
  <div class="wrap">
    <div class="foot-top">
      <div>
        <a class="brand" href="{attr(cfg['main_site'])}"><i class="fa fa-connectdevelop"></i>Populus Hu</a>
        <p class="foot-desc" data-i18n="footer-desc">我的数字家园，连接你我</p>
      </div>
      <nav class="foot-nav">
        <a href="{attr(cfg['main_site'])}" data-i18n="footer-home">主站首页</a>
        <a href="/" data-i18n="footer-records">全部记录</a>
        <a href="{attr(cfg['tools_site'])}" data-i18n="footer-tools">在线工具</a>
        <a href="{attr(cfg['contact_url'])}" data-i18n="footer-contact">联系我</a>
      </nav>
    </div>
    <div class="foot-bottom">
      <p data-i18n="footer-copyright">© 2026 Populus Hu | 保留所有权利。</p>
      <div class="foot-bottom-right">
        <select class="language-selector language-selector-dark" aria-label="语言 / Language">
          <option value="cn" selected>中文</option>
          <option value="en">English</option>
          <option value="jp">日本語</option>
        </select>
        <a class="to-top" href="#top" id="to-top"><i class="fa fa-angle-up"></i> <span data-i18n="to-top">回到顶部</span></a>
      </div>
    </div>
  </div>
</footer>
"""


def page(title_key: str, title_text: str, desc: str, desc_key: str, body: str,
         cfg: dict, active: str = "records", vendor_css: str = "", vendor_js: str = "") -> str:
    return (
        HEAD.format(title_key=title_key, title_text=esc(title_text), desc=attr(desc),
                    desc_key=desc_key, vendor_css=vendor_css, vendor_js=vendor_js)
        + navbar(cfg, active)
        + body
        + footer(cfg)
        + "\n</body>\n</html>\n"
    )


# ══════════════════════════════════════════════════════════════════════════
#  生成：项目页
# ══════════════════════════════════════════════════════════════════════════

def render_toc(tree: list[dict]) -> str:
    """侧栏目录（两级）"""
    if not tree:
        return ""
    parts = ['<nav class="doc-toc" id="doc-toc" aria-label="目录">',
             '<div class="toc-title"><i class="fa fa-list-ul"></i>'
             '<span data-i18n="doc-toc">目录</span>'
             '<span class="caret"><i class="fa fa-angle-right"></i></span></div>',
             "<ul>"]
    for head in tree:
        parts.append(f'<li class="lvl-2"><a href="#{attr(head["anchor"])}">{esc(head["title"])}</a>')
        if head.get("children"):
            parts.append("<ul>")
            for child in head["children"]:
                parts.append(f'<li class="lvl-3"><a href="#{attr(child["anchor"])}">{esc(child["title"])}</a></li>')
            parts.append("</ul>")
        parts.append("</li>")
    parts += ["</ul>", "</nav>"]
    return "\n".join(parts)


def render_meta_line(record: Record, chars: int, minutes: int) -> str:
    return f"""<div class="doc-meta">
          <span><i class="fa fa-calendar-o"></i> <span data-i18n="meta-created">创建</span> {esc(human_date(record.date))}</span>
          <span><i class="fa fa-refresh"></i> <span data-i18n="meta-updated">更新</span> {esc(human_date(record.updated))}</span>
          <span><i class="fa fa-file-text-o"></i> {chars} <span data-i18n="unit-chars">字</span></span>
          <span><i class="fa fa-clock-o"></i> <span data-i18n="unit-about">约</span> {minutes} <span data-i18n="unit-minutes">分钟</span></span>
        </div>"""


def generate_record(record: Record, cfg: dict, lang: str = "cn") -> dict:
    """生成一个项目的某个语言版本；返回这次生成的信息（供日志用）"""
    md = Markdown()
    body_html = md.render(record.body)
    chars, minutes = char_stats(record.body)

    tags = "".join(f'\n          <span class="tag">{esc(t)}</span>' for t in record.tags)
    tags_html = f'<div class="rec-tags">{tags}\n        </div>' if record.tags else ""

    langs = record.translations()
    if len(langs) > 1:
        links = "".join(
            f'<a href="/{record.slug}/{LANG_SUBDIR[code]}" data-i18n="{LANG_LABEL_KEY[code]}">'
            f"{LANG_FALLBACK[code]}</a>"
            for code in langs
        )
        langs_html = (
            '<div class="doc-langs"><span class="lbl" data-i18n="doc-langs">其它语言</span>'
            + links
            + "</div>"
        )
    else:
        langs_html = ""

    # 上下篇（同一套排序，跨语言时也能用）
    ordered = load_records()
    index = next((i for i, r in enumerate(ordered) if r.slug == record.slug), None)
    prev_record = ordered[index - 1] if index not in (None, 0) else None
    next_record = ordered[index + 1] if index is not None and index + 1 < len(ordered) else None

    def nav_item(item, kind: str) -> str:
        if not item:
            return '<span class="spacer"></span>'
        label = "doc-prev" if kind == "prev" else "doc-next"
        fallback = "上一篇" if kind == "prev" else "下一篇"
        return (
            f'<a class="{kind}" href="/{item.slug}/">'
            f'<span class="dir" data-i18n="{label}">{fallback}</span>'
            f'<span class="t">{esc(item.title)}</span></a>'
        )

    aside = render_toc(md.toc_tree())
    aside_html = f'<aside class="doc-aside">\n{aside}\n</aside>' if aside else ""

    # 只有一个项目时上下篇都是空的 —— 整块别渲染，否则会留一条空的分隔线
    nav_html = ""
    if prev_record or next_record:
        nav_html = (
            '<nav class="doc-nav" aria-label="上下篇">\n'
            f"        {nav_item(prev_record, 'prev')}\n"
            f"        {nav_item(next_record, 'next')}\n"
            "      </nav>"
        )

    body = f"""<main>
  <div class="doc wrap wrap-doc">

    {aside_html}

    <article class="doc-main">
      <header class="doc-head">
        <a class="doc-back" href="/"><i class="fa fa-angle-left"></i> <span data-i18n="doc-back">全部记录</span></a>
        <div class="doc-badges">
          <span class="status status-{attr(record.status)}" data-i18n="status-{attr(record.status)}">{STATUS_TEXT[record.status]}</span>{tags_html}
        </div>
        <h1>{esc(record.title)}</h1>
        {f'<p class="doc-summary">{esc(record.summary)}</p>' if record.summary else ''}
        {render_meta_line(record, chars, minutes)}
        {langs_html}
      </header>

      <hr class="doc-divider">

      <div class="md-body">
{body_html}
      </div>

      {nav_html}
    </article>

  </div>
</main>
"""

    vendor_css = '\n<link rel="stylesheet" href="/assets/vendor/github.min.css">' if md.has_code else ""
    vendor_js = ""
    if md.has_code:
        vendor_js += '\n<script defer src="/assets/vendor/highlight.min.js"></script>'
    if md.has_mermaid:
        vendor_js += '\n<script defer src="/assets/vendor/mermaid.min.js"></script>'

    html_text = page(
        title_key="__record_title__",
        title_text=f"{record.title} | Populus Hu",
        desc=record.summary or record.title,
        desc_key="__record_desc__",
        body=body,
        cfg=cfg,
        vendor_css=vendor_css,
        vendor_js=vendor_js,
    )
    # 项目页的标题与描述是逐项目变化的，不走语言包（语言包只放全站固定文案）。
    # 这里把 data-i18n 去掉，避免语言包把标题改回全站通用的那句。
    html_text = html_text.replace(' data-i18n="__record_title__"', "")
    html_text = html_text.replace(' data-i18n="__record_desc__"', "")

    out_dir = ROOT / record.slug / LANG_SUBDIR[lang]
    out_path = out_dir / "index.html"
    write_text(out_path, html_text)

    # 图片随页面走：content/<slug>/images → /<slug>/images
    copy_images(record, out_dir)

    return {
        "slug": record.slug,
        "lang": lang,
        "path": out_path,
        "chars": chars,
        "minutes": minutes,
        "sections": len(md.sections()),
        "headings": len(md.headings),
        "code": md.has_code,
        "mermaid": md.has_mermaid,
    }


def copy_images(record: Record, out_dir: Path) -> int:
    """把 content/<slug>/images 同步到生成目录（增量：跳过没变的）"""
    source = record.directory / "images"
    if not source.is_dir():
        return 0
    target = out_dir / "images"
    target.mkdir(parents=True, exist_ok=True)
    count = 0
    for item in source.rglob("*"):
        if not item.is_file():
            continue
        relative = item.relative_to(source)
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and destination.stat().st_size == item.stat().st_size \
                and destination.stat().st_mtime >= item.stat().st_mtime:
            continue
        shutil.copy2(item, destination)
        count += 1
    return count


# ══════════════════════════════════════════════════════════════════════════
#  生成：首页（卡片 + 目录预览）
# ══════════════════════════════════════════════════════════════════════════

STATUS_TEXT = {
    "active": "进行中", "done": "已完成", "paused": "暂停",
    "planned": "计划中", "archived": "已归档",
}


def render_card(record: Record, sections: list[dict], chars: int, minutes: int, cfg: dict) -> str:
    limit = int(cfg.get("toc_preview_limit") or 6)
    shown = sections[:limit]
    if shown:
        items = "".join(
            f'\n            <li><a href="/{record.slug}/#{attr(s["anchor"])}">{esc(s["title"])}</a></li>'
            for s in shown
        )
        toc_inner = f'<ol>{items}\n          </ol>'
        if len(sections) > len(shown):
            toc_inner += (
                f'\n          <div class="more"><span data-i18n="card-toc-more-label">还有</span> '
                f'<span class="n">{len(sections) - len(shown)}</span> '
                f'<span data-i18n="unit-sections">节</span></div>'
            )
    else:
        toc_inner = '<div class="none" data-i18n="card-toc-none">这篇还没有二级标题</div>'

    tags = "".join(f'\n          <span class="tag">{esc(t)}</span>' for t in record.tags)
    tags_html = f'<div class="rec-tags">{tags}\n        </div>' if record.tags else ""

    cover = record.meta.get("cover")
    cover_html = ""
    if cover:
        cover_html = (
            f'\n        <div class="rec-cover"><img src="/{record.slug}/{attr(safe_url(cover))}"'
            f' alt="{attr(record.title)}" loading="lazy"></div>'
        )

    pin = ('<span class="rec-pin"><i class="fa fa-thumb-tack"></i>'
           '<span data-i18n="card-pinned">置顶</span></span>') if record.pinned else ""

    return f"""      <a class="card card-hover rec-card" href="/{record.slug}/" data-status="{attr(record.status)}">
        <div class="rec-top">
          <div class="rec-icon bg-{attr(record.color)}"><i class="fa {attr(record.icon)}"></i></div>
          <div class="rec-flags">
            {pin}
            <span class="status status-{attr(record.status)}" data-i18n="status-{attr(record.status)}">{STATUS_TEXT[record.status]}</span>
          </div>
        </div>{cover_html}
        <h3>{esc(record.title)}</h3>
        <p class="rec-summary">{esc(record.summary) or "（还没有写摘要）"}</p>
        <div class="rec-meta">
          <span><i class="fa fa-refresh"></i> {esc(human_date(record.updated))}</span>
          <span><i class="fa fa-file-text-o"></i> {chars} <span data-i18n="unit-chars">字</span></span>
          <span><i class="fa fa-clock-o"></i> <span data-i18n="unit-about">约</span> {minutes} <span data-i18n="unit-minutes">分钟</span></span>
        </div>{tags_html}
        <div class="rec-toc">
          <div class="toc-title"><i class="fa fa-list-ul"></i> <span data-i18n="card-toc">目录预览</span></div>
          {toc_inner}
        </div>
        <div class="rec-foot">
          <span class="go"><span data-i18n="card-read">阅读全文</span> <i class="fa fa-arrow-right"></i></span>
          <span class="words">{len(sections)} <span data-i18n="unit-sections">节</span></span>
        </div>
      </a>"""


def render_catalog_item(record: Record, sections: list[dict]) -> str:
    if sections:
        rows = []
        for s in sections:
            rows.append(
                f'\n              <li><a href="/{record.slug}/#{attr(s["anchor"])}">'
                f'<i class="fa fa-angle-right"></i> {esc(s["title"])}</a></li>'
            )
        body = f'<div class="cat-body"><ol>{"".join(rows)}\n            </ol></div>'
    else:
        body = ('<div class="cat-body"><div class="sub" data-i18n="card-toc-none">'
                '这篇还没有二级标题</div></div>')
    return f"""        <details class="cat-proj">
          <summary>
            <span class="caret"><i class="fa fa-angle-right"></i></span>
            <span class="t">{esc(record.title)}</span>
            <span class="side">
              <span class="status status-{attr(record.status)}" data-i18n="status-{attr(record.status)}">{STATUS_TEXT[record.status]}</span>
              <span class="cat-date">{esc(human_date(record.updated))}</span>
            </span>
          </summary>{body}
          <div class="cat-actions">
            <a class="btn btn-sm" href="/{record.slug}/"><span data-i18n="cat-open">打开项目页</span> <i class="fa fa-arrow-right"></i></a>
          </div>
        </details>"""


def render_filter_bar(records: list[Record]) -> str:
    counts = {s: sum(1 for r in records if r.status == s) for s in STATUSES}
    chips = [("all", "filter-all", "全部", len(records))]
    chips += [(s, f"filter-{s}", STATUS_TEXT[s], counts[s]) for s in STATUSES if counts[s]]
    rows = []
    for status, key, text, count in chips:
        rows.append(
            f'        <button class="chip" type="button" data-status="{status}">'
            f'<span data-i18n="{key}">{text}</span> <span class="n">{count}</span></button>'
        )
    return (
        '<div class="filter-bar" id="filter-bar">\n'
        '        <span class="lbl" data-i18n="filter-label">状态</span>\n'
        + "\n".join(rows)
        + "\n      </div>"
    )


def generate_index(cfg: dict) -> dict:
    records = load_records()
    cards = []
    catalog = []
    total_chars = 0
    latest = ""
    for record in records:
        md = Markdown()
        md.render(record.body)
        chars, minutes = char_stats(record.body)
        total_chars += chars
        latest = max(latest, record.updated or "")
        cards.append(render_card(record, md.sections(), chars, minutes, cfg))
        catalog.append(render_catalog_item(record, md.sections()))

    if cards:
        grid = (
            '<div class="grid cols-3" id="record-grid">\n\n'
            + "\n\n".join(cards)
            + "\n\n    </div>\n    "
            + '<div class="notice notice-info mt-md hidden" id="filter-empty">'
            '<i class="fa fa-filter"></i><div data-i18n="filter-empty">没有符合这个状态的项目。</div></div>'
        )
    else:
        grid = (
            '<div class="empty">\n'
            '      <i class="fa fa-flask"></i>\n'
            '      <h3 data-i18n="empty-title">还没有记录</h3>\n'
            '      <p data-i18n="empty-desc">运行 tools/writepad.py 新建一个项目，'
            '生成之后首页会自动出现卡片和目录。</p>\n'
            "    </div>"
        )

    stats_html = ""
    if cfg.get("show_stats") and records:
        stats_html = f"""<div class="stat-strip">
        <span class="item"><span class="v">{len(records)}</span><span class="k" data-i18n="stat-projects">个项目</span></span>
        <span class="item"><span class="v">{esc(latest or "—")}</span><span class="k" data-i18n="stat-updated">最近更新</span></span>
        <span class="item"><span class="v">{total_chars:,}</span><span class="k" data-i18n="stat-chars">总字数</span></span>
      </div>
      """
    # 一个项目都没有的时候，统计与筛选都没有意义，别占地方
    controls_html = stats_html + render_filter_bar(records) if records else ""

    catalog_html = ""
    if cfg.get("show_catalog") and records:
        catalog_html = f"""<div class="section-head mt-lg">
        <span class="badge" data-i18n="cat-badge">完整目录</span>
        <h2 data-i18n="cat-title">按项目展开</h2>
        <p data-i18n="cat-desc">点开任意一个项目，可以直接跳到它下面的任意小节。</p>
      </div>

      <div class="catalog">
{chr(10).join(catalog)}
      </div>"""

    body = f"""<main>

  <section class="hero">
    <div class="hero-glow" aria-hidden="true"></div>
    <div class="wrap">
      <span class="badge" data-i18n="hero-badge">科研记录</span>
      <h1 data-i18n="hero-title">把实验过程<br><span class='grad'>完整留下来</span></h1>
      <p data-i18n="hero-desc">
        每个项目一篇长文：目的、装置与参数、步骤、原始数据、分析和结论。正文用本地写字板写，一键生成静态页面，首页的卡片与目录也会跟着重建。
      </p>
      <div class="hero-actions">
        <a class="btn-hero btn-hero-primary" href="#records" data-i18n="hero-btn-records">浏览全部记录</a>
        <a class="btn-hero btn-hero-ghost" href="{attr(cfg['main_site'])}" data-i18n="hero-btn-main">回到主站</a>
      </div>
    </div>
    <a class="hero-scroll" href="#records"><span data-i18n="hero-scroll">向下滚动</span><i class="fa fa-angle-down"></i></a>
  </section>

  <section class="section wrap" id="records">

    <div class="section-head">
      <span class="badge" data-i18n="sec-badge">全部记录</span>
      <h2 data-i18n="sec-title">项目与目录</h2>
      <p data-i18n="sec-desc">每张卡片是一个项目，卡片上直接列出该项目的章节目录，点进去就能跳到对应小节。</p>
    </div>

    {controls_html}

    {grid}

    {catalog_html}

  </section>

</main>
"""

    html_text = page(
        title_key="page-title",
        title_text="科研记录 | Populus Hu",
        desc="Populus Hu 的科研记录：每个实验项目一篇长文，含目的、装置、步骤、数据与结论。",
        desc_key="meta-desc",
        body=body,
        cfg=cfg,
    )
    write_text(ROOT / "index.html", html_text)
    return {"records": len(records), "chars": total_chars}


# ══════════════════════════════════════════════════════════════════════════
#  生成：404 / sitemap / robots / 语言包占位
# ══════════════════════════════════════════════════════════════════════════

def generate_404(cfg: dict) -> None:
    body = f"""<main>
  <section class="section wrap wrap-narrow">
    <div class="section-head">
      <span class="badge" data-i18n="notfound-badge">404</span>
      <h2 data-i18n="notfound-title">页面不存在</h2>
      <p data-i18n="notfound-desc">你要找的项目可能已经改名、归档或者还没开始写。</p>
    </div>
    <div class="hero-actions">
      <a class="btn-hero btn-hero-primary" href="/" data-i18n="notfound-btn-home">回到全部记录</a>
      <a class="btn-hero btn-hero-ghost" href="{attr(cfg['main_site'])}" data-i18n="notfound-btn-main">回到主站</a>
    </div>
  </section>
</main>
"""
    html_text = page(
        title_key="page-title-404",
        title_text="页面不存在 | Populus Hu 科研记录",
        desc="你访问的页面不存在，或者已经被移动/删除。",
        desc_key="meta-desc-404",
        body=body,
        cfg=cfg,
    )
    write_text(ROOT / "404.html", html_text)


def generate_sitemap(cfg: dict) -> None:
    base = cfg["base_url"].rstrip("/")
    rows = [f'  <url><loc>{base}/</loc><lastmod>{today()}</lastmod>'
            f'<changefreq>weekly</changefreq><priority>1.0</priority></url>']
    for record in load_records():
        rows.append(
            f'  <url><loc>{base}/{record.slug}/</loc>'
            f'<lastmod>{esc(record.updated or record.date or today())}</lastmod>'
            f'<changefreq>monthly</changefreq><priority>0.8</priority></url>'
        )
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
           + "\n".join(rows) + "\n</urlset>\n")
    write_text(ROOT / "sitemap.xml", xml)


def generate_robots(cfg: dict) -> None:
    base = cfg["base_url"].rstrip("/")
    write_text(ROOT / "robots.txt", f"User-agent: *\nAllow: /\n\nSitemap: {base}/sitemap.xml\n")


def ensure_lang_placeholders() -> list[str]:
    """en / jp 语言包缺失时，用 cn 镜像一份

    这样非中文浏览器不会因为 /lang/en.json 404 而在控制台里刷警告，
    也不会看到「界面翻了一半」的样子。等真要出英文版时，
    直接把 en.json 里的值换成英文即可 —— 生成器只在文件不存在时才写。
    """
    created = []
    cn_path = ROOT / "lang" / "cn.json"
    if not cn_path.exists():
        return created
    pack = read_json(cn_path)
    for lang in ("en", "jp"):
        path = ROOT / "lang" / f"{lang}.json"
        if not path.exists():
            write_json(path, pack)
            created.append(path.name)
    return created


# ══════════════════════════════════════════════════════════════════════════
#  整站生成
# ══════════════════════════════════════════════════════════════════════════

def build_all(slug: str | None = None) -> list[str]:
    """重新生成整站（或只生成一个项目 + 首页）。返回给写字板显示的日志行。"""
    cfg = load_site_config()
    log: list[str] = []

    for name in ensure_lang_placeholders():
        log.append(f"补充语言包占位：lang/{name}")

    if slug:
        slugs = [slug]
    else:
        slugs = list_slugs()

    records = load_records()
    total_sections = 0
    for record in records:
        if slug and record.slug != slug:
            continue
        for lang in record.translations():
            info = generate_record(record, cfg, lang)
            total_sections += info["sections"]
            flags = []
            if info["code"]:
                flags.append("代码高亮")
            if info["mermaid"]:
                flags.append("Mermaid")
            suffix = f"（{lang}）" if lang != "cn" else ""
            log.append(
                f"生成 {info['path'].relative_to(ROOT).as_posix()}{suffix}"
                f" —— {info['headings']} 个标题 / {info['chars']} 字"
                + (f" / {'、'.join(flags)}" if flags else "")
            )

    index_info = generate_index(cfg)
    log.append(f"重建首页 index.html —— {index_info['records']} 张卡片 / 共 {index_info['chars']:,} 字")

    generate_404(cfg)
    generate_sitemap(cfg)
    generate_robots(cfg)
    log.append("刷新 404.html、sitemap.xml、robots.txt")

    if slug and not any(s == slug for s in slugs):
        log.append(f"警告：没有找到项目 {slug}")
    return log


# ══════════════════════════════════════════════════════════════════════════
#  自检
# ══════════════════════════════════════════════════════════════════════════

def collect_i18n_keys() -> tuple[set[str], set[str]]:
    """扫描生成物与页面脚本，收集「用到」和「语言包里定义」的键

    这跟工具站 _verifyall.mjs 的思路一致：文案错漏肉眼很难发现，交给脚本抓。

    两类文件要分开扫：
      · HTML 里认 data-i18n="键名"
      · assets/*.js 里只认 i18n.t('键名') / T('键名')
    如果拿 data-i18n 的正则去扫 JS，文档注释里那句
    「标记写 data-i18n="键名"」会被当成一个真实的键，报出「页面用到但语言包里没有：键名」。
    """
    packs = {lang: read_json(ROOT / "lang" / f"{lang}.json") for lang in ("cn", "en", "jp")}
    cn_keys = set(packs["cn"].keys())

    used: set[str] = set()
    for path in _generated_html():
        text = read_text(path)
        used |= set(re.findall(r'data-i18n(?:-placeholder)?="([^"]+)"', text))
    for path in (ROOT / "assets").glob("*.js"):
        text = read_text(path)
        used |= set(re.findall(r"""i18n\.t\(\s*['"]([\w-]+)['"]""", text))
        used |= set(re.findall(r"""\bT\(\s*['"]([\w-]+)['"]""", text))
    return used, cn_keys


def unused_i18n_keys() -> list[str]:
    """语言包里定义了、但当前生成物里没人用的键

    只作为参考信息，不算错误 —— 很多键是「有条件才渲染」的：
    筛选按钮只在存在该状态的项目时出现，上下篇链接只有一个项目时不出现，
    语言切换只在有多个语言版本时出现。项目多起来它们就自然被用上了。
    """
    used, cn_keys = collect_i18n_keys()
    return sorted(cn_keys - used)


def _generated_html() -> list[Path]:
    files = [ROOT / "index.html", ROOT / "404.html"]
    for record in load_records():
        for lang in record.translations():
            files.append(ROOT / record.slug / LANG_SUBDIR[lang] / "index.html")
    return [f for f in files if f.exists()]


def run_checks() -> list[str]:
    """整站自检。返回问题列表（空列表 = 全部通过）"""
    problems: list[str] = []
    cfg = load_site_config()

    # 1. 语言包键一致性
    packs = {lang: read_json(ROOT / "lang" / f"{lang}.json") for lang in ("cn", "en", "jp")}
    cn_keys = set(packs["cn"].keys())
    if not cn_keys:
        problems.append("lang/cn.json 是空的")
    for lang in ("en", "jp"):
        missing = cn_keys - set(packs[lang].keys())
        if missing:
            problems.append(f"lang/{lang}.json 缺少 {len(missing)} 个键：{', '.join(sorted(missing)[:6])}…")

    used, cn_keys2 = collect_i18n_keys()
    unknown = used - cn_keys2
    if unknown:
        problems.append(f"页面用到但语言包里没有的键：{', '.join(sorted(unknown))}")

    # 2. 生成页面的链接与资源
    for path in _generated_html():
        text = read_text(path)
        rel = path.relative_to(ROOT).as_posix()
        base = path.parent
        for raw in re.findall(r'(?:href|src)="(/[^"#]*)"', text):
            target = raw.split("?")[0].split("#")[0]
            if not target or target == "/":
                continue
            candidate = ROOT / target.lstrip("/")
            if target.endswith("/"):
                candidate = candidate / "index.html"
            if not candidate.exists():
                problems.append(f"{rel} 里的链接指向不存在的文件：{raw}")
        # 站内锚点是否真的有对应 id
        for anchor in re.findall(r'href="#([^"]+)"', text):
            if anchor in ("top",):
                continue
            if f'id="{anchor}"' not in text:
                problems.append(f"{rel} 里 #%s 找不到对应的 id" % anchor)

    # 3. 不能有行内样式（约定：一切状态都靠类名）
    for path in _generated_html():
        hits = re.findall(r'<[^>]+\sstyle="', read_text(path))
        if hits:
            problems.append(f"{path.relative_to(ROOT).as_posix()} 里有 {len(hits)} 处行内 style")

    # 4. 不能引用外部 CDN 的资源
    #    只看「会被浏览器当资源加载」的地址：src=... 以及 href=...css/js。
    #    正文里的参考文献链接是普通超链接，不该被这条规则误伤。
    for path in _generated_html():
        rel = path.relative_to(ROOT).as_posix()
        text = read_text(path)
        remote = set(re.findall(r'src="(https?://[^"]+)"', text))
        remote |= set(re.findall(r'href="(https?://[^"]+\.(?:css|js|woff2?|ttf))"', text))
        for url in sorted(remote):
            problems.append(f"{rel} 引用了外部资源：{url}")

    # 5. 每个项目目录的文件是否合乎约定
    for slug in list_slugs():
        directory = CONTENT_DIR / slug
        if not (directory / "meta.json").exists():
            problems.append(f"content/{slug}/ 里没有 meta.json")
        if not (directory / "article.md").exists():
            problems.append(f"content/{slug}/ 里没有 article.md（中文正文）")
        meta = read_json(directory / "meta.json")
        if not meta.get("title"):
            problems.append(f"content/{slug}/meta.json 没有 title")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(meta.get("date") or "")):
            problems.append(f"content/{slug}/meta.json 的 date 不是 YYYY-MM-DD")
        if meta.get("status") not in STATUSES:
            problems.append(f"content/{slug}/meta.json 的 status 非法：{meta.get('status')!r}")
        cover = meta.get("cover")
        if cover and not (directory / cover).exists():
            problems.append(f"content/{slug}/meta.json 的封面图不存在：{cover}")

    # 6. 图片引用是否存在
    for record in load_records():
        for src in re.findall(r'!\[[^\]]*\]\(\s*([^\s)]+)', record.body):
            if re.match(r"^(https?:|/)", src):
                continue
            if not (record.directory / src).exists():
                problems.append(f"{record.slug} 引用了不存在的图片：{src}")

    # 7. 无 JS 时的中文兜底原文，必须与 cn 语言包一致
    #    这个错误肉眼极难发现（页面看着完全正常，只有在禁用 JS 或语言包没加载
    #    时才露出旧文案），所以交给脚本逐条对比。
    problems += _check_fallback_text()

    # 8. 生成物里用到的每个类名，assets/research.css 里都得有对应规则
    #    否则就是「HTML 写了 class 但样式没跟上」，页面会静默走版
    problems += _check_css_coverage()

    return problems


#: 这些类名不是 research.css 负责的：
#:   fa / fa-*      Font Awesome 图标
#:   hljs / language-*   highlight.js 自己加的高亮标记
#:   mermaid        mermaid 渲染出来的节点
#:   copy-sink      research.js 剪贴板降级用的临时输入框（有对应规则，前缀匹配放宽）
CSS_IGNORED_PREFIXES = ("fa", "hljs", "language-", "mermaid", "copy-sink")


def _css_text() -> str:
    return re.sub(r"/\*.*?\*/", "", read_text(ROOT / "assets" / "research.css"), flags=re.S)


def _html_classes() -> set[str]:
    used: set[str] = set()
    for path in _generated_html():
        # 单引号也要认：语言包里的 hero 标题写的是 <span class='grad'>，
        # 只匹配双引号会把 grad 误判成「没人用的样式」
        for group in re.findall(r"""class=['"]([^'"]+)['"]""", read_text(path)):
            used.update(group.split())
    return used


def _check_css_coverage() -> list[str]:
    css = _css_text()
    styled = set(re.findall(r"\.([A-Za-z][\w-]*)", css))
    unstyled = sorted(
        c for c in _html_classes()
        if c not in styled and not c.startswith(CSS_IGNORED_PREFIXES)
    )
    if unstyled:
        return [f"页面用了但 research.css 里没有样式的类名：{', '.join(unstyled)}"]
    return []


def unused_css_classes() -> list[str]:
    """CSS 里定义了、但当前生成物没用的类（参考信息）

    有些类只有特定情况才会出现：灯箱、toast、上下篇、语言切换、
    筛选条的选中态……所以这里只提示、不算错误。
    """
    css = _css_text()
    styled = set(re.findall(r"\.([A-Za-z][\w-]*)", css))
    used = _html_classes() | {"copy-sink", "toast", "is-open"}
    return sorted(c for c in styled if c not in used)


def _check_fallback_text() -> list[str]:
    problems: list[str] = []
    pack = read_json(ROOT / "lang" / "cn.json")

    def undecode(text: str) -> str:
        return (text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
                    .replace("&quot;", '"').replace("&#39;", "'").replace("&#x27;", "'"))

    def norm(text: str) -> str:
        return re.sub(r"\s+", " ", undecode(text)).strip()

    # 只比较「内容里没有子标签」的元素；带 <br>/<span>/<code> 的形状不同，跳过
    pattern = re.compile(r'<(\w+)([^>]*\sdata-i18n="([^"]+)"[^>]*)>([^<]*)</\1>')
    for path in _generated_html():
        rel = path.relative_to(ROOT).as_posix()
        text = read_text(path)
        for match in pattern.finditer(text):
            key, content = match.group(3), match.group(4)
            value = pack.get(key)
            if not isinstance(value, str) or "<" in value:
                continue
            if not content.strip():
                continue
            if norm(content) != norm(value):
                problems.append(
                    f"{rel} · {key}：HTML 兜底「{norm(content)[:40]}」"
                    f"≠ cn 语言包「{norm(value)[:40]}」"
                )
    return problems


# ══════════════════════════════════════════════════════════════════════════
#  命令行
# ══════════════════════════════════════════════════════════════════════════

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="research_core.py",
        description="科研记录站的生成核心（写字板 tools/writepad.py 是主要入口）",
    )
    sub = parser.add_subparsers(dest="command")

    p_build = sub.add_parser("build", help="重新生成整站；带 slug 则只生成该项目 + 首页")
    p_build.add_argument("slug", nargs="?", default=None)

    sub.add_parser("check", help="整站自检")
    sub.add_parser("list", help="列出所有项目")

    p_new = sub.add_parser("new", help="新建项目骨架")
    p_new.add_argument("slug")
    p_new.add_argument("title", nargs="?", default="")

    args = parser.parse_args(argv)

    if args.command in (None, "build"):
        for line in build_all(getattr(args, "slug", None)):
            print(line)
        return 0

    if args.command == "check":
        issues = run_checks()
        leftover = unused_i18n_keys()
        dead_css = unused_css_classes()
        if not issues:
            print("自检通过 ✅")
        else:
            print(f"发现 {len(issues)} 处问题 ❌")
            for issue in issues:
                print(f"  ✗ {issue}")
        if leftover:
            print(f"  · 参考：语言包里有 {len(leftover)} 个键当前没被用到"
                  f"（多数是「有条件才渲染」的，如某状态的筛选按钮）：")
            print(f"    {', '.join(leftover)}")
        if dead_css:
            print(f"  · 参考：CSS 里有 {len(dead_css)} 个类当前没被用到：")
            print(f"    {', '.join(dead_css)}")
        return 1 if issues else 0

    if args.command == "list":
        records = load_records()
        if not records:
            print("（还没有项目）")
            return 0
        for record in records:
            chars, minutes = char_stats(record.body)
            print(f"{record.slug:<28} {record.status:<9} {record.updated:<12} "
                  f"{chars:>7} 字  {record.title}")
        return 0

    if args.command == "new":
        record = create_record(args.slug, args.title)
        print(f"已新建：content/{record.slug}/（meta.json + article.md）")
        return 0

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
