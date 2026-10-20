# -*- coding: utf-8 -*-
"""test_pin.py — 驻留练级 (pin_scene) 的回归测试 (不需要游戏窗口)。

背景 (2026-10-20 事故): 在十里坡 (场景 7) 刷了一夜, 早上发现机器人自己跑到
场景 5, 然后在场景 2 / 4 之间来回踩门 512 次, 9 小时只赢 1 场。三个独立缺陷:
  ① 静态 scene_info 的 portals 漏了真踩响的门 (脚本动态创建的出口);
  ② graded_reach 在可达域 < REACH_MIN(2000) 时放宽到「无门禁」, 出口保护全撤;
  ③ 踩门传送绕过调度层的换场景熔断, 512 次横跳一次都没计数。

本测试钉住修法的三条腿:
  A. 避门: 出口从**实时对象表 + 脚本控制流**扫出来 (不看静态快照), pad=2 圈禁;
  B. 不离: 门禁分级永不降到「无门禁」; migrate 每帧复位; 不爬绳不调度;
  C. 回得来: 被送出去后按 scene_links 找到一个能回去的出口并走过去。
"""
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import pal_bot_mem4                                    # noqa: E402
from pal_bot_mem5 import MemBot5, cheb, kof, OP_CHANGESCENE   # noqa: E402
import botcfg                                          # noqa: E402

OK = []
BAD = []


def check(name, cond, extra=""):
    (OK if cond else BAD).append(name)
    print("  %s %s %s" % ("PASS" if cond else "FAIL", name, extra))


def kof_inv(a, b):
    """kof 的逆: 想要格坐标 K=(a,b), 对应的地图像素坐标是多少。"""
    return (16 * (a + b) + 16, 8 * b - 8 * a + 8)


class FakeObjMem(object):
    def open(self):
        return True


class FakeMem(object):
    def __init__(self, K):
        self.K = K

    def state(self):
        return {"K": self.K}


class FakePilot(object):
    """调度器替身: 记录 decide 有没有被问过。"""

    def __init__(self):
        self.n_decide = 0

    def decide(self, now, won):
        self.n_decide += 1
        return False, ""

    def note_change(self, t):
        pass

    def note_fail(self, t):
        pass

    def note_dud(self):
        return 0

    def on_scene(self, *a):
        return ""


# 本场景所有"触发脚本走得到换景"的对象 (元组布局: f[1],f[2]=px,py / f[4]=trig
# / f[5]=auto / f[6]=state / f[7]=mode / f[8]=spr)。故意做成三类:
#   A. mode=7 隐形, 走近触发  -> 出口 (离场型)
#   B. mode=1 隐形, 按键搜索  -> 出口 (绳子型)
#   C. mode=2 有贴图, 不换景 -> 不是出口 (路牌/摆设)
EXIT_A = (2, -2)
EXIT_B = (-3, 3)
PX_A = kof_inv(*EXIT_A)
PX_B = kof_inv(*EXIT_B)
PX_C = kof_inv(1, 1)

# trig 号 -> 是否换景
TRIG_CHANGE = {0x111: True, 0x222: True, 0x333: False}


def make_objs(points=None):
    def obj(px, py, trig, mode, spr, state=1):
        f = [0] * 16
        f[1], f[2] = px, py
        f[4], f[5], f[6], f[7], f[8] = trig, 0, state, mode, spr
        return tuple(f)
    a, b = (points or (EXIT_A, EXIT_B))
    return [obj(*(kof_inv(*a) + (0x111, 7, 0))),
            obj(*(kof_inv(*b) + (0x222, 1, 0))),
            obj(*(kof_inv(1, 1) + (0x333, 2, 210)))]


def shadow(pin=0, scene=7, links=None, exits=True,
           walk=None, exit_xy=None):
    o = MemBot5.__new__(MemBot5)
    o.scene = scene
    o.map = 4
    o.pin_scene = pin
    o._pin_auto = False
    o._pin_back = False
    o._pin_back_n = 0
    o._pin_open_tmp = False
    o._pin_warn_t = 0.0
    o._pin_hold_t = 0.0
    o._exit_cache = None
    o._scene_links = dict(links or {})
    o._open_doors_user = False
    o.blocked = set()
    o.report_doors = {}
    o.known_doors = {}
    o.open_doors = False
    o.migrate = False
    o.elem_mode = True
    o.home = None
    o.auto_scene = True
    o.migrate_cd = 0.0
    o._rope_goal = None
    o.battles_won = 0
    o.REACH_MIN = 2000                  # 与真值一致: 小地图必然"够不着"这档
    o.pin_scan = 0
    o.logs = []
    o.log = lambda m: o.logs.append(m)
    o.scene_info = {}
    o._objmem = FakeObjMem()
    o.mem = FakeMem((0, 0))
    floor = walk if walk is not None else \
        set((a, b) for a in range(-4, 5) for b in range(-4, 5))
    o.kwalk = lambda m: floor
    o.scene_objects = lambda s: make_objs(exit_xy) if exits else []
    o._script_tab = lambda: (1, 100000)

    def hits(entry, want):
        o.pin_scan += 1
        return ({OP_CHANGESCENE} if TRIG_CHANGE.get(entry) else set()) & set(want)
    o.script_hits = hits
    return o


print("=== 1. 出口识别: 实时扫脚本, 不看静态快照 ===")
o = shadow(pin=7)
ex = o.scene_exits()
check("扫出 2 个出口 (换景型), 排掉不换景的路牌",
      ex is not None and len(ex) == 2, str([e["K"] for e in (ex or [])]))
ks = set(e["K"] for e in (ex or []))
check("走近触发型 (mode=7) 命中", EXIT_A in ks, str(EXIT_A))
check("按键搜索型 (mode=1) 命中", EXIT_B in ks, str(EXIT_B))
check("不换景的对象 (路牌) 不算出口", (1, 1) not in ks)
check("如何触发标成 near/search",
      {e["K"]: e["how"] for e in ex}.get(EXIT_A) == "near"
      and {e["K"]: e["how"] for e in ex}.get(EXIT_B) == "search")

n0 = o.pin_scan
o.scene_exits()
check("出口清单有缓存 (60s 内不重扫)", o.pin_scan == n0,
      "扫描次数 %d -> %d" % (n0, o.pin_scan))
o.scene_exits(fresh=True)
check("fresh=True 强制重扫", o.pin_scan > n0)

o2 = shadow(pin=7, exits=False)
check("读不到对象表 -> None (不能当成「没有出口」)",
      o2.scene_exits() is None)

print("\n=== 2. 门外推: 亮出来的出口都圈 pad=2 ===")
o = shadow(pin=7)
b0 = o.door_ban(0)
b2 = o.door_ban(2)
check("pin 时 door_ban(0) 至少含出口格", EXIT_A in b0 and EXIT_B in b0)
check("pad=2 圈出 5x5 (25 格/出口)",
      all((EXIT_A[0] + ua, EXIT_A[1] + ub) in b2
          for ua in range(-2, 3) for ub in range(-2, 3)))
check("pad=2 的格子数 = 2 个出口 x 25 = 50 (重叠按集合算)",
      len(b2) == 50, "实际 %d" % len(b2))
check("pad=2 不会把出口圈到 3 格外",
      (EXIT_A[0] + 3, EXIT_A[1]) not in b2)

o = shadow(pin=0)
check("不驻留时 door_ban 不含出口 (老行为不变)",
      EXIT_A not in o.door_ban(2) and EXIT_B not in o.door_ban(2))

print("\n=== 3. 门禁分级: 驻留时永不「无门禁」 ===")
o = shadow(pin=0)
ban0, tag0, r0 = o.graded_reach((0, 0))
# 注: 三档可达域一样大时父类"相等不替换", 所以小地图停在 pad=0 ——
# 真正的风险是**可达域变小之后**放宽到最后一档, 那条由下面的 pin 用例挡住。
check("不驻留 + 小地图 -> 停在 pad=0 (父类相等不替换)",
      tag0 == "pad=0", tag0)

o = shadow(pin=7)
ban1, tag1, r1 = o.graded_reach((0, 0))
check("驻留时最低只到「驻留pad=0」, 绝不撤门禁",
      tag1 == "驻留pad=0" and "无门禁" not in tag1, tag1)
check("驻留时禁入集合非空 (出口还在禁)", EXIT_A not in r1,
      "禁 %d 格" % len(ban1))

# 造一个"只有撤门禁才走得通"的局面: 1 格宽的走廊, 门卡在咽喉 (0,0)。
#   pad=0 把 (0,0) 禁掉 -> 走廊断成两段, 可达域掉一半;
#   无门禁 -> 全通。父类遇到这种就会放宽到最后一档 (10-20 漏门的口子)。
#   注: 必须把门放进 known_doors —— 父类的 door_ban 只认"踩过的门", 它压根
#   不知道 scene_info 里写着哪些出口, 这本身就是缺陷之一。
CORRIDOR = set((a, 0) for a in range(-4, 5))
o = shadow(pin=0, walk=CORRIDOR, exit_xy=((-4, 0), (4, 0)))
o.known_doors = {7: [(0, 0)]}
_, tagb, _ = o.graded_reach((-3, 0))
check("父类在门锁咽喉时会一路放宽到「无门禁」", tagb == "无门禁", tagb)

o = shadow(pin=7, walk=CORRIDOR, exit_xy=((-4, 0), (4, 0)))
o.known_doors = {7: [(0, 0)]}
_, tagp, _ = o.graded_reach((-3, 0))
check("★驻留模式同等情况仍不撤门禁", "无门禁" not in tagp, tagp)

o = shadow(pin=7, walk=set((a, b) for a in range(-10, 11)
                           for b in range(-10, 11)),
           exit_xy=((8, -8), (-9, 9)))
ban2, tag2, r2 = o.graded_reach((0, 0))
check("可达域够用时停在 pad=2 (最大的避让半径)",
      tag2 == "驻留pad=2", "%s 可达 %d 格" % (tag2, len(r2)))
check("pad=2 档确实把出口圈住了", EXIT_A in ban2 or (8, -8) in ban2)

print("\n=== 4. 落地核对: 被送出场景立刻进入「走回去」 ===")
o = shadow(pin=7, scene=7)
o._pin_scene_tick(7, "(启动)")
check("还在驻留场景 -> 不触发回场", o._pin_back is False)

o = shadow(pin=7, scene=5)
o._pin_scene_tick(5, "(踩门)")
check("被送出 -> 置 _pin_back", o._pin_back is True)
check("记一条「意外离开」日志",
      any("意外离开驻留场景 7" in m for m in o.logs), "; ".join(o.logs[-1:]))

o._pin_scene_tick(7, "(穿门)")
check("回到驻留场景 -> 清 _pin_back", o._pin_back is False)
check("记一条「已回到」日志",
      any("已回到驻留场景 7" in m for m in o.logs))

o = shadow(pin=0, scene=5)
o._pin_scene_tick(5, "(踩门)")
check("没开驻留 -> 什么都不做", o._pin_back is False and not o.logs)

o = shadow(pin=7, scene=5)
o.home = {"scene": 7, "anchor": (0, 0), "t_end": 1e9}
o._pin_scene_tick(5, "(踩门)")
check("驻留时清掉 mem2 的「回家」(两套走回机制不许打架)",
      o.home is None)

print("\n=== 5. 回得来: 按门路图找一步能回去的出口 ===")
o = shadow(pin=7, scene=5, links={"5:%d,%d" % EXIT_A: 7})
check("scene_links 里指向 pin 的出口被挑出来",
      o._pin_link_door((0, 0)) == EXIT_A, str(o._pin_link_door((0, 0))))

o = shadow(pin=7, scene=5, links={"9:%d,%d" % EXIT_A: 7})
check("别的场景的门路不认", o._pin_link_door((0, 0)) is None)

o = shadow(pin=7, scene=5, links={"5:%d,%d" % EXIT_A: 6})
check("通向别的场景的门路不认", o._pin_link_door((0, 0)) is None)

o = shadow(pin=7, scene=5, links={"5:999,999": 7})
check("门路指向地图外的格子 -> 忽略", o._pin_link_door((0, 0)) is None)

print("\n=== 6. 走回去的目标: 优先门路, 否则最近的出口 ===")
o = shadow(pin=7, scene=5, links={"5:%d,%d" % EXIT_B: 7})
t = o._pin_return_target((0, 0))
check("有门路时直奔那条门", t == EXIT_B, str(t))

o = shadow(pin=7, scene=5)
t = o._pin_return_target((0, 0))
check("没门路时挑最近的出口", t in (EXIT_A, EXIT_B), str(t))
check("目标不是脚下这一格", t != (0, 0))
check("回场时临时打开穿门 (否则踩门会被弹回)", o.open_doors is True)
check("记住用户原来的设置", o._open_doors_user is False)

o = shadow(pin=7, scene=5, exits=False)
o.known_doors = {5: [(2, -2)]}
check("对象表读不到 -> 退回实测门表",
      o._pin_return_target((0, 0)) == (2, -2))

o = shadow(pin=7, scene=5, exits=False)
t = o._pin_return_target((0, 0))
check("彻底没出口 -> 返回 None 并告警",
      t is None and any("找不到任何出口" in m for m in o.logs))

print("\n=== 7. pick_target: 回场目标压过一切 ===")
o = shadow(pin=7, scene=5, links={"5:%d,%d" % EXIT_B: 7})
o._pin_back = True
check("_pin_back 时 pick_target 直接给出口",
      o.pick_target() == EXIT_B, str(o.pick_target()))

o = shadow(pin=7, scene=5, links={"5:%d,%d" % EXIT_B: 7})
o._pin_back = False
o.elem_mode = False                  # 让流程停在「转父类」那一行, 不往下走
o.migrate = True
_orig4 = pal_bot_mem4.MemBot4.pick_target
pal_bot_mem4.MemBot4.pick_target = lambda self, exclude=None: None
try:
    o.pick_target()
finally:
    pal_bot_mem4.MemBot4.pick_target = _orig4
check("驻留时 pick_target 顺手复位 migrate", o.migrate is False)

print("\n=== 8. step: 驻留时迁移阈值永远 99999 ===")
_orig = pal_bot_mem4.MemBot4.step


def _fake_step(self, key):
    self.MIGRATE_AFTER = 150          # 父类的"刚进场景还没开打"分支
    return "moved"


pal_bot_mem4.MemBot4.step = _fake_step
try:
    o = shadow(pin=7)
    o.MIGRATE_AFTER = 150
    o.step("right")
    check("驻留 -> 走一步就把 150 顶回 99999", o.MIGRATE_AFTER == 99999,
          str(o.MIGRATE_AFTER))

    o = shadow(pin=0)
    o.MIGRATE_AFTER = 400
    o.step("right")
    check("不驻留 -> 保持父类的 150 (老行为)", o.MIGRATE_AFTER == 150,
          str(o.MIGRATE_AFTER))
finally:
    pal_bot_mem4.MemBot4.step = _orig

print("\n=== 9. _pilot_tick / _rope_hunt: 驻留时不许调度换场景 ===")
o = shadow(pin=7)
o.pilot = FakePilot()
o._pilot_tick()
check("驻留时不问「这场景值不值得待」", o.pilot.n_decide == 0)

o = shadow(pin=0)
o.pilot = FakePilot()
o._pilot_tick()
check("不驻留时照旧问调度器", o.pilot.n_decide == 1)

print("\n=== 10. 配置面 ===")
check("botcfg 默认值 pin_scene = 0 (开箱即「不驻留」)",
      botcfg.DEFAULTS.get("pin_scene") == 0, str(botcfg.DEFAULTS.get("pin_scene")))
check("resolve_int 能解析 pin_scene",
      isinstance(botcfg.resolve_int("pin_scene")[0], int))
check("环境变量名 = PAL_PIN_SCENE",
      botcfg.env_name("pin_scene") == "PAL_PIN_SCENE")

import subprocess                                    # noqa: E402
_env = dict(os.environ, PAL_PIN_SCENE="-1", PYTHONIOENCODING="utf-8")
_r = subprocess.run([sys.executable, "-c",
                     "import botcfg;print(botcfg.resolve_int('pin_scene')[0])"],
                    cwd=BASE, env=_env, capture_output=True, text=True)
check("环境变量 PAL_PIN_SCENE=-1 能覆盖 (-1 = 钉启动场景)",
      _r.stdout.strip() == "-1", _r.stdout.strip() + _r.stderr[:80])

_env = dict(os.environ, PAL_PIN_SCENE="7", PYTHONIOENCODING="utf-8")
_r = subprocess.run([sys.executable, "-c",
                     "import botcfg;print(botcfg.resolve_int('pin_scene')[0])"],
                    cwd=BASE, env=_env, capture_output=True, text=True)
check("环境变量 PAL_PIN_SCENE=7 生效", _r.stdout.strip() == "7",
      _r.stdout.strip())

print("\n=== 11. kof 往返 (出口坐标算得对) ===")
for k in ((0, 0), (-79, 89), (2, -2), (-3, 3), (14, -7)):
    check("kof(kof_inv(%s)) == %s" % (k, k), kof(*kof_inv(*k)) == k,
          str(kof(*kof_inv(*k))))

print("\n=== 12. 常量口径 ===")
check("出口禁区半径 = 2 格 (只禁门格挡不住 1~2 格外的触发)", MemBot5.PIN_BAN_PAD == 2)
check("连续回场失败上限 = 5 (别把时间全耗在门上)", MemBot5.PIN_BACK_MAX == 5)

print("\n=== 13. 自动钉住「启动时所在的场景」(pin_scene = -1) ===")
o = shadow(pin=0)
o._pin_auto = True
o._pin_scene_tick(7, "(启动)")
check("第一次落地就钉住该场景 (不用知道场景号)", o.pin_scene == 7,
      str(o.pin_scene))
check("日志说明钉的是启动场景",
      any("已自动钉住当前场景 7" in m for m in o.logs))

o._pin_scene_tick(5, "(踩门)")
check("之后就按普通驻留规则走 (离开 -> 回场)", o._pin_back is True)

o = shadow(pin=0)
o._pin_auto = False
o._pin_scene_tick(7, "(启动)")
check("不勾驻留 -> 不钉任何场景也不回场",
      o.pin_scene == 0 and o._pin_back is False)

print("\n" + "=" * 56)
print("通过 %d 项, 失败 %d 项" % (len(OK), len(BAD)))
for b in BAD:
    print("  !! %s" % b)
sys.exit(1 if BAD else 0)
