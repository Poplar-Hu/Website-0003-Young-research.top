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

finally:
    if root is not None:
        try:
            root.destroy()
        except tk.TclError:
            pass
    cleanup()
    core.build_all()

print("\n" + "=" * 60)
print(f"共 {count[0]} 项断言，失败 {len(fails)} 项" + ("" if not fails else " ❌"))
for f in fails:
    print(f"  ✗ {f}")
sys.exit(1 if fails else 0)
