# -*- coding: utf-8 -*-
"""test_heal.py — 菜单宏 (pal_bot_heal.py, 现用于"战后自动存盘") 的回归测试。

被测对象:
  * HealMacro —— 按键序列宏本体。三道校验 (起点/开菜单/收尾) 的每一条
    分支都要走一遍, 特别是**熔岩场景的已知边缘**: 菜单特征在红地面上会
    漏判 (2026-09-30 事故的 known issue), 所以宏必须还能靠"画面差异"
    判定 —— 第 6 节专门验证这一点。
  * HealMixin —— 机器人侧的接入层: 开关 / 每 N 场 / 冷却 / 连败自动停用 /
    配置损坏不许炸。

铁律: **不碰游戏、不写运行目录**。按键是假的 (FakeG 只记录),
      配置文件全部落在临时目录; 环境变量用完即恢复。
"""
import os
import sys
import tempfile

import numpy as np

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import botcfg                                             # noqa: E402
import pal_bot_heal as H                                   # noqa: E402
from pal_bot_heal import HealMacro, HealMixin, diff_frac    # noqa: E402

OK, BAD = [], []


def check(name, cond, extra=""):
    (OK if cond else BAD).append(name)
    print("  %s %s %s" % ("PASS" if cond else "FAIL", name, extra))


# 把宏的等待全部调到接近 0 (测试只验证逻辑, 不验证真实节奏)
H.POLL_TIMES = 2
H.POLL_GAP = 0.01
GAP = 0.01

TMP = tempfile.mkdtemp(prefix="heal_test_")


# ======================================================================
# 假对象
# ======================================================================
def frame(tag=0, w=160, h=100):
    """造一张测试帧: 左上角像素当标签用 (0=地图 1=菜单 2=卡住的子页)。"""
    a = np.zeros((h, w, 3), dtype=np.uint8)
    a[:, :, 0] = 10
    a[0, 0] = (tag, tag, tag)
    return a


F_MAP, F_MENU, F_STUCK = frame(0), frame(1), frame(2)


def big_change(a):
    """在画面中间涂一大块 (面积 > 3%), 让 diff_frac 认得出"菜单出现了"。"""
    b = a.copy()
    b[20:60, 40:120] = (200, 180, 120)
    return b


F_MENU = big_change(F_MENU)
F_STUCK = big_change(F_STUCK)


class FakeG:
    """假游戏: 按预置脚本逐帧返回 shot(); 记录所有按键。"""

    def __init__(self, script):
        self.script = list(script)
        self.keys = []
        self.flushed = 0

    def shot(self):
        if len(self.script) > 1:
            return self.script.pop(0)
        return self.script[0]          # 脚本用完后停在最后一帧

    def key(self, name, dur=0.07):
        self.keys.append(name)

    def flush_keys(self):
        self.flushed += 1


class FakeEscape:
    def __init__(self, ok=True):
        self.n = 0
        self.ok = ok

    def __call__(self):
        self.n += 1
        return self.ok


def macro(g, menu_feat=True, battle=False, escape=None, gap=GAP):
    """组装一个依赖注入齐全的 HealMacro。menu_feat=True 表示特征可信。"""
    def feat(a):
        return ((0.5, 0.0) if menu_feat and a is F_MENU else (0.0, 0.0))

    return HealMacro(shot=g.shot, key=g.key, log=lambda m: None,
                     is_battle=(lambda a: battle),
                     menu_feat=feat,
                     menu_like=lambda red, tan: red > 0.2,
                     escape=escape, gap=gap)


# 当前默认序列 = **存档路径** (2026-10-15 用户真机复核)。
# ★ 这里刻意跟着 botcfg.SEQ_DEFAULT 走: 第 8/9 节会断言两者一致 ——
#   默认值一旦被改回旧的"补血"路径, 测试立刻红。
SEQ = ["esc", "enter", "enter", "enter"]

# 一条**更长的**序列: 只喂给"按到一半被打断"这类跟长度有关的用例
#   (中间键多, 才走得到"发了两键之后中止"的分支)。键名本身无意义。
SEQ_LONG = ["esc", "enter", "right", "enter", "enter", "right", "enter", "esc"]


# ======================================================================
print("=" * 66)
print("第 1 节: diff_frac 基础")
print("=" * 66)
a = frame(0)
check("同一张图 -> 0", diff_frac(a, a.copy()) == 0.0)
check("小噪点在容差内 -> 0", diff_frac(a, a + 5) == 0.0)
b = a.copy()
b[0, 0, 0] = 200                       # 1 个像素 (0.006%) 超容差
check("单像素超容差 -> >0 但很小", 0.0 < diff_frac(a, b) < 0.001,
      "%.5f" % diff_frac(a, b))
check("形状不一致 -> 1.0", diff_frac(a, np.zeros((5, 5, 3))) == 1.0)
check("缺帧 -> 1.0", diff_frac(None, a) == 1.0)
check("菜单块差异超过 CHANGE_FRAC",
      diff_frac(F_MAP, F_MENU) > H.CHANGE_FRAC,
      "%.3f" % diff_frac(F_MAP, F_MENU))

# ======================================================================
print("=" * 66)
print("第 2 节: 成功路径 (开菜单 -> 序列 -> 收尾回到地图)")
print("=" * 66)
g = FakeG([F_MAP, F_MENU, F_MAP])
r = macro(g).run(SEQ)
check("ok=True", r["ok"] is True, r["reason"])
check("opened=True", r["opened"] is True)
check("closed=True", r["closed"] is True)
check("按键一个不少", r["sent"] == SEQ, " ".join(r["sent"]))
check("没有补 esc", r["esc_extra"] == 0)
check("没走兜底", g.flushed == 0)

# ======================================================================
print("=" * 66)
print("第 3 节: 第一天 esc 被吞 -> 立即放弃 (后续键绝不发)")
print("=" * 66)
g = FakeG([F_MAP, F_MAP])              # esc 之后画面没变
esc = FakeEscape()
r = macro(g, escape=esc).run(SEQ)
check("ok=False", r["ok"] is False)
check("opened=False", r["opened"] is False)
check("只发了第一下 esc", r["sent"] == ["esc"], str(r["sent"]))
check("放弃原因提到画面没变", "没变" in r["reason"], r["reason"])
check("没调兜底", esc.n == 0)

# ======================================================================
print("=" * 66)
print("第 4 节: 起始就在菜单 -> 先关掉再执行 / 关不掉就放弃")
print("=" * 66)
esc = FakeEscape(ok=True)
g = FakeG([F_MENU, F_MAP, F_MENU, F_MAP])   # 起始菜单 -> 关掉 -> 宏正常跑
r = macro(g, escape=esc).run(SEQ)
check("先调了一次兜底关菜单", esc.n == 1, str(esc.n))
check("关掉后宏照常成功", r["ok"] is True and r["sent"] == SEQ,
      r["reason"])

esc = FakeEscape(ok=False)
g = FakeG([F_MENU, F_MENU])                 # 关不掉
r = macro(g, escape=esc).run(SEQ)
check("关不掉 -> ok=False 且一个键都不发",
      r["ok"] is False and r["sent"] == [], r["reason"])
check("关不掉 -> 兜底被调过", esc.n == 1)

# ======================================================================
print("=" * 66)
print("第 5 节: 收尾没回到地图 -> 补 esc; 补完就好了")
print("=" * 66)
# 帧预算 (v19.1 起, 2026-10-15 改成按序列长度算 —— 免得改序列时又要手数一遍):
#   a0 / a1 之后, 每个中间键各先拍 1 帧查战斗 (n_mid 帧 F_STUCK),
#   收尾轮询再吃 POLL_TIMES 帧 F_STUCK, 然后补 1 次 esc,
#   补完的第 2 帧给 F_MAP -> "补一次就好了"。
n_mid = len(SEQ) - 2
g = FakeG([F_MAP, F_MENU] + [F_STUCK] * (n_mid + H.POLL_TIMES)
          + [F_STUCK, F_MAP])
r = macro(g).run(SEQ)
check("补了 1 次 esc 后成功", r["ok"] is True and r["esc_extra"] == 1,
      "esc_extra=%d %s" % (r["esc_extra"], r["reason"]))
check("补的 esc 也记进 sent", r["sent"] == SEQ + ["esc"], " ".join(r["sent"]))

# ======================================================================
print("=" * 66)
print("第 6 节: 熔岩场景 (菜单特征失效) —— 靠画面差异仍能判定")
print("=" * 66)
# 红色地面上菜单特征会漏判 (menu_like 恒 False), 但画面差异还在
g = FakeG([F_MAP, F_MENU, F_MAP])
r = macro(g, menu_feat=False).run(SEQ)
check("特征失效也能成功", r["ok"] is True and r["opened"] is True,
      r["reason"])

# 特征失效 + 收尾卡住 -> 补 esc 兜底照常触发
g = FakeG([F_MAP, F_MENU, F_STUCK])
esc = FakeEscape(ok=False)
r = macro(g, menu_feat=False, escape=esc).run(SEQ)
check("特征失效时卡住 -> 补 esc 且走兜底",
      r["ok"] is False and r["esc_extra"] == H.MAX_ESC_RETRY and esc.n == 1,
      "esc_extra=%d escape=%d" % (r["esc_extra"], esc.n))
check("失败原因说明白", "进错子页" in r["reason"], r["reason"])

# ======================================================================
print("=" * 66)
print("第 7 节: 战斗中 / 空序列 -> 一个键都不发")
print("=" * 66)
g = FakeG([F_MAP])
r = macro(g, battle=True).run(SEQ)
check("战斗中不补", r["ok"] is False and r["sent"] == [], r["reason"])
check("原因=在战斗里", "战斗" in r["reason"], r["reason"])
g = FakeG([F_MAP])
r = macro(g).run([])
check("空序列直接放弃", r["ok"] is False and r["sent"] == [], r["reason"])

# ======================================================================
print("=" * 66)
print("第 8 节: HealMixin —— 配置/开关/场次/冷却/连败停用")
print("=" * 66)
CFG = os.path.join(TMP, "bot_config.json")
REAL_CFG = botcfg.CONFIG_PATH
botcfg.CONFIG_PATH = CFG          # ★ 把"无 path 调用"也钉到临时文件
                                  #   (after_battle_heal 内部走 heal_load())


class Host(HealMixin):
    """最小宿主: 只实现 mixin 需要的东西 (不加载地图数据/不开游戏)。"""

    def __init__(self, script):
        self.g = FakeG(script)
        self.logs = []
        self.battles_won = 0
        self._menu_feat = staticmethod(lambda a: (0.0, 0.0))
        self._menu_like = staticmethod(lambda red, tan: False)
        self.escape_called = 0

    def log(self, msg):
        self.logs.append(str(msg))

    def band0(self):
        return None

    def escape_menu(self):
        self.escape_called += 1
        return True


def wipe_env():
    for k in ("PAL_HEAL_AFTER_BATTLE", "PAL_HEAL_SEQ", "PAL_HEAL_EVERY",
              "PAL_HEAL_GAP"):
        os.environ.pop(k, None)


wipe_env()
host = Host([F_MAP, F_MENU, F_MAP])
c = host.heal_load(CFG)
check("默认: 开启", c["on"] is True and c["err"] == "")
check("默认序列 = 存档路径 4 键", c["keys"] == SEQ, " ".join(c["keys"]))
check("★ 默认序列钉死为存档路径 (不是旧的补血 8 键)",
      botcfg.SEQ_DEFAULT == "esc,enter,enter,enter", botcfg.SEQ_DEFAULT)
check("默认每 1 场 / 间隔 0.3s", c["every"] == 1 and abs(c["gap"] - 0.3) < 1e-9)
check("heal_summary 能读", "开启" in host.heal_summary(), host.heal_summary())

# 环境变量层 > 文件层 (GUI 就是靠这个把界面上的值钉给子进程)
os.environ["PAL_HEAL_SEQ"] = "esc,esc"
os.environ["PAL_HEAL_EVERY"] = "2"
os.environ["PAL_HEAL_GAP"] = "0.05"
os.environ["PAL_HEAL_AFTER_BATTLE"] = "1"
host2 = Host([F_MAP, F_MENU, F_MAP])
c2 = host2.heal_load(CFG)
check("env 覆盖序列", c2["keys"] == ["esc", "esc"], str(c2["keys"]))
check("env 覆盖场次", c2["every"] == 2)
check("env 覆盖间隔", abs(c2["gap"] - 0.05) < 1e-9)
wipe_env()

# 关掉: 一个键都不发
botcfg.put("heal_after_battle", False, CFG)
host3 = Host([F_MAP])
r3 = host3.after_battle_heal()
check("关闭时不发键", host3.g.keys == [] and r3 is False)
wipe_env()

# 每 N 场: 第 1 场不补, 第 2 场补
botcfg.put("heal_after_battle", True, CFG)
botcfg.put("heal_every", 2, CFG)
botcfg.put("heal_gap", 0.01, CFG)
host4 = Host([F_MAP, F_MENU, F_MAP, F_MENU, F_MAP])
host4.battles_won = 1
r4 = host4.after_battle_heal()
check("every=2 且第 1 场 -> 不补", r4 is False and host4.g.keys == [])
host4.battles_won = 2
r4 = host4.after_battle_heal()
check("every=2 且第 2 场 -> 补", r4 is True and len(host4.g.keys) >= 2,
      str(host4.g.keys))
check("宏跑完冲了键", host4.g.flushed >= 1)
wipe_env()

# 序列写坏 -> 停用但不炸
botcfg.put("heal_seq", "esc,ctrl,回车", CFG)
host5 = Host([F_MAP])
c5 = host5.heal_load(CFG, force=True)
check("坏序列 -> 配置层报错且停用", c5["on"] is False and "ctrl" in c5["err"],
      c5["err"])
r5 = host5.after_battle_heal()
check("坏序列 -> after_battle_heal 返回 False 且零按键",
      r5 is False and host5.g.keys == [])
check("坏序列 -> 日志里说清楚", any("无效" in m for m in host5.logs),
      " ; ".join(host5.logs)[-60:])

# 连败停用: 让宏每次都失败 (收尾永远回不到地图)。
# 冷却本来会挡住第二次尝试, 这里把冷却时间拨回过去, 模拟"冷却已过"。
botcfg.put("heal_seq", botcfg.SEQ_DEFAULT, CFG)
botcfg.put("heal_gap", 0.01, CFG)
H.POLL_TIMES = 1
H.POLL_GAP = 0.001
host6 = Host([F_MAP, F_MENU, F_STUCK])      # 脚本停在 STUCK -> 永不 settle
for i in range(H.FAIL_LIMIT):
    host6._heal_bail_t = 0.0                # 模拟: 距上次失败已过了冷却期
    host6.after_battle_heal()
check("连续失败 %d 次后自动停用" % H.FAIL_LIMIT, host6._heal_off is True)
n_keys_before = len(host6.g.keys)
host6.after_battle_heal()
check("停用后不再发键", len(host6.g.keys) == n_keys_before)
check("失败进了冷却", host6._heal_bail_t > 0)
check("统计计数正确", host6.heal_stat() == (0, H.FAIL_LIMIT),
      str(host6.heal_stat()))

# 冷却期跳过
host7 = Host([F_MAP, F_MENU, F_MAP])
host7.heal_load(CFG, force=True)
import time as _time
host7._heal_bail_t = _time.time() + 999
r7 = host7.after_battle_heal()
check("冷却期内跳过", r7 is False and host7.g.keys == [])

# 失败留证: 设了 heal_shot_dir 的宿主 -> 失败时存 heal_fail_*.png (上限 6 张)
SHOT_DIR = os.path.join(TMP, "heal_fail_shots")
os.makedirs(SHOT_DIR, exist_ok=True)
import glob as _glob
host8 = Host([F_MAP, F_MENU, F_STUCK])      # 同 host6: 永不 settle -> 必失败
host8.heal_shot_dir = SHOT_DIR
host8._heal_bail_t = 0.0
host8.after_battle_heal()
_shots = _glob.glob(os.path.join(SHOT_DIR, "heal_fail_*.png"))
check("失败留证: 存了 1 张现场截图", len(_shots) == 1, str(_shots))
check("截图文件名带键数与开合状态",
      len(_shots) == 1 and "_k" in os.path.basename(_shots[0])
      and os.path.basename(_shots[0]).endswith("_open.png"),
      os.path.basename(_shots[0]) if _shots else "无")
for _i in range(7):
    host8._heal_bail_t = 0.0
    host8._heal_off = False                 # 不让 FAIL_LIMIT 拦住, 专测上限
    host8._heal_streak = 0
    host8.after_battle_heal()
_shots = _glob.glob(os.path.join(SHOT_DIR, "heal_fail_*.png"))
check("留证上限 6 张 (新图顶旧图)", len(_shots) == 6, str(len(_shots)))
check("未设目录的宿主 (host6) 没有该属性 -> 一张不写",
      getattr(host6, "heal_shot_dir", None) is None)
check("未设目录的宿主失败路径不炸",
      host6._heal_fail_n == H.FAIL_LIMIT, str(host6._heal_fail_n))

wipe_env()
botcfg.CONFIG_PATH = REAL_CFG               # 还原, 别影响别的测试

# ======================================================================
print("=" * 66)
print("第 9 节: 序列解析 (中文键名 / 分隔符 / 非法项)")
print("=" * 66)
check("默认序列可解析", botcfg.parse_seq(botcfg.SEQ_DEFAULT) == SEQ)
check("中文与英文混写等价",
      botcfg.parse_seq("ESC → 回车 → 回车 → 回车") == SEQ)
check("空格分隔也认", botcfg.parse_seq("esc 回车 右 esc") ==
      ["esc", "enter", "right", "esc"])
check("上/下/左/空格 都有中文写法",
      botcfg.parse_seq("上,下,左,空格,确认,退出") ==
      ["up", "down", "left", "space", "enter", "esc"])
try:
    botcfg.parse_seq("esc,ctrl")
    check("非法键要被拦", False)
except ValueError as e:
    check("非法键要被拦", "ctrl" in str(e), str(e)[:40])
check("heal_summary 对坏序列友好",
      any(w in Host([F_MAP]).heal_summary() for w in ("无效", "开启", "关闭")))

# ======================================================================
print("=" * 66)
print("第 10 节: v19.1 中途战斗校验 —— 宏跑到一半进战斗, enter 绝不再发")
print("=" * 66)
# 事故指纹 (2026-10-10 用户报告): 怪游走贴身, 宏按完 esc 之后新战斗开始,
# 下一个键 enter 落进战斗指令菜单 -> 选中"攻击" -> 停在"选人"等 180s 超时。
# 战斗一开始就必须中止宏 (esc 多按无害, enter 绝不行)。


def macro_battle_script(flags, frames):
    """is_battle 按 flags 逐次返回; frames 是 shot() 的帧脚本。"""
    it = iter(flags)
    return HealMacro(shot=FakeG(frames).shot, key=lambda k: keys.append(k),
                     log=lambda m: None,
                     is_battle=lambda a: next(it, True),
                     menu_feat=lambda a: ((0.5, 0.0) if a is F_MENU
                                          else (0.0, 0.0)),
                     menu_like=lambda red, tan: red > 0.2,
                     escape=None, gap=GAP)


# ① 战斗在第一个中间键之前开始 -> 只发了开菜单的 esc
keys = []
m = macro_battle_script([False, True], [F_MAP, F_MENU, F_STUCK])
r = m.run(SEQ)
check("中途进战斗 -> ok=False", r["ok"] is False, r["reason"])
check("只发了开菜单的 esc", r["sent"] == ["esc"], str(r["sent"]))
check("enter 一个都没发", "enter" not in r["sent"])
check("原因提到战斗中止", "战斗" in r["reason"] and "中止" in r["reason"],
      r["reason"])

# ② 战斗在第 3 个键之前才开始 -> 前两个中间键照发, 之后立刻停
#    (用长序列 SEQ_LONG: 4 键的默认序列只有 2 个中间键, 走不到这个分支)
keys = []
m = macro_battle_script([False, False, False, True],
                        [F_MAP, F_MENU, F_MAP, F_MAP, F_MAP, F_STUCK])
r = m.run(SEQ_LONG)
check("前两键照发后中止", r["sent"] == ["esc", "enter", "right"],
      str(r["sent"]))
check("中止后 enter 不再发",
      "enter" not in r["sent"][len(["esc", "enter", "right"]):],
      str(r["sent"]))
check("ok=False", r["ok"] is False)

# ③ 全程没进战斗 -> 宏不受影响 (中途校验透明)
g = FakeG([F_MAP, F_MENU] + [F_MAP] * 6 + [F_MAP])
r = macro(g).run(SEQ)
check("无战斗时宏照常成功", r["ok"] is True and r["sent"] == SEQ,
      r["reason"])

# ======================================================================
print("=" * 66)
print("结果: PASS %d  FAIL %d" % (len(OK), len(BAD)))
print("=" * 66)
if BAD:
    print("失败项:")
    for n in BAD:
        print("  -", n)
import shutil
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if BAD else 0)
