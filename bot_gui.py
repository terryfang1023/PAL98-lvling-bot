# -*- coding: utf-8 -*-
"""仙剑98 挂机机器人 — 图形界面 (tkinter, 零额外依赖)

用法:
    python bot_gui.py             # 打开界面
    python bot_gui.py --selftest  # 自检 (不开窗口, 供 CI/排障用)

界面即参数面板: 选好参数点"开始"即启动 pal_bot_mem5.py 子进程 (v17:
直奔怪点刷怪 + 顺路开宝箱 + **效率驱动的自动换场景**; 取消勾选"要素巡回"则退回
v15 渐进巡逻);
"停止"写入 mem_stop.flag (优雅停机, 清键退出), "强制停止"杀进程并冲洗按键。

「战斗攻击键」= 战斗中 bot 反复按的那个键, 换地图/换版本后按键变了就在
这里改 (落盘到 bot_config.json, 下次打开还在)。SDLPAL 官方键表见
botcfg.ATTACK_KEY_CHOICES 上方注释。

版本号 (2026-10-12): 窗口标题与底部状态区右下角都显示 botcfg.APP_VERSION,
只有那一处定义 (对外发布号, 与 README/git tag 同源)。
⚠️ 改标题时**不许出现 "Pal"**(大小写敏感) —— 那是找游戏窗口的判据。
"""
import os
import ctypes
import re
import sys
import time
import queue
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext

import botcfg
import bot_itemmon                     # 只读物品栏 (独立 ctypes 实现, 不引用机器人内存引擎)
import bot_chestlog                    # 开箱台账 chest_log.json (独立, 不引用机器人内存引擎)
import bot_winlog                      # 累计总胜场 win_total.json (独立)
import bot_explog                      # 战斗收益 exp_log.json (独立, 只读)

BASE = os.path.dirname(os.path.abspath(__file__))

# 机器人启动必需的数据文件 (由 tools/build_data.py 从游戏数据生成)
REQUIRED_DATA = ["map_walk.npy", "gyt_scene_report.txt"]
OPTIONAL_DATA = ["map_walk2.npy"]          # scene_check.py 体检用
STOP_FLAG = os.path.join(BASE, "mem_stop.flag")

BIAS_CHOICES = ["", "上", "下", "左", "右", "右上", "右下", "左上", "左下"]

# 程序图标 (任务栏 / Alt+Tab / 标题栏)。设计: 仙剑 + 右下循环箭头(挂机),
# 底色取游戏本体的砖红/深棕面板色。生成器: tools/make_icon.py (每尺寸原生绘制)。
ICON_PATH = os.path.join(BASE, "assets", "pal98bot.ico")

# 显式应用 ID (2026-10-13 真机教训): Win11 任务栏对没有 AUMID 的窗口,
# 图标解析会退化成"白纸"—— 即使 WM_SETICON 已把图标挂上。必须在 tk.Tk()
# **之前**调用 SetCurrentProcessExplicitAppUserModelID 才生效 (见 _set_aumid)。
APP_AUMID = "TerryFang.PAL98Bot.GUI"


def _set_aumid():
    """注册显式 AppUserModelID。幂等、失败静默 (老系统可能没这 API)。"""
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_AUMID)
    except Exception:
        pass


_set_aumid()   # 模块加载即注册 (必须在任何 tk.Tk() 之前, import 时机天然满足)

# 攻击键下拉的候选项 (语义见 botcfg.ATTACK_KEY_CHOICES 上方的官方键表注释)
ATK_CHOICES = botcfg.ATTACK_KEY_CHOICES
ATK_HINT = ("r=重复行动 (挂机首选)  a=自动  f=法术  space=确认  "
            "d=防御  q=逃跑  e=物品")

# 子进程 (bot/体检) 强制 UTF-8 输出, 否则 Windows 管道默认 GBK -> 界面乱码
ENV_UTF8 = dict(os.environ, PYTHONIOENCODING="utf-8")


class BotGUI:

    def __init__(self, root: tk.Tk):
        self.root = root
        # ★ 标题带版本号 (口径与告警见 botcfg.APP_VERSION):
        #   任务栏 / Alt+Tab 一眼能看出跑的是哪一版 —— 尤其刚改完要确认新版有没有
        #   真生效时。⚠️ 不许往标题里塞 "Pal" (大小写敏感), 会被机器人当成游戏窗口。
        root.title("仙剑98 挂机机器人 (SDLPAL) %s" % botcfg.APP_VERSION)
        # ★ 2026-10-13: 窗口比例收敛到 4:3 (原来按钮一条 + 参数一行塞满, 把窗口
        #   撑到 ≈1400×890 = 1.57)。960/720 = 1.333; 实测控件"请求高度" 719,
        #   所以 720 高刚好不裁切 (改布局后请用 _diag/measure_gui.py 复核)。
        root.geometry("960x720")
        root.minsize(900, 675)
        # 任务栏/标题栏图标 (2026-10-13): 一眼认出哪个是机器人, 不再是
        # tkinter 默认的灰羽毛。
        self._apply_icon(root)

        self.proc = None                 # bot 子进程
        self.q = queue.Queue()           # 子进程日志队列
        self.wins = 0                    # 解析出的胜场 (本次启动)
        self.wins_total = 0              # 累计总胜场 (win_total.json)
        self._wt_t = 0.0                 # 累计胜场读盘节流
        self.elem = (0, 0, 0)            # (怪点命中, 走到没打, 开箱成功)
        self.chg = 0                     # 自动换场景次数
        self.cur_scene = None            # 解析出的当前场景
        self.pin_active = 0              # 本次运行钉住的场景号 (0 = 不驻留)
        self.t_start = None

        self._build_params()
        self._build_buttons()
        self._build_log()
        self._build_status()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(250, self._poll)
        root.after(1800, self._item_tick)   # 物品栏轮询 (主线程, 读 1.5KB 内存微秒级)

    # ---------- 界面 ----------
    @staticmethod
    def _apply_icon(root, path=None):
        """设置窗口/任务栏图标。

        - `default=` 让**之后创建的子窗口**(开箱记录等)继承同一图标;
        - 文件缺失 (别人只拷了 .py 没拷 assets/) / 平台不支持 -> 静默跳过,
          界面照样能开 —— 图标是锦上添花, 绝不能因为它起不来;
        - 非 Windows 走 iconphoto (PhotoImage 必须留引用, 否则被 GC 回收
          图标变空白 —— tkinter 的经典坑)。
        """
        path = path or ICON_PATH
        try:
            if not os.path.exists(path):
                return
            if sys.platform.startswith("win"):
                root.iconbitmap(default=path)      # 类图标 (子窗口继承)
                self._win_taskbar_icon(root, path)  # ★ 任务栏直达 (见下)
            else:
                img = tk.PhotoImage(file=os.path.splitext(path)[0] + ".png")
                root.iconphoto(True, img)
                root._icon_img = img
        except Exception:
            pass

    @staticmethod
    def _win_taskbar_icon(root, path=None):
        """★ 任务栏图标 = AUMID (见 _set_aumid) + WM_SETICON 直达窗口。

        真机取证 (2026-10-13): 只 `wm iconbitmap -default` 标题栏有图标但任务栏
        仍是"白纸"; 补上 WM_SETICON 也还不够, 必须再加显式 AUMID (在建窗前注册)
        任务栏才肯用窗口图标。这里用 ctypes 发三种 ICON_* (small/big/small2,
        Win11 任务栏读 small2), LoadImageW/SendMessageW 显式声明 argtypes ——
        64 位下句柄参数不声明类型会被截断。零前台切换。
        """
        path = path or ICON_PATH
        try:
            if not os.path.exists(path):
                return
            u32 = ctypes.windll.user32
            u32.LoadImageW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p,
                                       ctypes.c_uint, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_uint]
            u32.LoadImageW.restype = ctypes.c_void_p       # HICON
            u32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                         ctypes.c_size_t, ctypes.c_ssize_t]
            u32.SendMessageW.restype = ctypes.c_ssize_t
            hwnd = u32.GetParent(root.winfo_id())  # Tk 内部子窗 -> 外层 toplevel
            if not hwnd:
                return
            big = u32.LoadImageW(None, path, 1, 32, 32, 0x10)   # IMAGE_ICON
            small = u32.LoadImageW(None, path, 1, 16, 16, 0x10)
            for wparam, h in ((1, big), (0, small), (2, small)):
                if h:
                    u32.SendMessageW(hwnd, 0x80, wparam, h)  # WM_SETICON
        except Exception:
            pass  # 图标是锦上添花, 绝不因它起不来

    @staticmethod
    def _atk_from_cfg():
        """读配置里的攻击键; 值非法/文件损坏 -> 回退默认 (界面不许开不起来)。"""
        try:
            return botcfg.norm_key(botcfg.get("attack_key"))
        except Exception:
            return str(botcfg.DEFAULTS["attack_key"])

    def _build_params(self):
        f = ttk.LabelFrame(self.root, text="运行参数")
        f.pack(fill="x", padx=10, pady=(10, 4))

        def entry(parent, label, width=8, init=""):
            ttk.Label(parent, text=label).pack(side="left", padx=(10, 2))
            var = tk.StringVar(value=init)
            e = ttk.Entry(parent, textvariable=var, width=width)
            e.pack(side="left")
            return var

        # ---- 物品栏 + 人物状态实时监视 (只读内存; 必须先于各行 pack 才能占住右侧) ----
        self.fr_items = ttk.LabelFrame(f, text="物品 / 人物 (实时)")
        self.fr_items.pack(side="right", fill="y", padx=(4, 2), pady=2)
        self.lbl_focus = tk.Label(
            self.fr_items, text="%s × --" % bot_itemmon.ITEM_NAME,
            font=("Microsoft YaHei UI", 11, "bold"), fg="#8a5a00")
        self.lbl_focus.pack(anchor="w", padx=6, pady=(2, 0))
        # 人物等级/经验 (在队成员, 2 秒刷新); ★经验列 = 当前/升下一级所需
        self.tv_roles = ttk.Treeview(
            self.fr_items, columns=("name", "lv", "exp", "hp"),
            show="headings", height=3, selectmode="none")
        for col, txt, w, anc in (("name", "人物", 62, "w"),
                                 ("lv", "等级", 34, "e"),
                                 ("exp", "经验/升级", 88, "e"),
                                 ("hp", "体力", 58, "e")):
            self.tv_roles.heading(col, text=txt)
            self.tv_roles.column(col, width=w, anchor=anc, stretch=False)
        self.tv_roles.tag_configure("near", foreground="#c05000")   # 快升级了
        self.tv_roles.pack(fill="x", padx=4, pady=(1, 0))
        fr_tv = ttk.Frame(self.fr_items)
        fr_tv.pack(fill="both", expand=True, padx=4, pady=(1, 4))
        self.tv_items = ttk.Treeview(
            fr_tv, columns=("name", "n"), show="headings",
            height=5, selectmode="none")
        self.tv_items.heading("name", text="物品")
        self.tv_items.heading("n", text="数量")
        self.tv_items.column("name", width=96, anchor="w", stretch=False)
        self.tv_items.column("n", width=40, anchor="e", stretch=False)
        tv_sb = ttk.Scrollbar(fr_tv, orient="vertical",
                              command=self.tv_items.yview)
        self.tv_items.configure(yscrollcommand=tv_sb.set)
        self.tv_items.pack(side="left", fill="both", expand=True)
        tv_sb.pack(side="left", fill="y")
        self.tv_items.tag_configure("focus", foreground="#c05000")
        self.itemmon = bot_itemmon.ItemReader()
        self.item_names = bot_itemmon.load_names()
        self.rolemon = bot_itemmon.RoleReader()
        self.chest_log = bot_chestlog.ChestLog()   # 开箱台账 (只读/只写它自己的 json)
        self._chest_dlg = None                     # 「开箱记录」窗口 (避免重复开)
        self._item_prev_focus = None          # 傀儡虫上次数量 (变化 -> 日志)

        # ---- 参数行 (2026-10-13 重排) ----
        # 原则: 一行最多 3 组 / 一行别超过 ~640px —— 原来"能塞就塞"的排法把窗口
        # 撑到 1400+ 宽 (≈2:1), 现在改成多行窄条, 整窗收敛到 ≈4:3。
        r1 = ttk.Frame(f); r1.pack(fill="x", pady=(2, 3))
        self.v_maxb = entry(r1, "胜场上限 (0=不限)", 6, "0")
        self.v_mins = entry(r1, "运行时长/分钟 (0=不限)", 6, "0")
        self.v_stall = entry(r1, "无胜场保险/分钟 (0=关)", 6, "20")

        r1b = ttk.Frame(f); r1b.pack(fill="x", pady=(0, 2))
        self.v_r0 = entry(r1b, "巡逻半径起", 5, "28")
        self.v_rmax = entry(r1b, "巡逻半径上限", 5, "90")
        ttk.Label(r1b, text="方向偏置").pack(side="left", padx=(10, 2))
        self.v_bias = tk.StringVar(value="")
        ttk.Combobox(r1b, textvariable=self.v_bias, width=7,
                     values=BIAS_CHOICES, state="readonly").pack(side="left")
        self.v_open = tk.BooleanVar(value=False)
        ttk.Checkbutton(r1b, text="穿门模式 (踩到传送阵/楼梯就去下一层)",
                        variable=self.v_open).pack(side="left", padx=(14, 0))

        r1c = ttk.Frame(f); r1c.pack(fill="x", pady=(0, 2))
        ttk.Label(r1c, text="战斗攻击键").pack(side="left", padx=(10, 2))
        self.v_atk = tk.StringVar(value=self._atk_from_cfg())
        ttk.Combobox(r1c, textvariable=self.v_atk, width=7,
                     values=ATK_CHOICES).pack(side="left")
        ttk.Label(r1c, text="← " + ATK_HINT,
                  foreground="#666").pack(side="left", padx=(8, 0))

        # ---- 勾选项 (每行 2~3 个, 长的说明保留在括号里) ----
        r2 = ttk.Frame(f); r2.pack(fill="x", pady=(0, 2))
        self.v_elem = tk.BooleanVar(value=True)
        ttk.Checkbutton(r2, text="要素巡回 (直奔怪点刷怪 + 顺路开宝箱)",
                        variable=self.v_elem).pack(side="left", padx=(10, 0))
        self.v_auto = tk.BooleanVar(value=True)
        ttk.Checkbutton(r2, text="自动换场景", variable=self.v_auto).pack(
            side="left", padx=(14, 0))
        ttk.Label(r2, text="← 先点「场景评分」效果最好",
                  foreground="#666").pack(side="left", padx=(6, 0))

        r2b2 = ttk.Frame(f); r2b2.pack(fill="x", pady=(0, 2))
        self.v_rope = tk.BooleanVar(value=botcfg.as_bool(
            botcfg.get("rope"), True))
        ttk.Checkbutton(r2b2, text="绳子攀爬 (走上绳子按空格换层 —— 试炼窟等)",
                        variable=self.v_rope).pack(side="left", padx=(10, 0))
        self.v_rhunt = tk.BooleanVar(value=botcfg.as_bool(
            botcfg.get("rope_hunt"), True))
        ttk.Checkbutton(r2b2, text="遇到就爬 (绳子优先级最高)",
                        variable=self.v_rhunt).pack(side="left", padx=(14, 0))

        r2b3 = ttk.Frame(f); r2b3.pack(fill="x", pady=(0, 2))
        self.v_cmemo = tk.BooleanVar(value=botcfg.as_bool(
            botcfg.get("chest_memo"), True))
        ttk.Checkbutton(r2b3, text="记住开过的箱子 (跨重启跳过)",
                        variable=self.v_cmemo).pack(side="left", padx=(10, 0))
        self.v_cmem = tk.BooleanVar(value=botcfg.as_bool(
            botcfg.get("chest_mem"), True))
        ttk.Checkbutton(r2b3, text="空箱读内存跳过 (已开空/不可触发的直接忽略)",
                        variable=self.v_cmem).pack(side="left", padx=(14, 0))

        r2b4 = ttk.Frame(f); r2b4.pack(fill="x", pady=(0, 2))
        self.v_feat = tk.BooleanVar(value=botcfg.as_bool(
            botcfg.get("feat_log"), True))
        ttk.Checkbutton(r2b4, text="要素台账 (按编号记每次战斗)",
                        variable=self.v_feat).pack(side="left", padx=(10, 0))
        # ---- 走没走过的路 (2026-10-04) ----
        self.v_fresh = tk.BooleanVar(value=(
            botcfg.as_float(botcfg.get("revisit_lambda"), 0.15) > 0))
        ttk.Checkbutton(r2b4, text="优先走没走过的路 (寻路避重 + 怪点轮转)",
                        variable=self.v_fresh).pack(side="left", padx=(14, 0))

        r2b5 = ttk.Frame(f); r2b5.pack(fill="x", pady=(0, 2))
        self.v_explore = tk.BooleanVar(value=botcfg.as_bool(
            botcfg.get("explore"), True))
        ttk.Checkbutton(r2b5, text="每", variable=self.v_explore).pack(
            side="left", padx=(10, 1))
        self.v_explore_every = tk.StringVar(
            value=str(botcfg.get("explore_every")))
        ttk.Entry(r2b5, textvariable=self.v_explore_every, width=3).pack(
            side="left")
        ttk.Label(r2b5, text="场去一次没走过的区域 (探索配额)",
                  foreground="#666").pack(side="left", padx=(1, 0))

        # ---- ★ 驻留练级 (2026-10-20 用户诉求) ----
        #   「在某个地方练级的时候, 机器人不会主动离开, 并且会自动避开出入口」。
        #   引擎没有场景名表 (地名从不显示), 所以只能按**场景号**钉; 场景号
        #   在状态栏一直显示, 点「取当前」就从日志里抄一个, 不用记地名。
        r2b6 = ttk.Frame(f); r2b6.pack(fill="x", pady=(0, 2))
        _pin0 = botcfg.resolve_int("pin_scene")[0]
        self.v_pin_on = tk.BooleanVar(value=(_pin0 != 0))
        ttk.Checkbutton(r2b6, text="驻留练级 (钉住一个场景, 绝不主动离开)",
                        variable=self.v_pin_on).pack(side="left", padx=(10, 0))
        ttk.Label(r2b6, text="场景号").pack(side="left", padx=(14, 2))
        self.v_pin_scene = tk.StringVar(
            value=str(_pin0) if _pin0 > 0 else "")
        ttk.Entry(r2b6, textvariable=self.v_pin_scene, width=5).pack(
            side="left")
        ttk.Button(r2b6, text="取当前", width=7,
                   command=self.pin_take_current).pack(side="left", padx=4)
        ttk.Label(r2b6, text="(留空 = 钉住启动时所在的场景; 出入口自动圈禁区)",
                  foreground="#666").pack(side="left", padx=(4, 0))

        # ---- 自动存盘 (pal_bot_heal.py 的菜单宏; 内部标识沿用 heal_* 历史名,
        #      2026-10-13 用户把这项功能从"补血"改成"存盘" —— 引擎本来就是
        #      "跑一条菜单按键序列 + 四道校验", 换功能只换序列和文案) ----
        r2d = ttk.Frame(f); r2d.pack(fill="x", pady=(0, 2))
        self.v_heal = tk.BooleanVar(value=bool(
            botcfg.as_bool(botcfg.get("heal_after_battle"), True)))
        ttk.Checkbutton(r2d, text="战后自动存盘", variable=self.v_heal).pack(
            side="left", padx=(10, 0))
        ttk.Label(r2d, text="每").pack(side="left", padx=(14, 1))
        self.v_heal_every = tk.StringVar(value=str(botcfg.get("heal_every")))
        ttk.Entry(r2d, textvariable=self.v_heal_every, width=3).pack(
            side="left")
        ttk.Label(r2d, text="场一次   间隔").pack(side="left", padx=(1, 2))
        self.v_heal_gap = tk.StringVar(value=str(botcfg.get("heal_gap")))
        ttk.Entry(r2d, textvariable=self.v_heal_gap, width=4).pack(
            side="left")
        ttk.Label(r2d, text="s").pack(side="left")

        r2e = ttk.Frame(f); r2e.pack(fill="x", pady=(0, 2))
        ttk.Label(r2e, text="按键序列").pack(side="left", padx=(10, 2))
        self.v_heal_seq = tk.StringVar(value=str(
            botcfg.get("heal_seq") or botcfg.SEQ_DEFAULT))
        ttk.Entry(r2e, textvariable=self.v_heal_seq, width=31).pack(
            side="left")

        r2f = ttk.Frame(f); r2f.pack(fill="x", pady=(0, 3))
        ttk.Label(r2f, text="← 存档: ESC→系统→存档→选档位 (存完自动回地图)；"
                            "⚠ 退出/读档是危险项, 先「存盘测试」验一遍",
                  foreground="#b06000").pack(side="left", padx=(10, 0))

        r3 = ttk.Frame(f); r3.pack(fill="x", pady=(0, 4))
        self.v_exe = entry(r3, "Python 解释器 (bot 子进程使用)", 33,
                           sys.executable)

    def _build_buttons(self):
        """操作按钮 —— 2026-10-13 拆成两行。

        原来 14 个按钮挤在一行, 单这一条就把窗口撑到 1300+ 宽 (整窗 ≈2:1);
        拆两行后按钮区 ≈700px, 整窗收敛到 ≈4:3。顺序按"用得多 -> 用得少"。
        """
        f = ttk.Frame(self.root)
        f.pack(fill="x", padx=10, pady=4)
        row1 = ttk.Frame(f); row1.pack(fill="x", pady=(0, 3))
        row2 = ttk.Frame(f); row2.pack(fill="x")
        self.b_start = ttk.Button(row1, text="▶ 开始", command=self.start)
        self.b_start.pack(side="left", padx=(0, 6))
        self.b_stop = ttk.Button(row1, text="■ 停止 (优雅)", command=self.stop,
                                 state="disabled")
        self.b_stop.pack(side="left", padx=6)
        self.b_kill = ttk.Button(row1, text="⛔ 强制停止", command=self.force_stop,
                                 state="disabled")
        self.b_kill.pack(side="left", padx=6)
        ttk.Button(row1, text="场景体检", command=self.scene_check).pack(
            side="left", padx=6)
        ttk.Button(row1, text="场景评分", command=self.scene_score).pack(
            side="left", padx=6)
        ttk.Button(row1, text="环境体检", command=self.env_check).pack(
            side="left", padx=6)
        ttk.Button(row1, text="效率报告", command=self.run_report).pack(
            side="left", padx=6)
        ttk.Button(row1, text="清空日志", command=self.clear_log).pack(
            side="right", padx=6)
        ttk.Button(row2, text="门审计", command=self.door_audit).pack(
            side="left", padx=6)
        ttk.Button(row2, text="战斗识别", command=self.battle_probe).pack(
            side="left", padx=6)
        ttk.Button(row2, text="空箱体检", command=self.chest_probe).pack(
            side="left", padx=6)
        ttk.Button(row2, text="存盘测试", command=self.heal_once).pack(
            side="left", padx=6)
        ttk.Button(row2, text="刷新怪点/宝箱",
                   command=self.refresh_scene_info).pack(side="left", padx=6)
        ttk.Button(row2, text="开箱记录", command=self.chest_log_view).pack(
            side="left", padx=6)
        ttk.Button(row2, text="检测游戏窗口", command=self.detect_window).pack(
            side="left", padx=6)

    def _build_log(self):
        f = ttk.LabelFrame(self.root, text="运行日志")
        f.pack(fill="both", expand=True, padx=10, pady=4)
        body = ttk.Frame(f)
        body.pack(fill="both", expand=True)
        # ---- 左: 战斗收益 (2026-10-13 用户诉求) ----
        #   数据来自 exp_log.json —— 机器人每赢一场写入 (唯一写者), 这里只读。
        left = ttk.Frame(body)
        left.pack(side="left", fill="y", padx=(0, 8))
        ttk.Label(left, text="战斗收益 (本次运行)",
                  font=("", 9, "bold")).pack(anchor="w")
        self.v_gain_sum = tk.StringVar(value="经验 +0 / 金钱 +0")
        ttk.Label(left, textvariable=self.v_gain_sum,
                  font=("Consolas", 10, "bold")).pack(anchor="w")
        self.v_gain_rate = tk.StringVar(value="平均 -- 经验/时")
        ttk.Label(left, textvariable=self.v_gain_rate,
                  foreground="#666666").pack(anchor="w")
        cols = ("n", "t", "exp", "cash")
        self.tree_gain = ttk.Treeview(left, columns=cols, show="headings",
                                      height=6)
        for c, w, txt, anc in (("n", 52, "场次", "e"), ("t", 60, "时间", "center"),
                               ("exp", 62, "经验", "e"), ("cash", 56, "金钱", "e")):
            self.tree_gain.heading(c, text=txt)
            self.tree_gain.column(c, width=w, anchor=anc, stretch=False)
        self.tree_gain.pack(fill="both", expand=True, pady=(4, 0))
        ttk.Label(left, text="经验 = 每人所得 (三人在队各拿一份,\n"
                             "不是三倍总和); 开箱收益只计入累计",
                  foreground="#999999", font=("", 8),
                  justify="left").pack(anchor="w", pady=(2, 0))
        # ---- 右: 运行日志 (原有) ----
        right = ttk.Frame(body)
        right.pack(side="left", fill="both", expand=True)
        # width 给个较小的请求值, 由 fill/expand 撑开 —— 否则默认 80 列会把
        # 整窗撑得很宽 (2026-10-13 改 4:3 布局时收窄)。
        self.log = scrolledtext.ScrolledText(right, height=11, width=58,
                                             state="disabled",
                                             font=("Consolas", 9))
        self.log.pack(fill="both", expand=True)

    def _build_status(self):
        """底部: 胜场两行 (本次启动 / 累计总胜场) + 状态行。

        为什么要拆两段: 机器人进程里的 battles_won **每局归零**(回答"本次赢了多少"),
        用户还想要一个**一直积累、想清就清**的总数 —— 那个落在 win_total.json,
        机器人是唯一写者; 清空由本界面写 win_reset.flag 让机器人兑现 (见 bot_winlog)。
        """
        f = ttk.Frame(self.root)
        f.pack(fill="x", side="bottom")
        wf = ttk.Frame(f)
        wf.pack(fill="x", padx=10, pady=(3, 1))
        ttk.Label(wf, text="本次胜场").pack(side="left")
        self.v_wins_local = tk.StringVar(value="0")
        ttk.Label(wf, textvariable=self.v_wins_local, width=7, anchor="w",
                  font=("Consolas", 11, "bold")).pack(side="left", padx=(4, 0))
        ttk.Label(wf, text="累计总胜场").pack(side="left", padx=(14, 0))
        self.v_wins_total = tk.StringVar(value=str(self.wins_total))
        ttk.Label(wf, textvariable=self.v_wins_total, width=9, anchor="w",
                  font=("Consolas", 11, "bold")).pack(side="left", padx=(4, 0))
        ttk.Button(wf, text="清空累计胜场", command=self.wins_reset).pack(
            side="left", padx=(12, 0))
        ttk.Button(wf, text="要素覆盖率", command=self.feat_report).pack(
            side="left", padx=6)
        # ★ 底部右侧固定显示版本号 (2026-10-12): 截图分享时也能带出版本,
        #   免得"这张图是哪一版跑出来的"说不清。值只有一个来源 = botcfg.APP_VERSION。
        self.v_ver = tk.StringVar(value="版本 %s" % botcfg.APP_VERSION)
        ttk.Label(wf, textvariable=self.v_ver, anchor="e",
                  foreground="#777777").pack(side="right", padx=(0, 2))
        self.v_status = tk.StringVar(value="已停止")
        ttk.Label(f, textvariable=self.v_status, anchor="w",
                  relief="sunken").pack(fill="x")

    # ---------- 胜场 / 要素台账 ----------
    def wins_reset(self):
        """清空累计总胜场。

        机器人在跑 -> 只写 win_reset.flag (机器人是 win_total.json 的唯一写者,
        直接改文件会被它下一次"读-加-写"覆盖回去, 所以必须让它自己清);
        没在跑 -> 直接清 0, 立刻可见。
        """
        try:
            wl = bot_winlog.WinLog()
        except Exception as ex:
            self._err("清空失败", str(ex))
            return
        if self._running():
            if wl.request_reset():
                self.log_line("[GUI] 已请求清空累计胜场 —— 机器人下一个胜场时"
                              "从 0 重新计 (当前显示待刷新)")
            else:
                self._err("清空失败", "写 win_reset.flag 失败 (目录只读?)")
            return
        old = wl.total()
        if old and not messagebox.askyesno(
                "清空累计胜场", "累计总胜场 = %d 场。确定清 0?" % old,
                parent=self.root):
            return
        wl.reset()
        self.wins_total = 0
        self.v_wins_total.set("0")
        self.log_line("[GUI] 累计胜场已清 0 (原 %d 场)" % old)

    def feat_report(self):
        """生成「要素覆盖率报告」: 每张地图哪些怪点打过/各几次/哪些没打过。

        只读 (读 scene_info.json + feat_log.json + chest_log.json), 不碰机器人;
        报告写到运行目录 feat_report.txt / feat_report.json。
        """
        path = os.path.join(BASE, "tools", "feat_report.py")
        if not os.path.exists(path):
            self._err("缺少工具", "找不到 tools/feat_report.py")
            return

        def work():
            try:
                r = subprocess.run(
                    [sys.executable, path], cwd=BASE, capture_output=True,
                    timeout=180, env=ENV_UTF8,
                    text=True, encoding="utf-8", errors="replace")
                out = (r.stdout or "") + (r.stderr or "")
                for ln in out.splitlines()[-40:]:
                    self.q.put("[覆盖率] %s" % ln)
                self.q.put("[覆盖率] 报告已写入 feat_report.txt (退出码 %s)"
                           % r.returncode)
            except Exception as ex:
                self.q.put("[覆盖率] 运行失败: %s" % ex)

        self.log_line("[GUI] 生成要素覆盖率报告 (只读, 不影响运行中的机器人)…")
        threading.Thread(target=work, daemon=True).start()

    # ---------- 动作 ----------
    def _running(self):
        return self.proc is not None and self.proc.poll() is None

    def _err(self, title, msg):
        messagebox.showerror(title, msg, parent=self.root)

    def pin_take_current(self):
        """把状态栏的「场景 N」抄进驻留框 —— 用户不必知道地名, 也不必记编号。"""
        sc = self.cur_scene
        if not sc:
            self._err("还没有场景号",
                      "机器人还没报出当前场景 (状态栏会显示「场景 N」)。\n\n"
                      "两种办法:\n"
                      "  ① 先点「▶ 开始」, 等状态栏出现「场景 N」, 再点「取当前」;\n"
                      "  ② 直接在左边的框里手填场景号。")
            return
        self.v_pin_scene.set(str(sc))
        self.v_pin_on.set(True)
        self.log_line("[GUI] 驻留场景 = %d (抄自机器人日志), 下次点「开始」生效"
                      % sc)

    def start(self):
        if self._running():
            self._err("提示", "机器人已在运行中。")
            return
        # 1. 数据文件检查
        missing = [n for n in REQUIRED_DATA
                   if not os.path.exists(os.path.join(BASE, n))]
        if missing:
            self._err("缺少地图数据",
                      "未找到: %s\n\n请先用游戏数据生成:\n"
                      "  python tools/build_data.py <游戏目录>\n"
                      "详见 README.md「数据文件生成」一节。" % ", ".join(missing))
            return
        # 2. 参数合法性
        try:
            maxb = max(0, int(self.v_maxb.get() or "0"))
            mins = max(0, int(self.v_mins.get() or "0"))
            r0 = int(self.v_r0.get() or "28")
            rmax = int(self.v_rmax.get() or "90")
            stall = max(0, int(self.v_stall.get() or "20"))
        except ValueError:
            self._err("参数错误", "胜场/时长/半径/保险必须是整数。")
            return
        exe = self.v_exe.get().strip() or sys.executable
        if not os.path.exists(exe):
            self._err("参数错误", "Python 解释器路径不存在:\n%s" % exe)
            return
        # 攻击键: 语法校验 (不导入 pal_bot —— 它设 DPI 感知会把界面缩小组件)
        try:
            atk = botcfg.norm_key(self.v_atk.get())
        except ValueError as ex:
            self._err("攻击键错误", "「战斗攻击键」%s\n\n"
                      "常用: r=重复上次行动  a=自动战斗  f=法术  space=确认"
                      % ex)
            return
        self.v_atk.set(atk)                  # 回显规范化结果 (如 " R " -> "r")
        try:
            botcfg.put("attack_key", atk)    # 记下来, 下次打开界面还在
        except Exception as ex:
            self.log_line("[GUI] 攻击键写盘失败 (不影响本次运行): %s" % ex)
        # 存盘选项: 序列语法必须先过 —— 序列错了机器人会在菜单里乱按
        heal_on = bool(self.v_heal.get())
        try:
            heal_keys = botcfg.parse_seq(self.v_heal_seq.get())
        except ValueError as ex:
            self._err("存盘序列错误", "「按键序列」%s\n\n"
                      "写法示例: esc,下,下,回车,回车,回车 或 ESC → 下 → 回车"
                      % ex)
            return
        try:
            heal_every = max(1, int(self.v_heal_every.get() or "1"))
        except ValueError:
            self._err("参数错误", "「每 N 场一次」必须是整数。")
            return
        try:
            heal_gap = max(0.05, float(self.v_heal_gap.get() or "0.3"))
        except ValueError:
            self._err("参数错误", "「间隔」必须是数字 (秒)。")
            return
        heal_seq_txt = botcfg.format_seq(heal_keys)
        self.v_heal_seq.set(heal_seq_txt)    # 回显规范化结果
        # 走没走过的路: 探索周期必须是整数 (0 = 关掉探索)
        try:
            explore_every = max(0, int(self.v_explore_every.get() or "0"))
        except ValueError:
            self._err("参数错误", "「每 N 场去一次生区」必须是整数 (0 = 不探索)。")
            return
        try:
            botcfg.put("heal_after_battle", heal_on)
            botcfg.put("heal_seq", heal_seq_txt)
            botcfg.put("heal_every", heal_every)
            botcfg.put("heal_gap", heal_gap)
        except Exception as ex:
            self.log_line("[GUI] 存盘设置写盘失败 (不影响本次运行): %s" % ex)
        # ★ 驻留练级 (2026-10-20): 钉住场景号 -> 机器人不迁移/不换场景/不爬绳,
        #   并把该场景所有"触发脚本能换景"的对象圈成禁区; 万一被送出去会走回来。
        #   ★ 没填场景号时传 -1 = 钉住「启动时所在的场景」—— 把队伍带到练级点
        #     再点开始就行, 用户完全不需要知道场景号 (引擎也没有地名表)。
        pin_on = bool(self.v_pin_on.get())
        try:
            pin_val = int(str(self.v_pin_scene.get()).strip() or "0")
        except ValueError:
            pin_val = 0
        if pin_on and pin_val <= 0:
            pin_val = int(self.cur_scene or 0)     # 界面上有就顺手抄一个
            if pin_val > 0:
                self.v_pin_scene.set(str(pin_val))
        pin = (pin_val if pin_val > 0 else -1) if pin_on else 0
        try:
            botcfg.put("pin_scene", pin)
        except Exception as ex:
            self.log_line("[GUI] 驻留设置写盘失败 (不影响本次运行): %s" % ex)
        self.pin_active = pin
        secs = mins * 60
        args = [exe, "-u", "pal_bot_mem5.py", str(maxb), str(secs),
                str(r0), str(rmax), self.v_bias.get(),
                "穿门" if self.v_open.get() else "", str(stall * 60),
                "" if self.v_elem.get() else "巡逻",
                "" if self.v_auto.get() else "不换"]
        # 攻击键走环境变量, 而不是第 10 个位置参数 ——
        # ① 位置参数有 9 个的既定契约 (tools/dryrun5、bench_loop、gui_smoke 都按
        #    这个数目解析), 不动它就没有回归;
        # ② 环境变量能把"界面上此刻的值"钉死给子进程, 不怕两份 bot_config.json
        #    (母目录/仓库目录) 不一致。
        env = dict(ENV_UTF8)
        env[botcfg.env_name("attack_key")] = atk
        # ★ 驻留场景号 (0 = 不驻留): 与 GUI 上这一刻的值钉死
        env[botcfg.env_name("pin_scene")] = str(pin)
        # 绳子攀爬开关 (pal_bot_mem5: 无门可走时用绳子换层)
        rope_on = bool(self.v_rope.get())
        try:
            botcfg.put("rope", rope_on)
        except Exception as ex:
            self.log_line("[GUI] 绳子开关写盘失败 (不影响本次运行): %s" % ex)
        env[botcfg.env_name("rope")] = "1" if rope_on else "0"
        # ★ 绳子最高优先级「遇到就爬」(pal_bot_mem5._rope_hunt)
        rhunt_on = bool(self.v_rhunt.get()) and rope_on
        try:
            botcfg.put("rope_hunt", rhunt_on)
        except Exception as ex:
            self.log_line("[GUI] 遇到就爬开关写盘失败 (不影响本次运行): %s" % ex)
        env[botcfg.env_name("rope_hunt")] = "1" if rhunt_on else "0"
        # 开箱台账开关 (chest_log.json: 记住开过的箱子, 跨重启跳过)
        cmemo_on = bool(self.v_cmemo.get())
        try:
            botcfg.put("chest_memo", cmemo_on)
        except Exception as ex:
            self.log_line("[GUI] 台账开关写盘失败 (不影响本次运行): %s" % ex)
        env[botcfg.env_name("chest_memo")] = "1" if cmemo_on else "0"
        # ★ 空箱从内存判断 (pal_bot_mem5.chest_mem_scan): 已开空/不可触发的箱子
        #   根本不认, 连"走过去按一次空格"都省掉
        cmem_on = bool(self.v_cmem.get())
        try:
            botcfg.put("chest_mem", cmem_on)
        except Exception as ex:
            self.log_line("[GUI] 空箱内存判断开关写盘失败 (不影响本次运行): %s" % ex)
        env[botcfg.env_name("chest_mem")] = "1" if cmem_on else "0"
        # ★ 要素台账 (feat_log.json): 按编号记每一次战斗 (哪些怪点打过/几次)
        feat_on = bool(self.v_feat.get())
        try:
            botcfg.put("feat_log", feat_on)
        except Exception as ex:
            self.log_line("[GUI] 要素台账开关写盘失败 (不影响本次运行): %s" % ex)
        env[botcfg.env_name("feat_log")] = "1" if feat_on else "0"
        # ★ 走没走过的路 (2026-10-04): 寻路避重强度 + 定期探索配额
        #   λ 直接以数值走环境变量 (0 = 关掉避重, 保留老的最短路径行为)
        fresh_on = bool(self.v_fresh.get())
        lam = "0.15" if fresh_on else "0"
        explore_on = bool(self.v_explore.get()) and explore_every > 0
        try:
            botcfg.put("revisit_lambda", float(lam))
            botcfg.put("explore", explore_on)
            botcfg.put("explore_every", explore_every)
        except Exception as ex:
            self.log_line("[GUI] 生路偏好写盘失败 (不影响本次运行): %s" % ex)
        env[botcfg.env_name("revisit_lambda")] = lam
        env[botcfg.env_name("explore")] = "1" if explore_on else "0"
        env[botcfg.env_name("explore_every")] = str(explore_every)
        # 补血四项同样走环境变量 (理由同上: 钉死"界面上这一刻的值")
        env[botcfg.env_name("heal_after_battle")] = "1" if heal_on else "0"
        env[botcfg.env_name("heal_seq")] = heal_seq_txt
        env[botcfg.env_name("heal_every")] = str(heal_every)
        env[botcfg.env_name("heal_gap")] = str(heal_gap)
        # 3. 启动
        try:
            self.proc = subprocess.Popen(
                args, cwd=BASE, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW)
        except Exception as ex:
            self._err("启动失败", str(ex))
            return
        self._set_buttons(True)              # 先切按钮状态, 后续任何异常不影响
        self.wins = 0
        self.elem = (0, 0, 0)
        self.t_start = time.time()
        self._wt_t = 0.0                     # 累计胜场的读盘节流
        threading.Thread(target=self._reader, daemon=True).start()
        self.log_line("[GUI] 已启动: %s  (攻击键=%s, 开箱台账=%s, 空箱内存判断=%s, "
                      "要素台账=%s, 绳子=%s, 寻路避重=%s, 定期探索=%s)"
                      % (" ".join(args[1:]), atk,
                         "开" if cmemo_on else "关",
                         "开" if cmem_on else "关",
                         "开" if feat_on else "关",
                         ("遇到就爬" if rhunt_on
                          else ("仅无门时" if rope_on else "关")),
                         ("λ=" + lam) if fresh_on else "关",
                         ("每%d场" % explore_every) if explore_on else "关"))
        if pin:
            self.log_line("[GUI] ★ 驻留 = %s: 不迁移/不换场景/不爬绳, "
                          "出入口圈 %d 格禁区, 真被送出去会自动走回来"
                          % (("场景 %d" % pin) if pin > 0
                             else "启动时所在的场景 (落地即钉住)", 2))
        else:
            self.log_line("[GUI] 驻留 = 关 (效率驱动, 该换场景就换)")

    def stop(self):
        if not self._running():
            return
        with open(STOP_FLAG, "w") as f:
            f.write("")
        self.v_status.set("停止中… (等待 bot 清键退出)")

    def force_stop(self):
        if not self._running():
            return
        if not messagebox.askyesno(
                "强制停止",
                "直接杀死 bot 进程可能留下按住的方向键 (队伍自己走丢)。\n"
                "将自动冲洗游戏按键, 确认继续?", parent=self.root):
            return
        try:
            self.proc.terminate()
        except Exception:
            pass
        self._flush_game_keys()

    def scene_score(self):
        """后台跑 tools/scene_score.py, 生成全场景评分表并输出排名。"""
        script = os.path.join(BASE, "tools", "scene_score.py")
        if not os.path.exists(script) or not os.path.exists(
                os.path.join(BASE, "scene_info.json")):
            self._err("缺少数据",
                      "场景评分需要 scene_info.json + map_walk2.npy。\n"
                      "请先「刷新怪点/宝箱」再试。")
            return
        if not os.path.exists(os.path.join(BASE, "map_walk2.npy")):
            self._err("缺少数据",
                      "场景评分需要 map_walk2.npy\n"
                      "请先运行: python tools/build_data.py <游戏目录>")
            return
        exe = self.v_exe.get().strip() or sys.executable
        self.log_line("[评分] 开始扫描全部场景 (约 20~40 秒, 不阻塞界面)…")
        self._run_bg([exe, script, "--top", "15"], "评分")

    def env_check(self):
        """后台跑 tools/env_check.py: 报窗口/几何/内存基址 (只读, 不按键)。

        什么时候用: 换 exe、重开游戏、手动缩放过窗口之后 —— 先体检再开跑。
        """
        script = os.path.join(BASE, "tools", "env_check.py")
        if not os.path.exists(script):
            self._err("缺少工具", "未找到 tools/env_check.py")
            return
        exe = self.v_exe.get().strip() or sys.executable
        self.log_line("[体检] 开始 (只读, 不按键; 需要游戏在运行)…")
        self._run_bg([exe, script], "体检")

    def run_report(self):
        """后台跑 tools/run_report.py: 给"刚跑完的这一轮"做效率归因 (只读)。

        什么时候用: 跑完一轮觉得"怎么才赢了这么点"时, 点一下就知道
        时间被哪个机制吃掉了 (停机/绕行/自愈/迁移), 以及每格真实耗时。
        """
        script = os.path.join(BASE, "tools", "run_report.py")
        if not os.path.exists(script):
            self._err("缺少工具", "未找到 tools/run_report.py")
            return
        exe = self.v_exe.get().strip() or sys.executable
        self.log_line("[报告] 分析 bot_log_mem.txt 里最近一次运行 (只读)…")
        self._run_bg([exe, script], "报告")

    def door_audit(self):
        """后台跑 tools/door_audit.py: 当前场景「门」的只读审计。

        什么时候用: 日志里反复出现「迁移: 门未触发」、换场景 10 次 0 成功时。
        它会列出每扇门的 K/距离/是否可达/要多少步, 指出 pick_migrate_door
        现在会选哪扇、为什么不是别的, 以及日志里的「被挡」热点卡在哪扇门前面。
        """
        script = os.path.join(BASE, "tools", "door_audit.py")
        if not os.path.exists(script):
            self._err("缺少工具", "未找到 tools/door_audit.py")
            return
        exe = self.v_exe.get().strip() or sys.executable
        self.log_line("[门审计] 检查当前场景的门与可达性 (只读, 不按键)…")
        self._run_bg([exe, script], "门审计")

    def battle_probe(self):
        """后台跑 tools/battle_probe.py: 只读截一帧, 报告战斗/菜单判定细节。

        什么时候用: 换了新地图怀疑「战斗识别不出来」时 —— 把疑似战斗的
        画面停在屏幕上点一下, 它会列出 is_battle v2 的每个特征值
        (绿 MP 数字锚/白 HP 数字/棕框) 和菜单判定 red/tan,
        一眼看出哪个环节没过。只读, 不按键。
        """
        script = os.path.join(BASE, "tools", "battle_probe.py")
        if not os.path.exists(script):
            self._err("缺少工具", "未找到 tools/battle_probe.py")
            return
        exe = self.v_exe.get().strip() or sys.executable
        self.log_line("[战斗识别] 只读截帧判定 (不按键; 需要游戏在运行)…")
        self._run_bg([exe, script], "战斗识别")

    def chest_probe(self):
        """后台跑 tools/chest_probe.py: 只读体检当前场景/台账里的宝箱。

        什么时候用: 想看"机器人到底跳过了哪些箱子、凭什么判它空"时 ——
        它把 scene_info 快照 / 开箱台账 / 游戏内存三方摆在一起对账,
        顺带标出台账里被误记成「空」、其实物品还在的箱子。
        只读, 不按键, 不写台账 (核对用临时副本)。
        """
        script = os.path.join(BASE, "tools", "chest_probe.py")
        if not os.path.exists(script):
            self._err("缺少工具", "未找到 tools/chest_probe.py")
            return
        exe = self.v_exe.get().strip() or sys.executable
        self.log_line("[空箱体检] 只读对账 (不按键; 需要游戏在运行)…")
        self._run_bg([exe, script], "空箱体检")

    def heal_once(self):
        """后台跑 tools/heal_once.py: 对当前画面**真的执行一次**存盘宏。

        用途: 校准「按键序列」—— 存盘/补血都是"按一串菜单键", 序列对不对
        只有真按一遍才知道。机器人必须先停止 (两个进程抢键盘没法判断
        是谁按的); 脚本自己也会检测并在机器人还在跑时拒绝执行。
        序列沿用界面输入框里的当前值 (--seq 传过去, 不改配置文件)。
        (函数名/脚本名沿用 heal 历史名, 内部标识不动; 界面文字已是「存盘」。)
        """
        script = os.path.join(BASE, "tools", "heal_once.py")
        if not os.path.exists(script):
            self._err("缺少工具", "未找到 tools/heal_once.py")
            return
        exe = self.v_exe.get().strip() or sys.executable
        args = [exe, script]
        if self.v_heal_seq.get().strip():
            args += ["--seq", self.v_heal_seq.get().strip()]
        self.log_line("[存盘测试] 将对当前画面执行一次存盘宏 (会按键盘!) — "
                      "请确认机器人已停止、游戏画面停在地图上…")
        self._run_bg(args, "存盘测试")

    def _run_bg(self, cmd, tag):
        """后台跑一个命令, 输出逐行灌进日志 (不卡界面)。"""
        def work():
            try:
                r = subprocess.run(cmd, cwd=BASE, env=ENV_UTF8,
                                   capture_output=True, text=True,
                                   encoding="utf-8", errors="replace",
                                   timeout=900)
                out = ((r.stdout or "") + (r.stderr or "")).strip()
                rc = r.returncode
            except Exception as ex:
                out, rc = "执行失败: %s" % ex, -1
            if not out:
                out = "(无输出)"
            for ln in out.splitlines():
                self.q.put("[%s] %s" % (tag, ln))
            self.q.put("[%s] 结束 (退出码 %s)" % (tag, rc))
        threading.Thread(target=work, daemon=True).start()

    def scene_check(self):
        """运行 scene_check.py, 输出进日志。"""
        if not os.path.exists(os.path.join(BASE, "map_walk2.npy")):
            self._err("缺少数据", "场景体检需要 map_walk2.npy\n"
                      "请先运行: python tools/build_data.py <游戏目录>")
            return
        exe = self.v_exe.get().strip() or sys.executable
        try:
            r = subprocess.run([exe, "scene_check.py"], cwd=BASE,
                               env=ENV_UTF8,
                               capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=60)
            self.log_line("[体检] %s" % (r.stdout or r.stderr or "(无输出)"))
        except Exception as ex:
            self._err("体检失败", str(ex))

    def refresh_scene_info(self):
        """重新导出全图怪点/宝箱清单（编号 + 名字），约 2 秒。

        ★ v19.0: 数据源是**游戏原始 SSS.MKF**(离线), 所以**不需要游戏开着**,
        也不受"已开过的箱子被引擎改写脚本"影响 —— 清单永远是全量的
        (旧版读运行中的游戏内存, 开过的箱子会从清单里消失)。
        """
        exe = self.v_exe.get().strip() or sys.executable
        script = os.path.join(BASE, "tools", "build_scene_info.py")
        if not os.path.exists(script):
            self._err("缺少工具", "未找到 tools/build_scene_info.py")
            return
        try:
            r = subprocess.run([exe, script], cwd=BASE, env=ENV_UTF8,
                               capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=180)
        except Exception as ex:
            self._err("刷新失败", str(ex))
            return
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        self.log_line("[要素] " + (out[-2000:] or "(无输出)"))
        if r.returncode != 0:
            self._err("刷新失败",
                      out[-800:] or "找不到游戏目录 (需要 SSS.MKF) —— "
                      "用 `python tools/build_scene_info.py --game 游戏目录`")

    # ------------------------------------------------------------------
    #  开箱台账 (chest_log.json): 看机器人开过哪些箱子 / 清空重刷
    # ------------------------------------------------------------------
    def chest_log_view(self):
        """打开「开箱记录」窗口 (再点一次 -> 刷新并提到前面)。"""
        try:
            self.chest_log.reload()          # 机器人子进程可能刚写了新记录
        except Exception:
            pass
        if self._chest_dlg is not None:
            try:
                self._chest_dlg.lift()
                self._fill_chest_dlg()
                return
            except tk.TclError:              # 窗口已被用户关掉
                self._chest_dlg = None
        win = tk.Toplevel(self.root)
        self._chest_dlg = win
        win.title("开箱记录 (chest_log.json)")
        win.geometry("660x430")
        win.transient(self.root)
        win.protocol("WM_DELETE_WINDOW", self._close_chest_dlg)

        top = ttk.Frame(win)
        top.pack(side="top", fill="x", padx=10, pady=(8, 2))
        self.lbl_chest = ttk.Label(
            top, text="", font=("Microsoft YaHei UI", 10, "bold"))
        self.lbl_chest.pack(side="left")
        # 底部区域: 用 side="bottom" 时先 pack 的贴在窗口最下沿, 所以从下往上 pack
        self.lbl_chest_hint = ttk.Label(
            win, text="「空/打不开」= 试满 4 次没反应 (空箱, 或早就开过了); "
                      "想让机器人再试一次就点「清空空箱记录」",
            foreground="#666")
        self.lbl_chest_hint.pack(side="bottom", anchor="w", padx=10,
                                 pady=(0, 6))
        self.lbl_chest_path = ttk.Label(
            win, text="台账文件: %s" % self.chest_log.path,
            foreground="#666")
        self.lbl_chest_path.pack(side="bottom", anchor="w", padx=10)
        bot = ttk.Frame(win)
        bot.pack(side="bottom", fill="x", padx=10, pady=(2, 0))
        ttk.Button(bot, text="刷新", command=self._fill_chest_dlg).pack(
            side="left")
        ttk.Button(bot, text="清空空箱记录",
                   command=self.chest_log_forget_empty).pack(
            side="left", padx=8)
        ttk.Button(bot, text="清空全部", command=self.chest_log_clear).pack(
            side="left", padx=8)
        ttk.Button(bot, text="关闭", command=self._close_chest_dlg).pack(
            side="right")

        fr = ttk.Frame(win)
        fr.pack(fill="both", expand=True, padx=10, pady=4)
        cols = (("scene", "场景", 52, "e"), ("K", "格子 K", 88, "e"),
                ("t", "最近一次尝试", 148, "center"),
                ("state", "状态", 78, "center"),
                ("item", "开出", 128, "w"),
                ("dn", "数量", 44, "e"), ("n", "次数", 44, "e"))
        self.tv_chest = ttk.Treeview(fr, columns=[c[0] for c in cols],
                                     show="headings", height=14)
        for col, txt, w, anc in cols:
            self.tv_chest.heading(col, text=txt)
            self.tv_chest.column(col, width=w, anchor=anc, stretch=False)
        sb = ttk.Scrollbar(fr, orient="vertical",
                           command=self.tv_chest.yview)
        self.tv_chest.configure(yscrollcommand=sb.set)
        self.tv_chest.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        self.tv_chest.tag_configure("empty", foreground="#999")
        self._fill_chest_dlg()

    def _fill_chest_dlg(self):
        """把台账灌进列表 (读盘, 只读操作)。返回行数。"""
        tv = getattr(self, "tv_chest", None)
        if tv is None:
            return 0
        try:
            self.chest_log.reload()
            rows = self.chest_log.rows()
            s = self.chest_log.stat()
        except Exception:
            rows, s = [], {"n_chest": 0, "n_marks": 0, "n_scene": 0}
        try:
            tv.delete(*tv.get_children())
            for r in rows:
                tv.insert("", "end",
                          values=(r["scene"], r["K"], r["t"], r["state"],
                                  r["item"] or "--", r["dn"] or "", r["n"]),
                          tags=() if r["ok"] else ("empty",))
            self.lbl_chest.config(
                text="已记 %d 个箱子 (打开 %d / 空 %d, 累计跑 %d 次 / "
                     "%d 个场景)"
                     % (s["n_chest"], s["n_ok"], s["n_empty"], s["n_marks"],
                        s["n_scene"]))
        except tk.TclError:
            pass
        return len(rows)

    def chest_log_forget_empty(self):
        """只清"空/打不开"记录 (想让机器人再试一次那些箱子时用)。"""
        try:
            s = self.chest_log.stat()
        except Exception:
            s = {"n_empty": 0}
        if not s.get("n_empty"):
            messagebox.showinfo("开箱记录", "没有「空/打不开」记录。",
                                parent=self.root)
            return
        if not messagebox.askyesno(
                "清空空箱记录",
                "有 %d 个箱子是「试满 4 次没反应」记下的 (空箱或早就开过)。\n"
                "清掉后机器人会重新去试它们 —— 如果是真的开过了, 会再白跑一次。\n"
                "确定清空?" % s["n_empty"], parent=self.root):
            return
        gone = self.chest_log.forget_empty()
        self.log_line("[台账] 已清空 %d 条「空/打不开」记录 (会重新尝试)" % gone)
        self._fill_chest_dlg()

    def chest_log_clear(self):
        """清空台账 (要让机器人重新去开那些箱子, 就得清掉)。"""
        try:
            n = self.chest_log.count()
        except Exception:
            n = 0
        if not n:
            messagebox.showinfo("开箱记录", "台账是空的, 没什么可清。",
                                parent=self.root)
            return
        if not messagebox.askyesno(
                "清空开箱记录",
                "确定清空这 %d 条开箱记录?\n\n"
                "清空后机器人会重新去尝试这些箱子 (正在跑的那一局除外:\n"
                "本局已跳过哪些箱子记在内存里, 要下一局才重试)。" % n,
                parent=self.root):
            return
        gone = self.chest_log.clear()
        self.log_line("[台账] 已清空 %d 条开箱记录" % gone)
        self._fill_chest_dlg()

    def _close_chest_dlg(self):
        win, self._chest_dlg = self._chest_dlg, None
        self.tv_chest = None
        if win is not None:
            try:
                win.destroy()
            except tk.TclError:
                pass

    def detect_window(self):
        # ★ 在子进程里检测 (不把 pal_bot 导入 GUI 进程):
        #   pal_bot 导入时会设置 DPI 感知, 会把 tkinter 界面缩小组件
        exe = self.v_exe.get().strip() or sys.executable
        code = ("import sys; sys.path.insert(0, r'%s')\n"
                "from pal_bot import find_game_window\n"
                "print(len(find_game_window()))\n" % BASE)
        try:
            r = subprocess.run([exe, "-c", code], cwd=BASE, env=ENV_UTF8,
                               capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=30)
            n = int(r.stdout.strip().splitlines()[0])
        except Exception as ex:
            self._err("检测失败", str(ex))
            return
        if n:
            self.log_line("[GUI] 找到游戏窗口: %d 个, 可以开始" % n)
            messagebox.showinfo("检测游戏窗口",
                                "找到 %d 个游戏窗口, 可以开始。" % n,
                                parent=self.root)
        else:
            self.log_line("[GUI] 未找到游戏窗口! 请先启动游戏 (SDLPAL)")
            messagebox.showwarning(
                "检测游戏窗口",
                "未找到标题含 Pal 的游戏窗口。\n请先启动游戏再开始。",
                parent=self.root)

    def clear_log(self):
        self.log.config(state="normal")
        self.log.delete("1.0", "end")
        self.log.config(state="disabled")

    def on_close(self):
        if self._running():
            if not messagebox.askyesno(
                    "退出", "机器人还在运行, 停止并退出吗?",
                    parent=self.root):
                return
            self.stop()
            for _ in range(40):          # 最多等 10 秒优雅退出
                if not self._running():
                    break
                self.root.after(250, lambda: None)
                self.root.update()
            if self._running():
                self.proc.terminate()
                self._flush_game_keys()
        self.root.destroy()

    # ---------- 后台 ----------
    def _reader(self):
        try:
            for line in self.proc.stdout:
                self.q.put(line.rstrip())
        except Exception:
            pass
        self.q.put(None)

    def _item_tick(self):
        """物品栏轮询: 只读进程内存 (微秒级), 刷新右侧列表与焦点物品。"""
        try:
            rows = self.itemmon.entries()
            if rows is None:
                self.lbl_focus.config(
                    text="%s × -- (游戏未运行)" % bot_itemmon.ITEM_NAME,
                    foreground="#999")
            else:
                amap = {w: n for (_i, w, n, _u) in rows}
                focus = amap.get(bot_itemmon.ITEM_ID, 0)
                self.lbl_focus.config(
                    text="%s × %d" % (bot_itemmon.ITEM_NAME, focus),
                    foreground="#8a5a00")
                if (self._item_prev_focus is not None
                        and focus != self._item_prev_focus):
                    d = focus - self._item_prev_focus
                    self.log_line("[物品] %s %s%d -> %d"
                                  % (bot_itemmon.ITEM_NAME,
                                     "+" if d > 0 else "", d, focus))
                self._item_prev_focus = focus
                self._fill_items(rows, amap)
        except Exception:
            pass                          # 监视是锦上添花, 绝不带崩界面
        try:
            self._role_tick()
        except Exception:
            pass
        try:
            self.root.after(2000, self._item_tick)
        except tk.TclError:
            pass                          # 窗口已关

    def _role_tick(self):
        """人物等级/经验轮询: 只读内存, 刷新右侧小表 (在队成员)。"""
        tv = self.tv_roles
        d = self.rolemon.read(self.item_names)
        if d is None:
            tv.delete(*tv.get_children())
            for name in ("--", "--", "--"):
                tv.insert("", "end", values=(name, "", "", ""))
            return
        tv.delete(*tv.get_children())
        for m in d["members"]:
            hp = "%d/%d" % (m["hp"], m["maxhp"]) if m.get("maxhp") else ""
            near = (m.get("pct") or 0) >= 90          # 差一点升级 -> 橙色
            tv.insert("", "end", values=(
                m["name"], "Lv%d" % m["level"],
                bot_itemmon.exp_text(m), hp),
                tags=("near",) if near else ())

    def _fill_items(self, rows, amap):
        """刷新 Treeview: 按数量降序; 数量 0 的不上榜; 傀儡虫置顶高亮。"""
        named = []
        for (_i, w, n, _u) in rows:
            if n <= 0:
                continue
            named.append((self.item_names.get(w, "物品 0x%03X" % w), n,
                          w == bot_itemmon.ITEM_ID))
        named.sort(key=lambda t: (not t[2], -t[1], t[0]))
        tv = self.tv_items
        tv.delete(*tv.get_children())
        for (name, n, focus) in named:
            tv.insert("", "end", values=(name, n),
                      tags=("focus",) if focus else ())

    def _poll(self):
        # 收日志
        try:
            while True:
                line = self.q.get_nowait()
                if line is None:
                    code = self.proc.poll() if self.proc else None
                    self.log_line("[GUI] 机器人已退出 (退出码 %s)"
                                  % ("0" if code == 0 else code))
                    self._set_buttons(False)
                    if os.path.exists(STOP_FLAG):
                        try:
                            os.remove(STOP_FLAG)
                        except OSError:
                            pass
                    continue
                # ★ v19.0 胜场: 机器人每赢一场打一行 `[胜场] 本次 X / 累计 Y`
                #   (本次 = 进程内 battles_won, 每局归零; 累计 = win_total.json)
                #   旧版解析的是 `战斗结束 #n` —— 那是**战斗序号**不是胜场, 已弃用
                m = re.search(r"\[胜场\]\s*本次\s*(\d+)\s*/\s*累计\s*(\d+)", line)
                if m:
                    self.wins = max(self.wins, int(m.group(1)))
                    self.wins_total = int(m.group(2))
                m = re.search(r"\[要素\]\s*怪点命中\s*(\d+)\s*/", line)
                if m:
                    visit = re.search(r"走到没打\s*(\d+)", line)
                    box = re.search(r"开箱\s*(\d+)", line)
                    self.elem = (int(m.group(1)),
                                 int(visit.group(1)) if visit else 0,
                                 int(box.group(1)) if box else 0)
                m = re.search(r"★ 换场景 \(第(\d+)次\)", line)
                if m:
                    self.chg = max(self.chg, int(m.group(1)))
                m = re.search(r"\[调度\] 进入场景 (\d+)", line)
                if m:
                    self.cur_scene = int(m.group(1))
                self.log_line(line)
        except queue.Empty:
            pass
        # 胜场显示: 本次来自日志; 累计以 win_total.json 为准 (每秒最多读一次盘,
        # 这样机器人子进程写进去的值 / 外部清空都能反映出来)
        now = time.time()
        if now - self._wt_t >= 1.0:
            self._wt_t = now
            try:
                wl = bot_winlog.WinLog()
                t = wl.total()
                if t != self.wins_total:
                    self.wins_total = t
                self.v_wins_total.set(
                    ("%d (待清空)" % self.wins_total) if wl.pending_reset()
                    else str(self.wins_total))
            except Exception:
                pass
            self.v_wins_local.set(str(self.wins))
            self._gain_refresh()          # 战斗收益左栏 (同样 1s 节流)
        # 状态栏
        if self._running():
            el = int(time.time() - self.t_start) if self.t_start else 0
            hit, miss, box = self.elem
            extra = ""
            if hit or miss or box:
                extra = " | 怪点命中 %d (走到没打 %d) | 开箱 %d" % (hit, miss, box)
            if self.chg:
                extra += " | 换场景 %d 次" % self.chg
            _sc = ("场景 %s" % self.cur_scene) if self.cur_scene else "启动中"
            if self.pin_active:
                _sc += (" [驻留 %d]" % self.pin_active) if self.pin_active > 0 \
                    else " [驻留:本场]"
            self.v_status.set("运行中 %02d:%02d:%02d | %s | 本次 %d / 累计 %d%s"
                              % (el // 3600, el // 60 % 60, el % 60,
                                 _sc, self.wins, self.wins_total, extra))
        self.root.after(250, self._poll)

    def _gain_refresh(self):
        """读 exp_log.json 刷新左栏战斗收益 (机器人唯一写者, 这里只读)。

        文件缺失/损坏/机器人没跑 -> 保持原显示 (上次运行的台账仍有参考价值,
        时间口径看「运行不足/时均」与场次号)。列表只在内容变化时重建, 防闪烁。
        """
        d = bot_explog.GainLog().load()
        if not d:
            return
        if d.get("version") != bot_explog.VERSION:
            # ★ 旧口径的台账是错的 (v1 六人求和 3 倍虚高; v2 基线被反复清零 ->
            #   时均只有真值的 1/10)。机器人下次赢一场就会按新口径重写, 在那之前
            #   宁可不显示, 免得用户又被旧数迷惑。
            self.v_gain_sum.set("旧口径台账 v%s (机器人下次胜场后自动刷新)"
                                % d.get("version"))
            self.v_gain_rate.set("")
            t = self.tree_gain
            t.delete(*t.get_children())
            self._gain_sig = None
            return
        de = d.get("d_exp") or 0
        dc = d.get("d_cash") or 0
        el = d.get("elapsed") or 0
        bad = d.get("bad") or 0
        msg = "经验 +%d (每人) / 金钱 +%d" % (de, dc)
        if bad:
            # ★ v3: 会话内被跳过(脏数据)的场次。正常情况下应该是 0;
            #   持续变大说明读档/换 exe/内存读花, 时均会被低估, 值得看一眼。
            msg += "  · 跳过 %d 场" % bad
        self.v_gain_sum.set(msg)
        if el >= 60:
            self.v_gain_rate.set(
                "平均 %s 经验/时 · %s 金钱/时"
                % (format(bot_explog.rate_per_hour(de, el), ","),
                   format(bot_explog.rate_per_hour(dc, el), ",")))
        else:
            self.v_gain_rate.set("运行不足 1 分钟, 暂无时均")
        rows = d.get("battles") or []
        sig = (d.get("wins"), de, dc, len(rows))
        if sig == getattr(self, "_gain_sig", None):
            return
        self._gain_sig = sig
        t = self.tree_gain
        t.delete(*t.get_children())
        for b in rows:
            t.insert("", "end", values=(
                "#%s" % b.get("n"), b.get("t"),
                "+%s" % b.get("exp"), "+%s" % b.get("cash")))

    def _set_buttons(self, running):
        self.b_start.config(state="disabled" if running else "normal")
        self.b_stop.config(state="normal" if running else "disabled")
        self.b_kill.config(state="normal" if running else "disabled")
        if running:
            self.v_status.set("运行中…")

    def log_line(self, text):
        self.log.config(state="normal")
        self.log.insert("end", text + "\n")
        if int(self.log.index("end-1c").split(".")[0]) > 3000:
            self.log.delete("1.0", "1000.0")   # 防止无限膨胀
        self.log.see("end")
        self.log.config(state="disabled")

    @staticmethod
    def _flush_game_keys():
        """强制停止后冲洗游戏窗口全部按键, 防卡键走丢。(子进程执行, 同上)"""
        code = ("import sys, time; sys.path.insert(0, r'%s')\n"
                "from pal_bot import find_game_window, Game\n"
                "for h in find_game_window():\n"
                "    g = Game(h)\n"
                "    for _ in range(3):\n"
                "        g.flush_keys(); time.sleep(0.1)\n"
                "print('flushed')" % BASE)
        try:
            subprocess.run([sys.executable, "-c", code], cwd=BASE,
                           env=ENV_UTF8, capture_output=True, timeout=30,
                           creationflags=subprocess.CREATE_NO_WINDOW)
            print("[GUI] 已冲洗游戏按键")
        except Exception as ex:
            print("[GUI] 冲洗按键失败: %s" % ex)


def selftest():
    """不开界面的自检: 数据文件 / 参数拼装 / 停止标志路径 / 攻击键配置。"""
    root = tk.Tk()
    root.withdraw()
    app = BotGUI(root)
    missing = [n for n in REQUIRED_DATA
               if not os.path.exists(os.path.join(BASE, n))]
    print("SELFTEST 数据文件缺失:", missing or "无")
    args = [sys.executable, "-u", "pal_bot_mem5.py", "0", "3600", "28",
            "90", "右上", "穿门", "1200", "", ""]
    print("SELFTEST 示例命令行:", " ".join(args))
    print("SELFTEST 停止标志:", STOP_FLAG)
    print("SELFTEST 配置文件:", botcfg.CONFIG_PATH)
    print("SELFTEST 攻击键: 界面=%r 候选=%s 环境变量=%s"
          % (app.v_atk.get(), ",".join(ATK_CHOICES),
             botcfg.env_name("attack_key")))
    print("SELFTEST OK")
    root.destroy()
    return 0 if not missing else 1


def main():
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except Exception:
        pass
    BotGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
