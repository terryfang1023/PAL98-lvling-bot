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
"""
import os
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

BASE = os.path.dirname(os.path.abspath(__file__))

# 机器人启动必需的数据文件 (由 tools/build_data.py 从游戏数据生成)
REQUIRED_DATA = ["map_walk.npy", "gyt_scene_report.txt"]
OPTIONAL_DATA = ["map_walk2.npy"]          # scene_check.py 体检用
STOP_FLAG = os.path.join(BASE, "mem_stop.flag")

BIAS_CHOICES = ["", "上", "下", "左", "右", "右上", "右下", "左上", "左下"]

# 攻击键下拉的候选项 (语义见 botcfg.ATTACK_KEY_CHOICES 上方的官方键表注释)
ATK_CHOICES = botcfg.ATTACK_KEY_CHOICES
ATK_HINT = ("r=重复上次行动 (挂机首选)  a=自动战斗  f=法术  "
            "space/enter=确认  d=防御  q=逃跑  e=物品  w=掷物  s=状态")

# 子进程 (bot/体检) 强制 UTF-8 输出, 否则 Windows 管道默认 GBK -> 界面乱码
ENV_UTF8 = dict(os.environ, PYTHONIOENCODING="utf-8")


class BotGUI:

    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("仙剑98 挂机机器人 (SDLPAL)")
        root.minsize(860, 560)

        self.proc = None                 # bot 子进程
        self.q = queue.Queue()           # 子进程日志队列
        self.wins = 0                    # 解析出的胜场 (本次启动)
        self.wins_total = 0              # 累计总胜场 (win_total.json)
        self._wt_t = 0.0                 # 累计胜场读盘节流
        self.elem = (0, 0, 0)            # (怪点命中, 走到没打, 开箱成功)
        self.chg = 0                     # 自动换场景次数
        self.cur_scene = None            # 解析出的当前场景
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

        r1 = ttk.Frame(f); r1.pack(fill="x", pady=4)
        self.v_maxb = entry(r1, "胜场上限 (0=不限)", 8, "0")
        self.v_mins = entry(r1, "运行时长/分钟 (0=不限)", 10, "0")
        self.v_r0 = entry(r1, "巡逻半径起", 6, "28")
        self.v_rmax = entry(r1, "巡逻半径上限", 6, "90")
        self.v_stall = entry(r1, "无胜场保险/分钟 (0=关)", 10, "20")

        r2 = ttk.Frame(f); r2.pack(fill="x", pady=(0, 2))
        ttk.Label(r2, text="方向偏置 (优先往该方向探索)").pack(
            side="left", padx=(10, 2))
        self.v_bias = tk.StringVar(value="")
        cb = ttk.Combobox(r2, textvariable=self.v_bias, width=8,
                          values=BIAS_CHOICES, state="readonly")
        cb.pack(side="left")
        self.v_open = tk.BooleanVar(value=False)
        ttk.Checkbutton(r2, text="穿门模式 (不避开传送阵/楼梯, 踩到就去下一层)",
                        variable=self.v_open).pack(side="left", padx=(16, 0))
        self.v_elem = tk.BooleanVar(value=True)
        ttk.Checkbutton(r2, text="要素巡回 (直奔怪点刷怪 + 顺路开宝箱)",
                        variable=self.v_elem).pack(side="left", padx=(16, 0))

        r2b = ttk.Frame(f); r2b.pack(fill="x", pady=(0, 6))
        self.v_auto = tk.BooleanVar(value=True)
        ttk.Checkbutton(r2b, text="自动换场景 (刷不动就穿门去下一层, 按场景评分决定)",
                        variable=self.v_auto).pack(side="left", padx=(10, 0))
        ttk.Label(r2b, text="← 先点「场景评分」生成评分表, 效果最好",
                  foreground="#666").pack(side="left", padx=(8, 0))

        r2b2 = ttk.Frame(f); r2b2.pack(fill="x", pady=(0, 6))
        self.v_rope = tk.BooleanVar(value=botcfg.as_bool(
            botcfg.get("rope"), True))
        ttk.Checkbutton(r2b2, text="绳子攀爬 (走上绳子按空格换层 —— 试炼窟等)",
                        variable=self.v_rope).pack(side="left", padx=(10, 0))
        self.v_rhunt = tk.BooleanVar(value=botcfg.as_bool(
            botcfg.get("rope_hunt"), True))
        ttk.Checkbutton(r2b2, text="遇到就爬 (绳子优先级最高)",
                        variable=self.v_rhunt).pack(side="left", padx=(16, 0))
        self.v_cmemo = tk.BooleanVar(value=botcfg.as_bool(
            botcfg.get("chest_memo"), True))
        ttk.Checkbutton(r2b2, text="记住开过的箱子 (写台账, 跨重启跳过不重复开)",
                        variable=self.v_cmemo).pack(side="left", padx=(16, 0))
        self.v_cmem = tk.BooleanVar(value=botcfg.as_bool(
            botcfg.get("chest_mem"), True))
        ttk.Checkbutton(r2b2, text="空箱读内存跳过 (已开空/不可触发的直接忽略)",
                        variable=self.v_cmem).pack(side="left", padx=(16, 0))
        self.v_feat = tk.BooleanVar(value=botcfg.as_bool(
            botcfg.get("feat_log"), True))
        ttk.Checkbutton(r2b2, text="要素台账 (按编号记每次战斗, 可查哪些怪点没打过)",
                        variable=self.v_feat).pack(side="left", padx=(16, 0))

        # ---- 走没走过的路 (2026-10-04) ----
        r2b3 = ttk.Frame(f); r2b3.pack(fill="x", pady=(0, 6))
        self.v_fresh = tk.BooleanVar(value=(
            botcfg.as_float(botcfg.get("revisit_lambda"), 0.15) > 0))
        ttk.Checkbutton(r2b3, text="优先走没走过的路 (寻路避重 + 怪点轮转)",
                        variable=self.v_fresh).pack(side="left", padx=(10, 0))
        self.v_explore = tk.BooleanVar(value=botcfg.as_bool(
            botcfg.get("explore"), True))
        ttk.Checkbutton(r2b3, text="每", variable=self.v_explore).pack(
            side="left", padx=(16, 1))
        self.v_explore_every = tk.StringVar(
            value=str(botcfg.get("explore_every")))
        ttk.Entry(r2b3, textvariable=self.v_explore_every, width=3).pack(
            side="left")
        ttk.Label(r2b3, text="场去一次没走过的区域",
                  foreground="#666").pack(side="left", padx=(1, 0))

        r2c = ttk.Frame(f); r2c.pack(fill="x", pady=(0, 6))
        ttk.Label(r2c, text="战斗攻击键").pack(side="left", padx=(10, 2))
        self.v_atk = tk.StringVar(value=self._atk_from_cfg())
        ttk.Combobox(r2c, textvariable=self.v_atk, width=8,
                     values=ATK_CHOICES).pack(side="left")
        ttk.Label(r2c, text="← " + ATK_HINT,
                  foreground="#666").pack(side="left", padx=(8, 0))

        # ---- 战后自动补血 (pal_bot_heal.py 的菜单宏) ----
        r2d = ttk.Frame(f); r2d.pack(fill="x", pady=(0, 6))
        self.v_heal = tk.BooleanVar(value=bool(
            botcfg.as_bool(botcfg.get("heal_after_battle"), True)))
        ttk.Checkbutton(r2d, text="战后自动补血", variable=self.v_heal).pack(
            side="left", padx=(10, 0))
        ttk.Label(r2d, text="每").pack(side="left", padx=(14, 1))
        self.v_heal_every = tk.StringVar(value=str(botcfg.get("heal_every")))
        ttk.Entry(r2d, textvariable=self.v_heal_every, width=3).pack(
            side="left")
        ttk.Label(r2d, text="场一次   按键序列").pack(side="left", padx=(1, 2))
        self.v_heal_seq = tk.StringVar(value=str(
            botcfg.get("heal_seq") or botcfg.SEQ_DEFAULT))
        ttk.Entry(r2d, textvariable=self.v_heal_seq, width=44).pack(
            side="left")
        ttk.Label(r2d, text="间隔").pack(side="left", padx=(8, 1))
        self.v_heal_gap = tk.StringVar(value=str(botcfg.get("heal_gap")))
        ttk.Entry(r2d, textvariable=self.v_heal_gap, width=4).pack(
            side="left")
        ttk.Label(r2d, text="s").pack(side="left")
        r2e = ttk.Frame(f); r2e.pack(fill="x", pady=(0, 6))
        ttk.Label(r2e, text="← 序列默认是 ESC开菜单(光标在仙术)→回车选巫后→"
                            "右→回车→回车→右→回车→ESC；"
                            "序列不对点「补血测试」一步步校准",
                  foreground="#666").pack(side="left", padx=(10, 0))

        r3 = ttk.Frame(f); r3.pack(fill="x", pady=(0, 6))
        self.v_exe = entry(r3, "Python 解释器 (bot 子进程使用)", 46,
                           sys.executable)
        ttk.Label(r3, text="").pack(side="left")

    def _build_buttons(self):
        f = ttk.Frame(self.root)
        f.pack(fill="x", padx=10, pady=4)
        self.b_start = ttk.Button(f, text="▶ 开始", command=self.start)
        self.b_start.pack(side="left", padx=(0, 6))
        self.b_stop = ttk.Button(f, text="■ 停止 (优雅)", command=self.stop,
                                 state="disabled")
        self.b_stop.pack(side="left", padx=6)
        self.b_kill = ttk.Button(f, text="⛔ 强制停止", command=self.force_stop,
                                 state="disabled")
        self.b_kill.pack(side="left", padx=6)
        ttk.Button(f, text="场景体检", command=self.scene_check).pack(
            side="left", padx=6)
        ttk.Button(f, text="场景评分", command=self.scene_score).pack(
            side="left", padx=6)
        ttk.Button(f, text="环境体检", command=self.env_check).pack(
            side="left", padx=6)
        ttk.Button(f, text="效率报告", command=self.run_report).pack(
            side="left", padx=6)
        ttk.Button(f, text="门审计", command=self.door_audit).pack(
            side="left", padx=6)
        ttk.Button(f, text="战斗识别", command=self.battle_probe).pack(
            side="left", padx=6)
        ttk.Button(f, text="空箱体检", command=self.chest_probe).pack(
            side="left", padx=6)
        ttk.Button(f, text="补血测试", command=self.heal_once).pack(
            side="left", padx=6)
        ttk.Button(f, text="刷新怪点/宝箱", command=self.refresh_scene_info).pack(
            side="left", padx=6)
        ttk.Button(f, text="开箱记录", command=self.chest_log_view).pack(
            side="left", padx=6)
        ttk.Button(f, text="检测游戏窗口", command=self.detect_window).pack(
            side="left", padx=6)
        ttk.Button(f, text="清空日志", command=self.clear_log).pack(
            side="right", padx=6)

    def _build_log(self):
        f = ttk.LabelFrame(self.root, text="运行日志")
        f.pack(fill="both", expand=True, padx=10, pady=4)
        self.log = scrolledtext.ScrolledText(f, height=18, state="disabled",
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
        # 补血选项: 序列语法必须先过 —— 序列错了机器人会在菜单里乱按
        heal_on = bool(self.v_heal.get())
        try:
            heal_keys = botcfg.parse_seq(self.v_heal_seq.get())
        except ValueError as ex:
            self._err("补血序列错误", "「按键序列」%s\n\n"
                      "写法示例: esc,回车,右,回车,esc 或 ESC → 回车 → 右 → ESC"
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
            self.log_line("[GUI] 补血设置写盘失败 (不影响本次运行): %s" % ex)
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
        """后台跑 tools/heal_once.py: 对当前画面**真的执行一次**补血宏。

        用途: 校准「按键序列」。机器人必须先停止 (两个进程抢键盘没法判断
        是谁按的); 脚本自己也会检测并在机器人还在跑时拒绝执行。
        序列沿用界面输入框里的当前值 (--seq 传过去, 不改配置文件)。
        """
        script = os.path.join(BASE, "tools", "heal_once.py")
        if not os.path.exists(script):
            self._err("缺少工具", "未找到 tools/heal_once.py")
            return
        exe = self.v_exe.get().strip() or sys.executable
        args = [exe, script]
        if self.v_heal_seq.get().strip():
            args += ["--seq", self.v_heal_seq.get().strip()]
        self.log_line("[补血测试] 将对当前画面执行一次补血宏 (会按键盘!) — "
                      "请确认机器人已停止、游戏画面停在地图上…")
        self._run_bg(args, "补血测试")

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
        # 状态栏
        if self._running():
            el = int(time.time() - self.t_start) if self.t_start else 0
            hit, miss, box = self.elem
            extra = ""
            if hit or miss or box:
                extra = " | 怪点命中 %d (走到没打 %d) | 开箱 %d" % (hit, miss, box)
            if self.chg:
                extra += " | 换场景 %d 次" % self.chg
            self.v_status.set("运行中 %02d:%02d:%02d | %s | 本次 %d / 累计 %d%s"
                              % (el // 3600, el // 60 % 60, el % 60,
                                 ("场景 %s" % self.cur_scene)
                                 if self.cur_scene else "启动中",
                                 self.wins, self.wins_total, extra))
        self.root.after(250, self._poll)

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
