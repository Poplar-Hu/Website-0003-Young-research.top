"""写字板的冒烟测试。

    python tools/tests/test_writepad.py

不去点按钮 —— 会弹对话框的入口（新建 / 删除 / 预览 / 自检 / 选图）全部跳过，
只驱动底层真实流程：载入项目 → 填表 → 插入 Markdown → 保存 → 改路径 →
生成 → 内置预览服务器取页面 → 自检。

会短暂闪出一个 tkinter 窗口，属正常（跑完自己销毁）。需要图形界面环境。
会在 content/ 下临时建一个 smoke-test 项目并在结束时清理，
不碰 content/ 里已有的项目。退出码非 0 表示有断言失败。
"""
import shutil
import sys
import tkinter as tk
import tkinter.font as tkfont
import types
import urllib.request
from pathlib import Path

# tools/tests/test_writepad.py → 上两级就是仓库根
REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

import research_core as core      # noqa: E402
import writepad                   # noqa: E402

SLUG = "smoke-test"
NEW_SLUG = "smoke-test-renamed"

fails = []
count = [0]


def ok(cond, label):
    count[0] += 1
    print(("  ok    " if cond else "  FAIL  ") + label)
    if not cond:
        fails.append(label)


def same_color(a, b) -> bool:
    """Tk 会把颜色值规范化（大小写等），比较时统一降到小写"""
    return str(a).strip().lower() == str(b).strip().lower()


def font_size(widget) -> int:
    """取控件当前实际字号。cget('font') 可能是字符串，交给 tkfont 解析。"""
    return int(tkfont.Font(root=widget, font=widget.cget("font")).cget("size"))


def cleanup():
    for slug in (SLUG, NEW_SLUG):
        d = core.CONTENT_DIR / slug
        if d.exists():
            shutil.rmtree(d)
        page = core.ROOT / slug
        if page.exists():
            shutil.rmtree(page)
    trash = core.TRASH_DIR
    if trash.exists():
        shutil.rmtree(trash)


cleanup()
root = None
# 状态文件里存的是用户自己的主题/缩放/窗口位置。测试会覆写它来验证持久化，
# 所以先原样备份，跑完再放回去 —— 不能因为跑个测试就把用户的偏好清掉。
state_backup = (writepad.STATE_PATH.read_text(encoding="utf-8")
                if writepad.STATE_PATH.exists() else None)
if writepad.STATE_PATH.exists():
    writepad.STATE_PATH.unlink()
try:
    # 先造一个项目，专门给测试用（绝不碰 example-record）
    core.create_record(SLUG, "冒烟测试项目", status="paused")
    record = core.load_record(SLUG)
    core.save_record(SLUG, {**record.meta, "summary": "初始摘要", "tags": ["测试"]},
                     "## 第一节\n\n正文甲。\n\n## 第二节\n\n正文乙。\n")

    root = tk.Tk()
    app = writepad.WritepadApp(root)
    root.update()

    print("=== 载入与表单 ===")
    ok(SLUG in app._slugs, "项目列表里能看到新建的项目")
    app.load_record(SLUG)
    root.update()
    ok(app.current and app.current.slug == SLUG, "当前项目已切换到冒烟测试项目")
    ok(app.var_title.get() == "冒烟测试项目", f"标题载入正确（{app.var_title.get()!r}）")
    ok(app.var_status.get() == "暂停", f"状态映射回中文（{app.var_status.get()!r}）")
    ok(app.var_tags.get() == "测试", f"标签载入（{app.var_tags.get()!r}）")
    ok(app.summary_text.get("1.0", "end-1c") == "初始摘要", "摘要载入")
    ok(app.dirty is False, "刚载入时没有 dirty 标记")
    ok("正文甲" in app.body.get("1.0", "end-1c"), "正文载入")
    ok(bool(app.var_counts.get()), f"状态栏字数已计算（{app.var_counts.get()}）")

    print("\n=== 插入工具 ===")
    app.body.delete("1.0", "end")
    app.body.insert("1.0", "选中我")
    app.body.tag_add(tk.SEL, "1.0", "1.3")
    app.wrap_sel("**", "**", "加粗文字")
    ok(app.body.get("1.0", "end-1c") == "**选中我**", f"加粗包裹选区（{app.body.get('1.0', 'end-1c')!r}）")
    # <<Modified>> 是排队派发的，dirty 要等事件循环跑一轮才立起来；
    # has_unsaved() 会直接比内容，所以两种读法这里都验一下
    ok(app.has_unsaved() is True, "has_unsaved() 立刻就能看出正文变了（不依赖事件时序）")
    root.update()
    ok(app.dirty is True, "事件循环跑过之后 dirty 立起来")

    app.body.delete("1.0", "end")
    app.body.insert("1.0", "行首")
    app.body.mark_set(tk.INSERT, "1.2")
    app.insert_block("## ", "", "小节标题")
    text = app.body.get("1.0", "end-1c")
    ok(text.startswith("行首\n\n## 小节标题"), f"块级插入落在当前行下面并空行隔开（{text!r}）")

    # 连点两次：第二个标题应该在第一个下面，而不是把它顶掉
    app.insert_block("## ", "", "第二个标题")
    text = app.body.get("1.0", "end-1c")
    ok(text.index("小节标题") < text.index("第二个标题"), "连续插入的标题保持先后顺序")

    app.body.delete("1.0", "end")
    app.insert_mermaid()
    text = app.body.get("1.0", "end-1c")
    ok(text.startswith("```mermaid") and text.rstrip().endswith("```"), "插入 Mermaid 代码块且围栏成对")

    app.body.delete("1.0", "end")
    app.insert_table()
    text = app.body.get("1.0", "end-1c")
    ok(text.count("|") == 16 and text.count("\n") >= 3, f"插入表格骨架（{text.count('|')} 个竖线）")

    app.body.delete("1.0", "end")
    app.body.insert("1.0", "看这里")
    app.body.tag_add(tk.SEL, "1.0", "1.3")
    app.insert_link()
    ok("[看这里](https://)" in app.body.get("1.0", "end-1c"), "选区转链接")

    print("\n=== 保存 ===")
    app.body.delete("1.0", "end")
    app.body.insert("1.0", "## 改过的第一节\n\n新的正文。\n")
    app.summary_text.delete("1.0", "end")
    app.summary_text.insert("1.0", "改过的摘要")
    app.var_tags.set("测试，第二轮")
    saved = app.save(silent=True)
    ok(saved, "save() 返回成功")

    on_disk = core.read_text(core.CONTENT_DIR / SLUG / "article.md")
    ok("新的正文" in on_disk, "正文已写入磁盘")
    meta = core.read_json(core.CONTENT_DIR / SLUG / "meta.json")
    ok(meta["summary"] == "改过的摘要", "摘要已写入 meta.json")
    ok(meta["tags"] == ["测试", "第二轮"], f"全角逗号也能拆标签（{meta['tags']}）")
    ok(meta["updated"] == core.today(), f"正文改动后「更新」自动设为今天（{meta['updated']}）")
    ok(app.dirty is False, "保存后 dirty 已清")

    print("\n=== 改路径 = 改目录 ===")
    app.var_slug.set(NEW_SLUG)
    ok(app.save(silent=True), "改路径后保存成功")
    ok((core.CONTENT_DIR / NEW_SLUG).is_dir(), "新目录已建立")
    ok(not (core.CONTENT_DIR / SLUG).exists(), "旧目录已移走")
    ok(core.read_json(core.CONTENT_DIR / NEW_SLUG / "meta.json")["slug"] == NEW_SLUG,
       "meta.json 里的 slug 同步更新")

    print("\n=== 校验与拒绝 ===")
    app.var_slug.set("有中文的路径")
    try:
        app._collect_meta()
        ok(False, "中文路径应当被拒绝（而不是被静默改成 record）")
    except ValueError as error:
        ok("不能用" in str(error), f"中文路径被明确拒绝（{str(error).splitlines()[0]}）")
    app.var_slug.set("")
    try:
        app._collect_meta()
        ok(False, "空路径应当被拒绝")
    except ValueError as error:
        ok("不能用" in str(error), "空路径被拒绝")
    app.var_slug.set("My Project_2")
    ok(app._collect_meta()["slug"] == "my-project-2", "空格与下划线被整理成连字符")
    app.var_slug.set(NEW_SLUG)

    app.var_date.set("2026/10/03")
    try:
        app._collect_meta()
        ok(False, "错误日期格式应当被拒绝")
    except ValueError as error:
        ok("格式" in str(error), f"错误日期格式被拒绝（{error}）")
    app.var_date.set(core.today())

    app.var_order.set("abc")
    try:
        app._collect_meta()
        ok(False, "非整数排序应当被拒绝")
    except ValueError as error:
        ok("整数" in str(error), f"非整数排序被拒绝（{error}）")
    app.var_order.set("100")
    ok(isinstance(app._collect_meta(), dict), "_collect_meta 正常返回字典")

    print("\n=== 生成 + 预览服务器 ===")
    lines = core.build_all(NEW_SLUG)
    ok(any("index.html" in line for line in lines), "生成日志里有页面输出")
    ok((core.ROOT / NEW_SLUG / "index.html").exists(), "项目页已生成")
    page = core.read_text(core.ROOT / NEW_SLUG / "index.html")
    ok("改过的摘要" in core.read_text(core.ROOT / "index.html"), "首页卡片用上了新摘要")
    ok("新的正文" in page, "项目页用上了新正文")

    server = writepad.PreviewServer(core.ROOT)
    try:
        url = server.url_for(f"{NEW_SLUG}/")
        ok(url.startswith("http://127.0.0.1:"), f"预览地址是本机 HTTP（{url}）")
        with urllib.request.urlopen(url, timeout=10) as response:
            body = response.read().decode("utf-8")
            ok(response.status == 200, "预览服务器返回 200")
            ok("新的正文" in body, "预览内容是新正文")
            ok("/assets/research.css" in body, "预览页面引用根路径资源")
        with urllib.request.urlopen(server.url_for("assets/research.css"), timeout=10) as response:
            ok(response.status == 200, "静态资源也能通过预览服务器取到")
        index_url = server.url_for("")
        with urllib.request.urlopen(index_url, timeout=10) as response:
            ok(response.status == 200, "首页也能预览")
    finally:
        server.stop()
    ok(server.port is None, "stop() 之后端口已释放")

    print("\n=== 自检 ===")
    issues = core.run_checks()
    ok(not issues, f"整站自检通过（问题数 {len(issues)}）")
    for issue in issues:
        print(f"        ! {issue}")

    print("\n=== 空项目列表时的表现 ===")
    app.load_record(NEW_SLUG)
    root.update()
    ok(app._collect_meta()["title"] == "冒烟测试项目", "重新载入后表单仍然正确")

    # ── 主题 ──────────────────────────────────────────────────────────
    print("\n=== 主题 ===")
    ok(set(writepad.PALETTES["light"]) == set(writepad.PALETTES["dark"]),
       "亮暗两套配色的键完全一致（少一个键就会 KeyError）")
    ok(all(v.startswith("#") or v.startswith("rgb") for v in writepad.PALETTES["dark"].values()),
       "暗色配色的值都是颜色")

    app.set_theme("light", announce=False)
    light = app._palette()
    root.update()
    ok(same_color(app.body.cget("background"), light["editor_bg"]), "亮色：正文底色跟随主题")
    ok(same_color(app.root.cget("background"), light["bg"]), "亮色：窗口底色跟随主题")
    ok(same_color(app.log_text.cget("background"), light["log_bg"]), "亮色：日志底色跟随主题")
    ok(same_color(app.view_menu.cget("background"), light["field"]), "亮色：弹出菜单也重新配色")

    app.set_theme("dark", announce=False)
    dark = app._palette()
    root.update()
    ok(same_color(app.body.cget("background"), dark["editor_bg"]), "暗色：正文底色跟随主题")
    ok(same_color(app.body.cget("foreground"), dark["editor_fg"]), "暗色：正文前景色跟随主题")
    ok(same_color(app.root.cget("background"), dark["bg"]), "暗色：窗口底色跟随主题")
    ok(same_color(app.log_text.cget("background"), dark["log_bg"]), "暗色：日志底色跟随主题")
    ok(same_color(app.view_menu.cget("background"), dark["field"]), "暗色：弹出菜单也重新配色")
    ok(same_color(app.body.tag_cget("curline", "background"), dark["curline"]),
       "暗色：当前行高亮底色已更新")
    ok(same_color(app.body.tag_cget("find", "background"), dark["find_bg"]),
       "暗色：查找高亮底色已更新")
    ok(dark["editor_bg"] != light["editor_bg"], "两套主题确实不一样")

    app.set_theme("light", announce=False)
    app.toggle_theme()
    ok(app.theme_choice == "dark", f"toggle_theme 从亮色切到暗色（{app.theme_choice}）")
    app.toggle_theme()
    ok(app.theme_choice == "light", "toggle_theme 再切回亮色")
    app.set_theme("system", announce=False)
    ok(app._palette() in (writepad.PALETTES["light"], writepad.PALETTES["dark"]),
       "跟随系统能解析成一套具体配色")

    # ── 缩放 ──────────────────────────────────────────────────────────
    print("\n=== 缩放 ===")
    app.set_theme("light", announce=False)
    app.set_zoom(1.0, announce=False)
    base_ui = tkfont.nametofont("TkDefaultFont").cget("size")
    base_body = font_size(app.body)
    app.set_zoom(1.5, announce=False)
    root.update()
    ok(tkfont.nametofont("TkDefaultFont").cget("size") == round(10 * 1.5),
       f"150% 时界面字体放大（{base_ui} → {tkfont.nametofont('TkDefaultFont').cget('size')}）")
    ok(font_size(app.body) == round(11 * 1.5),
       f"150% 时正文字体放大（{base_body} → {font_size(app.body)}）")
    ok(app.var_zoom.get() == "缩放 150%", f"状态栏显示缩放比例（{app.var_zoom.get()}）")

    minsize_big = app.root.minsize()
    app.set_zoom(1.0, announce=False)
    root.update()
    minsize_small = app.root.minsize()
    ok(minsize_big[0] >= minsize_small[0], f"缩放变大时窗口最小宽度跟着变大（{minsize_small[0]} → {minsize_big[0]}）")
    ok(minsize_small[0] > 0 and minsize_small[1] > 0, "最小尺寸有有效值")

    app.zoom_in()
    ok(app.zoom == 1.1, f"zoom_in 一档 +10%（{app.zoom}）")
    app.zoom_out()
    ok(app.zoom == 1.0, f"zoom_out 一档 -10%（{app.zoom}）")
    for _ in range(20):
        app.zoom_in()
    ok(app.zoom == writepad.ZOOM_MAX, f"放大有上限（{app.zoom}）")
    for _ in range(30):
        app.zoom_out()
    ok(app.zoom == writepad.ZOOM_MIN, f"缩小有下限（{app.zoom}）")
    app.zoom_reset()
    ok(app.zoom == 1.0, "zoom_reset 回到 100%")
    app._on_ctrl_wheel(types.SimpleNamespace(delta=120))
    ok(app.zoom == 1.1, f"Ctrl+滚轮向上放大（{app.zoom}）")
    app._on_ctrl_wheel(types.SimpleNamespace(delta=-120))
    ok(app.zoom == 1.0, "Ctrl+滚轮向下缩小")

    # ── 编辑器行为 ────────────────────────────────────────────────────
    print("\n=== 编辑器行为 ===")
    app.body.delete("1.0", "end")

    app.body.insert("1.0", "一行文字")
    app.body.mark_set("insert", "1.2")
    app._on_tab()
    ok(app.body.get("1.0", "end-1c") == "    一行文字",
       f"Tab 插入四个空格而不是切换焦点（{app.body.get('1.0', 'end-1c')!r}）")
    app._on_tab(dedent=True)
    ok(app.body.get("1.0", "end-1c") == "一行文字", "Shift+Tab 反缩进四个空格")

    app.body.delete("1.0", "end")
    app.body.insert("1.0", "甲\n乙\n丙")
    app.body.tag_add(tk.SEL, "1.0", "3.end")
    app._on_tab()
    lines = app.body.get("1.0", "end-1c").split("\n")
    ok(all(line.startswith("    ") for line in lines), f"多行选中整块缩进（{lines}）")
    app._on_tab(dedent=True)
    lines = app.body.get("1.0", "end-1c").split("\n")
    ok(all(not line.startswith(" ") for line in lines), "多行选中整块反缩进")

    # 选区停在下一行行首时，那一行不算被选中（和多数编辑器的约定一致）
    app.body.tag_remove(tk.SEL, "1.0", "end")
    app.body.tag_add(tk.SEL, "1.0", "3.0")
    app._on_tab()
    lines = app.body.get("1.0", "end-1c").split("\n")
    ok(lines[0].startswith("    ") and lines[1].startswith("    ")
       and not lines[2].startswith(" "),
       f"选区停在行首时最后一行不缩进（{lines}）")
    app.body.tag_remove(tk.SEL, "1.0", "end")
    for line in range(1, 4):
        app.body.delete(f"{line}.0", f"{line}.4")

    app.body.delete("1.0", "end")
    app.body.insert("1.0", "- 第一条")
    app.body.mark_set("insert", "end")
    app._on_return()
    ok(app.body.get("1.0", "end-1c") == "- 第一条\n- ", "回车延续无序列表标记")
    app.body.insert("insert", "第二条")
    app._on_return()
    ok(app.body.get("1.0", "end-1c").endswith("- 第二条\n- "), "第二条也会延续")

    app.body.delete("1.0", "end")
    app.body.insert("1.0", "3. 第三项")
    app.body.mark_set("insert", "end")
    app._on_return()
    ok(app.body.get("1.0", "end-1c") == "3. 第三项\n4. ", "回车让有序列表编号递增")

    app.body.delete("1.0", "end")
    app.body.insert("1.0", "- [ ] 待办")
    app.body.mark_set("insert", "end")
    app._on_return()
    ok(app.body.get("1.0", "end-1c") == "- [ ] 待办\n- [ ] ", "回车延续任务列表并重置为未勾选")

    app.body.delete("1.0", "end")
    app.body.insert("1.0", "- ")
    app.body.mark_set("insert", "end")
    app._on_return()
    ok(app.body.get("1.0", "end-1c") == "\n",
       f"空列表项上回车把标记去掉、只留一个空行（{app.body.get('1.0', 'end-1c')!r}）")

    app.body.delete("1.0", "end")
    app.body.insert("1.0", "  indented")
    app.body.mark_set("insert", "end")
    app._on_return()
    ok(app.body.get("1.0", "end-1c") == "  indented\n  ", "回车延续普通缩进")

    app.body.delete("1.0", "end")
    app.body.insert("1.0", "第一行\n第二行\n第三行")
    app.body.mark_set("insert", "2.1")
    app._on_cursor_move()
    ranges = app.body.tag_ranges("curline")
    ok(len(ranges) == 2, "当前行高亮已打上")
    ok(str(ranges[0]) == "2.0" and str(ranges[1]) == "3.0",
       f"高亮范围正好是第 2 行（{ranges[0]} → {ranges[1]}）")
    ok(app.var_cursor.get() == "行 2 · 列 2", f"状态栏显示行列（{app.var_cursor.get()}）")

    # ── 查找 / 替换 ───────────────────────────────────────────────────
    print("\n=== 查找 / 替换 ===")
    app.body.delete("1.0", "end")
    app.body.insert("1.0", "薄膜沉积\n薄膜厚度\n与薄膜无关的一行")
    app.on_find()
    root.update()
    ok(app.find_bar.winfo_ismapped(), "Ctrl+F 后查找条显示出来")
    app.find_var.set("薄膜")
    app._highlight_matches()
    ok(len(app._matches()) == 3, f"找到 3 处匹配（{len(app._matches())}）")
    ok(app.var_find_info.get() == "3 处", f"匹配数量显示在查找条上（{app.var_find_info.get()}）")
    ok(len(app.body.tag_ranges("find")) == 6, "所有匹配都打上了高亮 tag")

    app.body.mark_set("insert", "1.0")
    # 光标停在第一处上，向后走一格 → 第二处（「薄膜」两个字，末尾在第 2 列）
    app.find_next(True)
    ok(app.body.index("insert") == "2.2", f"下一个：从第一处走到第二处（{app.body.index('insert')}）")
    ok(app.body.get(tk.SEL_FIRST, tk.SEL_LAST) == "薄膜", "找到之后这一段被选中，替换才有东西可换")
    app.find_next(True)
    ok(app.body.index("insert") == "3.3", f"下一个：继续往后（{app.body.index('insert')}）")
    app.find_next(True)
    ok(app.body.index("insert") == "1.2", f"下一个：到底了绕回第一处（{app.body.index('insert')}）")
    app.find_next(False)
    ok(app.body.index("insert") == "3.3", f"上一个：从第一处绕回最后一处（{app.body.index('insert')}）")

    app.find_var.set("不存在的内容")
    app._highlight_matches()
    ok(app._matches() == [] and app.var_find_info.get() == "没找到", "找不到时给出提示")

    # 替换词刻意不含查找词：否则「全部替换」会把上一次替换的结果再换一遍，
    # 那属于替换语义本身，不该混进这条断言里
    app.find_var.set("薄膜")
    app._highlight_matches()
    app.replace_var.set("膜层")
    app.body.mark_set("insert", "1.0")
    app.find_next(True)                 # 落到第二处，并选中它
    app.replace_one()
    ok(app.body.get("1.0", "2.end") == "薄膜沉积\n膜层厚度",
       f"替换当前匹配（{app.body.get('1.0', '2.end')!r}）")
    app.replace_all()
    ok(app.body.get("1.0", "end-1c") == "膜层沉积\n膜层厚度\n与膜层无关的一行",
       f"全部替换剩余匹配（{app.body.get('1.0', 'end-1c')!r}）")
    ok(app.dirty is True, "替换算作修改，已标脏")

    app.find_var.set("")
    app.close_find()
    root.update()
    ok(not app.find_bar.winfo_ismapped(), "关闭后查找条隐藏")
    ok(len(app.body.tag_ranges("find")) == 0, "关闭后查找高亮清干净")
    ok(len(app.body.tag_ranges("find-current")) == 0, "关闭后当前匹配高亮也清干净")

    # ── 状态持久化 ────────────────────────────────────────────────────
    print("\n=== 状态持久化 ===")
    app.set_theme("dark", announce=False)
    app.set_zoom(1.3, announce=False)
    app.log_visible = True
    app._save_state()
    saved = core.read_json(writepad.STATE_PATH)
    ok(saved.get("theme") == "dark", "主题被写进状态文件")
    ok(abs(float(saved.get("zoom", 0)) - 1.3) < 1e-6, f"缩放被写进状态文件（{saved.get('zoom')}）")
    ok("geometry" in saved and "sash" in saved, "窗口尺寸与分栏位置也记下来了")
    ok(saved.get("log_visible") is True, "日志面板可见性被记录")

    ok(app._clamp_geometry("3000x2000+9000+9000") != "3000x2000+9000+9000",
       "屏幕外的窗口坐标会被夹回屏幕内")
    ok(app._clamp_geometry("1200x800") == "1200x800", "没有坐标的尺寸原样保留")
    ok(app._clamp_geometry("坏数据") == "坏数据", "无法解析的尺寸不炸，原样返回")

    app.load_record(NEW_SLUG)
    root.update()
    ok(app._collect_meta()["title"] == "冒烟测试项目", "测试新功能之后项目数据仍然完好")

finally:
    if root is not None:
        try:
            root.destroy()
        except tk.TclError:
            pass
    cleanup()
    core.build_all()
    # 恢复用户原本的视图偏好
    if state_backup is not None:
        writepad.STATE_PATH.write_text(state_backup, encoding="utf-8")
    elif writepad.STATE_PATH.exists():
        writepad.STATE_PATH.unlink()

print("\n" + "=" * 60)
print(f"共 {count[0]} 项断言，失败 {len(fails)} 项" + ("" if not fails else " ❌"))
for f in fails:
    print(f"  ✗ {f}")
sys.exit(1 if fails else 0)
