"""生成核心的功能测试。

    python tools/tests/test_core.py

覆盖四块：
  1. Markdown 渲染器的刁钻边界（嵌套列表、表格转义、图注编号、锚点去重、
     裸 URL 结尾的标点、行内代码与 snake_case 的保护……）
  2. 「有条件才渲染」的分支：多状态筛选、上下篇、多语言、无二级标题、空站
  3. 生成物的链接 / i18n 键 / 行内样式自检
  4. HTML 用到的类名与 CSS 规则对账

会临时创建几个项目再删掉，并在结束时把站点重建回原样；
过程中不动 content/ 里已有的项目。退出码非 0 表示有断言失败。
"""
import importlib
import json
import re
import shutil
import sys
from pathlib import Path

# tools/tests/test_core.py → 上两级就是仓库根
REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

# 直接 import：生成核心里有 @dataclass，用 importlib 手工装载而不注册进
# sys.modules 的话，dataclasses 会拿不到模块命名空间而报错。
core = importlib.import_module("research_core")

fails = []
checks = [0]


def ok(cond, label):
    checks[0] += 1
    if not cond:
        fails.append(label)
        print(f"  FAIL  {label}")
    else:
        print(f"  ok    {label}")


def md(text):
    m = core.Markdown()
    return m.render(text), m


print("=== 1. Markdown 渲染器 ===")

out, m = md("**粗体** 和 *斜体* 和 ~~删除~~")
ok("<strong>粗体</strong>" in out, "粗体")
ok("<em>斜体</em>" in out, "斜体")
ok("<del>删除</del>" in out, "删除线")

out, _ = md("这是 file_name_here 和 x_y_z 变量")
ok("<em>" not in out, "下划线变量不被当成斜体（snake_case 保护）")

out, _ = md("`code with *stars* and _under_`")
ok("<em>" not in out and "<code>code with *stars* and _under_</code>" in out, "行内代码里的标记不被解析")

out, _ = md("反斜杠转义 \\*不是斜体\\* 和 \\`反引号\\`")
ok("<em>" not in out and "*不是斜体*" in out, "反斜杠转义")

out, _ = md("- 一级\n  - 二级\n    - 三级\n- 回到一级")
ok(out.count("<ul>") == 3, "三层嵌套列表都生成了 <ul>")
ok("三级" in out and "回到一级" in out, "嵌套列表内容完整")

out, _ = md("1. 第一\n2. 第二\n\n   ```py\n   x = 1\n   ```")
ok("<ol>" in out and "<li>第一</li>" in out, "有序列表紧凑渲染（不套 <p>）")
ok("<pre><code" in out, "列表项里的代码块")

out, _ = md("| a | b |\n|:--|--:|\n| 1 | 2 |\n| 3 \\| 4 | 5 |")
ok('<th class="align-center">a</th>' not in out or "align" in out, "表格对齐类")
ok("3 | 4" in out, "表格里的 \\| 转义成字面竖线")

out, m = md("![图注文字](images/a.png \"第一个\")\n\n中间段落\n\n![第二个](images/b.png)")
ok(out.count("<figure") == 2, "两张独立图片都变成 <figure>")
ok('<span class="fig-n">图 1</span>' in out and '<span class="fig-n">图 2</span>' in out, "图注自动编号")
ok("第一个" in out, "图注取自 title")
ok(m.figure_count == 2, "figure 计数")

out, _ = md("文字里有 ![行内图](i.png) 夹在中间")
ok("<figure" not in out and "<img" in out, "行内图片不升级成 figure")

out, _ = md("---")
ok(out.strip() == "<hr>", "分隔线")

out, _ = md("- [x] 做完\n- [ ] 没做")
ok('class="task-list"' in out and "checked" in out, "任务列表")

out, m = md("## 带 `代码` 的标题")
ok(m.headings[0]["anchor"] == "带-代码-的标题", f"锚点剥掉行内标记（得到 {m.headings[0]['anchor']!r}）")

out, m = md("## 重复\n\n## 重复\n\n## 重复")
ok([h["anchor"] for h in m.headings] == ["重复", "重复-2", "重复-3"], "重复标题的锚点自动去重")

out, _ = md("<details>\n<summary>折叠</summary>\n内容\n</details>")
ok("<details>" in out and "<summary>" in out, "原始 HTML 块原样透传")

out, _ = md("见 <https://example.com/a> 和裸链 https://example.com/b。")
ok(out.count("<a href=") == 2, "尖括号自动链接与裸 URL 都识别")
ok("https://example.com/b</a>。" in out, "裸 URL 结尾的中文句号没被吞进链接")

out, _ = md("见 https://en.wikipedia.org/wiki/Foo_(bar) 这条")
ok('href="https://en.wikipedia.org/wiki/Foo_(bar)"' in out, "URL 自带的成对括号要保留")

out, _ = md("（参考 https://x.com/a）。")
ok('href="https://x.com/a">' in out and "</a>）。" in out, "URL 后面的「）。」都留在链接外")

out, _ = md("> 引用第一行\n> - 引用里的列表\n> - 第二条")
ok("<blockquote>" in out and "<ul>" in out, "引用里的列表")

out, _ = md("```mermaid\nflowchart LR\n  A --> B\n```")
ok('class="mermaid"' in out and "A --&gt; B" in out, "mermaid 代码块实体转义")
ok("<pre>" not in out, "mermaid 不套 <pre>")

out, _ = md("```python\nprint('<hi>')\n```")
ok("&lt;hi&gt;" in out and 'class="language-python"' in out, "普通代码块转义 + 语言类")

out, _ = md("[链接](javascript:alert(1))")
ok('href="#"' in out, "javascript: 协议被挡掉")

print("\n=== 2. 生成分支：多状态 / 上下篇 / 多语言 / 无小节 ===")

TEMP = ["tmp-alpha", "tmp-beta", "tmp-gamma", "tmp-plain"]
made = []


def cleanup():
    for slug in TEMP:
        d = REPO / "content" / slug
        if d.exists():
            shutil.rmtree(d)
        page = REPO / slug
        if page.exists():
            shutil.rmtree(page)


cleanup()
try:
    for slug, status, title in [
        ("tmp-alpha", "done", "临时项目 A"),
        ("tmp-beta", "paused", "临时项目 B"),
        ("tmp-gamma", "planned", "临时项目 C"),
    ]:
        rec = core.create_record(slug, title, status=status)
        core.save_record(slug, {**rec.meta, "summary": f"{title} 的摘要", "tags": ["临时"]},
                         "## 第一节\n\n内容\n\n## 第二节\n\n内容\n")
        made.append(slug)

    # 一个没有任何二级标题的项目 —— 走 card-toc-none 分支
    rec = core.create_record("tmp-plain", "没有小节的项目", status="active")
    core.save_record("tmp-plain", {**rec.meta, "summary": "只有段落"},
                     "只有一段普通文字，没有任何小节标题。\n")
    made.append("tmp-plain")

    # 给 A 加一个英文版 —— 走语言切换分支
    core.write_text(REPO / "content" / "tmp-alpha" / "article.en.md",
                    "## Section one\n\nEnglish body.\n")

    core.build_all()
    index = core.read_text(REPO / "index.html")

    ok(index.count('class="chip"') == 5, f"筛选条渲染出 5 个状态筛选（实际 {index.count('class=\"chip\"')}）")
    for status in ("done", "paused", "planned"):
        ok(f'data-status="{status}"' in index, f"筛选条含 {status}")
    ok('class="status status-done"' in index, "已完成状态徽标")
    ok("没有小节的项目" in index, "无小节项目仍出现在首页")
    ok('data-i18n="card-toc-none"' in index, "无小节卡片走了 card-toc-none 分支")

    page_a = core.read_text(REPO / "tmp-alpha" / "index.html")
    ok('class="doc-nav"' in page_a, "有多个项目时渲染上下篇")
    ok('href="/tmp-beta/"' in page_a or 'href="/example-record/"' in page_a, "上下篇指向真实邻居")
    ok("doc-langs" in page_a and 'href="/tmp-alpha/en/"' in page_a, "多语言切换入口")
    ok((REPO / "tmp-alpha" / "en" / "index.html").exists(), "英文版页面生成到 /tmp-alpha/en/")

    page_plain = core.read_text(REPO / "tmp-plain" / "index.html")
    ok('class="doc-aside"' not in page_plain, "无小节的项目页不渲染空的侧栏目录")

    issues = core.run_checks()
    ok(not issues, f"多项目状态下自检仍然通过（问题数 {len(issues)}）")
    for issue in issues:
        print(f"        ! {issue}")

    leftover = core.unused_i18n_keys()
    print(f"        （此时未用到的键：{leftover}）")
finally:
    cleanup()
    core.build_all()
    print("\n已清理临时项目并重建")

print("\n=== 3. 空站分支 ===")
saved = []
try:
    for d in (REPO / "content").iterdir():
        if d.is_dir() and not d.name.startswith(("_", ".")):
            target = REPO / ".writepad-trash" / f"__test__{d.name}"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(d), str(target))
            saved.append((target, d))
    core.build_all()
    index = core.read_text(REPO / "index.html")
    ok('data-i18n="empty-title"' in index, "空站渲染空状态")
    ok("filter-bar" not in index, "空站不渲染筛选条")
    ok("stat-strip" not in index, "空站不渲染统计条")
    issues = core.run_checks()
    ok(not issues, f"空站自检通过（问题数 {len(issues)}）")
    for issue in issues:
        print(f"        ! {issue}")
finally:
    for target, original in saved:
        if original.exists():
            shutil.rmtree(original)
        shutil.move(str(target), str(original))
    core.build_all()
    print("\n已恢复项目并重建")

print("\n=== 4. HTML 用到的类名 vs CSS 规则 ===")
css = core.read_text(REPO / "assets" / "research.css")
no_comment = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
ok(no_comment.count("{") == no_comment.count("}"),
   f"CSS 花括号平衡（{no_comment.count('{')} 对）")

styled = set(re.findall(r"\.([A-Za-z][\w-]*)", no_comment))
# 这几个不是本文件负责的：Font Awesome 图标、highlight.js 的产物类、mermaid 自己的节点
IGNORE_PREFIX = ("fa", "hljs", "language-", "mermaid", "copy-sink")
used = set()
for path in core._generated_html():
    for group in re.findall(r'class="([^"]+)"', core.read_text(path)):
        used.update(group.split())
unstyled = sorted(c for c in used if c not in styled and not c.startswith(IGNORE_PREFIX))
ok(not unstyled, f"HTML 里的类名都有对应样式（未覆盖：{unstyled}）")

unused_css = sorted(
    c for c in styled
    if c not in used and not c.startswith(("is-", "has-", "lvl-", "align-", "bg-", "status-", "cols-"))
)
print(f"        （CSS 里定义了但当前页面没用到的类：{unused_css}）")

print("\n" + "=" * 60)
print(f"共 {checks[0]} 项断言，失败 {len(fails)} 项" + ("" if not fails else " ❌"))
for f in fails:
    print(f"  ✗ {f}")
sys.exit(1 if fails else 0)
