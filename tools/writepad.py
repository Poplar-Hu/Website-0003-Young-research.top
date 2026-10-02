#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
科研记录写字板 —— research.who-young.top 的图形化录入工具
============================================================================
左边填元数据、右边写正文，点一下就生成静态页面并重建首页。

    python tools/writepad.py            打开写字板
    python tools/writepad.py <slug>     直接打开某个项目

为什么是 tkinter 而不是网页版编辑器
    「输入 → 本地出 HTML → 打包上传」这条链子必须在断网、也没有任何第三方
    Python 包的机器上跑得起来，所以只用标准库。真正把 Markdown 编译成 HTML
    的活由同目录的 research_core.py 干，这个文件只负责界面。

界面速查
    Ctrl+S        保存（写回 content/<slug>/）
    Ctrl+Enter    生成当前项目 + 重建首页
    F5            在浏览器里预览（内置了一个只读的本地预览服务器）
    Ctrl+B/I/K    加粗 / 斜体 / 链接
    Ctrl+Z        撤销（正文框开了 undo）

几个刻意的设计
1. 预览走本地 HTTP 而不是 file://
   生成出来的页面用的是 /assets/... 这种根路径，file:// 打开会全崩；
   内置服务器监听 127.0.0.1 的随机端口，只服务本仓库目录，仅本机可访问。
2. 删项目不真删
   挪到仓库根的 .writepad-trash/，那里已被 .gitignore 忽略，
   所以不会被「从分支部署」发布出去，反悔了还能捞回来。
3. 改路径 = 改目录名
   保存时如果 slug 变了，会调用 core.rename_record 把目录改名并清掉旧页面。
"""

from __future__ import annotations

import datetime as _dt
import functools
import http.server
import os
import re
import shutil
import socketserver
import sys
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, font as tkfont, messagebox, ttk

# 同目录导入：写这个文件的时候它就在 tools/ 下，Python 会把脚本所在目录
# 放进 sys.path，所以直接 import 就行
sys.path.insert(0, str(Path(__file__).resolve().parent))
import research_core as core  # noqa: E402

STATE_PATH = core.TOOLS_DIR / ".writepad-state.json"

STATUS_CHOICES = [
    ("active", "进行中"), ("done", "已完成"), ("paused", "暂停"),
    ("planned", "计划中"), ("archived", "已归档"),
]
COLOR_CHOICES = [
    ("blue", "蓝"), ("pink", "粉"), ("green", "绿"),
    ("lav", "紫"), ("amber", "琥珀"), ("gray", "灰"),
]

#: 图标的中文说法。写字板里只显示名字，看不懂 fa-thermometer-half 是干什么的，
#: 所以给每个候选图标配一句人话（图标本身要页面上才看得到）。
ICON_LABELS = {
    "fa-flask": "锥形瓶 · 化学",
    "fa-microscope": "显微镜 · 观察",
    "fa-eyedropper": "滴管 · 取样",
    "fa-thermometer-half": "温度 · 热学",
    "fa-bolt": "闪电 · 电学 / 快",
    "fa-cube": "立方体 · 样品",
    "fa-cogs": "齿轮组 · 装置",
    "fa-gears": "齿轮 · 机械",
    "fa-magic": "魔法棒 · 调参",
    "fa-puzzle-piece": "拼图 · 组合",
    "fa-line-chart": "折线图 · 趋势",
    "fa-area-chart": "面积图 · 累积",
    "fa-bar-chart": "柱状图 · 对比",
    "fa-pie-chart": "饼图 · 占比",
    "fa-table": "表格 · 数据",
    "fa-calculator": "计算器 · 计算",
    "fa-superscript": "上标 · 公式",
    "fa-percent": "百分比 · 比例",
    "fa-database": "数据库 · 数据管理",
    "fa-server": "服务器 · 后端",
    "fa-cloud": "云 · 云端",
    "fa-hdd-o": "硬盘 · 存储",
    "fa-code": "代码 · 编程",
    "fa-terminal": "终端 · 脚本",
    "fa-laptop": "笔记本 · 软件",
    "fa-desktop": "台式机 · 工作站",
    "fa-mobile": "手机 · 移动端",
    "fa-file-text-o": "文档 · 论文",
    "fa-book": "书 · 文献 / 说明",
    "fa-bookmark": "书签 · 标记",
    "fa-sticky-note": "便签 · 待整理",
    "fa-clipboard": "剪贴板 · 记录",
    "fa-camera": "相机 · 拍照",
    "fa-picture-o": "图片 · 图像",
    "fa-video-camera": "摄像机 · 视频",
    "fa-eye": "眼睛 · 观测",
    "fa-rocket": "火箭 · 推进 / 启动",
    "fa-plane": "飞机 · 出行 / 留学",
    "fa-globe": "地球 · 全球",
    "fa-map-o": "地图 · 路线",
    "fa-compass": "指南针 · 方向",
    "fa-leaf": "叶子 · 生物 / 环境",
    "fa-tint": "水滴 · 流体",
    "fa-fire": "火 · 高温",
    "fa-snowflake-o": "雪花 · 低温",
    "fa-sun-o": "太阳 · 光照",
    "fa-heartbeat": "心跳 · 生理",
    "fa-stethoscope": "听诊器 · 医学",
    "fa-user-md": "医生 · 临床",
    "fa-medkit": "医疗箱 · 生物医学",
    "fa-lightbulb-o": "灯泡 · 想法",
    "fa-star": "星星 · 重点",
    "fa-flag": "旗帜 · 里程碑",
    "fa-check-circle": "对钩 · 已完成",
    "fa-question-circle": "问号 · 待查",
}


def normalize_slug(raw: str) -> str:
    """把用户输入的路径整理一下：转小写，空格和下划线换成连字符

    刻意【不用】core.slugify —— 它会把中文整段丢掉，最后退回 "record"。
    于是输入「有中文的路径」会静默变成 record，一保存就把项目改到了
    /record/ 上，而用户看着输入框里的中文还以为没事。
    这里只做无害的整理，合法与否交给 core.valid_slug 明确拒绝。
    """
    text = (raw or "").strip().lower().replace("_", "-").replace(" ", "-")
    text = re.sub(r"-{2,}", "-", text)
    return text.strip("-")


# ══════════════════════════════════════════════════════════════════════════
#  内置预览服务器
# ══════════════════════════════════════════════════════════════════════════

class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    """只服务仓库目录，且不往控制台刷日志"""

    def log_message(self, *args):
        pass


class PreviewServer:
    """本机只读预览服务器：监听 127.0.0.1 的随机端口

    生成出来的页面用 /assets/... 这种根路径，直接用 file:// 打开样式全丢，
    所以预览必须走 HTTP。绑 127.0.0.1 而不是 0.0.0.0，外部访问不到。
    """

    def __init__(self, root: Path):
        self.root = root
        self.port: int | None = None
        self._server: socketserver.TCPServer | None = None

    def ensure(self) -> int:
        if self.port:
            return self.port
        handler = functools.partial(_QuietHandler, directory=str(self.root))
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._server = server
        self.port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return self.port

    def url_for(self, relative: str) -> str:
        port = self.ensure()
        return f"http://127.0.0.1:{port}/{relative.lstrip('/')}"

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
            self.port = None


# ══════════════════════════════════════════════════════════════════════════
#  新建项目对话框
# ══════════════════════════════════════════════════════════════════════════

class NewProjectDialog(tk.Toplevel):
    """新建项目：标题 + 路径

    路径必须是小写 ASCII 的短名。中文标题不能直接当路径 —— GitHub Pages 会把
    中文路径服务成一长串百分号编码，分享和记笔记时都很难用。
    """

    def __init__(self, master, cfg: dict):
        super().__init__(master)
        self.title("新建项目")
        self.resizable(False, False)
        self.transient(master)
        self.result: tuple[str, str, str] | None = None

        self.var_title = tk.StringVar()
        self.var_slug = tk.StringVar(value=f"exp-{_dt.date.today().strftime('%Y%m%d')}")
        self.var_status = tk.StringVar(value="进行中")
        self._slug_touched = False

        body = ttk.Frame(self, padding=18)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)

        ttk.Label(body, text="项目标题").grid(row=0, column=0, sticky="w", pady=(0, 4))
        entry_title = ttk.Entry(body, textvariable=self.var_title, width=42)
        entry_title.grid(row=0, column=1, sticky="ew", pady=(0, 4))

        ttk.Label(body, text="路径（URL）").grid(row=1, column=0, sticky="w", pady=4)
        entry_slug = ttk.Entry(body, textvariable=self.var_slug, width=42)
        entry_slug.grid(row=1, column=1, sticky="ew", pady=4)
        self.var_slug.trace_add("write", self._on_slug_change)

        ttk.Label(
            body,
            text="只用小写字母、数字和连字符，例如 annealing-rate。\n"
                 "它同时是目录名和网址：research.who-young.top/路径/",
            foreground="#6B7280", justify="left",
        ).grid(row=2, column=1, sticky="w", pady=(0, 8))

        ttk.Label(body, text="初始状态").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Combobox(body, textvariable=self.var_status, state="readonly", width=12,
                     values=[label for _, label in STATUS_CHOICES]).grid(row=3, column=1, sticky="w", pady=4)

        ttk.Label(body, text="正文模板").grid(row=4, column=0, sticky="w", pady=4)
        ttk.Label(body, text="目的与假设 / 装置与参数 / 步骤 / 原始数据 / 分析与讨论 / 结论",
                  foreground="#6B7280").grid(row=4, column=1, sticky="w", pady=4)

        buttons = ttk.Frame(body)
        buttons.grid(row=5, column=0, columnspan=2, sticky="e", pady=(16, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(buttons, text="创建", command=self._ok, style="Accent.TButton").pack(side="right")

        entry_title.focus_set()
        self.bind("<Return>", lambda _e: self._ok())
        self.bind("<Escape>", lambda _e: self.destroy())
        self.grab_set()
        self.wait_visibility()

    def _on_slug_change(self, *_args):
        self._slug_touched = True

    def _ok(self):
        title = self.var_title.get().strip()
        slug = normalize_slug(self.var_slug.get())
        if not title:
            messagebox.showwarning("还差一步", "项目标题不能为空。", parent=self)
            return
        if not core.valid_slug(slug):
            messagebox.showwarning(
                "路径不合法",
                f"「{slug}」不能作为路径。\n\n"
                "只允许小写字母、数字和连字符，且不能占用 "
                "assets / lang / content / tools / en / jp 这些保留名。",
                parent=self,
            )
            return
        if (core.CONTENT_DIR / slug).exists():
            messagebox.showwarning("路径已被占用", f"content/{slug}/ 已经存在了。", parent=self)
            return
        status = dict((label, value) for value, label in STATUS_CHOICES)[self.var_status.get()]
        self.result = (slug, title, status)
        self.destroy()


# ══════════════════════════════════════════════════════════════════════════
#  站点设置对话框
# ══════════════════════════════════════════════════════════════════════════

class SettingsDialog(tk.Toplevel):
    """编辑 content/_site.json（外链、目录预览条数、首页开关）"""

    FIELDS = [
        ("base_url", "站点地址", "写进 sitemap.xml 与 robots.txt"),
        ("main_site", "主站地址", "导航栏「主站」指向"),
        ("tools_site", "工具站地址", "导航栏「在线工具」指向"),
        ("contact_url", "联系页地址", "页脚「联系我」指向"),
        ("repo_url", "仓库地址", "备用，暂时只写在配置里"),
        ("toc_preview_limit", "卡片目录条数", "首页每张卡片最多列几节目录"),
    ]
    SWITCHES = [
        ("show_stats", "首页显示统计条"),
        ("show_catalog", "首页显示完整目录"),
    ]

    def __init__(self, master, cfg: dict):
        super().__init__(master)
        self.title("站点设置")
        self.resizable(False, False)
        self.transient(master)
        self.saved = False
        self.cfg = dict(cfg)
        self.vars: dict[str, tk.StringVar] = {}
        self.switch_vars: dict[str, tk.BooleanVar] = {}

        body = ttk.Frame(self, padding=18)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)

        for row, (key, label, hint) in enumerate(self.FIELDS):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", pady=4)
            var = tk.StringVar(value=str(self.cfg.get(key, "")))
            self.vars[key] = var
            ttk.Entry(body, textvariable=var, width=46).grid(row=row, column=1, sticky="ew", pady=4)
            ttk.Label(body, text=hint, foreground="#9CA3AF").grid(
                row=row, column=2, sticky="w", padx=(10, 0), pady=4)

        for offset, (key, label) in enumerate(self.SWITCHES):
            var = tk.BooleanVar(value=bool(self.cfg.get(key, True)))
            self.switch_vars[key] = var
            ttk.Checkbutton(body, text=label, variable=var).grid(
                row=len(self.FIELDS) + offset, column=1, sticky="w", pady=2)

        note = ttk.Label(
            body,
            text="界面上的固定文案（标题、按钮、页脚）在 lang/cn.json 里，\n"
                 "改完这里记得点「重新生成全部」让页面生效。",
            foreground="#6B7280", justify="left",
        )
        note.grid(row=len(self.FIELDS) + len(self.SWITCHES), column=0, columnspan=3,
                  sticky="w", pady=(12, 0))

        buttons = ttk.Frame(body)
        buttons.grid(row=len(self.FIELDS) + len(self.SWITCHES) + 1, column=0, columnspan=3,
                     sticky="e", pady=(16, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(buttons, text="保存", command=self._ok, style="Accent.TButton").pack(side="right")

        self.bind("<Return>", lambda _e: self._ok())
        self.bind("<Escape>", lambda _e: self.destroy())
        self.grab_set()
        self.wait_visibility()

    def _ok(self):
        data = dict(self.cfg)
        for key, var in self.vars.items():
            value = var.get().strip()
            if key == "toc_preview_limit":
                try:
                    value = max(1, min(20, int(value)))
                except ValueError:
                    messagebox.showwarning("数值不对", "目录条数要是 1~20 的整数。", parent=self)
                    return
            data[key] = value
        for key, var in self.switch_vars.items():
            data[key] = bool(var.get())
        core.write_json(core.SITE_JSON, data)
        self.saved = True
        self.destroy()


# ══════════════════════════════════════════════════════════════════════════
#  主窗口
# ══════════════════════════════════════════════════════════════════════════

class WritepadApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.cfg = core.load_site_config()
        self.records: list[core.Record] = []
        self.current: core.Record | None = None
        self.dirty = False
        self._loading = False          # 载入项目时抑制 dirty 标记
        self._count_job = None
        self._preview: PreviewServer | None = None
        self._body_snapshot = ""       # 用来判断正文是否真的改过

        self.mono = tk.BooleanVar(value=False)
        self.auto_update = tk.BooleanVar(value=True)
        self.var_title = tk.StringVar()
        self.var_slug = tk.StringVar()
        self.var_date = tk.StringVar()
        self.var_updated = tk.StringVar()
        self.var_status = tk.StringVar()
        self.var_icon = tk.StringVar()
        self.var_color = tk.StringVar()
        self.var_tags = tk.StringVar()
        self.var_order = tk.StringVar()
        self.var_pinned = tk.BooleanVar(value=False)
        self.var_cover = tk.StringVar()
        self.var_project = tk.StringVar()
        self.var_statusbar = tk.StringVar(value="就绪")
        self.var_counts = tk.StringVar(value="")

        self._setup_fonts()
        self._build_ui()
        self._bind_keys()
        self.reload_records()
        self._restore_state()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ── 外观 ─────────────────────────────────────────────────────────────
    def _setup_fonts(self):
        """统一字体。tk 默认字体在中文 Windows 上是宋体，标题和正文都难看。"""
        family = "Microsoft YaHei UI"
        available = set(tkfont.families(self.root))
        if family not in available:
            family = "Microsoft YaHei" if "Microsoft YaHei" in available else "Segoe UI"
        for name, size in (("TkDefaultFont", 10), ("TkTextFont", 10), ("TkMenuFont", 10)):
            try:
                tkfont.nametofont(name).configure(family=family, size=size)
            except tk.TclError:
                pass
        self.ui_family = family
        self.editor_family = "Consolas" if "Consolas" in available else "Courier New"

        style = ttk.Style(self.root)
        # clam 下按钮才有正常的 padding 和主题色，Windows 原生主题不认 Accent
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("Accent.TButton", foreground="#ffffff", background="#3B82F6")
        style.map("Accent.TButton",
                  background=[("active", "#2563EB"), ("disabled", "#93C5FD")])
        style.configure("Tool.TButton", padding=(8, 3))
        style.configure("Group.TLabelframe.Label", foreground="#374151")

    def _build_ui(self):
        self.root.title("科研记录写字板 —— research.who-young.top")
        self.root.minsize(1100, 680)

        self._build_toolbar()

        # pack 的顺序决定分配顺序：先把底部两条占掉，剩下的都给正文区
        self._build_statusbar()
        self._build_log()

        paned = ttk.PanedWindow(self.root, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=10, pady=(0, 8))

        left = ttk.Frame(paned, width=420)
        right = ttk.Frame(paned)
        paned.add(left, weight=0)
        paned.add(right, weight=1)
        self._build_meta_panel(left)
        self._build_editor(right)

    def _build_toolbar(self):
        bar = ttk.Frame(self.root, padding=(10, 8, 10, 6))
        bar.pack(fill="x")

        ttk.Label(bar, text="项目").pack(side="left")
        self.project_box = ttk.Combobox(bar, textvariable=self.var_project, state="readonly",
                                        width=34, values=[])
        self.project_box.pack(side="left", padx=(6, 10))
        self.project_box.bind("<<ComboboxSelected>>", lambda _e: self.on_pick_project())

        ttk.Button(bar, text="新建", style="Tool.TButton", command=self.on_new).pack(side="left")
        ttk.Button(bar, text="删除", style="Tool.TButton", command=self.on_delete).pack(side="left", padx=4)

        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=10)

        ttk.Button(bar, text="保存", style="Accent.TButton", command=self.on_save).pack(side="left")
        ttk.Button(bar, text="生成此页", style="Tool.TButton",
                   command=self.on_build_current).pack(side="left", padx=4)
        ttk.Button(bar, text="重新生成全部", style="Tool.TButton",
                   command=self.on_build_all).pack(side="left")

        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=10)

        ttk.Button(bar, text="预览", style="Tool.TButton", command=self.on_preview).pack(side="left")
        ttk.Button(bar, text="预览首页", style="Tool.TButton",
                   command=self.on_preview_index).pack(side="left", padx=4)
        ttk.Button(bar, text="自检", style="Tool.TButton", command=self.on_check).pack(side="left")

        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=10)

        ttk.Button(bar, text="打开输出目录", style="Tool.TButton",
                   command=self.on_open_folder).pack(side="left")
        ttk.Button(bar, text="站点设置", style="Tool.TButton",
                   command=self.on_settings).pack(side="left", padx=4)

    def _build_statusbar(self):
        bar = ttk.Frame(self.root, padding=(12, 4))
        bar.pack(side="bottom", fill="x")
        ttk.Label(bar, textvariable=self.var_statusbar).pack(side="left")
        ttk.Label(bar, textvariable=self.var_counts, foreground="#6B7280").pack(side="right")

    def _build_log(self):
        frame = ttk.LabelFrame(self.root, text="运行日志", style="Group.TLabelframe",
                               padding=(8, 4, 8, 6))
        frame.pack(side="bottom", fill="x", padx=10, pady=(0, 4))

        wrap = ttk.Frame(frame)
        wrap.pack(fill="both", expand=True)
        self.log_text = tk.Text(wrap, height=7, wrap="none", relief="flat",
                                background="#F8FAFC", foreground="#334155",
                                font=(self.editor_family, 9))
        scroll = ttk.Scrollbar(wrap, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set, state="disabled")
        self.log_text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self.log_text.tag_configure("ok", foreground="#047857")
        self.log_text.tag_configure("warn", foreground="#B45309")
        self.log_text.tag_configure("err", foreground="#B91C1C")
        self.log_text.tag_configure("dim", foreground="#94A3B8")

    # ── 左侧：元数据 ─────────────────────────────────────────────────────
    def _build_meta_panel(self, parent):
        frame = ttk.LabelFrame(parent, text="项目元数据", style="Group.TLabelframe",
                               padding=(12, 8, 12, 12))
        frame.pack(fill="both", expand=True, padx=(0, 8))
        frame.columnconfigure(1, weight=1)
        row = 0

        def add_label(text, r):
            ttk.Label(frame, text=text).grid(row=r, column=0, sticky="w", pady=3)

        add_label("标题", row)
        ttk.Entry(frame, textvariable=self.var_title).grid(row=row, column=1, columnspan=2,
                                                           sticky="ew", pady=3)
        row += 1

        add_label("路径", row)
        ttk.Entry(frame, textvariable=self.var_slug).grid(row=row, column=1, columnspan=2,
                                                          sticky="ew", pady=3)
        row += 1
        ttk.Label(frame, text="改这里等于改目录名和网址，保存时生效",
                  foreground="#9CA3AF").grid(row=row, column=1, columnspan=2, sticky="w")
        row += 1

        add_label("创建日期", row)
        ttk.Entry(frame, textvariable=self.var_date, width=14).grid(row=row, column=1, sticky="w", pady=3)
        ttk.Button(frame, text="今天", style="Tool.TButton",
                   command=lambda: self.var_date.set(core.today())).grid(row=row, column=2, sticky="w")
        row += 1

        add_label("更新日期", row)
        ttk.Entry(frame, textvariable=self.var_updated, width=14).grid(row=row, column=1, sticky="w", pady=3)
        ttk.Button(frame, text="今天", style="Tool.TButton",
                   command=lambda: self.var_updated.set(core.today())).grid(row=row, column=2, sticky="w")
        row += 1
        ttk.Checkbutton(frame, text="保存时把「更新」设为今天", variable=self.auto_update).grid(
            row=row, column=1, columnspan=2, sticky="w")
        row += 1

        add_label("状态", row)
        ttk.Combobox(frame, textvariable=self.var_status, state="readonly", width=12,
                     values=[label for _, label in STATUS_CHOICES]).grid(row=row, column=1, sticky="w", pady=3)
        row += 1

        add_label("图标", row)
        self.icon_labels = [f"{ICON_LABELS.get(i, i)}（{i}）" for i in core.ICON_CHOICES]
        self.icon_map = dict(zip(self.icon_labels, core.ICON_CHOICES))
        self.icon_box = ttk.Combobox(frame, textvariable=self.var_icon, state="readonly",
                                     values=self.icon_labels)
        self.icon_box.grid(row=row, column=1, columnspan=2, sticky="ew", pady=3)
        row += 1

        add_label("颜色", row)
        self.color_labels = [f"{label}（{value}）" for value, label in COLOR_CHOICES]
        self.color_map = dict(zip(self.color_labels, [v for v, _ in COLOR_CHOICES]))
        self.color_box = ttk.Combobox(frame, textvariable=self.var_color, state="readonly",
                                      values=self.color_labels)
        self.color_box.grid(row=row, column=1, columnspan=2, sticky="ew", pady=3)
        row += 1

        add_label("标签", row)
        ttk.Entry(frame, textvariable=self.var_tags).grid(row=row, column=1, columnspan=2,
                                                          sticky="ew", pady=3)
        row += 1
        ttk.Label(frame, text="用逗号分隔，例如：薄膜, XRD, 退火",
                  foreground="#9CA3AF").grid(row=row, column=1, columnspan=2, sticky="w")
        row += 1

        add_label("摘要", row)
        self.summary_text = tk.Text(frame, height=4, wrap="word", relief="solid", borderwidth=1,
                                    font=(self.ui_family, 10), padx=6, pady=4)
        self.summary_text.grid(row=row, column=1, columnspan=2, sticky="ew", pady=3)
        row += 1
        ttk.Label(frame, text="显示在首页卡片上，一两句话即可",
                  foreground="#9CA3AF").grid(row=row, column=1, columnspan=2, sticky="w")
        row += 1

        add_label("封面图", row)
        ttk.Entry(frame, textvariable=self.var_cover).grid(row=row, column=1, sticky="ew", pady=3)
        ttk.Button(frame, text="选图", style="Tool.TButton",
                   command=self.on_pick_cover).grid(row=row, column=2, sticky="w", padx=(4, 0))
        row += 1

        add_label("排序", row)
        ttk.Spinbox(frame, textvariable=self.var_order, from_=0, to=999, width=6).grid(
            row=row, column=1, sticky="w", pady=3)
        ttk.Checkbutton(frame, text="置顶", variable=self.var_pinned).grid(
            row=row, column=2, sticky="w", padx=(4, 0))
        row += 1
        ttk.Label(frame, text="数字小的排前面；勾了置顶的排在最前",
                  foreground="#9CA3AF").grid(row=row, column=1, columnspan=2, sticky="w")

        for var in (self.var_title, self.var_slug, self.var_date, self.var_updated,
                    self.var_tags, self.var_cover, self.var_order):
            var.trace_add("write", lambda *_a: self.mark_dirty())
        self.summary_text.bind("<<Modified>>", self._on_summary_modified)
        self.var_pinned.trace_add("write", lambda *_a: self.mark_dirty())
        self.var_status.trace_add("write", lambda *_a: self.mark_dirty())
        self.var_icon.trace_add("write", lambda *_a: self.mark_dirty())
        self.var_color.trace_add("write", lambda *_a: self.mark_dirty())
        self.var_date.trace_add("write", lambda *_a: self._sync_date_display())

    def _on_summary_modified(self, _event=None):
        if self.summary_text.edit_modified():
            self.summary_text.edit_modified(False)
            self.mark_dirty()

    def _sync_date_display(self):
        """新建时创建日期跟着更新日期走，省得每次点两次「今天」"""
        if self._loading:
            return
        if not self.var_updated.get().strip():
            self.var_updated.set(self.var_date.get().strip())

    # ── 右侧：正文 ───────────────────────────────────────────────────────
    def _build_editor(self, parent):
        frame = ttk.LabelFrame(parent, text="正文（Markdown）", style="Group.TLabelframe",
                               padding=(10, 6, 10, 10))
        frame.pack(fill="both", expand=True)

        tools = ttk.Frame(frame)
        tools.pack(fill="x", pady=(0, 6))
        buttons = [
            ("二级标题", lambda: self.insert_block("## ", "", "小节标题")),
            ("三级标题", lambda: self.insert_block("### ", "", "小小节标题")),
            ("加粗", lambda: self.wrap_sel("**", "**", "加粗文字")),
            ("斜体", lambda: self.wrap_sel("*", "*", "斜体文字")),
            ("行内代码", lambda: self.wrap_sel("`", "`", "code")),
            ("代码块", self.insert_code_block),
            ("表格", self.insert_table),
            ("图片", self.insert_image),
            ("图注", self.insert_figure_caption),
            ("引用", lambda: self.insert_block("> ", "", "引用文字")),
            ("列表", lambda: self.insert_block("- ", "", "列表项")),
            ("有序列表", lambda: self.insert_block("1. ", "", "列表项")),
            ("任务", lambda: self.insert_block("- [ ] ", "", "待办事项")),
            ("链接", self.insert_link),
            ("分隔线", lambda: self.insert_block("\n---\n", "", "")),
            ("Mermaid", self.insert_mermaid),
        ]
        for text, command in buttons:
            ttk.Button(tools, text=text, style="Tool.TButton", command=command).pack(side="left", padx=1)

        ttk.Checkbutton(tools, text="等宽", variable=self.mono,
                        command=self.apply_editor_font).pack(side="right")

        wrap = ttk.Frame(frame)
        wrap.pack(fill="both", expand=True)
        self.body = tk.Text(wrap, wrap="word", undo=True, maxundo=-1, autoseparators=True,
                            relief="solid", borderwidth=1, padx=10, pady=8,
                            insertbackground="#1E293B")
        scroll = ttk.Scrollbar(wrap, orient="vertical", command=self.body.yview)
        self.body.configure(yscrollcommand=scroll.set)
        self.body.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self.body.bind("<<Modified>>", self._on_body_modified)
        self.apply_editor_font()

    def apply_editor_font(self):
        family = self.editor_family if self.mono.get() else self.ui_family
        size = 10 if self.mono.get() else 11
        self.body.configure(font=(family, size), spacing1=1, spacing3=3)

    # ── 快捷键 ───────────────────────────────────────────────────────────
    def _bind_keys(self):
        self.root.bind("<Control-s>", lambda _e: (self.on_save(), "break")[1])
        self.root.bind("<Control-S>", lambda _e: (self.on_save(), "break")[1])
        self.root.bind("<Control-Return>", lambda _e: (self.on_build_current(), "break")[1])
        self.root.bind("<F5>", lambda _e: (self.on_preview(), "break")[1])
        self.root.bind("<Control-b>", lambda _e: (self.wrap_sel("**", "**", "加粗文字"), "break")[1])
        self.root.bind("<Control-i>", lambda _e: (self.wrap_sel("*", "*", "斜体文字"), "break")[1])
        self.root.bind("<Control-k>", lambda _e: (self.insert_link(), "break")[1])

    # ── 状态与日志 ───────────────────────────────────────────────────────
    def log(self, message: str, kind: str = ""):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message + "\n", kind or ())
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def log_lines(self, lines, kind: str = ""):
        for line in lines:
            self.log(line, kind)

    def say(self, message: str):
        self.var_statusbar.set(message)
        self.root.update_idletasks()

    def mark_dirty(self):
        if self._loading:
            return
        if not self.dirty:
            self.dirty = True
            self._refresh_title()

    def _refresh_title(self):
        name = self.current.title if self.current else "（没有项目）"
        star = " *" if self.dirty else ""
        self.root.title(f"科研记录写字板 —— {name}{star}")

    def _refresh_counts(self):
        body = self.body.get("1.0", "end-1c")
        chars, minutes = core.char_stats(body)
        lines = body.count("\n") + 1
        self.var_counts.set(f"{chars} 字 · 约 {minutes} 分钟 · {lines} 行")

    def _on_body_modified(self, _event=None):
        if not self.body.edit_modified():
            return
        self.body.edit_modified(False)
        self.mark_dirty()
        # 统计要遍历全文，按键时别每一下都算，停 400ms 再算
        if self._count_job:
            self.root.after_cancel(self._count_job)
        self._count_job = self.root.after(400, self._refresh_counts)

    # ── 项目读写 ─────────────────────────────────────────────────────────
    def reload_records(self, keep: str | None = None):
        self.records = core.load_records()
        labels = [self._record_label(r) for r in self.records]
        self.project_box.configure(values=labels)
        self._labels = dict(zip(labels, [r.slug for r in self.records]))
        self._slugs = [r.slug for r in self.records]

        if not self.records:
            self.current = None
            self._clear_form()
            self.say("还没有项目，点「新建」开始")
            self._refresh_counts()
            self._refresh_title()
            return

        target = keep or (self.current.slug if self.current else None)
        if target not in self._slugs:
            target = self._slugs[0]
        self.load_record(target)

    @staticmethod
    def _record_label(record: core.Record) -> str:
        pin = "★ " if record.pinned else ""
        status = dict((v, label) for v, label in STATUS_CHOICES).get(record.status, record.status)
        return f"{pin}{record.title}　·　{status}　·　{record.updated or '—'}"

    def _select_in_box(self, slug: str):
        for label, value in self._labels.items():
            if value == slug:
                self.var_project.set(label)
                return

    def load_record(self, slug: str):
        record = core.load_record(slug)
        self.current = record
        self._loading = True
        try:
            meta = record.meta
            self.var_title.set(meta.get("title", ""))
            self.var_slug.set(record.slug)
            self.var_date.set(meta.get("date", ""))
            self.var_updated.set(meta.get("updated", ""))
            status_label = dict((v, label) for v, label in STATUS_CHOICES).get(record.status, "进行中")
            self.var_status.set(status_label)
            self.var_tags.set("，".join(record.tags) if record.tags else "")
            self.var_order.set(str(record.order))
            self.var_pinned.set(record.pinned)
            self.var_cover.set(meta.get("cover", ""))

            self.summary_text.delete("1.0", "end")
            self.summary_text.insert("1.0", record.summary)
            # 必须把 modified 标记清掉：不清的话 <<Modified>> 事件会在下一次
            # 事件循环里触发，把刚打开的项目立刻标成「有未保存改动」，
            # 标题栏多一个 *、切项目时还会被追问要不要保存。
            self.summary_text.edit_modified(False)

            # 图标 / 颜色：把实际值对应回下拉里的标签；非候选值就临时补一项，
            # 免得打开旧项目时下拉框显示空白、一保存就把设置抹掉
            self.var_icon.set(self._label_for_icon(record.icon))
            self.var_color.set(self._label_for_color(record.color))

            self.body.delete("1.0", "end")
            self.body.insert("1.0", record.body)
            self.body.edit_reset()
            self.body.edit_modified(False)
            self._body_snapshot = record.body

            self._select_in_box(record.slug)
            self.dirty = False
            self._refresh_title()
            self._refresh_counts()
            self.say(f"已打开 content/{record.slug}/article.md")
        finally:
            self._loading = False

    def _label_for_icon(self, icon: str) -> str:
        """把 meta 里的图标类名对应回下拉框的显示项

        旧项目里可能存着不在候选表里的图标。这时临时往列表里补一项，
        否则下拉框会是空白，用户一碰就把它改成了默认值。
        """
        for label, value in self.icon_map.items():
            if value == icon:
                return label
        extra = f"{icon}（自定义）"
        if extra not in self.icon_map:
            self.icon_map[extra] = icon
            self.icon_labels = self.icon_labels + [extra]
            self.icon_box.configure(values=self.icon_labels)
        return extra

    def _label_for_color(self, color: str) -> str:
        for label, value in self.color_map.items():
            if value == color:
                return label
        extra = f"{color}（自定义）"
        if extra not in self.color_map:
            self.color_map[extra] = color
            self.color_labels = self.color_labels + [extra]
            self.color_box.configure(values=self.color_labels)
        return extra

    def _clear_form(self):
        self._loading = True
        try:
            for var in (self.var_title, self.var_slug, self.var_date, self.var_updated,
                        self.var_tags, self.var_cover):
                var.set("")
            self.var_order.set("100")
            self.var_pinned.set(False)
            self.var_status.set("进行中")
            self.summary_text.delete("1.0", "end")
            self.body.delete("1.0", "end")
            self.var_project.set("")
        finally:
            self._loading = False
        self.dirty = False

    def _collect_meta(self) -> dict:
        title = self.var_title.get().strip()
        slug = normalize_slug(self.var_slug.get())
        if not title:
            raise ValueError("标题不能为空。")
        if not core.valid_slug(slug):
            raise ValueError(
                f"路径「{self.var_slug.get().strip()}」不能用。\n"
                "只能用「小写字母、数字、连字符」，并且不能占用 "
                "assets / lang / content / tools / en / jp 这些保留名。\n"
                "中文写在「标题」里，标题是什么语言都不影响网址。"
            )
        for label, value in (("创建日期", self.var_date.get()), ("更新日期", self.var_updated.get())):
            value = value.strip()
            if value and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                raise ValueError(f"{label}要写成 2026-10-03 这种格式。")
        try:
            order = int(self.var_order.get() or 100)
        except ValueError:
            raise ValueError("排序要填整数。")

        return {
            "title": title,
            "slug": slug,
            "date": self.var_date.get().strip() or core.today(),
            "updated": self.var_updated.get().strip() or self.var_date.get().strip() or core.today(),
            "status": dict((label, value) for value, label in STATUS_CHOICES).get(self.var_status.get(), "active"),
            "tags": [t.strip() for t in re.split(r"[,，、]", self.var_tags.get()) if t.strip()],
            "summary": self.summary_text.get("1.0", "end-1c").strip(),
            "icon": self.icon_map.get(self.var_icon.get(), core.META_DEFAULTS["icon"]),
            "color": self.color_map.get(self.var_color.get(), core.META_DEFAULTS["color"]),
            "cover": self.var_cover.get().strip(),
            "order": order,
            "pinned": bool(self.var_pinned.get()),
        }

    def save(self, silent: bool = False) -> bool:
        try:
            meta = self._collect_meta()
        except ValueError as error:
            messagebox.showwarning("元数据有问题", str(error), parent=self.root)
            return False

        body = self.body.get("1.0", "end-1c")
        old_slug = self.current.slug if self.current else None
        new_slug = meta["slug"]

        # 正文动过就把「更新」刷成今天 —— 绝大多数时候这就是想要的
        if self.auto_update.get() and old_slug and body != self._body_snapshot:
            meta["updated"] = core.today()
            self._loading = True
            self.var_updated.set(meta["updated"])
            self._loading = False

        try:
            if old_slug and new_slug != old_slug:
                core.rename_record(old_slug, new_slug)
                self.log(f"路径改名：{old_slug} → {new_slug}（旧的 /{old_slug}/ 已清理）", "warn")
            core.save_record(new_slug, meta, body)
        except (OSError, ValueError, FileExistsError) as error:
            messagebox.showerror("保存失败", str(error), parent=self.root)
            return False

        self._body_snapshot = body
        self.dirty = False
        self.reload_records(keep=new_slug)
        self.say(f"已保存 content/{new_slug}/")
        if not silent:
            self.log(f"保存 content/{new_slug}/article.md + meta.json", "ok")
        return True

    # ── 工具栏动作 ───────────────────────────────────────────────────────
    def on_pick_project(self):
        slug = self._labels.get(self.var_project.get())
        if not slug or (self.current and slug == self.current.slug):
            return
        if not self.confirm_discard():
            self._select_in_box(self.current.slug if self.current else "")
            return
        self.load_record(slug)

    def has_unsaved(self) -> bool:
        """正文有没有和最近一次载入/保存不一样

        不能只看 self.dirty：<<Modified>> 是排队派发的虚拟事件，
        插入文字之后立刻读 dirty 可能还是 False。真正决定要不要拦下用户、
        问一句「还没保存」的时候，得拿正文实际内容比一次。
        """
        return self.dirty or self.body.get("1.0", "end-1c") != self._body_snapshot

    def confirm_discard(self) -> bool:
        """有未保存改动时问一句。返回 True = 可以继续"""
        if not self.has_unsaved():
            return True
        answer = messagebox.askyesnocancel(
            "还没保存",
            f"「{self.current.title if self.current else '当前项目'}」有未保存的改动。\n\n"
            "选「是」先保存，选「否」丢弃改动。",
            parent=self.root,
        )
        if answer is None:
            return False
        if answer:
            return self.save()
        return True

    def on_new(self):
        if not self.confirm_discard():
            return
        dialog = NewProjectDialog(self.root, self.cfg)
        self.root.wait_window(dialog)
        if not dialog.result:
            return
        slug, title, status = dialog.result
        try:
            core.create_record(slug, title, status=status)
        except (OSError, ValueError, FileExistsError) as error:
            messagebox.showerror("新建失败", str(error), parent=self.root)
            return
        self.reload_records(keep=slug)
        self.body.focus_set()
        self.log(f"新建 content/{slug}/ —— 正文已带好小节模板", "ok")
        self.say(f"新项目 {slug} 建好了，可以开始写")

    def on_delete(self):
        if not self.current:
            messagebox.showinfo("没有项目", "先新建一个项目吧。", parent=self.root)
            return
        title = self.current.title
        slug = self.current.slug
        if not messagebox.askyesno(
            "删除项目",
            f"确定删除「{title}」吗？\n\n"
            f"content/{slug}/ 会被挪到 .writepad-trash/（不是真删，还能捞回来），\n"
            f"生成出来的 /{slug}/ 会一起清掉。",
            parent=self.root,
        ):
            return
        try:
            target = core.delete_record(slug)
        except OSError as error:
            messagebox.showerror("删除失败", str(error), parent=self.root)
            return
        self.current = None
        self.dirty = False
        self.reload_records()
        self.log(f"已删除 {slug} → 回收站 {target.relative_to(core.ROOT).as_posix()}", "warn")
        self._after_build(f"已删除 {slug}")

    def on_save(self):
        if not self.current:
            messagebox.showinfo("没有项目", "先新建一个项目吧。", parent=self.root)
            return
        self.save()

    def on_build_current(self):
        if self.current is None:
            messagebox.showinfo("没有项目", "先新建一个项目吧。", parent=self.root)
            return
        if not self.save(silent=True):
            return
        slug = self.current.slug
        self._run_build(lambda: core.build_all(slug), f"已生成 {slug} 并重建首页")

    def on_build_all(self):
        if not self.confirm_discard():
            return
        self._run_build(lambda: core.build_all(), "整站已重新生成")

    def _run_build(self, job, message: str):
        self.say("正在生成……")
        self.root.configure(cursor="watch")
        try:
            lines = job()
        except Exception as error:                       # noqa: BLE001 - 界面上要看到原因
            self.root.configure(cursor="")
            self.log(f"生成失败：{type(error).__name__}: {error}", "err")
            messagebox.showerror("生成失败", f"{type(error).__name__}: {error}", parent=self.root)
            self.say("生成失败")
            return
        finally:
            self.root.configure(cursor="")

        self.log_lines(lines)
        self._after_build(message)

    def _after_build(self, message: str):
        current = self.current.slug if self.current else None
        self.reload_records(keep=current)
        self.say(message)
        self.log(message, "ok")

    def on_preview(self, index: bool = False):
        if not index:
            if self.current is None:
                messagebox.showinfo("没有项目", "先新建一个项目吧。", parent=self.root)
                return
            # 预览前先落盘，否则看到的还是上一次的内容
            if not self.save(silent=True):
                return
            if not self._build_silently(self.current.slug):
                return
            target = f"{self.current.slug}/"
        else:
            if not self._build_silently(None):
                return
            target = ""

        if self._preview is None:
            self._preview = PreviewServer(core.ROOT)
        try:
            url = self._preview.url_for(target)
        except OSError as error:
            # 起不了本地服务器就退回 file://，并提醒样式可能不全
            self.log(f"预览服务器启动失败（{error}），改用 file:// 打开", "warn")
            path = core.ROOT / target / "index.html"
            webbrowser.open(path.as_uri())
            return
        webbrowser.open(url)
        self.log(f"预览 {url}", "dim")
        self.say("已在浏览器中打开预览")

    def on_preview_index(self):
        self.on_preview(index=True)

    def _build_silently(self, slug) -> bool:
        self.root.configure(cursor="watch")
        try:
            core.build_all(slug)
        except Exception as error:                       # noqa: BLE001
            self.log(f"生成失败：{type(error).__name__}: {error}", "err")
            messagebox.showerror("生成失败", f"{type(error).__name__}: {error}", parent=self.root)
            return False
        finally:
            self.root.configure(cursor="")
        return True

    def on_check(self):
        if not self.confirm_discard():
            return
        if not self._build_silently(None):
            return
        self.say("正在自检……")
        issues = core.run_checks()
        if issues:
            self.log(f"自检发现 {len(issues)} 处问题：", "err")
            for issue in issues:
                self.log(f"  ✗ {issue}", "err")
            messagebox.showwarning(
                "自检没通过",
                f"发现 {len(issues)} 处问题，详见下方「运行日志」。\n\n"
                + "\n".join(f"· {i}" for i in issues[:6])
                + ("\n…" if len(issues) > 6 else ""),
                parent=self.root,
            )
            self.say(f"自检发现 {len(issues)} 处问题")
            return
        self.log("自检通过：链接、语言包、行内样式、CSS 类名、图片引用全部正常", "ok")
        unused = core.unused_i18n_keys()
        if unused:
            self.log(f"（参考）语言包里有 {len(unused)} 个键当前没被用到：{', '.join(unused)}", "dim")
        dead = core.unused_css_classes()
        if dead:
            self.log(f"（参考）CSS 里有 {len(dead)} 个类当前没被用到：{', '.join(dead)}", "dim")
        messagebox.showinfo("自检通过", "链接、语言包、行内样式、CSS 类名、图片引用都正常。",
                            parent=self.root)
        self.say("自检通过")

    def on_open_folder(self):
        """在资源管理器里打开输出目录（站点根目录）"""
        path = core.ROOT
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(path))                  # noqa: S606 - 打开目录，不是执行文件
            elif sys.platform == "darwin":
                os.system(f'open "{path}"')              # noqa: S605
            else:
                os.system(f'xdg-open "{path}"')          # noqa: S605
            self.log(f"已打开 {path}", "dim")
        except OSError as error:
            messagebox.showerror("打不开目录", str(error), parent=self.root)

    def on_settings(self):
        dialog = SettingsDialog(self.root, core.load_site_config())
        self.root.wait_window(dialog)
        if dialog.saved:
            self.cfg = core.load_site_config()
            self.log("站点设置已保存 —— 点「重新生成全部」让页面生效", "ok")
            self.say("站点设置已保存")

    # ── 插入 Markdown 片段 ───────────────────────────────────────────────
    def _has_selection(self) -> bool:
        try:
            self.body.get(tk.SEL_FIRST, tk.SEL_LAST)
            return True
        except tk.TclError:
            return False

    def wrap_sel(self, before: str, after: str, placeholder: str):
        """把选中内容包起来；没有选中就插入占位文字并选中它，方便直接改"""
        self.body.focus_set()
        if self._has_selection():
            text = self.body.get(tk.SEL_FIRST, tk.SEL_LAST)
            self.body.delete(tk.SEL_FIRST, tk.SEL_LAST)
            self.body.insert(tk.INSERT, f"{before}{text}{after}")
            return
        start = self.body.index(tk.INSERT)
        self.body.insert(tk.INSERT, f"{before}{placeholder}{after}")
        self.body.tag_add(tk.SEL, f"{start}+{len(before)}c", f"{start}+{len(before) + len(placeholder)}c")
        self.body.mark_set(tk.INSERT, f"{start}+{len(before) + len(placeholder)}c")

    def insert_block(self, prefix: str, suffix: str, placeholder: str, select: bool = True):
        """整段插入（标题 / 引用 / 列表 / 代码块 / 表格……）

        光标所在行已经有内容时，把整块插到【这一行下面】并空一行隔开 ——
        连点两次「二级标题」时，第二个标题应该出现在第一个下面，
        而不是把它顶掉。空行则直接就地插入。
        """
        self.body.focus_set()
        line_start = self.body.index("insert linestart")
        line_end = self.body.index("insert lineend")
        if self.body.get(line_start, line_end).strip():
            anchor, lead = line_end, "\n\n"
        else:
            anchor, lead = line_start, ""

        block = f"{lead}{prefix}{placeholder}{suffix}\n"
        self.body.insert(anchor, block)
        end_index = f"{anchor}+{len(block)}c"
        self.body.mark_set(tk.INSERT, end_index)
        if placeholder and select:
            # 选中占位文字，用户直接敲键盘就能替换掉
            p_start = f"{end_index}-{len(suffix) + 1 + len(placeholder)}c"
            self.body.tag_remove(tk.SEL, "1.0", "end")
            self.body.tag_add(tk.SEL, p_start, f"{p_start}+{len(placeholder)}c")

    def insert_code_block(self):
        if self._has_selection():
            code = self.body.get(tk.SEL_FIRST, tk.SEL_LAST)
            self.body.delete(tk.SEL_FIRST, tk.SEL_LAST)
        else:
            code = ""
        # 已经有代码就别再选中它 —— 用户下一步多半是去改别的地方
        self.insert_block("```python\n", "\n```", code or "print('hello')", select=not code)

    def insert_table(self):
        template = ("| 参数 | 取值 | 说明 |\n"
                    "|---|---|---|\n"
                    "|  |  |  |\n"
                    "|  |  |  |")
        self.insert_block("\n" + template + "\n", "", "")

    def insert_mermaid(self):
        template = ("flowchart LR\n"
                    "  A[原始数据] --> B[处理]\n"
                    "  B --> C[结果]")
        self.insert_block("```mermaid\n", "\n```", template)

    def insert_link(self):
        self.body.focus_set()
        if self._has_selection():
            text = self.body.get(tk.SEL_FIRST, tk.SEL_LAST)
            self.body.delete(tk.SEL_FIRST, tk.SEL_LAST)
            self.body.insert(tk.INSERT, f"[{text}](https://)")
            self.body.mark_set(tk.INSERT, "insert-1c")
            return
        start = self.body.index(tk.INSERT)
        self.body.insert(tk.INSERT, "[链接文字](https://)")
        self.body.tag_add(tk.SEL, start, f"{start}+4c")

    def insert_figure_caption(self):
        """图注小抄：提醒写法，不直接改内容"""
        messagebox.showinfo(
            "图注怎么写",
            "图片单独占一段就会自动变成带编号的插图，图注写在图片后面的引号里：\n\n"
            "![替代文字](images/图.png \"这里是图注，会自动编号成「图 1」\")\n\n"
            "· 图片文件请放在 content/<项目>/images/ 下，生成时会自动复制到项目页目录\n"
            "· 编号是自动的，图注里不要再手写「图 1」\n"
            "· 点「图片」按钮可以直接把本地图片复制进来并插入这行写法",
            parent=self.root,
        )

    def insert_image(self):
        if not self._prepare_images_dir():
            return
        paths = filedialog.askopenfilenames(
            title="选择图片（会复制到 content/<项目>/images/）",
            filetypes=[("图片", "*.png *.jpg *.jpeg *.gif *.webp *.svg"), ("所有文件", "*.*")],
            parent=self.root,
        )
        if not paths:
            return
        slug = normalize_slug(self.var_slug.get())
        images_dir = core.CONTENT_DIR / slug / "images"
        inserted = []
        for raw in paths:
            source = Path(raw)
            target = images_dir / source.name
            # 重名就加后缀，别悄悄覆盖已有插图
            index = 2
            while target.exists() and target.stat().st_size != source.stat().st_size:
                target = images_dir / f"{source.stem}-{index}{source.suffix}"
                index += 1
            try:
                if not target.exists() or target.stat().st_size != source.stat().st_size:
                    shutil.copy2(source, target)
            except OSError as error:
                messagebox.showerror("复制图片失败", f"{source.name}：{error}", parent=self.root)
                continue
            rel = f"images/{target.name}"
            inserted.append(f'![{source.stem}]({rel} "")')

        self.body.focus_set()
        self.insert_block("\n" + "\n\n".join(inserted) + "\n", "", "")
        self.log(f"已复制 {len(inserted)} 张图片到 content/{slug}/images/，"
                 f"记得把图注填进末尾的引号里", "ok")
        self.say("图片已插入")

    def _prepare_images_dir(self) -> bool:
        """插图前得先有个已保存的项目目录，否则不知道图片该放哪"""
        if not self.current:
            messagebox.showinfo("还没有项目", "先新建并保存一个项目，再插入图片。", parent=self.root)
            return False
        slug = normalize_slug(self.var_slug.get())
        if not core.valid_slug(slug):
            messagebox.showwarning("路径要先填对", "先把「路径」填成合法值（小写字母、数字、连字符）。",
                                   parent=self.root)
            return False
        if slug != self.current.slug or self.dirty:
            if not self.save():
                return False
        (core.CONTENT_DIR / slug / "images").mkdir(parents=True, exist_ok=True)
        return True

    def on_pick_cover(self):
        if not self._prepare_images_dir():
            return
        slug = normalize_slug(self.var_slug.get())
        path = filedialog.askopenfilename(
            title="选择封面图（会复制到 content/<项目>/images/）",
            filetypes=[("图片", "*.png *.jpg *.jpeg *.gif *.webp"), ("所有文件", "*.*")],
            parent=self.root,
        )
        if not path:
            return
        source = Path(path)
        target = core.CONTENT_DIR / slug / "images" / source.name
        try:
            if not target.exists():
                shutil.copy2(source, target)
        except OSError as error:
            messagebox.showerror("复制封面失败", str(error), parent=self.root)
            return
        self.var_cover.set(f"images/{target.name}")
        self.log(f"封面设为 images/{target.name}（首页卡片顶部会通栏显示）", "ok")

    # ── 状态持久化与关闭 ─────────────────────────────────────────────────
    def _restore_state(self):
        state = core.read_json(STATE_PATH) or {}
        geometry = state.get("geometry")
        if geometry:
            try:
                self.root.geometry(geometry)
            except tk.TclError:
                pass
        if state.get("mono"):
            self.mono.set(True)
            self.apply_editor_font()
        if state.get("auto_update") is False:
            self.auto_update.set(False)

        wanted = state.get("slug")
        if wanted and wanted in (self._slugs if hasattr(self, "_slugs") else []):
            if not self.dirty:
                self.load_record(wanted)
        self.log("写字板就绪。内容改完点「生成此页」或按 Ctrl+Enter，"
                 "首页的卡片与目录会自动重建。", "dim")
        if not self.records:
            self.log("目前一个项目都没有：点左上角「新建」开始第一篇记录。", "warn")
        else:
            self.say(f"共 {len(self.records)} 个项目")

    def _save_state(self):
        state = {
            "geometry": self.root.winfo_geometry(),
            "slug": self.current.slug if self.current else None,
            "mono": bool(self.mono.get()),
            "auto_update": bool(self.auto_update.get()),
        }
        try:
            core.write_json(STATE_PATH, state)
        except OSError:
            pass          # 记不住窗口大小不是大事，别拦着用户关窗口

    def on_close(self):
        if not self.confirm_discard():
            return
        if self._preview:
            self._preview.stop()
        self._save_state()
        self.root.destroy()


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    root = tk.Tk()
    app = WritepadApp(root)
    if argv:
        slug = argv[0].strip()
        if slug in getattr(app, "_slugs", []):
            app.load_record(slug)
        else:
            app.log(f"找不到项目 {slug}", "err")
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
