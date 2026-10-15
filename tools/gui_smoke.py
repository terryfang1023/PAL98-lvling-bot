# -*- coding: utf-8 -*-
"""GUI 离线自检 —— 隐藏窗口走 BotGUI 的真实「开始」路径, 但把子进程换成假的,
所以**不操作游戏、秒级完成**。检查九件事:

  ① 参数拼装: 捕获 Popen 收到的 argv, 逐项对照 pal_bot_mem5.py 的 argv 语义;
     并验证勾选/取消「要素巡回」分别对应空串与 "巡逻" (第 8 参数)。
  ①b 战斗攻击键: 界面从 bot_config.json 读值 -> 经 PAL_ATTACK_KEY 环境变量
     传给子进程 -> 落盘; 且没占用位置参数 (仍是 9 个)。
  ② 状态栏解析: 注入 bot 真实格式的统计行, 确认胜场与要素统计能解析出来。
  ③ 停止路径: 点「停止」是否真的写出 mem_stop.flag。
  ⑥ 物品栏监视面板: 假数据填充 (数量0不上榜/降序/傀儡虫置顶高亮)、
     游戏未运行时 _item_tick 不崩。
  ⑦ 人物等级/经验面板: 假 RoleReader 数据填充与"读不到"兜底。
  ⑧ 开箱台账: 开关 / 记录窗口 / 清空空箱。
  ⑨ v19.0 胜场拆分: 「本次胜场 / 累计总胜场」两个显示位; 停机时「清空累计胜场」
     直接清 0、运行中只写 win_reset.flag (等机器人自己兑现) 且界面标「(待清空)」;
     「要素台账」勾选框 -> PAL_FEAT_LOG; 「要素覆盖率」按钮 -> tools/feat_report.py。
  ⑩ 攻击键非法时被拦下 (不起子进程), 合法值被规范化。
  ⑪ 版本号显示 (2026-10-12): 窗口标题 + 底部状态区都要带 botcfg.APP_VERSION;
  ⑫ 战斗收益左栏 (v19.3): 左收益右日志分栏, exp_log.json 只读 + 缺文件容错;
     ★ v3 口径: bad>0 显示「跳过 N 场」, 旧版本台账 (v1 虚高/v2 时均偏小) 不展示。
  ⑬ 窗口比例 (2026-10-13): 控件请求尺寸接近 4:3 (宽/高 1.20~1.45) —— 用户嫌
     原布局"太宽", 参数/按钮重排后整窗收敛到 960x720 = 1.333。
  ⑭ 程序图标 (2026-10-13): assets/pal98bot.ico 多尺寸 32bpp (16/32 必备),
     GUI 接 iconbitmap + WM_SETICON 且文件缺失时静默跳过 (只拷 .py 不拷
     assets 也能开)。★任务栏 = AUMID(建窗前) + WM_SETICON 三连 —— 只
     iconbitmap 时 Win11 任务栏显示"白纸" (真机取证)。
     并钉死「标题不许含大小写敏感的 'Pal'」—— 那是找游戏窗口的判据, 撞上就会
     把自家 GUI 当成游戏去读内存。

零副作用: 停止标志与 bot_config.json **都重定向到临时目录** ——
  前者曾经把正在跑的机器人停掉, 后者会把用户真实的攻击键设置改掉;
  v19.0 起开箱台账 / 累计总胜场 / 胜场清空标志 / 要素台账也一并重定向
  (「清空累计胜场」按钮否则会把用户积累几千场的总数清掉)。

用法:  python tools/gui_smoke.py        (退出码 0 = 全过)
       需要带 tkinter 的解释器 (如 C:/Python311/python.exe; anaconda 也行)。
"""
import os
import re
import sys
import time
import types
import io

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
os.chdir(BASE)

import tkinter as tk                        # noqa: E402
import bot_gui as G                         # noqa: E402

# ★★★ 2026-09-30 血的教训: 本自检原来直接写 <BASE>/mem_stop.flag, 而
#   pal_bot_mem*.py 每步都在轮询这个文件 -> **跑一次自检就会把正在跑的机器人
#   优雅停掉** (实测: 机器人 12:22:47 打印"检测到 mem_stop.flag, 收键退出")。
#   自检必须零副作用: 把停止标志重定向到临时目录。
import tempfile                             # noqa: E402
_TMPDIR = tempfile.mkdtemp(prefix="gui_smoke_")
G.STOP_FLAG = os.path.join(_TMPDIR, "mem_stop.flag")
assert os.path.dirname(G.STOP_FLAG) != BASE, "自检绝不能碰运行目录的停止标志"
# 同理: 「开始」会把攻击键写进 bot_config.json —— 同样重定向到临时目录,
# 否则跑一次自检就把用户真实的攻击键设置改掉了。
G.botcfg.CONFIG_PATH = os.path.join(_TMPDIR, "bot_config.json")
assert os.path.dirname(G.botcfg.CONFIG_PATH) != BASE, "自检绝不能碰运行目录的配置"
# 同理: 开箱台账 chest_log.json 也在运行目录 —— 重定向, 免得自检把用户
# 真实的"已开箱子"记录清掉。
G.bot_chestlog.CHESTLOG_PATH = os.path.join(_TMPDIR, "chest_log.json")
assert os.path.dirname(G.bot_chestlog.CHESTLOG_PATH) != BASE, \
    "自检绝不能碰运行目录的开箱台账"
# 同理 (v19.0): 累计总胜场 win_total.json / 清空标志 win_reset.flag 也在运行目录 ——
# 「清空累计胜场」按钮会直接写它们, 不重定向就会把用户积累了几千场的总胜场清掉。
G.bot_winlog.WINS_PATH = os.path.join(_TMPDIR, "win_total.json")
G.bot_winlog.RESET_FLAG = os.path.join(_TMPDIR, "win_reset.flag")
assert os.path.dirname(G.bot_winlog.WINS_PATH) != BASE, "自检绝不能碰运行目录的累计胜场"
assert os.path.dirname(G.bot_winlog.RESET_FLAG) != BASE, "自检绝不能碰运行目录的胜场标志"
# 同理: 要素台账 feat_log.json (要素覆盖率报告会读它, 不该被自检写脏)。
#   bot_gui 目前不直接 import bot_featlog, 这里按模块路径兜住 —— 将来 GUI 若
#   直接读台账, 这条重定向就自动生效 (先断言再改, 免得漏掉)。
import bot_featlog                          # noqa: E402
bot_featlog.FEATLOG_PATH = os.path.join(_TMPDIR, "feat_log.json")
assert os.path.dirname(bot_featlog.FEATLOG_PATH) != BASE, "自检绝不能碰运行目录的要素台账"
if hasattr(G, "bot_featlog"):
    G.bot_featlog.FEATLOG_PATH = bot_featlog.FEATLOG_PATH
# 同理 (v19.3): 战斗收益 exp_log.json —— GUI 左栏会读它 (只读), 但为防将来
# 写路径混进来, 一并重定向 + 断言。
G.bot_explog.EXPLOG_PATH = os.path.join(_TMPDIR, "exp_log.json")
assert os.path.dirname(G.bot_explog.EXPLOG_PATH) != BASE, \
    "自检绝不能碰运行目录的战斗收益台账"

# 预置一个"用户已经改过攻击键"的配置, 用来验证界面真的会读它 (而不是只显示默认值)
with open(G.botcfg.CONFIG_PATH, "w", encoding="utf-8") as _f:
    _f.write('{"attack_key": "space"}\n')

OK, BAD = [], []


def _cfg_state():
    """运行目录真实 bot_config.json 的 (是否存在, 大小, mtime) —— 用来证明零副作用。"""
    return _file_state("bot_config.json")


def _chest_state():
    """运行目录真实 chest_log.json 的 (是否存在, 大小, mtime)。"""
    return _file_state("chest_log.json")


def _file_state(name):
    p = os.path.join(BASE, name)
    try:
        st = os.stat(p)
        return (True, st.st_size, int(st.st_mtime))
    except OSError:
        return (False, 0, 0)


_CFG_BEFORE = _cfg_state()
_CHEST_BEFORE = _chest_state()
_WIN_BEFORE = _file_state("win_total.json")
_FEAT_BEFORE = _file_state("feat_log.json")


def _not_shrunk(name, before):
    """台账文件「没被写小」(零副作用断言的活机器人健壮版)。

    机器人每赢一场就写 win_total.json / feat_log.json (只增不删),
    自检跑几秒的窗口里必然撞车 —— 2026-10-12 实测: 机器人在跑时
    size+mtime 快照相等断言误报 2 项。所以退化成「运行目录那份没有被
    清小」: 自检若误把真台账清掉, 文件一定变小, 仍然抓得住。
    真正防误写靠的是 WINS_PATH / FEATLOG_PATH / RESET_FLAG 全部
    重定向进临时目录的断言 (见下文)。"""
    a = _file_state(name)
    if not a[0]:
        return not before[0]          # 都不存在 = 没动
    if not before[0]:
        return True                   # 新建 (机器人首胜建的; 误写由重定向断言兜底)
    return a[1] >= before[1]          # 只许长大, 不许变小


def check(name, cond, extra=""):
    (OK if cond else BAD).append(name)
    print("  %s %s%s" % ("OK  " if cond else "FAIL", name,
                         ("  <%s>" % extra) if extra else ""))


class FakeProc:
    """假装在跑的子进程: stdout 立刻 EOF, poll() 恒为 None。"""

    def __init__(self, args):
        self.args = args
        self.stdout = io.StringIO()
        self.returncode = None

    def poll(self):
        return None

    def kill(self):
        self.returncode = -9

    def terminate(self):
        self.returncode = -15

    def wait(self, timeout=None):
        return 0


def main():
    root = tk.Tk()
    root.withdraw()
    app = G.BotGUI(root)

    cap = {}
    real_subprocess = G.subprocess
    # ★ 只替换 bot_gui 模块看到的那个名字, 不动全局 subprocess 模块
    G.subprocess = types.SimpleNamespace(
        Popen=lambda args, **kw: (cap.__setitem__("args", args),
                                  cap.__setitem__("kw", kw),
                                  FakeProc(args))[-1],
        run=lambda *a, **k: (cap.__setitem__("run_args", a[0] if a else []),
                             types.SimpleNamespace(stdout="", stderr="",
                                                   returncode=0))[-1],
        PIPE=real_subprocess.PIPE,
        STDOUT=real_subprocess.STDOUT,
        CREATE_NO_WINDOW=getattr(real_subprocess, "CREATE_NO_WINDOW", 0))

    try:
        print("① 参数拼装 (要素巡回 = 勾选)")
        app.v_maxb.set("0")
        app.v_mins.set("1")
        app.v_stall.set("20")
        app.v_elem.set(True)
        app.start()
        a = cap.get("args") or []
        check("Popen 被调用", bool(a), " ".join(a[3:]))
        # GUI 传 [exe, -u, script, 9 个业务参数]; python 侧 sys.argv[1:] == a[3:]
        sa = list(a[3:]) + [""] * (9 - len(a[3:]))
        check("业务参数 9 个", len(a[3:]) == 9, "%d 个" % len(a[3:]))
        check("脚本名是 pal_bot_mem5.py", len(a) > 2 and a[2] == "pal_bot_mem5.py",
              a[2] if len(a) > 2 else "")
        check("承接口 + 静默管道",
              cap.get("kw", {}).get("stdout") is real_subprocess.PIPE,
              str(cap.get("kw", {}).get("stdout")))
        check("第 2 参数(秒) = 60", sa[1] == "60", "实际 %r" % sa[1])
        check("半径起/上限可转整数", sa[2].isdigit() and sa[3].isdigit(),
              "%s / %s" % (sa[2], sa[3]))
        check("方向偏置合法", sa[4] in G.BIAS_CHOICES, "%r" % sa[4])
        check("穿门参数合法", sa[5] in ("", "穿门"), "%r" % sa[5])
        check("要素开 -> 第 8 参数为空", sa[7] == "", "实际 %r" % sa[7])
        check("要素开 -> elem_mode 未被关 (等价于 bot 侧不写 巡逻)", sa[7] != "巡逻")
        check("自动换场景默认开 -> 第 9 参数为空", sa[8] == "", "实际 %r" % sa[8])

        print("①b 战斗攻击键 (界面 <- bot_config.json -> 子进程)")
        check("界面读出了配置里的攻击键", app.v_atk.get() == "space",
              "实际 %r" % app.v_atk.get())
        check("候选含 r (SDLPAL kKeyRepeat)",
              "r" in G.ATK_CHOICES, ",".join(G.ATK_CHOICES))
        check("环境变量 PAL_ATTACK_KEY 已传给子进程",
              cap.get("kw", {}).get("env", {}).get("PAL_ATTACK_KEY") == "space",
              str(cap.get("kw", {}).get("env", {}).get("PAL_ATTACK_KEY")))
        check("参数个数仍是 9 (攻击键没占位置参数)",
              len(a[3:]) == 9, "%d 个" % len(a[3:]))
        check("攻击键落盘到 (临时) 配置文件",
              G.botcfg.load(G.botcfg.CONFIG_PATH).get("attack_key") == "space",
              str(G.botcfg.load(G.botcfg.CONFIG_PATH)))
        check("配置文件写在临时目录 (零副作用)",
              os.path.dirname(G.botcfg.CONFIG_PATH) != BASE,
              G.botcfg.CONFIG_PATH)

        print("①c 战后自动存盘 (界面 -> PAL_HEAL_* 环境变量)")
        env1 = cap.get("kw", {}).get("env", {})
        check("界面默认勾上战后自动存盘", app.v_heal.get() is True,
              str(app.v_heal.get()))
        check("PAL_HEAL_AFTER_BATTLE 已传 (开)",
              env1.get("PAL_HEAL_AFTER_BATTLE") == "1",
              str(env1.get("PAL_HEAL_AFTER_BATTLE")))
        check("PAL_HEAL_SEQ 已传且等于默认 8 键序列",
              G.botcfg.parse_seq(env1.get("PAL_HEAL_SEQ", "")) ==
              G.botcfg.parse_seq(G.botcfg.SEQ_DEFAULT),
              str(env1.get("PAL_HEAL_SEQ")))
        check("PAL_HEAL_EVERY 已传", env1.get("PAL_HEAL_EVERY") == "1",
              str(env1.get("PAL_HEAL_EVERY")))
        check("PAL_HEAL_GAP 已传 (>0)",
              float(env1.get("PAL_HEAL_GAP", "0") or 0) > 0,
              str(env1.get("PAL_HEAL_GAP")))
        _cfg1 = G.botcfg.load(G.botcfg.CONFIG_PATH)
        check("存盘四项落盘到 (临时) 配置文件",
              _cfg1.get("heal_after_battle") is True
              and _cfg1.get("heal_seq") == G.botcfg.SEQ_DEFAULT,
              str(_cfg1))
        # 关掉勾选 -> 环境变量传 0
        app.proc = None
        cap.clear()
        app.v_heal.set(False)
        app.start()
        check("取消勾选 -> PAL_HEAL_AFTER_BATTLE=0",
              (cap.get("kw", {}).get("env", {})
               ).get("PAL_HEAL_AFTER_BATTLE") == "0",
              str((cap.get("kw", {}).get("env", {})
                   ).get("PAL_HEAL_AFTER_BATTLE")))
        app.v_heal.set(True)

        check("停止写出 mem_stop.flag",
              (app.stop() or True) and os.path.exists(G.STOP_FLAG))
        if os.path.exists(G.STOP_FLAG):
            os.remove(G.STOP_FLAG)

        print("② 参数拼装 (要素巡回 = 取消)")
        app.proc = None                      # 假装上一轮已退出
        app.v_elem.set(False)
        cap.clear()
        app.start()
        a2 = cap.get("args") or []
        sa2 = list(a2[3:]) + [""] * (9 - len(a2[3:]))
        check("要素关 -> 第 8 参数为 巡逻", sa2[7] == "巡逻", "实际 %r" % sa2[7])

        print("②b 参数拼装 (自动换场景 = 取消)")
        app.proc = None
        app.v_elem.set(True)
        app.v_auto.set(False)
        cap.clear()
        app.start()
        a2b = cap.get("args") or []
        sa2b = list(a2b[3:]) + [""] * (9 - len(a2b[3:]))
        check("自动换场景关 -> 第 9 参数为 不换", sa2b[8] == "不换",
              "实际 %r" % sa2b[8])
        app.proc = FakeProc([])      # 保持「运行中」语义, 好让状态栏可测
        app.t_start = time.time()
        app.v_auto.set(True)

        print("③ 状态栏解析")
        if os.path.exists(G.STOP_FLAG):
            os.remove(G.STOP_FLAG)
        # ★ v19.0: 胜场改从 `[胜场] 本次 X / 累计 Y` 解析 (旧版解析的
        #   `[战斗结束] #n` 是**战斗序号**不是胜场, 已弃用)
        G.bot_winlog.WinLog().save_total(1057)
        app._wt_t = 0.0                            # 清掉读盘节流, 让它真的读一次
        app.q.put("[胜场] 本次 7 / 累计 1057 | 记 1 (221-3813)")
        app.q.put("[要素] 怪点命中 4 / 走到没打 0 / 开箱 1(试2) / 回退巡逻 0")
        app.q.put("[调度] ★ 换场景 (第3次): 300s 无战斗 -> 走最近的门 K(-40, 118)")
        app.q.put("[调度] 进入场景 229 (开门穿门) — 主刷: 可达怪点 14 个(共14)"
                  ", 巡回 104 格, 上限 2.78 场/分, 耐心 x1.60")
        for _ in range(8):
            root.update()
            time.sleep(0.05)
        check("本次胜场解析 = 7", app.wins == 7, "实际 %d" % app.wins)
        check("★累计总胜场从 win_total.json 读 = 1057",
              app.wins_total == 1057, "实际 %s" % app.wins_total)
        check("底部显示位填上了这两个数",
              app.v_wins_local.get() == "7"
              and app.v_wins_total.get() == "1057",
              "%s / %s" % (app.v_wins_local.get(), app.v_wins_total.get()))
        check("要素统计解析 = (4, 0, 1)", app.elem == (4, 0, 1), str(app.elem))
        check("状态栏文本含要素统计",
              "怪点命中" in app.v_status.get(), app.v_status.get())
        check("换场景次数解析 = 3", app.chg == 3, "实际 %s" % app.chg)
        check("当前场景解析 = 229", app.cur_scene == 229, "实际 %s" % app.cur_scene)
        check("状态栏文本含「本次 x / 累计 y」",
              "本次 7 / 累计 1057" in app.v_status.get(), app.v_status.get())
        check("状态栏文本含换场景/场景号",
              "换场景 3 次" in app.v_status.get()
              and "场景 229" in app.v_status.get(), app.v_status.get())

        print("④ 无关行不误匹配")
        app.q.put("[要素] 清单载入: 136 个场景 / 怪点 1968 / 宝箱 1178")
        for _ in range(4):
            root.update()
            time.sleep(0.05)
        check("清单行不污染统计", app.elem == (4, 0, 1), str(app.elem))

        print("⑤ 环境体检按钮")
        app.proc = None
        cap.clear()
        app.env_check()
        for _ in range(40):            # _run_bg 在工作线程里起进程, 等它落地
            if cap.get("run_args"):
                break
            root.update()
            time.sleep(0.05)
        a5 = cap.get("run_args") or []
        check("体检命令指向 tools/env_check.py",
              len(a5) >= 2 and a5[-1].replace("\\", "/").endswith(
                  "tools/env_check.py"), " ".join(str(x) for x in a5))
        check("体检与本机解释器同源",
              bool(a5) and a5[0] == app.v_exe.get().strip(), str(a5[:1]))

        print("⑥ 效率报告按钮")
        app.proc = None
        cap.clear()
        app.run_report()
        for _ in range(40):
            if cap.get("run_args"):
                break
            root.update()
            time.sleep(0.05)
        a6 = cap.get("run_args") or []
        check("效率报告命令指向 tools/run_report.py",
              len(a6) >= 2 and a6[-1].replace("\\", "/").endswith(
                  "tools/run_report.py"), " ".join(str(x) for x in a6))
        check("效率报告不写停止标志 (只读)",
              os.path.dirname(G.STOP_FLAG) != BASE, G.STOP_FLAG)

        print("⑦ 门审计按钮")
        app.proc = None
        cap.clear()
        app.door_audit()
        for _ in range(40):
            if cap.get("run_args"):
                break
            root.update()
            time.sleep(0.05)
        a7 = cap.get("run_args") or []
        check("门审计命令指向 tools/door_audit.py",
              len(a7) >= 2 and a7[-1].replace("\\", "/").endswith(
                  "tools/door_audit.py"), " ".join(str(x) for x in a7))
        check("门审计不写停止标志 (只读)",
              os.path.dirname(G.STOP_FLAG) != BASE, G.STOP_FLAG)

        print("⑦b 空箱体检按钮 (v18.9)")
        app.proc = None
        cap.clear()
        app.chest_probe()
        for _ in range(40):
            if cap.get("run_args"):
                break
            root.update()
            time.sleep(0.05)
        a7b = cap.get("run_args") or []
        check("空箱体检命令指向 tools/chest_probe.py",
              len(a7b) >= 2 and a7b[-1].replace("\\", "/").endswith(
                  "tools/chest_probe.py"), " ".join(str(x) for x in a7b))
        check("空箱体检不写停止标志 (只读)",
              os.path.dirname(G.STOP_FLAG) != BASE, G.STOP_FLAG)
        print("⑧ 攻击键非法 -> 拦下且不起进程")
        app.proc = None
        cap.clear()
        errs = []
        real_err = app._err
        app._err = lambda title, msg, **k: errs.append((title, msg))
        try:
            app.v_atk.set("ctrl")            # 非法: 多字符且不是具名键
            app.start()
            check("非法攻击键 -> 弹错误框", bool(errs), str(errs[:1]))
            check("非法攻击键 -> 不起子进程", not cap.get("args"),
                  " ".join(str(x) for x in (cap.get("args") or [])))
        finally:
            app._err = real_err
            app.v_atk.set("r")

        print("⑧b 存盘序列非法 -> 拦下且不起进程")
        app.proc = None
        cap.clear()
        errs = []
        app._err = lambda title, msg, **k: errs.append((title, msg))
        try:
            app.v_heal_seq.set("esc,ctrl,回车")   # ctrl 不是合法键名
            app.start()
            check("非法序列 -> 弹错误框", bool(errs), str(errs[:1]))
            check("非法序列 -> 不起子进程", not cap.get("args"),
                  " ".join(str(x) for x in (cap.get("args") or [])))
        finally:
            app._err = real_err
            app.v_heal_seq.set(G.botcfg.SEQ_DEFAULT)
        # 规范化: 中文键名/箭头 -> 规范序列回显
        app.proc = None
        cap.clear()
        app.v_heal_seq.set("ESC → 回车 → 右 → ESC")
        app.start()
        check("中文序列被规范化回显",
              app.v_heal_seq.get() == "esc,enter,right,esc",
              app.v_heal_seq.get())
        check("中文序列传给子进程",
              (cap.get("kw", {}).get("env", {})
               ).get("PAL_HEAL_SEQ") == "esc,enter,right,esc",
              str((cap.get("kw", {}).get("env", {})).get("PAL_HEAL_SEQ")))

        # 规范化: " R " -> "r"
        app.proc = None
        cap.clear()
        app.v_atk.set(" R ")
        app.start()
        check("攻击键大小写/空格被规范化",
              cap.get("kw", {}).get("env", {}).get("PAL_ATTACK_KEY") == "r",
              str(cap.get("kw", {}).get("env", {}).get("PAL_ATTACK_KEY")))
        check("绳子开关默认开并传给子进程 (PAL_ROPE=1)",
              hasattr(app, "v_rope")
              and cap.get("kw", {}).get("env", {}).get("PAL_ROPE") == "1",
              str((cap.get("kw", {}).get("env", {})).get("PAL_ROPE")))
        check("★「遇到就爬」开关存在且默认开, 并传给子进程 (PAL_ROPE_HUNT=1)",
              hasattr(app, "v_rhunt") and bool(app.v_rhunt.get())
              and cap.get("kw", {}).get("env", {}).get("PAL_ROPE_HUNT") == "1",
              str(cap.get("kw", {}).get("env", {}).get("PAL_ROPE_HUNT")))
        # 关掉绳子总开关时「遇到就爬」自动关 (子开关跟主开关联动)
        app.proc = None                      # ★ 必须清掉假进程, 否则弹"已在运行"模态框卡死
        cap.clear()
        app.v_rope.set(False)
        app.start()
        check("绳子总开关关 -> 遇到就爬也关 (PAL_ROPE_HUNT=0)",
              cap.get("kw", {}).get("env", {}).get("PAL_ROPE_HUNT") == "0",
              str(cap.get("kw", {}).get("env", {}).get("PAL_ROPE_HUNT")))
        app.proc = None
        cap.clear()
        app.v_rope.set(True)
        app.start()
        check("绳子开关还原 -> PAL_ROPE_HUNT 回到 1",
              cap.get("kw", {}).get("env", {}).get("PAL_ROPE_HUNT") == "1",
              str(cap.get("kw", {}).get("env", {}).get("PAL_ROPE_HUNT")))
        # ★ 走没走过的路 (2026-10-04): 寻路避重 + 定期探索配额
        _env = cap.get("kw", {}).get("env", {})
        check("★ 寻路避重开关存在且默认开, 传给子进程 (PAL_REVISIT_LAMBDA>0)",
              hasattr(app, "v_fresh") and bool(app.v_fresh.get())
              and float(_env.get("PAL_REVISIT_LAMBDA") or 0) > 0,
              str(_env.get("PAL_REVISIT_LAMBDA")))
        check("★ 定期探索开关存在且默认开, 传给子进程 (PAL_EXPLORE=1)",
              hasattr(app, "v_explore") and bool(app.v_explore.get())
              and _env.get("PAL_EXPLORE") == "1",
              str(_env.get("PAL_EXPLORE")))
        check("探索周期以整数传给子进程 (PAL_EXPLORE_EVERY)",
              _env.get("PAL_EXPLORE_EVERY") == str(
                  max(0, int(app.v_explore_every.get() or "0"))),
              str(_env.get("PAL_EXPLORE_EVERY")))
        app.proc = None
        cap.clear()
        app.v_fresh.set(False)
        app.start()
        check("关掉避重 -> PAL_REVISIT_LAMBDA=0 (逃生开关: 回到纯最短路径)",
              cap.get("kw", {}).get("env", {})
              .get("PAL_REVISIT_LAMBDA") == "0",
              str(cap.get("kw", {}).get("env", {}).get("PAL_REVISIT_LAMBDA")))
        app.proc = None
        cap.clear()
        app.v_fresh.set(True)
        app.start()
        check("避重还原 -> PAL_REVISIT_LAMBDA 回到 0.15",
              cap.get("kw", {}).get("env", {})
              .get("PAL_REVISIT_LAMBDA") == "0.15",
              str(cap.get("kw", {}).get("env", {}).get("PAL_REVISIT_LAMBDA")))
        app.proc = None
        cap.clear()
        app.v_explore_every.set("0")
        app.start()
        check("探索周期填 0 -> 探索自动关 (PAL_EXPLORE=0)",
              cap.get("kw", {}).get("env", {}).get("PAL_EXPLORE") == "0",
              str(cap.get("kw", {}).get("env", {}).get("PAL_EXPLORE")))
        app.proc = None
        cap.clear()
        app.v_explore_every.set("25")
        app.start()
        check("探索周期还原 -> 探索又开 (PAL_EXPLORE=1)",
              cap.get("kw", {}).get("env", {}).get("PAL_EXPLORE") == "1",
              str(cap.get("kw", {}).get("env", {}).get("PAL_EXPLORE")))
        # ★ 空箱从内存判断 (2026-10-04): 已开空/不可触发的箱子直接忽略
        check("★ 空箱内存判断开关存在且默认开, 传给子进程 (PAL_CHEST_MEM=1)",
              hasattr(app, "v_cmem") and bool(app.v_cmem.get())
              and _env.get("PAL_CHEST_MEM") == "1",
              str(_env.get("PAL_CHEST_MEM")))
        app.proc = None
        cap.clear()
        app.v_cmem.set(False)
        app.start()
        check("关掉空箱内存判断 -> PAL_CHEST_MEM=0 (退回走过去试一次)",
              cap.get("kw", {}).get("env", {}).get("PAL_CHEST_MEM") == "0",
              str(cap.get("kw", {}).get("env", {}).get("PAL_CHEST_MEM")))
        app.proc = None
        cap.clear()
        app.v_cmem.set(True)
        app.start()
        check("空箱判断还原 -> PAL_CHEST_MEM 回到 1",
              cap.get("kw", {}).get("env", {}).get("PAL_CHEST_MEM") == "1",
              str(cap.get("kw", {}).get("env", {}).get("PAL_CHEST_MEM")))
        check("运行目录的 bot_config.json 没被动过 (零副作用)",
              _cfg_state() == _CFG_BEFORE, "前 %s -> 后 %s"
              % (_CFG_BEFORE, _cfg_state()))

        # ---- ⑥ 物品栏监视面板 (只读; 注入假数据验证填充与"游戏未运行"路径) ----
        import bot_itemmon as IM              # noqa: E402
        check("物品面板/列表控件存在",
              hasattr(app, "tv_items") and hasattr(app, "lbl_focus"))
        fake_rows = [(0, IM.ITEM_ID, 21, 0), (1, 0x05F, 3, 0),
                     (2, 0x061, 87, 0), (3, 0x114, 0, 0)]
        fake_amap = {w: n for (_i, w, n, _u) in fake_rows}
        app._fill_items(fake_rows, fake_amap)
        items = [(app.tv_items.item(iid)["values"][0],
                  int(app.tv_items.item(iid)["values"][1]))
                 for iid in app.tv_items.get_children()]
        check("数量0不上榜", all(n > 0 for _nm, n in items), items)
        check("按数量降序且傀儡虫置顶",
              items[0][1] == 21 and items == sorted(
                  items, key=lambda t: (t[0] != IM.ITEM_NAME, -t[1], t[0])),
              items)
        check("傀儡虫行有高亮 tag",
              "focus" in app.tv_items.item(app.tv_items.get_children()[0])["tags"])
        # 游戏未运行: entries() -> None, _item_tick 必须安静
        real_entries = app.itemmon.entries
        app.itemmon.entries = lambda: None
        try:
            app._item_tick()
            check("游戏未运行时 _item_tick 不崩且显示 --",
                  "游戏未运行" in app.lbl_focus.cget("text"),
                  app.lbl_focus.cget("text"))
        finally:
            app.itemmon.entries = real_entries

        # ---- ⑦ 人物等级/经验面板 (注入假 RoleReader 数据) ----
        check("人物面板 tv_roles / rolemon 存在",
              hasattr(app, "tv_roles") and hasattr(app, "rolemon"))
        fake_roles = {"members": [
            {"role": 0, "name_id": 0x24, "name": "李逍遥", "level": 59,
             "exp": 6813, "next_exp": 9630, "pct": 70, "hp": 814, "maxhp": 963},
            {"role": 1, "name_id": 0x25, "name": "赵灵儿", "level": 42,
             "exp": 1725, "next_exp": 23665, "pct": 7, "hp": 755, "maxhp": 786},
            {"role": 4, "name_id": 0x27, "name": "阿奴", "level": 42,
             "exp": 21189, "next_exp": 21540, "pct": 98, "hp": 582, "maxhp": 685},
        ]}
        real_read = app.rolemon.read
        app.rolemon.read = lambda names=None: fake_roles
        try:
            app._role_tick()
            rows = [app.tv_roles.item(iid)["values"]
                    for iid in app.tv_roles.get_children()]
            check("人物行按队伍顺序填充",
                  [r[0] for r in rows] == ["李逍遥", "赵灵儿", "阿奴"], str(rows))
            check("等级/经验/体力列对位",
                  rows[0][1] == "Lv59" and rows[0][3] == "814/963", str(rows[0]))
            check("★经验列 = 当前/升下一级所需 (8B 步长修正后)",
                  rows[1][2] == "1725/23665" and rows[0][2] == "6813/9630",
                  str(rows[1]))
            check("快到级的人标 near tag",
                  "near" in app.tv_roles.item(
                      app.tv_roles.get_children()[2])["tags"]
                  and "near" not in app.tv_roles.item(
                      app.tv_roles.get_children()[1])["tags"])
        finally:
            app.rolemon.read = real_read
        app.rolemon.read = lambda names=None: None
        try:
            app._role_tick()
            rows = [app.tv_roles.item(iid)["values"]
                    for iid in app.tv_roles.get_children()]
            check("读不到时显示 -- 占位行", len(rows) == 3
                  and all(r[0] == "--" for r in rows), str(rows))
        finally:
            app.rolemon.read = real_read

        # ---- ⑧ 开箱台账: 开关 / 记录窗口 / 清空空箱 (写盘已重定向) ----
        check("台账开关 v_cmemo 存在且默认开",
              hasattr(app, "v_cmemo") and bool(app.v_cmemo.get()))
        check("台账路径已重定向到临时目录 (不碰运行目录)",
              os.path.dirname(app.chest_log.path) == _TMPDIR
              and os.path.dirname(app.chest_log.path) != BASE,
              app.chest_log.path)
        app.chest_log.clear()
        app.chest_log.mark(220, (-53, 152), item="傀儡虫", dn=3)
        app.chest_log.mark(157, (7, 80), ok=False, tries=4)
        app.chest_log_view()
        tv = app.tv_chest
        vals = [[str(x) for x in tv.item(i)["values"]]
                for i in tv.get_children()]
        check("记录窗口列出 2 条 (按场景排序)",
              len(vals) == 2 and vals[0][0] == "157" and vals[1][0] == "220",
              str(vals))
        check("状态列区分 已开 / 空",
              {v[3] for v in vals} == {"已开", "空/打不开"}, str(vals))
        check("空箱行有灰色 empty tag",
              any("empty" in tv.item(i)["tags"] for i in tv.get_children()))
        check("摘要写明 打开/空 计数",
              "打开 1" in app.lbl_chest.cget("text")
              and "空 1" in app.lbl_chest.cget("text"),
              app.lbl_chest.cget("text"))
        real_ask = G.messagebox.askyesno
        G.messagebox.askyesno = lambda *a, **k: True
        try:
            app.chest_log_forget_empty()
        finally:
            G.messagebox.askyesno = real_ask
        check("清空空箱后只剩已开的",
              app.chest_log.count() == 1
              and app.chest_log.has(220, (-53, 152))
              and not app.chest_log.has(157, (7, 80)),
              str(app.chest_log.keys()))
        check("运行目录的 chest_log.json 没被动过 (零副作用)",
              _chest_state() == _CHEST_BEFORE,
              "前 %s -> 后 %s" % (_CHEST_BEFORE, _chest_state()))
        app._close_chest_dlg()
        check("关掉窗口后句柄清空", app._chest_dlg is None)

        # ---- ⑨ v19.0 胜场拆分 / 清空 / 要素台账开关 ----
        print("⑨ 胜场拆分 + 清空累计 + 要素台账")
        check("底部有「本次胜场」/「累计总胜场」两个变量",
              hasattr(app, "v_wins_local") and hasattr(app, "v_wins_total"))
        check("有「清空累计胜场」与「要素覆盖率」两个按钮",
              callable(getattr(app, "wins_reset", None))
              and callable(getattr(app, "feat_report", None)))
        check("胜场文件已重定向到临时目录 (不碰运行目录)",
              os.path.dirname(G.bot_winlog.WINS_PATH) == _TMPDIR
              and os.path.dirname(G.bot_winlog.RESET_FLAG) == _TMPDIR,
              G.bot_winlog.WINS_PATH)

        # 机器人没在跑 -> 直接清 0 (会先弹确认框, 这里替掉)
        app.proc = None
        G.bot_winlog.WinLog().save_total(999)
        real_ask = G.messagebox.askyesno
        G.messagebox.askyesno = lambda *a, **k: True
        try:
            app.wins_reset()
        finally:
            G.messagebox.askyesno = real_ask
        check("★ 停机时「清空累计胜场」把它清 0 了",
              G.bot_winlog.WinLog().total() == 0
              and app.wins_total == 0 and app.v_wins_total.get() == "0",
              "%s / %s" % (G.bot_winlog.WinLog().total(),
                           app.v_wins_total.get()))
        check("清空走的是临时目录那份 win_total.json (运行目录没动)",
              _not_shrunk("win_total.json", _WIN_BEFORE),
              "前 %s -> 后 %s" % (_WIN_BEFORE, _file_state("win_total.json")))

        # 机器人在跑 -> 只能写 win_reset.flag, 让机器人自己兑现
        app.proc = FakeProc([])
        G.bot_winlog.WinLog().save_total(555)
        app.wins_reset()
        check("★ 运行中「清空」只写 win_reset.flag (机器人是唯一写者)",
              G.bot_winlog.WinLog().pending_reset()
              and G.bot_winlog.WinLog().total() == 555)
        # 待清空的状态要在界面上标出来
        app._wt_t = 0.0
        for _ in range(6):
            root.update()
            time.sleep(0.05)
        check("待清空时界面标出「(待清空)」",
              "(待清空)" in app.v_wins_total.get(), app.v_wins_total.get())
        # 机器人兑现: 下一个胜场 -> 先清 0 再 +1
        check("★ 机器人下一个胜场时兑现 (清 0 后 +1 = 1)",
              G.bot_winlog.WinLog().add(1) == 1
              and not G.bot_winlog.WinLog().pending_reset())
        app.proc = None

        # 要素台账勾选框 + 启动时写成环境变量
        check("「要素台账」勾选框存在且默认开",
              hasattr(app, "v_feat") and bool(app.v_feat.get()))
        app.proc = None
        cap.clear()
        app.v_feat.set(False)
        app.start()
        env_last = (cap.get("kw") or {}).get("env") or {}
        check("取消勾选 -> 子进程收到 PAL_FEAT_LOG=0",
              env_last.get("PAL_FEAT_LOG") == "0", str(env_last.get("PAL_FEAT_LOG")))
        check("取消勾选也落盘 (下次打开还在)",
              G.botcfg.load().get("feat_log") is False,
              str(G.botcfg.load().get("feat_log")))
        app.v_feat.set(True)
        cap.clear()
        app.proc = None
        app.start()
        check("勾上 -> 子进程收到 PAL_FEAT_LOG=1",
              ((cap.get("kw") or {}).get("env") or {}).get("PAL_FEAT_LOG") == "1",
              str(((cap.get("kw") or {}).get("env") or {}).get("PAL_FEAT_LOG")))
        app.proc = None
        check("运行目录的 feat_log.json 没被动过 (零副作用)",
              _not_shrunk("feat_log.json", _FEAT_BEFORE),
              "前 %s -> 后 %s" % (_FEAT_BEFORE, _file_state("feat_log.json")))

        # 「要素覆盖率」按钮 -> 后台跑 tools/feat_report.py (只 stub 子进程, 不真跑)
        app.proc = None
        cap.clear()
        app.feat_report()
        for _ in range(60):
            if cap.get("run_args"):
                break
            root.update()
            time.sleep(0.05)
        a9 = cap.get("run_args") or []
        check("「要素覆盖率」命令指向 tools/feat_report.py",
              len(a9) >= 2 and a9[-1].replace("\\", "/").endswith(
                  "tools/feat_report.py"), " ".join(str(x) for x in a9))
        check("覆盖率报告不写停止标志 (只读的工具)",
              os.path.dirname(G.STOP_FLAG) != BASE, G.STOP_FLAG)
        check("★ win_total.json 没被写小 / win_reset.flag 没落在运行目录",
              _not_shrunk("win_total.json", _WIN_BEFORE)
              and _file_state("win_reset.flag")[0] is False,
              str(_file_state("win_total.json")))

        # ---- ⑪ 版本号显示 (2026-10-12) ----
        # 为什么要测: 界面上看不出跑的是哪一版, 刚改完 bug 想确认新版有没有真生效
        # 时只能靠猜。另外版本号出现在窗口标题里 —— 而机器人/GUI 找游戏窗口用的
        # 判据是「标题含 'Pal'」(游戏真标题 = `Pal Win95 [...]`), 自家 GUI 靠
        # 全大写的 "SDLPAL" 侥幸不撞车 -> 标题里一旦混进大小写敏感的 "Pal",
        # 机器人就会把 GUI 当游戏去读内存。这条断言把这个雷钉死。
        print("⑪ 版本号显示 + 标题不撞游戏窗口判据")
        check("botcfg.APP_VERSION 存在且像个版本号",
              isinstance(G.botcfg.APP_VERSION, str)
              and G.botcfg.APP_VERSION.startswith("v")
              and len(G.botcfg.APP_VERSION) > 2,
              repr(getattr(G.botcfg, "APP_VERSION", None)))
        check("★ APP_VERSION 里不含 'Pal' (大小写敏感, 游戏窗口判据)",
              "Pal" not in G.botcfg.APP_VERSION, G.botcfg.APP_VERSION)
        check("窗口标题带版本号", G.botcfg.APP_VERSION in root.title(), root.title())
        check("★ 窗口标题里不含 'Pal' (否则机器人会误认成游戏窗口)",
              "Pal" not in root.title(), root.title())
        check("标题仍然是自家 GUI (含「挂机机器人」, 排除逻辑仍然生效)",
              "挂机机器人" in root.title(), root.title())
        check("底部状态区有版本号显示位",
              hasattr(app, "v_ver")
              and G.botcfg.APP_VERSION in app.v_ver.get(), app.v_ver.get())
        check("版本号只有一个来源 (底部显示 == APP_VERSION, 无副本)",
              app.v_ver.get().endswith(G.botcfg.APP_VERSION), app.v_ver.get())

        # ---- ⑫ 战斗收益左栏 (v19.3, exp_log.json) ----
        # 为什么要测: 左栏数据来自机器人子进程写的 exp_log.json, GUI 只读;
        # 文件不存在 (机器人从没跑过/刚清掉) 时绝不能弹错、也不能崩。
        print("⑫ 战斗收益左栏 (左日志右收益分栏)")
        check("左栏树表存在", hasattr(app, "tree_gain"))
        check("左栏统计显示位存在",
              hasattr(app, "v_gain_sum") and hasattr(app, "v_gain_rate"))
        # 空文件场景: 载入失败 -> 保持原显示, 不异常
        app._gain_refresh()
        check("exp_log.json 缺失时 _gain_refresh 不崩", True)
        # 塞一份台账进临时目录 -> 刷新后左栏要显示出来
        G.bot_explog.GainLog().record(
            wins=7, elapsed=3600.0, t_start="2026-10-13 10:00:00",
            d_exp=6844, d_cash=658,
            last={"n": 7, "t": "11:00:00", "exp": 62, "cash": 94})
        app._gain_refresh()
        check("统计行显示累计",
              "+6844" in app.v_gain_sum.get() and "+658" in app.v_gain_sum.get(),
              app.v_gain_sum.get())
        check("时均行显示 1 小时均值",
              "6,844" in app.v_gain_rate.get() and "658" in app.v_gain_rate.get(),
              app.v_gain_rate.get())
        rows = app.tree_gain.get_children()
        check("列表显示最近一场", len(rows) == 1
              and "#7" in str(app.tree_gain.item(rows[0], "values")),
              str(app.tree_gain.item(rows[0], "values") if rows else "-"))
        # ★ exp_log.json 只落在临时目录。运行目录那份是**机器人**写的 (每胜一场
        #   就重写), 不能断言"不存在" —— 机器人跑着时会误报; 判据 = 它不含有
        #   本自检写进去的独特标记 (t_start="2026-10-13 10:00:00")。只读。
        _real = os.path.join(BASE, "exp_log.json")
        _touched = False
        if os.path.exists(_real):
            try:
                with open(_real, encoding="utf-8") as fh:
                    _touched = "2026-10-13 10:00:00" in fh.read()
            except Exception:
                pass
        check("★ exp_log.json 只落在临时目录 (运行目录那份不是自检写的)",
              os.path.dirname(G.bot_explog.GainLog().path) == _TMPDIR
              and not _touched, _real)
        # ★ v3 口径: bad>0 时统计行要给出「跳过 N 场」提示 (时均偏低的线索)
        G.bot_explog.GainLog().record(
            wins=8, elapsed=3600.0, t_start="2026-10-13 10:00:00",
            d_exp=6906, d_cash=700, bad=3,
            last={"n": 8, "t": "11:00:00", "exp": 62, "cash": 42})
        app._gain_refresh()
        check("bad>0 -> 统计行提示跳过场次",
              "跳过 3 场" in app.v_gain_sum.get(), app.v_gain_sum.get())
        # ★ 旧口径 (version != VERSION) 的台账不展示 (v1 虚高 / v2 时均偏小)
        import json as _json
        with open(G.bot_explog.GainLog().path, "w", encoding="utf-8") as fh:
            _json.dump({"version": 2, "t_start": "old", "elapsed": 10.0,
                        "wins": 99, "d_exp": 12481, "d_cash": 658,
                        "last": {"n": 99, "t": "x", "exp": 62, "cash": 94},
                        "battles": [{"n": 99, "t": "x", "exp": 62, "cash": 94}]},
                       fh)
        app._gain_refresh()
        check("★ 旧口径台账不显示 (避免用户被错数迷惑)",
              "旧口径" in app.v_gain_sum.get()
              and "12481" not in app.v_gain_sum.get()
              and not app.tree_gain.get_children(),
              app.v_gain_sum.get())
        # 复原成新口径, 免得后面的检查看到"旧口径"提示
        G.bot_explog.GainLog().record(
            wins=8, elapsed=3600.0, t_start="2026-10-13 10:00:00",
            d_exp=6906, d_cash=700,
            last={"n": 8, "t": "11:00:00", "exp": 62, "cash": 42})
        app._gain_refresh()
        check("新口径台账恢复正常显示",
              "+6906" in app.v_gain_sum.get(), app.v_gain_sum.get())

        # ---- ⑬ 窗口比例 ≈4:3 (2026-10-13) ----
        # 为什么要测: 用户嫌"整窗太宽"。重排后请求尺寸 = 914x719 (宽/高 1.27),
        # 默认 geometry 960x720 = 1.333。用"请求比例"做断言 (区间放宽到
        # 1.20~1.45, 字体/DPI 有浮动); 若哪天又把参数行加宽了, 这里会叫。
        # 注意窗口是 withdraw 的, winfo_width 恒为 1 —— 只能查 geometry 字符串。
        print("⑬ 窗口比例 ≈4:3")
        _rw, _rh = root.winfo_reqwidth(), root.winfo_reqheight()
        _ratio = _rw / float(max(1, _rh))
        check("整窗请求比例接近 4:3 (1.20 ~ 1.45)",
              1.20 <= _ratio <= 1.45, "%dx%d = %.3f" % (_rw, _rh, _ratio))
        _m = re.match(r"(\d+)x(\d+)", root.geometry() or "")
        _gw = int(_m.group(1)) if _m else 0
        _gh = int(_m.group(2)) if _m else 0
        check("默认 geometry 是 4:3 (±2%)",
              _gw and _gh and abs(_gw / float(_gh) - 4.0 / 3.0) <= 0.02,
              root.geometry())

        # ---- ⑭ 程序图标 (2026-10-13) ----
        # 为什么要测: 图标文件是"锦上添花", 但用户特意要求任务栏能认出机器人;
        # 这里钉三件事: ① 文件在且是合法的多尺寸 ICO (16/32 必须有 —— 任务栏
        # 用的就是这两档); ② GUI 接了 iconbitmap; ③ 文件缺失时绝不炸 (别人只
        # 拷 .py 不拷 assets/ 也要能开界面)。
        print("⑭ 程序图标 (任务栏/标题栏)")
        check("assets/pal98bot.ico 存在", os.path.exists(G.ICON_PATH),
              G.ICON_PATH)
        if os.path.exists(G.ICON_PATH):
            with open(G.ICON_PATH, "rb") as fh:
                head = fh.read(6 + 16 * 32)
            _cnt = int.from_bytes(head[4:6], "little")
            _sizes = set()
            for _i in range(_cnt):
                _o = 6 + 16 * _i
                _w, _h = head[_o], head[_o + 1]          # 0 表示 256
                _bpp = int.from_bytes(head[_o + 6:_o + 8], "little")
                _sizes.add(((_w or 256), (_h or 256), _bpp))
            check("ICO 是多尺寸 32bpp (16/32 必备)", _cnt >= 5
                  and (16, 16, 32) in _sizes and (32, 32, 32) in _sizes,
                  sorted(_sizes))
        srcg = open(os.path.join(BASE, "bot_gui.py"), encoding="utf-8").read()
        check("GUI 接了图标 (iconbitmap + WM_SETICON + 容错函数)",
              "iconbitmap" in srcg and "_apply_icon" in srcg
              and "_win_taskbar_icon" in srcg and "ICON_PATH" in srcg)
        # 任务栏图标 = AUMID(建窗前注册) + WM_SETICON 三连 (small/big/small2)。
        # 真机教训: 只 iconbitmap 时 Win11 任务栏显示"白纸", 见 _set_aumid 注释。
        check("显式 AUMID 在建窗前注册 (模块加载即调用)",
              "APP_AUMID" in srcg and "SetCurrentProcessExplicitAppUserModelID"
              in srcg and srcg.index("_set_aumid()") < srcg.index("def main("))
        check("WM_SETICON 挂了 small/big/small2 三档",
              "WM_SETICON" in srcg and "(2, small)" in srcg)
        try:
            app._apply_icon(root, os.path.join(_TMPDIR, "不存在的.ico"))
            check("图标文件缺失时静默跳过 (不炸界面)", True)
        except Exception as _ex:
            check("图标文件缺失时静默跳过 (不炸界面)", False, repr(_ex))
    finally:
        G.subprocess = real_subprocess
        if os.path.exists(G.STOP_FLAG):
            os.remove(G.STOP_FLAG)
        try:
            import shutil
            shutil.rmtree(_TMPDIR, ignore_errors=True)   # 临时配置一起收掉
        except Exception:
            pass
        try:
            root.destroy()
        except Exception:
            pass

    print("\n通过 %d 项, 失败 %d 项" % (len(OK), len(BAD)))
    if BAD:
        print("失败项:", ", ".join(BAD))
    print("GUI SMOKE", "OK" if not BAD else "FAIL")
    return 0 if not BAD else 1


if __name__ == "__main__":
    sys.exit(main())
