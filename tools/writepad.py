#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
科研记录写字板 —— research.who-young.top 的图形化录入工具
============================================================================
左边填元数据、右边写正文，点一下就生成静态页面并重建首页。

    python tools/writepad.py                     打开写字板
    python tools/writepad.py <slug>              直接打开某个项目
    python tools/writepad.py --theme dark        本次以暗色主题启动
    python tools/writepad.py --zoom 125          本次以 125% 缩放启动

为什么是 tkinter 而不是网页版编辑器
    「输入 → 本地出 HTML → 打包上传」这条链子必须在断网、也没有任何第三方
    Python 包的机器上跑得起来，所以只用标准库。真正把 Markdown 编译成 HTML
    的活由同目录的 research_core.py 干，这个文件只负责界面。

界面速查
    文件    Ctrl+N 新建 · Ctrl+S 保存 · Ctrl+Enter 保存并生成 · F5 预览
    编辑    Ctrl+F 查找替换 · F3 / Shift+F3 下一个 / 上一个 · Esc 关掉查找条
            Ctrl+Z / Ctrl+Y 撤销 / 重做 · Tab / Shift+Tab 缩进 / 反缩进
            Ctrl+B / Ctrl+I / Ctrl+K 加粗 / 斜体 / 链接
    视图    Ctrl+= / Ctrl+- / Ctrl+0 放大 / 缩小 / 复位（Ctrl+滚轮也可以）
            Ctrl+T 亮暗主题切换 · Ctrl+L 运行日志面板 · F11 全屏

几个刻意的设计
1. 预览走本地 HTTP 而不是 file://
   生成出来的页面用的是 /assets/... 这种根路径，file:// 打开会全崩；
   内置服务器监听 127.0.0.1 的随机端口，只服务本仓库目录，仅本机可访问。
2. 删项目不真删
   挪到仓库根的 .writepad-trash/，那里已被 .gitignore 忽略，
   所以不会被「从分支部署」发布出去，反悔了还能捞回来。
3. 改路径 = 改目录名
   保存时如果 slug 变了，会调用 core.rename_record 把目录改名并清掉旧页面。
4. 主题与缩放都是「本机偏好」
   存在 tools/.writepad-state.json（已 gitignore），不进仓库、不影响生成的页面。
   生成出来的站点目前只有亮色一套配色 —— 那是另一件事。
"""

from __future__ import annotations

import argparse
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

# ── 缩放 ──────────────────────────────────────────────────────────────────
ZOOM_MIN, ZOOM_MAX, ZOOM_STEP = 0.8, 2.0, 0.1
ZOOM_DEFAULT = 1.0
BASE_UI_SIZE, BASE_EDITOR_SIZE, BASE_SUMMARY_SIZE, BASE_LOG_SIZE = 10, 11, 10, 9
#: 左栏（项目元数据）在 100% 缩放下的宽度；缩放时会按比例调整
META_WIDTH_BASE = 420

# ── 主题 ──────────────────────────────────────────────────────────────────
#: 两套配色的键必须完全一致；"system" 会在运行时解析成其中之一
PALETTES: dict[str, dict[str, str]] = {
    "light": {
        "bg": "#E4E9F0",          # 窗口底色（面板之间露出来的部分）
        "panel": "#F8FAFC",       # 面板 / 卡片
        "field": "#FFFFFF",       # 输入框、按钮
        "border": "#CBD5E1",
        "text": "#1E293B",
        "muted": "#64748B",
        "accent": "#3B82F6",
        "accent_dark": "#2563EB",
        "accent_text": "#FFFFFF",
        "hover": "#E9EEF5",
        "editor_bg": "#FFFFFF",
        "editor_fg": "#1E293B",
        "select_bg": "#BFDBFE",
        "select_fg": "#0F172A",
        "curline": "#F1F5F9",     # 当前行底色
        "find_bg": "#FDE68A",     # 所有匹配
        "find_fg": "#78350F",
        "log_bg": "#FFFFFF",
        "ok": "#047857", "warn": "#B45309", "err": "#B91C1C", "dim": "#94A3B8",
    },
    "dark": {
        "bg": "#0B1220",
        "panel": "#151E2E",
        "field": "#0F172A",
        "border": "#2C3B54",
        "text": "#E2E8F0",
        "muted": "#93A6C0",
        "accent": "#3B82F6",
        "accent_dark": "#2563EB",
        "accent_text": "#FFFFFF",
        "hover": "#22304A",
        "editor_bg": "#0F172A",
        "editor_fg": "#DCE6F5",
        "select_bg": "#1D4ED8",
        "select_fg": "#FFFFFF",
        "curline": "#1A2436",
        "find_bg": "#7A5810",
        "find_fg": "#FDE68A",
        "log_bg": "#0F172A",
        "ok": "#34D399", "warn": "#FBBF24", "err": "#F87171", "dim": "#64748B",
    },
}

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

SHORTCUTS = [
    ("文件", ""),
    ("Ctrl+N", "新建项目"),
    ("Ctrl+S", "保存（写回 content/<项目>/）"),
    ("Ctrl+Enter", "保存 → 生成这个项目 → 重建首页"),
    ("F5", "在浏览器里预览当前项目"),
    ("", ""),
    ("编辑", ""),
    ("Ctrl+F", "查找与替换（Esc 关闭）"),
    ("F3 / Shift+F3", "下一个 / 上一个匹配"),
    ("Ctrl+Z / Ctrl+Y", "撤销 / 重做"),
    ("Tab / Shift+Tab", "缩进 / 反缩进（多选时整块处理）"),
    ("Enter", "自动延续缩进与列表标记"),
    ("Ctrl+B / Ctrl+I / Ctrl+K", "加粗 / 斜体 / 链接"),
    ("", ""),
    ("视图", ""),
    ("Ctrl+= / Ctrl+-", "放大 / 缩小界面（Ctrl+滚轮同效）"),
    ("Ctrl+0", "恢复 100% 缩放"),
    ("Ctrl+T", "亮色 / 暗色主题切换"),
    ("Ctrl+L", "显示 / 隐藏运行日志面板"),
    ("F11", "全屏切换"),
]


# ══════════════════════════════════════════════════════════════════════════
#  小工具
# ══════════════════════════════════════════════════════════════════════════

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


def system_prefers_dark() -> bool:
    """读 Windows 的「应用模式」设置。非 Windows 或读不到时按亮色处理。"""
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return int(value) == 0
    except Exception:
        return False


def enable_dpi_awareness() -> None:
    """声明 DPI 感知，高分屏上文字才不发虚。

    不声明的话 Windows 会把整个窗口位图拉伸放大，150% 缩放下字会糊。
    声明之后 Tk 自己会按真实 DPI 换算字号，界面整体也会略大一点 —— 这正是想要的。
    """
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)   # PROCESS_SYSTEM_DPI_AWARE
    except Exception:
        try:
            import ctypes
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


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
#  对话框
# ══════════════════════════════════════════════════════════════════════════

class _ThemedToplevel(tk.Toplevel):
    """跟着主窗口配色的 Toplevel 基类。

    ttk 的样式是全局的，所以内部控件会自动跟随；只有 Toplevel 自己的
    背景色要手动设 —— 否则暗色主题下会露出一圈系统色的白边。
    """

    def __init__(self, master, palette: dict):
        super().__init__(master)
        self.palette = palette
        self.configure(background=palette["panel"])


class NewProjectDialog(_ThemedToplevel):
    """新建项目：标题 + 路径

    路径必须是小写 ASCII 的短名。中文标题不能直接当路径 —— GitHub Pages 会把
    中文路径服务成一长串百分号编码，分享和记笔记时都很难用。
    """

    def __init__(self, master, cfg: dict, palette: dict):
        super().__init__(master, palette)
        self.title("新建项目")
        self.resizable(False, False)
        self.transient(master)
        self.result: tuple[str, str, str] | None = None

        self.var_title = tk.StringVar()
        self.var_slug = tk.StringVar(value=f"exp-{_dt.date.today().strftime('%Y%m%d')}")
        self.var_status = tk.StringVar(value="进行中")

        body = ttk.Frame(self, padding=18)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)

        ttk.Label(body, text="项目标题").grid(row=0, column=0, sticky="w", pady=(0, 4))
        entry_title = ttk.Entry(body, textvariable=self.var_title, width=42)
        entry_title.grid(row=0, column=1, sticky="ew", pady=(0, 4))

        ttk.Label(body, text="路径（URL）").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(body, textvariable=self.var_slug, width=42).grid(row=1, column=1, sticky="ew", pady=4)

        ttk.Label(
            body, style="Muted.TLabel", justify="left",
            text="只用小写字母、数字和连字符，例如 annealing-rate。\n"
                 "它同时是目录名和网址：research.who-young.top/路径/",
        ).grid(row=2, column=1, sticky="w", pady=(0, 8))

        ttk.Label(body, text="初始状态").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Combobox(body, textvariable=self.var_status, state="readonly", width=12,
                     values=[label for _, label in STATUS_CHOICES]).grid(row=3, column=1, sticky="w", pady=4)

        ttk.Label(body, text="正文模板").grid(row=4, column=0, sticky="w", pady=4)
        ttk.Label(body, style="Muted.TLabel",
                  text="目的与假设 / 装置与参数 / 步骤 / 原始数据 / 分析与讨论 / 结论",
                  ).grid(row=4, column=1, sticky="w", pady=4)

        buttons = ttk.Frame(body)
        buttons.grid(row=5, column=0, columnspan=2, sticky="e", pady=(16, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(buttons, text="创建", style="Accent.TButton", command=self._ok).pack(side="right")

        entry_title.focus_set()
        self.bind("<Return>", lambda _e: self._ok())
        self.bind("<Escape>", lambda _e: self.destroy())
        self.grab_set()
        self.wait_visibility()

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


class SettingsDialog(_ThemedToplevel):
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

    def __init__(self, master, cfg: dict, palette: dict):
        super().__init__(master, palette)
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
            ttk.Label(body, style="Muted.TLabel", text=hint).grid(
                row=row, column=2, sticky="w", padx=(10, 0), pady=4)

        for offset, (key, label) in enumerate(self.SWITCHES):
            var = tk.BooleanVar(value=bool(self.cfg.get(key, True)))
            self.switch_vars[key] = var
            ttk.Checkbutton(body, text=label, variable=var).grid(
                row=len(self.FIELDS) + offset, column=1, sticky="w", pady=2)

        ttk.Label(
            body, style="Muted.TLabel", justify="left",
            text="界面上的固定文案（标题、按钮、页脚）在 lang/cn.json 里，\n"
                 "改完这里记得点「重新生成全部」让页面生效。",
        ).grid(row=len(self.FIELDS) + len(self.SWITCHES), column=0, columnspan=3,
               sticky="w", pady=(12, 0))

        buttons = ttk.Frame(body)
        buttons.grid(row=len(self.FIELDS) + len(self.SWITCHES) + 1, column=0, columnspan=3,
                     sticky="e", pady=(16, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(buttons, text="保存", style="Accent.TButton", command=self._ok).pack(side="right")

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


class ShortcutsDialog(_ThemedToplevel):
    """快捷键与图注写法的小抄"""

    def __init__(self, master, palette: dict, ui_family: str, key_family: str):
        super().__init__(master, palette)
        self.title("快捷键")
        self.transient(master)
        self.geometry("580x600")

        wrap = ttk.Frame(self, padding=(14, 12, 14, 12))
        wrap.pack(fill="both", expand=True)

        text = tk.Text(wrap, wrap="none", relief="flat", borderwidth=0,
                       background=palette["panel"], foreground=palette["text"],
                       font=(ui_family, 10), cursor="arrow")
        scroll = ttk.Scrollbar(wrap, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        text.tag_configure("head", foreground=palette["accent"], spacing1=10, spacing3=4)
        text.tag_configure("key", foreground=palette["text"], font=(key_family, 10))
        text.tag_configure("note", foreground=palette["muted"])

        for key, note in SHORTCUTS:
            if not key and not note:
                text.insert("end", "\n")
            elif not note:
                text.insert("end", f"{key}\n", "head")
            else:
                text.insert("end", f"{key:<24}", "key")
                text.insert("end", f"{note}\n", "note")
        text.configure(state="disabled")

        ttk.Button(self, text="关闭", command=self.destroy).pack(pady=(0, 14))
        self.bind("<Escape>", lambda _e: self.destroy())
        self.grab_set()
        self.wait_visibility()


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
        self._find_job = None
        self._preview: PreviewServer | None = None
        self._body_snapshot = ""       # 用来判断正文是否真的改过
        self._menus: list[tk.Menu] = []
        self._scroll_rows: list[tuple[tk.Canvas, object]] = []
        self._zoom_done = False
        self._palette_id: int | None = None

        # 视图偏好
        self.theme_choice = "system"    # light / dark / system
        self.zoom = ZOOM_DEFAULT
        self.log_visible = True

        self.mono = tk.BooleanVar(value=False)
        self.auto_update = tk.BooleanVar(value=True)
        self.var_theme = tk.StringVar(value=self.theme_choice)
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
        self.var_cursor = tk.StringVar(value="")
        self.var_zoom = tk.StringVar(value="缩放 100%")
        self.find_var = tk.StringVar()
        self.replace_var = tk.StringVar()
        self.find_nocase = tk.BooleanVar(value=True)
        self.var_find_info = tk.StringVar(value="")

        self._setup_fonts()
        self._build_ui()
        self._bind_keys()
        self.apply_theme()
        self.set_zoom(self.zoom, announce=False)
        self.reload_records()
        self._restore_state()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.bind("<FocusIn>", self._on_root_focus)

    # ══════════════════════════════════════════════════════════════════
    #  主题
    # ══════════════════════════════════════════════════════════════════
    def _palette(self) -> dict:
        """把 light / dark / system 解析成一套具体颜色"""
        choice = self.theme_choice
        if choice == "system":
            choice = "dark" if system_prefers_dark() else "light"
        return PALETTES[choice if choice in PALETTES else "light"]

    def apply_theme(self):
        """把当前配色刷到所有控件上

        ttk 的样式是全局的（按 root 记），所以配一次就够；tk 的原生控件
        （Text / Canvas / Menu）不吃 ttk 样式，必须单独 configure。
        """
        pal = self._palette()
        self._palette_id = id(pal)
        style = ttk.Style(self.root)

        self.root.configure(background=pal["bg"])

        # ── 通用默认 ──
        style.configure(".", background=pal["panel"], foreground=pal["text"],
                        fieldbackground=pal["field"], bordercolor=pal["border"],
                        lightcolor=pal["panel"], darkcolor=pal["panel"],
                        troughcolor=pal["bg"], focuscolor=pal["accent"],
                        selectbackground=pal["accent"], selectforeground=pal["accent_text"])

        style.configure("TFrame", background=pal["panel"])
        style.configure("Bg.TFrame", background=pal["bg"])
        style.configure("TLabel", background=pal["panel"], foreground=pal["text"])
        style.configure("Muted.TLabel", background=pal["panel"], foreground=pal["muted"])
        style.configure("Status.TLabel", background=pal["panel"], foreground=pal["muted"])

        style.configure("TLabelframe", background=pal["panel"], bordercolor=pal["border"],
                        relief="solid", borderwidth=1)
        for name in ("TLabelframe.Label", "Group.TLabelframe.Label"):
            style.configure(name, background=pal["panel"], foreground=pal["muted"])

        style.configure("TSeparator", background=pal["border"])

        # ── 按钮 ──
        # ⚠ width=0 很关键：clam 主题给 TButton 预设了 -width -11，
        #   意思是「11 个字符宽」，于是「自检」和「重新生成全部」都是 110px，
        #   整条工具栏被凭空撑宽四成（1440 的屏放不下，右边按钮就点不到了）。
        #   清零之后按钮按内容自适应。
        style.configure("TButton", width=0, background=pal["field"], foreground=pal["text"],
                        bordercolor=pal["border"], lightcolor=pal["field"],
                        darkcolor=pal["field"], arrowcolor=pal["muted"])
        style.map("TButton",
                  background=[("disabled", pal["panel"]), ("pressed", pal["hover"]),
                              ("active", pal["hover"])],
                  foreground=[("disabled", pal["muted"])],
                  bordercolor=[("active", pal["accent"])])
        style.configure("Tool.TButton", width=0, background=pal["field"], foreground=pal["text"],
                        bordercolor=pal["border"], lightcolor=pal["field"],
                        darkcolor=pal["field"])
        style.map("Tool.TButton",
                  background=[("pressed", pal["hover"]), ("active", pal["hover"])])
        style.configure("Accent.TButton", width=0, background=pal["accent"],
                        foreground=pal["accent_text"],
                        bordercolor=pal["accent"], lightcolor=pal["accent"],
                        darkcolor=pal["accent"])
        style.map("Accent.TButton",
                  background=[("disabled", pal["muted"]), ("pressed", pal["accent_dark"]),
                              ("active", pal["accent_dark"])],
                  foreground=[("disabled", pal["panel"])])

        style.configure("TMenubutton", width=0, background=pal["field"], foreground=pal["text"],
                        bordercolor=pal["border"], arrowcolor=pal["muted"],
                        lightcolor=pal["field"], darkcolor=pal["field"])
        style.map("TMenubutton",
                  background=[("pressed", pal["hover"]), ("active", pal["hover"])])

        # ── 输入控件 ──
        for name in ("TEntry", "TSpinbox"):
            style.configure(name, fieldbackground=pal["field"], foreground=pal["text"],
                            bordercolor=pal["border"], insertcolor=pal["text"],
                            lightcolor=pal["border"], darkcolor=pal["border"],
                            arrowcolor=pal["muted"])
            style.map(name,
                      bordercolor=[("focus", pal["accent"])],
                      lightcolor=[("focus", pal["accent"])],
                      darkcolor=[("focus", pal["accent"])],
                      fieldbackground=[("disabled", pal["panel"])],
                      foreground=[("disabled", pal["muted"])])

        style.configure("TCombobox", fieldbackground=pal["field"], background=pal["field"],
                        foreground=pal["text"], arrowcolor=pal["muted"],
                        bordercolor=pal["border"], lightcolor=pal["border"],
                        darkcolor=pal["border"], selectbackground=pal["field"],
                        selectforeground=pal["text"], insertcolor=pal["text"])
        style.map("TCombobox",
                  fieldbackground=[("readonly", pal["field"]), ("disabled", pal["panel"])],
                  foreground=[("readonly", pal["text"]), ("disabled", pal["muted"])],
                  selectbackground=[("readonly", pal["field"]), ("disabled", pal["panel"])],
                  selectforeground=[("readonly", pal["text"])],
                  arrowcolor=[("disabled", pal["muted"])],
                  bordercolor=[("focus", pal["accent"])],
                  lightcolor=[("focus", pal["accent"])],
                  darkcolor=[("focus", pal["accent"])])
        # 下拉列表是 Tk 的 Listbox，不受 ttk 样式管，只能走 option 数据库
        self.root.option_add("*TCombobox*Listbox.background", pal["field"])
        self.root.option_add("*TCombobox*Listbox.foreground", pal["text"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", pal["accent"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", pal["accent_text"])
        self.root.option_add("*TCombobox*Listbox.borderWidth", 0)

        style.configure("TCheckbutton", background=pal["panel"], foreground=pal["text"],
                        indicatorcolor=pal["field"], focuscolor=pal["accent"])
        style.map("TCheckbutton",
                  background=[("active", pal["panel"])],
                  indicatorcolor=[("selected", pal["accent"]), ("pressed", pal["accent_dark"]),
                                  ("active", pal["field"])],
                  foreground=[("disabled", pal["muted"])])

        # ── 容器与滚动条 ──
        style.configure("TPanedwindow", background=pal["bg"])
        style.configure("Sash", background=pal["border"], sashthickness=8)
        for name in ("TScrollbar", "Vertical.TScrollbar", "Horizontal.TScrollbar"):
            style.configure(name, background=pal["hover"], troughcolor=pal["bg"],
                            bordercolor=pal["bg"], arrowcolor=pal["muted"],
                            lightcolor=pal["hover"], darkcolor=pal["hover"])
            style.map(name, background=[("active", pal["muted"])])

        # ── 弹出菜单 ──
        for menu in self._menus:
            menu.configure(background=pal["field"], foreground=pal["text"],
                           activebackground=pal["accent"], activeforeground=pal["accent_text"],
                           selectcolor=pal["accent"], disabledforeground=pal["muted"],
                           borderwidth=0, relief="flat", activeborderwidth=0)

        # ── tk 原生控件 ──
        for widget in (self.body, self.summary_text, self.find_entry, self.replace_entry):
            widget.configure(background=pal["editor_bg"], foreground=pal["editor_fg"],
                             insertbackground=pal["text"],
                             selectbackground=pal["select_bg"], selectforeground=pal["select_fg"])
        self.body.tag_configure("curline", background=pal["curline"])
        self.body.tag_configure("find", background=pal["find_bg"], foreground=pal["find_fg"])
        self.body.tag_configure("find-current", background=pal["accent"],
                                foreground=pal["accent_text"])
        self.log_text.configure(background=pal["log_bg"], foreground=pal["text"],
                                insertbackground=pal["text"])
        for tag in ("ok", "warn", "err", "dim"):
            self.log_text.tag_configure(tag, foreground=pal[tag])
        if hasattr(self, "meta_canvas"):
            self.meta_canvas.configure(background=pal["bg"])
        for canvas, _refresh in self._scroll_rows:
            canvas.configure(background=pal["bg"])

        self.var_zoom.set(f"缩放 {round(self.zoom * 100)}%")

    def set_theme(self, choice: str, announce: bool = True):
        if choice not in ("light", "dark", "system"):
            return
        self.theme_choice = choice
        self.var_theme.set(choice)
        self.apply_theme()
        if announce:
            label = {"light": "亮色", "dark": "暗色", "system": "跟随系统"}[choice]
            self.log(f"主题：{label}", "dim")
            self.say(f"主题已切换到{label}")

    def toggle_theme(self):
        """在亮 / 暗之间来回切。当前是「跟随系统」时，切到与系统相反的那一套。"""
        self.set_theme("light" if self._palette() is PALETTES["dark"] else "dark")

    def _on_root_focus(self, _event=None):
        """选了「跟随系统」时，窗口重新获得焦点就重新读一次系统设置"""
        if self.theme_choice != "system":
            return
        if self._palette_id != id(self._palette()):
            self.apply_theme()

    # ══════════════════════════════════════════════════════════════════
    #  缩放
    # ══════════════════════════════════════════════════════════════════
    def set_zoom(self, factor, announce: bool = True):
        factor = max(ZOOM_MIN, min(ZOOM_MAX, round(float(factor), 2)))
        self.zoom = factor
        self._zoom_done = True
        self._apply_fonts()
        if announce:
            self.log(f"缩放 {round(factor * 100)}%", "dim")
        self.say(f"界面缩放 {round(factor * 100)}%")

    def zoom_in(self):
        self.set_zoom(self.zoom + ZOOM_STEP)

    def zoom_out(self):
        self.set_zoom(self.zoom - ZOOM_STEP)

    def zoom_reset(self):
        self.set_zoom(ZOOM_DEFAULT)

    def _on_ctrl_wheel(self, event):
        """Ctrl+滚轮缩放。Windows 的 delta 是 ±120 的整数倍。"""
        self.set_zoom(self.zoom + (ZOOM_STEP if event.delta > 0 else -ZOOM_STEP))
        return "break"

    def _apply_fonts(self):
        """按缩放倍数重设所有字体

        tkinter 的字体是按「点」算的，缩放只能靠自己改字号。
        ttk 的内边距是像素，字号变大后要跟着变大，否则按钮会显得很挤。
        """
        z = self.zoom

        def size(base: int) -> int:
            return max(7, round(base * z))

        for name, base in (("TkDefaultFont", BASE_UI_SIZE), ("TkTextFont", BASE_UI_SIZE),
                           ("TkMenuFont", BASE_UI_SIZE), ("TkHeadingFont", BASE_UI_SIZE),
                           ("TkIconFont", BASE_UI_SIZE), ("TkTooltipFont", BASE_LOG_SIZE)):
            try:
                tkfont.nametofont(name).configure(family=self.ui_family, size=size(base))
            except tk.TclError:
                pass

        self.body.configure(font=(self.editor_family if self.mono.get() else self.ui_family,
                                  size(BASE_EDITOR_SIZE)),
                            spacing1=max(1, round(1 * z)), spacing3=max(0, round(2 * z)))
        self.summary_text.configure(font=(self.ui_family, size(BASE_SUMMARY_SIZE)))
        self.log_text.configure(font=(self.editor_family, size(BASE_LOG_SIZE)))
        for entry in (self.find_entry, self.replace_entry):
            entry.configure(font=(self.ui_family, size(BASE_UI_SIZE)))

        style = ttk.Style(self.root)
        style.configure("TButton", padding=(round(9 * z), round(4 * z)))
        style.configure("Tool.TButton", padding=(round(8 * z), round(3 * z)))
        style.configure("Accent.TButton", padding=(round(11 * z), round(4 * z)))
        style.configure("TMenubutton", padding=(round(9 * z), round(4 * z)))

        self.var_zoom.set(f"缩放 {round(z * 100)}%")
        self._sync_pane_width()
        self._sync_minsize()
        self._refresh_scroll_rows()

    def _sync_minsize(self):
        """窗口最小尺寸跟着内容走，必要时把窗口撑大

        工具栏是一整排按钮，缩放变大后如果窗口还能缩得比工具栏窄，
        最右边的按钮就会被裁掉、点不到。这里让最小宽度始终 = 工具栏所需宽度。
        而且 wm minsize 只约束「以后」的缩放，不会把已经显示出来的窗口顶大，
        所以还得主动 geometry 一次 —— 否则 150% 下窗口纹丝不动，右边照样被切。
        """
        self.root.update_idletasks()
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        need_w = min(self.toolbar.winfo_reqwidth() + 32, screen_w - 20)
        need_h = min(560 + (self.log_frame.winfo_reqheight() if self.log_visible else 0),
                     screen_h - 60)
        self.root.minsize(need_w, need_h)

        # 窗口还没映射出来时不碰它：那时 winfo_width() 是 1，
        # 会把 _restore_state 里刚恢复的窗口尺寸覆盖掉
        if not self.root.winfo_ismapped():
            return
        width, height = self.root.winfo_width(), self.root.winfo_height()
        if width >= need_w and height >= need_h:
            return

        new_w, new_h = max(width, need_w), max(height, need_h)
        x, y = self.root.winfo_x(), self.root.winfo_y()
        # 撑大之后必须把位置夹回屏幕内：窗口是从左上角向右下「长」的，
        # 原本靠右的窗口撑大后会把右半截伸到屏幕外 —— 用户看不见也点不到，
        # 看起来就像那排按钮凭空消失了。
        x = max(0, min(x, max(0, screen_w - new_w)))
        y = max(0, min(y, max(0, screen_h - new_h)))
        self.root.geometry(f"{new_w}x{new_h}+{x}+{y}")

    def _sync_pane_width(self):
        """左栏宽度跟着缩放走

        缩放只改字号，ttk 控件的固定像素宽度不会自动变，于是 150% 时左栏还是
        420px，元数据的输入框和提示文字会被横着切掉一半。
        """
        if not hasattr(self, "paned"):
            return
        try:
            total = self.paned.winfo_width()
        except tk.TclError:
            return
        if total < 200:
            return          # 还没映射，交给初始宽度
        wanted = int(META_WIDTH_BASE * self.zoom)
        # 正文那一栏至少留 380px；实在放不下就宁可压左栏
        wanted = min(wanted, max(240, total - 380))
        try:
            self.paned.sashpos(0, wanted)
        except tk.TclError:
            pass

    # ══════════════════════════════════════════════════════════════════
    #  界面搭建
    # ══════════════════════════════════════════════════════════════════
    def _setup_fonts(self):
        """挑字体。tk 默认字体在中文 Windows 上是宋体，标题和正文都难看。"""
        family = "Microsoft YaHei UI"
        available = set(tkfont.families(self.root))
        if family not in available:
            family = "Microsoft YaHei" if "Microsoft YaHei" in available else "Segoe UI"
        self.ui_family = family
        self.editor_family = "Consolas" if "Consolas" in available else "Courier New"

        style = ttk.Style(self.root)
        # clam 下按钮才有正常的 padding 和主题色，Windows 原生主题不认 Accent
        if "clam" in style.theme_names():
            style.theme_use("clam")

    def _build_ui(self):
        self.root.title("科研记录写字板 —— research.who-young.top")

        self._build_toolbar()

        # 底部一整块：日志面板 + 状态栏。做成一整块是为了让「折叠日志」
        # 只在块内增删控件，不动根窗口的 pack 顺序（否则会抢不到空间）。
        self.bottom = ttk.Frame(self.root)
        self.bottom.pack(side="bottom", fill="x")
        self._build_log(self.bottom)
        self._build_statusbar(self.bottom)

        paned = ttk.PanedWindow(self.root, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        self.paned = paned

        left = ttk.Frame(paned, width=META_WIDTH_BASE)
        right = ttk.Frame(paned)
        paned.add(left, weight=0)
        paned.add(right, weight=1)
        self._build_meta_panel(left)
        self._build_editor(right)

    def _scroll_row(self, parent, padding=(0, 0, 0, 0), pady=(0, 0)) -> ttk.Frame:
        """把一条横向工具栏放进可横向滚动的 Canvas，返回装按钮的内容 Frame

        为什么非做不可：缩放调大之后，一排按钮的总宽度可能超过屏幕本身
        （1440 的屏在 150% 下要 ~1545px），而左栏又跟着缩放一起变宽、
        右栏反而更窄 —— 光靠撑大窗口解决不了。被裁掉的按钮是点不到的，
        功能等于凭空消失，所以放不下时给滚动条，而不是裁掉。
        """
        # Canvas 与滚动条放进一个自成一体的容器里，再整体 pack 给父级：
        # 否则滚动条是父级的直接子节点，pack 顺序排在会 expand 的正文框之后，
        # 需要滚动时它根本抢不到高度，等于没有。
        container = ttk.Frame(parent)
        container.pack(fill="x", pady=pady)

        canvas = tk.Canvas(container, highlightthickness=0, borderwidth=0, height=36)
        hbar = ttk.Scrollbar(container, orient="horizontal", command=canvas.xview)
        canvas.configure(xscrollcommand=hbar.set)
        canvas.pack(fill="x")

        inner = ttk.Frame(canvas, padding=padding)
        window = canvas.create_window((0, 0), window=inner, anchor="nw")

        def refresh(_event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))
            height = max(inner.winfo_reqheight(), 24)
            if int(float(canvas.cget("height"))) != height:
                canvas.configure(height=height)
            have = canvas.winfo_width()
            if have < 50:
                return          # 还没排过版，等下一次 Configure 再决定
            # 用 winfo_manager() 判断「是否被 pack 管理」而不是 winfo_ismapped()：
            # 父窗口还没显示出来时 ismapped() 恒为 0，隐藏分支就永远不会执行，
            # 滚动条会在布局初期被 pack 上去然后一直赖着不走。
            managed = bool(hbar.winfo_manager())
            if inner.winfo_reqwidth() > have + 4:
                if not managed:
                    hbar.pack(fill="x", side="bottom")
            elif managed:
                hbar.pack_forget()
                canvas.xview_moveto(0)

        inner.bind("<Configure>", refresh)
        canvas.bind("<Configure>", refresh)
        self._scroll_rows.append((canvas, refresh))
        return inner

    def _refresh_scroll_rows(self):
        """字号变了之后，滚动区域和滚动条的有无都要重新算"""
        for canvas, refresh in self._scroll_rows:
            try:
                refresh()
            except tk.TclError:
                pass

    def _build_toolbar(self):
        host = ttk.Frame(self.root)
        host.pack(fill="x")
        self.toolbar_host = host

        bar = self._scroll_row(host, padding=(10, 8, 10, 6))
        self.toolbar = bar

        ttk.Label(bar, text="项目").pack(side="left")
        self.project_box = ttk.Combobox(bar, textvariable=self.var_project, state="readonly",
                                        width=28, values=[])
        self.project_box.pack(side="left", padx=(6, 10))
        self.project_box.bind("<<ComboboxSelected>>", lambda _e: self.on_pick_project())

        ttk.Button(bar, text="新建", style="Tool.TButton", command=self.on_new).pack(side="left")
        ttk.Button(bar, text="删除", style="Tool.TButton", command=self.on_delete).pack(side="left", padx=4)

        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)

        ttk.Button(bar, text="保存", style="Accent.TButton", command=self.on_save).pack(side="left")
        ttk.Button(bar, text="生成此页", style="Tool.TButton",
                   command=self.on_build_current).pack(side="left", padx=4)
        ttk.Button(bar, text="重新生成全部", style="Tool.TButton",
                   command=self.on_build_all).pack(side="left")

        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)

        ttk.Button(bar, text="预览", style="Tool.TButton", command=self.on_preview).pack(side="left")
        ttk.Button(bar, text="自检", style="Tool.TButton", command=self.on_check).pack(side="left", padx=4)

        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)

        # 「视图」与「更多」收进下拉菜单：一是让工具栏在最小窗口下也放得下，
        # 二是这些操作不常用，摊平了反而找不到重点。
        self.view_button = ttk.Menubutton(bar, text="视图 ▾", style="TMenubutton")
        self.view_menu = tk.Menu(self.view_button, tearoff=0)
        self._build_view_menu()
        self.view_button.configure(menu=self.view_menu)
        self.view_button.pack(side="left")

        self.more_button = ttk.Menubutton(bar, text="更多 ▾", style="TMenubutton")
        self.more_menu = tk.Menu(self.more_button, tearoff=0)
        self.more_menu.add_command(label="打开输出目录", command=self.on_open_folder)
        self.more_menu.add_command(label="站点设置…", command=self.on_settings)
        self.more_menu.add_separator()
        self.more_menu.add_command(label="快捷键…", command=self.on_shortcuts)
        self.more_menu.add_command(label="生成核心自检", command=self.on_check)
        self.more_button.configure(menu=self.more_menu)
        self.more_button.pack(side="left", padx=(4, 0))
        # 主题切换时要重新给菜单配色，这里登记全部菜单
        self._menus.append(self.more_menu)

    def _build_view_menu(self):
        menu = self.view_menu
        menu.delete(0, "end")

        theme_menu = tk.Menu(menu, tearoff=0)
        for value, label in (("light", "亮色"), ("dark", "暗色"), ("system", "跟随系统")):
            theme_menu.add_radiobutton(label=label, value=value, variable=self.var_theme,
                                       command=lambda v=value: self.set_theme(v))
        menu.add_cascade(label="主题", menu=theme_menu)

        menu.add_separator()
        menu.add_command(label="放大", accelerator="Ctrl+=", command=self.zoom_in)
        menu.add_command(label="缩小", accelerator="Ctrl+-", command=self.zoom_out)
        menu.add_command(label="恢复 100%", accelerator="Ctrl+0", command=self.zoom_reset)
        menu.add_separator()
        self.var_log_menu = tk.BooleanVar(value=self.log_visible)
        menu.add_checkbutton(label="显示运行日志面板", accelerator="Ctrl+L",
                             variable=self.var_log_menu, command=self.toggle_log)
        self.var_fullscreen = tk.BooleanVar(value=False)
        menu.add_checkbutton(label="全屏", accelerator="F11",
                             variable=self.var_fullscreen, command=self.toggle_fullscreen)

        self._menus = [self.view_menu, theme_menu]

    def _build_statusbar(self, parent):
        bar = ttk.Frame(parent, padding=(12, 4))
        bar.pack(side="bottom", fill="x")
        self.status_bar = bar

        # 右边的字数 / 行列 / 缩放先 pack（它们更重要），左边那句状态提示最后 pack。
        # pack 是按调用顺序分配空间的，所以位置不够时被压缩的是左边那句，
        # 而不是把最右边的「缩放 150%」切掉。
        ttk.Label(bar, textvariable=self.var_zoom, style="Status.TLabel").pack(side="right")
        ttk.Label(bar, text="·", style="Status.TLabel").pack(side="right", padx=8)
        ttk.Label(bar, textvariable=self.var_cursor, style="Status.TLabel").pack(side="right")
        ttk.Label(bar, text="·", style="Status.TLabel").pack(side="right", padx=8)
        ttk.Label(bar, textvariable=self.var_counts, style="Status.TLabel").pack(side="right")
        self.status_hint = ttk.Label(bar, textvariable=self.var_statusbar, style="Status.TLabel")
        self.status_hint.pack(side="left")

    def _build_log(self, parent):
        self.log_frame = ttk.LabelFrame(parent, text="运行日志", style="Group.TLabelframe",
                                        padding=(8, 4, 8, 6))
        self.log_frame.pack(fill="x", padx=10, pady=(0, 4))

        wrap = ttk.Frame(self.log_frame)
        wrap.pack(fill="both", expand=True)
        self.log_text = tk.Text(wrap, height=7, wrap="none", relief="flat",
                                borderwidth=0, font=(self.editor_family, BASE_LOG_SIZE))
        scroll = ttk.Scrollbar(wrap, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set, state="disabled")
        self.log_text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

    def _build_meta_panel(self, parent):
        """左栏：项目元数据

        整栏放在一个可滚动的 Canvas 里 —— 字段有十几行，缩放调到 150% 以上
        或者窗口拉矮时，固定布局会被截断、底下的字段根本够不着。
        """
        outer = ttk.Frame(parent)
        outer.pack(fill="both", expand=True, padx=(0, 8))

        self.meta_canvas = tk.Canvas(outer, highlightthickness=0, borderwidth=0)
        scroll = ttk.Scrollbar(outer, orient="vertical", command=self.meta_canvas.yview)
        self.meta_canvas.configure(yscrollcommand=scroll.set)
        self.meta_canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        frame = ttk.LabelFrame(self.meta_canvas, text="项目元数据", style="Group.TLabelframe",
                               padding=(12, 8, 12, 12))
        window = self.meta_canvas.create_window((0, 0), window=frame, anchor="nw")
        frame.columnconfigure(1, weight=1)

        def on_frame_configure(_event=None):
            self.meta_canvas.configure(scrollregion=self.meta_canvas.bbox("all"))

        def on_canvas_configure(event):
            self.meta_canvas.itemconfigure(window, width=event.width)

        frame.bind("<Configure>", on_frame_configure)
        self.meta_canvas.bind("<Configure>", on_canvas_configure)

        # 滚轮只在指针位于这一栏时接管；移出去就还回去，免得抢了正文的滚动
        def grab_wheel(_event=None):
            self.meta_canvas.bind_all(
                "<MouseWheel>",
                lambda e: self.meta_canvas.yview_scroll(-1 if e.delta > 0 else 1, "units"))

        def release_wheel(_event=None):
            self.meta_canvas.unbind_all("<MouseWheel>")

        self.meta_canvas.bind("<Enter>", grab_wheel)
        self.meta_canvas.bind("<Leave>", release_wheel)

        row = 0

        def add_label(text, r):
            ttk.Label(frame, text=text).grid(row=r, column=0, sticky="w", pady=3)

        def add_hint(text, r):
            ttk.Label(frame, text=text, style="Muted.TLabel").grid(
                row=r, column=1, columnspan=2, sticky="w")

        add_label("标题", row)
        ttk.Entry(frame, textvariable=self.var_title).grid(row=row, column=1, columnspan=2,
                                                           sticky="ew", pady=3)
        row += 1

        add_label("路径", row)
        ttk.Entry(frame, textvariable=self.var_slug).grid(row=row, column=1, columnspan=2,
                                                          sticky="ew", pady=3)
        row += 1
        add_hint("改这里等于改目录名和网址，保存时生效", row)
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
        add_hint("用逗号分隔，例如：薄膜, XRD, 退火", row)
        row += 1

        add_label("摘要", row)
        self.summary_text = tk.Text(frame, height=4, wrap="word", relief="solid", borderwidth=1,
                                    padx=6, pady=4)
        self.summary_text.grid(row=row, column=1, columnspan=2, sticky="ew", pady=3)
        row += 1
        add_hint("显示在首页卡片上，一两句话即可", row)
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
        add_hint("数字小的排前面；勾了置顶的排在最前", row)

        for var in (self.var_title, self.var_slug, self.var_date, self.var_updated,
                    self.var_tags, self.var_cover, self.var_order):
            var.trace_add("write", lambda *_a: self.mark_dirty())
        self.summary_text.bind("<<Modified>>", self._on_summary_modified)
        for var in (self.var_pinned, self.var_status, self.var_icon, self.var_color):
            var.trace_add("write", lambda *_a: self.mark_dirty())
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

    # ══════════════════════════════════════════════════════════════════
    #  右侧：正文
    # ══════════════════════════════════════════════════════════════════
    def _build_editor(self, parent):
        frame = ttk.LabelFrame(parent, text="正文（Markdown）", style="Group.TLabelframe",
                               padding=(10, 6, 10, 10))
        frame.pack(fill="both", expand=True)
        self.editor_frame = frame

        tools = self._scroll_row(frame, pady=(0, 6))

        # 靠右的这个先 pack：pack 是按调用顺序分配空间的，排在后面又靠右的控件
        # 会被左边那一排按钮挤成零宽 —— 「等宽」原本就是这么消失的
        ttk.Checkbutton(tools, text="等宽", variable=self.mono,
                        command=self.apply_editor_font).pack(side="right")

        # 高频的摊在外面，低频的收进「插入 ▾」——
        # 十六个按钮平铺会超出右栏宽度，末尾几个根本点不到
        for text, command in (
            ("二级标题", lambda: self.insert_block("## ", "", "小节标题")),
            ("三级标题", lambda: self.insert_block("### ", "", "小小节标题")),
            ("加粗", lambda: self.wrap_sel("**", "**", "加粗文字")),
            ("斜体", lambda: self.wrap_sel("*", "*", "斜体文字")),
            ("行内代码", lambda: self.wrap_sel("`", "`", "code")),
            ("引用", lambda: self.insert_block("> ", "", "引用文字")),
            ("列表", lambda: self.insert_block("- ", "", "列表项")),
            ("任务", lambda: self.insert_block("- [ ] ", "", "待办事项")),
        ):
            ttk.Button(tools, text=text, style="Tool.TButton", command=command).pack(side="left", padx=1)

        self.insert_button = ttk.Menubutton(tools, text="插入 ▾", style="TMenubutton")
        insert_menu = tk.Menu(self.insert_button, tearoff=0)
        insert_menu.add_command(label="代码块", command=self.insert_code_block)
        insert_menu.add_command(label="表格", command=self.insert_table)
        insert_menu.add_separator()
        insert_menu.add_command(label="图片…", command=self.insert_image)
        insert_menu.add_command(label="图注怎么写…", command=self.insert_figure_caption)
        insert_menu.add_command(label="Mermaid 图表", command=self.insert_mermaid)
        insert_menu.add_separator()
        insert_menu.add_command(label="链接", accelerator="Ctrl+K", command=self.insert_link)
        insert_menu.add_command(label="分隔线", command=lambda: self.insert_block("\n---\n", "", ""))
        self.insert_button.configure(menu=insert_menu)
        self.insert_button.pack(side="left", padx=(6, 1))

        ttk.Button(tools, text="查找", style="Tool.TButton",
                   command=self.on_find).pack(side="left", padx=(6, 1))

        self._build_find_bar(frame)

        wrap = ttk.Frame(frame)
        wrap.pack(fill="both", expand=True)
        self.editor_wrap = wrap
        self.body = tk.Text(wrap, wrap="word", undo=True, maxundo=-1, autoseparators=True,
                            relief="solid", borderwidth=1, padx=10, pady=8)
        scroll = ttk.Scrollbar(wrap, orient="vertical", command=self.body.yview)
        self.body.configure(yscrollcommand=scroll.set)
        self.body.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self.body.bind("<<Modified>>", self._on_body_modified)
        self.body.bind("<KeyRelease>", self._on_cursor_move)
        self.body.bind("<ButtonRelease-1>", self._on_cursor_move)
        self.body.bind("<FocusIn>", self._on_cursor_move)
        self.body.bind("<Control-MouseWheel>", self._on_ctrl_wheel)
        self.apply_editor_font()

    def _build_find_bar(self, parent):
        """查找替换条：默认不显示，Ctrl+F 才出来"""
        bar = ttk.Frame(parent)
        self.find_bar = bar

        # 关闭按钮先 pack 并且靠右 —— 同样是为了不被前面的控件挤掉：
        # 缩放调大之后这一行会变宽，最右边的关闭按钮必须始终够得着
        ttk.Button(bar, text="✕", style="Tool.TButton",
                   command=self.close_find).pack(side="right")

        ttk.Label(bar, text="查找").pack(side="left")
        self.find_entry = tk.Entry(bar, textvariable=self.find_var, width=16, relief="solid",
                                   borderwidth=1)
        self.find_entry.pack(side="left", padx=(4, 4))
        ttk.Button(bar, text="下一个", style="Tool.TButton",
                   command=lambda: self.find_next(True)).pack(side="left")
        ttk.Button(bar, text="上一个", style="Tool.TButton",
                   command=lambda: self.find_next(False)).pack(side="left", padx=2)
        ttk.Checkbutton(bar, text="忽略大小写", variable=self.find_nocase,
                        command=self._highlight_matches).pack(side="left", padx=(6, 8))

        ttk.Label(bar, text="替换为").pack(side="left")
        self.replace_entry = tk.Entry(bar, textvariable=self.replace_var, width=16,
                                      relief="solid", borderwidth=1)
        self.replace_entry.pack(side="left", padx=(4, 4))
        ttk.Button(bar, text="替换", style="Tool.TButton",
                   command=self.replace_one).pack(side="left")
        ttk.Button(bar, text="全部替换", style="Tool.TButton",
                   command=self.replace_all).pack(side="left", padx=2)

        self.var_find_info.set("")
        ttk.Label(bar, textvariable=self.var_find_info, style="Muted.TLabel").pack(
            side="left", padx=(8, 0))

        self.find_var.trace_add("write", lambda *_a: self._highlight_matches())
        self.find_entry.bind("<Return>", lambda _e: (self.find_next(True), "break")[1])
        self.find_entry.bind("<Escape>", lambda _e: (self.close_find(), "break")[1])
        self.replace_entry.bind("<Return>", lambda _e: (self.replace_one(), "break")[1])
        self.replace_entry.bind("<Escape>", lambda _e: (self.close_find(), "break")[1])

    def apply_editor_font(self):
        self._apply_fonts()

    # ══════════════════════════════════════════════════════════════════
    #  查找 / 替换
    # ══════════════════════════════════════════════════════════════════
    def on_find(self):
        if self.find_bar.winfo_ismapped():
            self.find_entry.focus_set()
            self.find_entry.select_range(0, "end")
            return
        self.find_bar.pack(fill="x", pady=(0, 6), before=self.editor_wrap)
        self.find_entry.focus_set()
        if self._has_selection():
            selected = self.body.get(tk.SEL_FIRST, tk.SEL_LAST)
            if "\n" not in selected:
                self.find_var.set(selected)
        self._highlight_matches()

    def close_find(self):
        if self.find_bar.winfo_ismapped():
            self.find_bar.pack_forget()
        self.body.tag_remove("find", "1.0", "end")
        self.body.tag_remove("find-current", "1.0", "end")
        self.var_find_info.set("")
        self.body.focus_set()

    def _matches(self) -> list[tuple[str, str]]:
        needle = self.find_var.get()
        if not needle:
            return []
        nocase = bool(self.find_nocase.get())
        out: list[tuple[str, str]] = []
        index = self.body.search(needle, "1.0", stopindex="end", nocase=nocase)
        while index:
            end = f"{index}+{len(needle)}c"
            out.append((index, end))
            index = self.body.search(needle, end, stopindex="end", nocase=nocase)
            if len(out) > 5000:      # 兜底：别为了高亮把界面卡死
                break
        return out

    def _highlight_matches(self):
        self.body.tag_remove("find", "1.0", "end")
        self.body.tag_remove("find-current", "1.0", "end")
        if not self.find_bar.winfo_ismapped():
            return
        matches = self._matches()
        for start, end in matches:
            self.body.tag_add("find", start, end)
        # 当前焦点所在的那个匹配单独上色
        cursor = self.body.index("insert")
        for start, end in matches:
            if self.body.compare(start, "<=", cursor) and self.body.compare(cursor, "<=", end):
                self.body.tag_add("find-current", start, end)
                break
        self.body.tag_raise("find")
        self.body.tag_raise("find-current")
        self.var_find_info.set(f"{len(matches)} 处" if matches else "没找到")

    def find_next(self, forward: bool = True):
        """跳到下一个 / 上一个匹配

        按【匹配下标】导航，而不是比较光标位置：光标在匹配内部或正好停在
        匹配末尾时，位置比较会把它自己也当成「光标之前的一处」，
        于是在第一处按 Shift+F3 不会绕回最后一处，而是原地不动。
        先定位光标当前落在第几处，再 ±1 取模，行为就和常见编辑器一致了。
        """
        matches = self._matches()
        if not matches:
            self._highlight_matches()
            self.say("没找到")
            return

        cursor = self.body.index("insert")
        needle_len = len(self.find_var.get())
        current = None
        for index, (start, end) in enumerate(matches):
            if self.body.compare(start, "<=", cursor) and self.body.compare(cursor, "<=", end):
                current = index
                break

        if current is None:
            # 光标不在任何匹配里：向前取光标之后的第一个，向后取光标之前的最后一个
            ahead = [i for i, (start, _e) in enumerate(matches) if self.body.compare(start, ">", cursor)]
            behind = [i for i, (start, _e) in enumerate(matches) if self.body.compare(start, "<", cursor)]
            if forward:
                target_index = ahead[0] if ahead else 0
            else:
                target_index = behind[-1] if behind else len(matches) - 1
        else:
            target_index = (current + 1) % len(matches) if forward else (current - 1) % len(matches)

        target = matches[target_index][0]
        end = f"{target}+{needle_len}c"

        # 找到之后要【选中】这一段，并把光标放到末尾：
        #   · 选中了「替换」才有东西可换（否则它只能一直往后跳）
        #   · 光标落在末尾，正好落在这处匹配的范围内，下一次导航才会继续往后
        self.body.mark_set("insert", end)
        self.body.tag_remove(tk.SEL, "1.0", "end")
        self.body.tag_add(tk.SEL, target, end)
        self.body.see(target)
        self.body.tag_remove("find-current", "1.0", "end")
        self.body.tag_add("find-current", target, end)
        self.body.tag_raise("find-current")
        self._update_cursor_pos()
        self.say(f"第 {target_index + 1} / {len(matches)} 处")

    def replace_one(self):
        needle = self.find_var.get()
        if not needle:
            return
        try:
            start, end = self.body.index(tk.SEL_FIRST), self.body.index(tk.SEL_LAST)
        except tk.TclError:
            start = end = None
        # 只有当前正好选中这个匹配时才替换，否则先跳过去（和多数编辑器一致）
        if not start or self.body.get(start, end) != needle:
            self.find_next(True)
            return
        self.body.edit_separator()
        self.body.delete(start, end)
        self.body.insert(start, self.replace_var.get())
        self.body.edit_separator()
        self.body.mark_set("insert", f"{start}+{len(self.replace_var.get())}c")
        self.find_next(True)
        self._highlight_matches()

    def replace_all(self):
        needle = self.find_var.get()
        if not needle:
            return
        matches = self._matches()
        if not matches:
            self.say("没找到")
            return
        replacement = self.replace_var.get()
        self.body.edit_separator()
        # 从后往前替换，前面的下标才不会因为长度变化而失效
        for start, end in reversed(matches):
            self.body.delete(start, end)
            self.body.insert(start, replacement)
        self.body.edit_separator()
        self.mark_dirty()
        self._highlight_matches()
        self.var_find_info.set(f"替换了 {len(matches)} 处")
        self.log(f"全部替换：{len(matches)} 处「{needle}」→「{replacement}」", "warn")
        self.say(f"替换了 {len(matches)} 处")

    # ══════════════════════════════════════════════════════════════════
    #  快捷键
    # ══════════════════════════════════════════════════════════════════
    def _bind_keys(self):
        def bind(sequence, handler):
            self.root.bind(sequence, handler)

        bind("<Control-s>", lambda _e: (self.on_save(), "break")[1])
        bind("<Control-S>", lambda _e: (self.on_save(), "break")[1])
        bind("<Control-n>", lambda _e: (self.on_new(), "break")[1])
        bind("<Control-Return>", lambda _e: (self.on_build_current(), "break")[1])
        bind("<F5>", lambda _e: (self.on_preview(), "break")[1])
        bind("<Control-f>", lambda _e: (self.on_find(), "break")[1])
        bind("<F3>", lambda _e: (self.find_next(True), "break")[1])
        bind("<Shift-F3>", lambda _e: (self.find_next(False), "break")[1])
        bind("<Control-b>", lambda _e: (self.wrap_sel("**", "**", "加粗文字"), "break")[1])
        bind("<Control-i>", lambda _e: (self.wrap_sel("*", "*", "斜体文字"), "break")[1])
        bind("<Control-k>", lambda _e: (self.insert_link(), "break")[1])
        bind("<Control-t>", lambda _e: (self.toggle_theme(), "break")[1])
        bind("<Control-l>", lambda _e: (self.toggle_log(), "break")[1])
        bind("<F11>", lambda _e: (self.toggle_fullscreen(), "break")[1])
        # 缩放：等号与加号都要认，小键盘的加减也认
        for sequence in ("<Control-equal>", "<Control-plus>", "<Control-KP_Add>"):
            bind(sequence, lambda _e: (self.zoom_in(), "break")[1])
        for sequence in ("<Control-minus>", "<Control-KP_Subtract>"):
            bind(sequence, lambda _e: (self.zoom_out(), "break")[1])
        bind("<Control-Key-0>", lambda _e: (self.zoom_reset(), "break")[1])
        bind("<Control-MouseWheel>", self._on_ctrl_wheel)

        # 编辑器行为：Tab 必须自己吃掉，否则 Tk 默认会拿它去切换焦点
        self.body.bind("<Tab>", self._on_tab)
        self.body.bind("<Shift-Tab>", lambda e: self._on_tab(e, dedent=True))
        self.body.bind("<ISO_Left_Tab>", lambda e: self._on_tab(e, dedent=True))
        self.body.bind("<Return>", self._on_return)

    # ══════════════════════════════════════════════════════════════════
    #  编辑器行为
    # ══════════════════════════════════════════════════════════════════
    def _on_tab(self, _event=None, dedent: bool = False):
        """Tab / Shift+Tab：缩进或反缩进，绝不把焦点移走

        选中多行时整块处理；没有选中就把当前行当一块处理 ——
        单行反缩进比「删掉光标前一个字符」更符合直觉。
        """
        if self._has_selection():
            first = self.body.index(tk.SEL_FIRST)
            last = self.body.index(tk.SEL_LAST)
            start_line = int(first.split(".")[0])
            end_line = int(last.split(".")[0])
            # 选区正好停在行首时，那一行不该被算进来
            if last.split(".")[1] == "0" and end_line > start_line:
                end_line -= 1
        else:
            start_line = end_line = int(self.body.index("insert").split(".")[0])

        self.body.edit_separator()
        for line in range(start_line, end_line + 1):
            if dedent:
                head = self.body.get(f"{line}.0", f"{line}.4")
                if head.startswith("\t"):
                    self.body.delete(f"{line}.0", f"{line}.1")
                else:
                    strip = len(head) - len(head.lstrip(" "))
                    if strip:
                        self.body.delete(f"{line}.0", f"{line}.{min(strip, 4)}")
            else:
                self.body.insert(f"{line}.0", " " * 4)
        self.body.edit_separator()

        if self._has_selection():
            # 缩进后保持原选区，用户才能连着按几次
            self.body.tag_remove(tk.SEL, "1.0", "end")
            self.body.tag_add(tk.SEL, f"{start_line}.0", f"{end_line}.end+1c")
        self.mark_dirty()
        return "break"

    def _on_return(self, _event=None):
        """回车时延续缩进与列表标记 —— 写清单和条目时省一半手"""
        line_start = self.body.index("insert linestart")
        line = self.body.get(line_start, "insert")

        # 当前行只有标记没有内容时，回车把标记清掉（结束这个列表）
        if re.fullmatch(r"[ \t]*(?:[-*+]|\d{1,9}[.)])[ \t]+(?:\[[ xX]\][ \t]+)?", line):
            self.body.delete(line_start, "insert")
            self.body.insert("insert", "\n")
            self.body.see("insert")
            self.mark_dirty()
            return "break"

        item = re.match(r"^([ \t]*)([-*+]|\d{1,9}[.)])([ \t]+)(\[[ xX]\][ \t]+)?", line)
        if item:
            lead, mark, gap, box = item.groups()
            if mark[0].isdigit():
                mark = f"{int(re.match(r'\d+', mark).group(0)) + 1}{mark[-1]}"
            carry = f"{lead}{mark}{gap}" + ("[ ] " if box else "")
        else:
            carry = re.match(r"[ \t]*", line).group(0)

        self.body.insert("insert", "\n" + carry)
        self.body.see("insert")
        self.mark_dirty()
        return "break"

    def _on_cursor_move(self, _event=None):
        self._update_current_line()
        self._update_cursor_pos()

    def _update_current_line(self):
        self.body.tag_remove("curline", "1.0", "end")
        self.body.tag_add("curline", "insert linestart", "insert lineend+1c")
        # 当前行底色要垫在最下面，不然会盖住查找高亮和选区
        self.body.tag_lower("curline")

    def _update_cursor_pos(self):
        line, column = self.body.index("insert").split(".")
        self.var_cursor.set(f"行 {line} · 列 {int(column) + 1}")

    def toggle_log(self):
        self.log_visible = not self.log_visible
        if self.log_visible:
            self.log_frame.pack(fill="x", padx=10, pady=(0, 4))
        else:
            self.log_frame.pack_forget()
        if hasattr(self, "var_log_menu"):
            self.var_log_menu.set(self.log_visible)
        self._sync_minsize()
        self.say("运行日志面板：" + ("显示" if self.log_visible else "隐藏"))

    def toggle_fullscreen(self):
        state = not bool(self.root.attributes("-fullscreen"))
        self.root.attributes("-fullscreen", state)
        if hasattr(self, "var_fullscreen"):
            self.var_fullscreen.set(state)

    def on_shortcuts(self):
        ShortcutsDialog(self.root, self._palette(), self.ui_family, self.editor_family)

    # ══════════════════════════════════════════════════════════════════
    #  状态与日志
    # ══════════════════════════════════════════════════════════════════
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
        self._update_cursor_pos()

    def _on_body_modified(self, _event=None):
        if not self.body.edit_modified():
            return
        self.body.edit_modified(False)
        self.mark_dirty()
        self._update_current_line()
        # 统计要遍历全文，按键时别每一下都算，停 400ms 再算
        if self._count_job:
            self.root.after_cancel(self._count_job)
        self._count_job = self.root.after(400, self._refresh_counts)
        if self.find_bar.winfo_ismapped():
            if self._find_job:
                self.root.after_cancel(self._find_job)
            self._find_job = self.root.after(400, self._highlight_matches)

    # ══════════════════════════════════════════════════════════════════
    #  项目读写
    # ══════════════════════════════════════════════════════════════════
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
            self._update_current_line()
            self._highlight_matches()

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

    # ══════════════════════════════════════════════════════════════════
    #  工具栏动作
    # ══════════════════════════════════════════════════════════════════
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
        dialog = NewProjectDialog(self.root, self.cfg, self._palette())
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
        self.log("自检通过：链接、语言包、行内样式、HTML 结构、CSS 类名、图片引用全部正常", "ok")
        unused = core.unused_i18n_keys()
        if unused:
            self.log(f"（参考）语言包里有 {len(unused)} 个键当前没被用到：{', '.join(unused)}", "dim")
        dead = core.unused_css_classes()
        if dead:
            self.log(f"（参考）CSS 里有 {len(dead)} 个类当前没被用到：{', '.join(dead)}", "dim")
        messagebox.showinfo("自检通过",
                            "链接、语言包、行内样式、HTML 结构、CSS 类名、图片引用都正常。",
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
        dialog = SettingsDialog(self.root, core.load_site_config(), self._palette())
        self.root.wait_window(dialog)
        if dialog.saved:
            self.cfg = core.load_site_config()
            self.log("站点设置已保存 —— 点「重新生成全部」让页面生效", "ok")
            self.say("站点设置已保存")

    # ══════════════════════════════════════════════════════════════════
    #  插入 Markdown 片段
    # ══════════════════════════════════════════════════════════════════
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
            "· 点「插入 ▾ → 图片」可以直接把本地图片复制进来并插入这行写法",
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

    # ══════════════════════════════════════════════════════════════════
    #  状态持久化与关闭
    # ══════════════════════════════════════════════════════════════════
    def _restore_state(self):
        state = core.read_json(STATE_PATH) or {}

        # 视图偏好要在界面搭好之后、载入项目之前应用
        if state.get("theme") in ("light", "dark", "system"):
            self.theme_choice = state["theme"]
            self.var_theme.set(self.theme_choice)
        self.apply_theme()

        zoom = state.get("zoom")
        if isinstance(zoom, (int, float)) and ZOOM_MIN <= float(zoom) <= ZOOM_MAX:
            self.set_zoom(float(zoom))

        if state.get("mono"):
            self.mono.set(True)
            self.apply_editor_font()
        if state.get("auto_update") is False:
            self.auto_update.set(False)
        if state.get("log_visible") is False:
            self.toggle_log()

        geometry = state.get("geometry")
        if geometry:
            try:
                self.root.geometry(self._clamp_geometry(geometry))
            except tk.TclError:
                pass
        if state.get("zoomed"):
            try:
                self.root.state("zoomed")
            except tk.TclError:
                pass

        sash = state.get("sash")
        if isinstance(sash, int) and sash > 200:
            try:
                self.paned.sashpos(0, sash)
            except tk.TclError:
                pass

        wanted = state.get("slug")
        if wanted and wanted in getattr(self, "_slugs", []) and not self.dirty:
            self.load_record(wanted)

        self._sync_minsize()
        self.log("写字板就绪。改完点「生成此页」或按 Ctrl+Enter，"
                 "首页的卡片与目录会自动重建。", "dim")
        if not self.records:
            self.log("目前一个项目都没有：点左上角「新建」开始第一篇记录。", "warn")
        else:
            self.say(f"共 {len(self.records)} 个项目")

    def _clamp_geometry(self, geometry: str) -> str:
        """把上次的窗口位置夹回屏幕内 —— 换显示器后旧坐标可能落在屏幕外"""
        match = re.match(r"(\d+)x(\d+)(?:([+-]\d+)([+-]\d+))?$", geometry or "")
        if not match:
            return geometry
        width, height = int(match.group(1)), int(match.group(2))
        screen_w, screen_h = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        width, height = min(width, screen_w), min(height, screen_h)
        if match.group(3) is None:
            return f"{width}x{height}"
        x, y = int(match.group(3)), int(match.group(4))
        x = max(-20, min(x, screen_w - 200))
        y = max(0, min(y, screen_h - 120))
        return f"{width}x{height}+{x}+{y}"

    def _save_state(self):
        state = {
            "geometry": self.root.winfo_geometry(),
            "zoomed": self.root.state() == "zoomed",
            "slug": self.current.slug if self.current else None,
            "mono": bool(self.mono.get()),
            "auto_update": bool(self.auto_update.get()),
            "theme": self.theme_choice,
            "zoom": round(self.zoom, 2),
            "log_visible": bool(self.log_visible),
        }
        try:
            state["sash"] = int(self.paned.sashpos(0))
        except tk.TclError:
            pass
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
    parser = argparse.ArgumentParser(
        prog="writepad.py", description="科研记录写字板（research.who-young.top）")
    parser.add_argument("slug", nargs="?", help="启动后直接打开的项目路径")
    parser.add_argument("--theme", choices=("light", "dark", "system"),
                        help="覆盖本次启动的主题（不写回配置）")
    parser.add_argument("--zoom", type=int, help="覆盖本次启动的缩放百分比（不写回配置）")
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))

    enable_dpi_awareness()
    root = tk.Tk()
    app = WritepadApp(root)

    if args.theme:
        app.set_theme(args.theme, announce=False)
    if args.zoom:
        app.set_zoom(args.zoom / 100.0, announce=False)

    if args.slug:
        if args.slug in getattr(app, "_slugs", []):
            app.load_record(args.slug)
        else:
            app.log(f"找不到项目 {args.slug}", "err")

    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
