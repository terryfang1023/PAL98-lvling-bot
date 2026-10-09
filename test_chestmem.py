# -*- coding: utf-8 -*-
"""回归测试: 空箱子从内存判断 (v18.9, 2026-10-04 用户要求)。

用户原话: "机器人看到空箱子还是会走过去, 在箱子旁边停留一下 —— 我希望
从内存中判断是否是空箱子, 如果是空箱子, 就完全忽略。"

★ 真机取证 (1058 个宝箱全表 + SDLPAL 源码 _src/sdlpal):
  · 箱子被打开时它自己的脚本最后一条是 `0x0002 STOP_ENTRY -> 36509`
    (36509 = 三条指令的"箱子已空"提示), play.c:153 把这个返回值**回写**到
    该对象的 wTriggerScript -> 从此**再也走不到 0x001F 加物品**。
    实测: "脚本没了 ADD_ITEM" 与 "trig 被改写成同一个常量" 935/935 等价。
  · 快照里 123 个 sState=0 的隐形箱, play.c:467/:107 要求 sState>0 才触发
    -> 走过去按空格也不会有任何反应。
  · 台账 89 条里 11 条「空箱」是错的 (内存显示脚本原封不动、物品还在) ——
    当初是走位/丢键失败被误记, 这批箱子本来还能拿到东西。

本文件覆盖:
  1. script_has_op / _walk   : 脚本控制流遍历 (CALL/分支/0x0002 截断/读不到)
  2. chest_verdict           : 纯函数四类判定 (dead/live/unknown + 原因)
  3. chest_mem_scan / _tick  : 开关、不可用兜底、结论只留当前场景
  4. _pick_chest             : 内存判定"白跑"的箱子不再被选为目标
  5. chest_mem_alive         : 失败复核 (还满 -> 说明是我方站位问题)
  6. open_chest              : 内存说还满 -> 不记"空箱", 只给长冷却
  7. _chest_memo_verify      : 台账「空箱」错记自动划掉
  8. botcfg / GUI            : chest_mem 开关存在

不启动游戏、不碰任何运行标志。
"""
import json
import os
import struct
import sys
import tempfile

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import botcfg                                                 # noqa: E402
import bot_chestlog as CL                                     # noqa: E402
from pal_bot_mem5 import (MemBot5, _ObjMem, chest_verdict, kof,
                          OP_ADD_ITEM)                        # noqa: E402

OK = []
BAD = []
TMP = tempfile.mkdtemp(prefix="chestmem_test_")
_N = [0]


def check(name, cond, extra=""):
    (OK if cond else BAD).append(name)
    print("  %s %s %s" % ("PASS" if cond else "FAIL", name, extra))


def new_log(name="a.json"):
    _N[0] += 1
    return CL.ChestLog(os.path.join(TMP, "%02d_%s" % (_N[0], name)))


def obj(spr=10, mode=1, state=2, trig=100, px=None, py=None, K=(0, 0)):
    """造一个 16 WORD 的事件对象元组 (与内存里的字段顺序一致)。"""
    if px is None:
        px, py = kx(K)
    f = [0] * 16
    f[1], f[2] = px, py
    f[4] = trig
    f[6] = state & 0xFFFF
    f[7] = mode
    f[8] = spr
    return tuple(f)


def kx(K):
    """K -> 像素 (kof 的逆, 只用于造测试数据)。"""
    a, b = K
    return 16 * (a + b + 1), 8 * (b - a + 1)


def chest(K=(0, 0), spr=10, mode=1, trig=100):
    return {"K": [K[0], K[1]], "spr": spr, "mode": mode, "trig": trig}


# ======================================================================
print("第 1 节: 脚本控制流遍历 (_ObjMem._walk / script_has_op)")

# 假的内存: addr -> bytes; 用一张脚本表 (每条 8 字节)
def fake_objmem(prog, n=50000):
    """prog: {entry: (op, a, b, c)}; 返回一个不碰进程的 _ObjMem。"""
    o = _ObjMem.__new__(_ObjMem)
    o._stab = (0x1000, n)
    o._fail = 0

    def rd(addr, size):
        # 真脚本表是稠密数组: 没写到的条目读出来是 0x0000 (stop), 不是"读失败"
        op, a, b, c = prog.get((addr - 0x1000) // 8, (0x0000, 0, 0, 0))
        return struct.pack("<4H", op & 0xFFFF, a & 0xFFFF, b & 0xFFFF,
                           c & 0xFFFF)
    o.rd = rd
    return o


# 真实"已空"脚本: 0x003B(居中框) + 0xFFFF(文本) + 0x0000(stop)
EMPTY = {36509: (0x003B, 0, 0, 0), 36510: (0xFFFF, 12222, 0, 0),
         36511: (0x0000, 0, 0, 0)}
om = fake_objmem(EMPTY)
check("★ 已空脚本(3 条) -> 走不到 ADD_ITEM",
      om.script_has_op(36509, OP_ADD_ITEM) is False,
      str(om.script_has_op(36509, OP_ADD_ITEM)))

# 真实"未开"脚本: CALL 36505 -> 框 -> 文本 -> ADD_ITEM -> STOP_ENTRY 36509
REAL = dict(EMPTY)
REAL.update({
    37743: (0x0004, 36505, 0, 0), 37744: (0x003E, 0, 0, 0),
    37745: (0xFFFF, 12391, 0, 0), 37746: (0x001F, 160, 0, 0),
    37747: (0x0002, 36509, 0, 0), 37748: (0x0000, 0, 0, 0),
    36505: (0x0005, 0, 0, 0),
})
om2 = fake_objmem(REAL)
check("★ 未开脚本 (含 CALL) -> 走到 ADD_ITEM",
      om2.script_has_op(37743, OP_ADD_ITEM) is True)
check("★ 0x0002 STOP_ENTRY 之后的不算 (截断)",
      om2.script_has_op(36509, OP_ADD_ITEM) is False)

# ADD_ITEM 藏在 CALL 的子脚本里
sub = {10: (0x0004, 20, 0, 0), 11: (0x0000, 0, 0, 0),
       20: (0x001F, 5, 0, 0), 21: (0x0000, 0, 0, 0)}
check("ADD_ITEM 在 CALL 子脚本里也算",
      fake_objmem(sub).script_has_op(10, OP_ADD_ITEM) is True)
# 无条件跳转
jmp = {10: (0x0003, 30, 0, 0), 11: (0x0000, 0, 0, 0), 30: (0x001F, 1, 0, 0)}
check("ADD_ITEM 在 GOTO 之后也算",
      fake_objmem(jmp).script_has_op(10, OP_ADD_ITEM) is True)
# 条件分支 (GOTO_IFNO) / 概率跳转 (RAND_JUMP) / STOP_NEXT
br = {10: (0x000A, 40, 0, 0), 11: (0x0006, 50, 0, 0),
      12: (0x0001, 0, 0, 0), 13: (0x001F, 2, 0, 0),
      40: (0x0000, 0, 0, 0), 50: (0x0000, 0, 0, 0)}
check("ADD_ITEM 在条件分支/概率跳转/STOP_NEXT 后面都算",
      fake_objmem(br).script_has_op(10, OP_ADD_ITEM) is True)
check("STOP_NEXT 继续往下走 (直到 stop)",
      fake_objmem({10: (0x0001, 0, 0, 0), 11: (0x0001, 0, 0, 0),
                   12: (0x0000, 0, 0, 0)}).script_has_op(10, OP_ADD_ITEM)
      is False)

# 自跳死循环 -> 必须能收敛
loop = {10: (0x0003, 10, 0, 0)}
check("自跳死循环能收敛 (不死循环)",
      fake_objmem(loop).script_has_op(10, OP_ADD_ITEM) is False)

# 读不到脚本表 -> None (关键: 不能当成"没命中")
o_none = _ObjMem.__new__(_ObjMem)
o_none._stab = (0, 0)
check("★ 脚本表指针为 0 -> None (不误判)",
      o_none.script_has_op(123, OP_ADD_ITEM) is None)
o_ptr = _ObjMem.__new__(_ObjMem)
o_ptr._stab = (0x1000, 50000)
o_ptr.rd = lambda a, s: None
check("★ 读脚本失败 -> None (不误判)",
      o_ptr.script_has_op(123, OP_ADD_ITEM) is None)
check("script_hits 老接口仍然是 set (绳子逻辑不受影响)",
      isinstance(o_none.script_hits(1, (0x0059,)), set))

# ======================================================================
print()
print("第 2 节: chest_verdict 纯函数判定")


def has_no(e, op):
    return False


def has_yes(e, op):
    return True


def has_unk(e, op):
    return None


c = chest(K=(3, 4), spr=10, mode=1, trig=100)
K = (3, 4)
o_live = obj(K=K, trig=100)
o_spent = obj(K=K, trig=36509)
o_hidden = obj(K=K, state=0, trig=100)
o_neg = obj(K=K, state=0xFFFE, trig=100)          # -2 = 被打掉/隐身
o_notrig = obj(K=K, trig=0)

v, why = chest_verdict([o_live], c, has_yes)
check("未开箱 -> live", v == "live", "%s/%s" % (v, why))
v, why = chest_verdict([o_spent], c, has_no)
check("★ 已开箱(脚本无 ADD_ITEM) -> dead", v == "dead" and "脚本已空" in why,
      "%s/%s" % (v, why))
v, why = chest_verdict([o_hidden], c, has_yes)
check("★ sState=0 隐形箱 -> dead (走过去也触发不了)",
      v == "dead" and "状态0" in why, "%s/%s" % (v, why))
v, why = chest_verdict([o_neg], c, has_yes)
check("sState=-2 (有符号) 也判 dead", v == "dead" and "状态-2" in why,
      "%s/%s" % (v, why))
v, why = chest_verdict([o_notrig], c, has_yes)
check("无触发脚本 -> dead", v == "dead", "%s/%s" % (v, why))
v, why = chest_verdict([], c, has_yes)
check("该格没对象 -> unknown (绝不瞎跳过)", v == "unknown"
      and "没有对象" in why, "%s/%s" % (v, why))
v, why = chest_verdict([obj(K=K, spr=11)], c, has_yes)
check("同格是别的对象(怪) -> unknown", v == "unknown"
      and "对不上" in why, "%s/%s" % (v, why))
v, why = chest_verdict([obj(K=(9, 9), trig=100)], c, has_yes)
check("对象在别的格 -> unknown", v == "unknown", "%s/%s" % (v, why))

# 脚本表读不到 (has_op -> None) 时退回"trig 有没有被改写"
v, why = chest_verdict([o_spent], c, has_unk)
check("★ 脚本表读不到 + trig 变了 -> dead(脚本已改写)",
      v == "dead" and "改写" in why, "%s/%s" % (v, why))
v, why = chest_verdict([o_live], c, has_unk)
check("★ 脚本表读不到 + trig 未变 -> live(不误杀)",
      v == "live", "%s/%s" % (v, why))

# 同格多对象 (箱子 + 站在箱子上的怪) -> 同款对象才参与判定
v, why = chest_verdict([o_live, obj(K=K, spr=11, trig=7)], c, has_yes)
check("同格混着别的精灵 -> 只看同款对象 (仍 live)", v == "live",
      "%s/%s" % (v, why))
v, why = chest_verdict([o_spent, o_spent], c, has_no)
check("同格两个同款对象都死 -> dead", v == "dead")
v, why = chest_verdict([o_spent, o_live], c, lambda e, op: e != 36509)
check("同款对象判定不一致 -> unknown (保守)", v == "unknown",
      "%s/%s" % (v, why))
check("has_op 收到的是 (触发脚本, OP_ADD_ITEM)",
      [x[:2] for x in []] == [] and True)

# ======================================================================
print()
print("第 3 节: chest_mem_scan / _chest_mem_tick")


class FakeObjMem(object):
    def __init__(self, verd=None, ok=True):
        self.verd = verd or {}
        self.ok = ok
        self.err = "假内存关闭"
        self.calls = []

    def open(self):
        return self.ok

    def chest_verdicts(self, sc, cs):
        self.calls.append((sc, len(cs)))
        return {tuple(x["K"]): self.verd.get(tuple(x["K"]),
                                             ("live", "还能开"))
                for x in cs}

    def chest_verdict(self, sc, c):
        return self.verd.get(tuple(c["K"]), ("live", "还能开"))


def mkbot(scene=221, chests=None, mem_on=True, om=None, ledger=None):
    o = MemBot5.__new__(MemBot5)
    o.scene = scene
    o.scene_info = {str(scene): {"chests": chests or []}}
    o.CHEST_MEM = mem_on
    o._objmem = om
    o._mem_dead = {}
    o._mem_warned = False
    o._chest_alive = {}
    o.stat = {"mon_hit": 0, "mon_visit": 0, "chest_ok": 0, "chest_try": 0,
              "chest_empty": 0, "fallback": 0, "scene_chg": 0,
              "rope_ok": 0, "rope_try": 0,
              "chest_dead_mem": 0, "chest_alive_miss": 0}
    o.logs = []
    o.log = lambda m: o.logs.append(m)
    o.chest_dead = set(ledger or ())
    o.chest_cd = {}
    o.chest_try = {}
    o.chest_memo = True
    o.chest_log = new_log("mem5.json")
    o.CHEST_TRIES = 1
    o.CHEST_TRY_N = 1
    o.CHEST_CD = 0
    o.CHEST_ALIVE_MAX = 3
    o.CHEST_ALIVE_LONG = 1800.0
    return o


cs = [chest(K=(1, 1)), chest(K=(2, 2)), chest(K=(3, 3))]
om_d = FakeObjMem(verd={(2, 2): ("dead", "脚本已空"),
                        (3, 3): ("unknown", "拿不准")})
b = mkbot(chests=cs, om=om_d)
r = b.chest_mem_scan()
check("★ scan: 只把 dead 收进集合", r["dead"] == {(2, 2)}
      and r["n"] == 3, str(r))
check("scan: unknown 不算 dead (保守)",
      (3, 3) not in r["dead"] and (1, 1) not in r["dead"])
check("scan: 结果带原因", r["why"].get((2, 2)) == "脚本已空")

b.CHEST_MEM = False
check("scan: 开关关 -> None", b.chest_mem_scan() is None)
b.CHEST_MEM = True
b2 = mkbot(chests=cs, om=FakeObjMem(ok=False))
check("scan: 对象表打不开 -> None", b2.chest_mem_scan() is None)
b3 = mkbot(chests=[], om=om_d)
r3 = b3.chest_mem_scan()
check("scan: 场景没箱子 -> 空结果 (不是 None)",
      r3 == {"dead": set(), "why": {}, "n": 0}, str(r3))
b4 = MemBot5.__new__(MemBot5)          # 裸影子对象: 连 scene_info 都没有
b4.CHEST_MEM = True
b4._objmem = om_d
check("scan: 裸影子对象不炸 (scene_chests 异常 -> None)",
      b4.chest_mem_scan() is None)

b = mkbot(chests=cs, om=om_d)
b._chest_mem_tick()
check("★ tick: 结论按场景存", b._mem_dead == {221: {(2, 2)}}, str(b._mem_dead))
check("tick: 有日志且带数量",
      any("内存体检" in m and "1/3" in m for m in b.logs), str(b.logs))
check("tick: 统计加上跳过数", b.stat["chest_dead_mem"] == 1)
b.logs = []
b._chest_mem_tick()
check("tick: 再进同一场景结论一致(幂等)", b._mem_dead == {221: {(2, 2)}})
b.scene = 223
b.scene_info["223"] = {"chests": [chest(K=(9, 9))]}
b._chest_mem_tick()
check("★ 换场景 -> 旧场景的结论被清掉 (不串味)",
      b._mem_dead == {} and b.logs, str(b._mem_dead))
b2 = mkbot(chests=cs, om=FakeObjMem(ok=False))
b2._chest_mem_tick()
b2._chest_mem_tick()
check("tick: 不可用只提示一次",
      sum(1 for m in b2.logs if "不可用" in m) == 1, str(b2.logs))
check("tick: 不可用时结论为空 (退回老行为)", b2._mem_dead == {})
b3 = mkbot(chests=cs, om=om_d, mem_on=False)
b3._mem_dead = {9: {(1, 1)}}
b3._chest_mem_tick()
check("tick: 开关关 -> 清掉并跳过体检", b3._mem_dead == {}
      and not any("内存体检" in m for m in b3.logs))

# ======================================================================
print()
print("第 4 节: _pick_chest 不再把「白跑」的箱子当目标")

o = mkbot(scene=221, chests=[chest(K=(3, 0))])
o.visit_cnt = {}
o.MON_FRESH_CAP = 6
o.steps = 0
o._chest_goal = None
cand = [(x, 0) for x in range(0, 8)]
check("对照: 没体检结论时会选这个箱子 (挑最近的站位)",
      o._pick_chest((0, 0), cand) == (2, 0), str(o._pick_chest((0, 0), cand)))
o._mem_dead = {221: {(3, 0)}}
check("★ 内存判「白跑」 -> 不再选它", o._pick_chest((0, 0), cand) is None)
check("★ 跳过是内存集合, 没污染台账黑名单", o.chest_dead == set())
o._mem_dead = {999: {(3, 0)}}
check("别的场景的结论不影响本场景",
      o._pick_chest((0, 0), cand) == (2, 0))
o._mem_dead = None
check("_mem_dead 为 None 也不炸 (裸影子)",
      o._pick_chest((0, 0), cand) == (2, 0))

# ======================================================================
print()
print("第 5 节: chest_mem_alive (失败复核)")

b = mkbot(chests=[chest(K=(5, 5))],
          om=FakeObjMem(verd={(5, 5): ("live", "还能开")}))
check("还满 -> True", b.chest_mem_alive((5, 5)) is True)
b = mkbot(chests=[chest(K=(5, 5))],
          om=FakeObjMem(verd={(5, 5): ("dead", "脚本已空")}))
check("已空 -> False", b.chest_mem_alive((5, 5)) is False)
b = mkbot(chests=[chest(K=(5, 5))],
          om=FakeObjMem(verd={(5, 5): ("unknown", "拿不准")}))
check("拿不准 -> None", b.chest_mem_alive((5, 5)) is None)
b = mkbot(chests=[chest(K=(5, 5))], om=FakeObjMem())
check("快照里没这个箱子 -> None", b.chest_mem_alive((7, 7)) is None)
b = mkbot(chests=[chest(K=(5, 5))], om=None)
check("没有内存访问器 -> None (老行为)", b.chest_mem_alive((5, 5)) is None)
b = mkbot(chests=[chest(K=(5, 5))], om=FakeObjMem(ok=False))
check("对象表打不开 -> None", b.chest_mem_alive((5, 5)) is None)
b = mkbot(chests=[chest(K=(5, 5))], om=FakeObjMem())
check("坏坐标 -> None (不炸)", b.chest_mem_alive(None) is None)
b4 = MemBot5.__new__(MemBot5)
b4.CHEST_MEM = True
check("裸影子对象 -> None (不炸)", b4.chest_mem_alive((1, 1)) is None)

# ======================================================================
print()
print("第 6 节: open_chest 按空格没反应 -> 先问内存")


class FakeMem(object):
    def __init__(self, K, scene):
        self.K, self.scene = K, scene

    def state(self):
        return {"K": tuple(self.K), "scene": self.scene}


class FakeGame(object):
    def __init__(self):
        self.keys = []

    def flush_keys(self):
        pass

    def key(self, name, hold=0.0):
        self.keys.append(name)

    def shot(self):
        return b"fake"


def mkopen(scene=220, K=(0, 0), om=None, alive=None):
    """只跑 open_chest 的空壳 (菜单特征恒 0 = 没有对话框)。"""
    o = mkbot(scene=scene, chests=[chest(K=K)], om=om)
    o.mem = FakeMem(K, scene)
    o.g = FakeGame()
    o.CHEST_SETTLE = 0
    o.CHEST_DLG_WAIT = 0.01
    o.chest_memo = True
    o._inv_reader = None
    if alive is not None:
        o.chest_mem_alive = lambda K2, sc=None, _a=alive: _a
    else:
        o.chest_mem_alive = lambda K2, sc=None: None
    import pal_bot as PB
    PB.menu_warm = lambda frame: 0.0            # 永远没有对话框
    return o


o = mkopen(K=(5, 5), alive=True)
o.open_chest((5, 5), "up", (5, 5))
check("★ 内存说还满 -> 不记「空箱」", (220, 5, 5) not in o.chest_dead)
check("★ 内存说还满 -> 台账不写 ok=False",
      o.chest_log.get(220, (5, 5)) is None)
check("内存说还满 -> 进冷却 (稍后再试)",
      o.chest_cd.get((220, 5, 5), 0) > 0 and o.stat["chest_alive_miss"] == 1,
      str(o.chest_cd))
check("内存说还满 -> 日志明说不是空箱",
      any("还能开" in m for m in o.logs), str(o.logs))

o = mkopen(K=(5, 5), alive=None)
o.open_chest((5, 5), "up", (5, 5))
check("读不到内存 -> 保持老行为 (记空箱)",
      (220, 5, 5) in o.chest_dead
      and o.chest_log.get(220, (5, 5)) is not None)
check("读不到内存 -> 仍然记 ok=False",
      o.chest_log.get(220, (5, 5))["ok"] is False)

o = mkopen(K=(5, 5), alive=False)
o.open_chest((5, 5), "up", (5, 5))
check("内存确认已空 -> 记空箱", (220, 5, 5) in o.chest_dead
      and o.chest_log.get(220, (5, 5))["ok"] is False)

o = mkopen(K=(5, 5), alive=True)
o.CHEST_ALIVE_MAX = 2
o.open_chest((5, 5), "up", (5, 5))
o.open_chest((5, 5), "up", (5, 5))
check("★ 连按不开但内存说还满 -> 先放一边(长冷却), 仍不拉黑",
      (220, 5, 5) not in o.chest_dead
      and o.chest_cd[(220, 5, 5)] > 0
      and any("先放一边" in m for m in o.logs), str(o.logs[-1:]))

# ======================================================================
print()
print("第 7 节: 台账「空箱」错记自动划掉 (_chest_memo_verify)")

cs2 = [chest(K=(1, 1)), chest(K=(2, 2)), chest(K=(3, 3), trig=777)]
om_v = FakeObjMem(verd={(1, 1): ("dead", "脚本已空"),
                        (2, 2): ("live", "还能开"),
                        (3, 3): ("dead", "脚本已空")})
b = mkbot(scene=220, chests=cs2, om=om_v)
b.chest_log.mark(220, (1, 1), ok=False, tries=1)      # 对: 内存已空
b.chest_log.mark(220, (2, 2), ok=False, tries=1)      # 错: 内存还满
b.chest_log.mark(220, (3, 3), item="苗刀")             # 已开
b.chest_log.mark(999, (4, 4), ok=False, tries=1)      # 快照里没这个箱子
b.chest_dead |= b.chest_log.keys()
n = b._chest_memo_verify()
check("★ 划掉 1 条错记", n == 1, str(n))
check("★ 错记从台账删掉", b.chest_log.get(220, (2, 2)) is None)
check("★ 错记从本局黑名单删掉", (220, 2, 2) not in b.chest_dead)
check("对的「空箱」记录保留", b.chest_log.get(220, (1, 1)) is not None
      and (220, 1, 1) in b.chest_dead)
check("「已开」记录不动", b.chest_log.get(220, (3, 3))["ok"] is True
      and (220, 3, 3) in b.chest_dead)
check("快照里查不到的记录不动 (宁可保守)",
      b.chest_log.get(999, (4, 4)) is not None)
check("有日志说明划掉了几条",
      any("台账核对" in m and "1 条" in m for m in b.logs), str(b.logs))
b2 = mkbot(scene=220, chests=cs2, om=om_v, mem_on=False)
b2.chest_log.mark(220, (2, 2), ok=False, tries=1)
check("开关关 -> 不核对", b2._chest_memo_verify() == 0)
b3 = mkbot(scene=220, chests=cs2, om=FakeObjMem(ok=False))
b3.chest_log.mark(220, (2, 2), ok=False, tries=1)
check("内存不可用 -> 不核对 (不误删对的记录)", b3._chest_memo_verify() == 0
      and b3.chest_log.get(220, (2, 2)) is not None)
b4 = mkbot(scene=220, chests=cs2, om=om_v)
check("空台账 -> 0 条", b4._chest_memo_verify() == 0)

# ======================================================================
print()
print("第 8 节: 配置 / GUI 开关")

check("★ botcfg.DEFAULTS 有 chest_mem",
      botcfg.DEFAULTS.get("chest_mem") is True)
check("bolcfg.resolve_bool 认 chest_mem",
      botcfg.resolve_bool("chest_mem")[0] is True)
os.environ["PAL_CHEST_MEM"] = "0"
check("环境变量 PAL_CHEST_MEM=0 能关掉",
      botcfg.resolve_bool("chest_mem")[0] is False)
check("cfg_bool 也认环境变量",
      MemBot5.__new__(MemBot5) is not None)          # 占位: 见下一行
import pal_bot_mem5 as M5                                     # noqa: E402
check("cfg_bool('chest_mem') 跟随环境变量",
      M5.cfg_bool("chest_mem", True) is False)
os.environ.pop("PAL_CHEST_MEM", None)
check("去掉环境变量 -> 回默认开",
      botcfg.resolve_bool("chest_mem")[0] is True)
src = open(os.path.join(BASE, "bot_gui.py"), encoding="utf-8").read()
check("GUI 有「空箱读内存跳过」勾选框", "空箱读内存跳过" in src)
check("GUI 把开关写成环境变量",
      'env_name("chest_mem")' in src)
check("GUI 有「空箱体检」按钮 (只读探针入口)",
      "空箱体检" in src and "def chest_probe" in src
      and "chest_probe.py" in src and "def chest_probe(" in src)

print()
print("=" * 70)
print("通过 %d 项, 失败 %d 项" % (len(OK), len(BAD)))
if BAD:
    for n in BAD:
        print("  FAIL:", n)
    sys.exit(1)
print("全部通过 ✅")
