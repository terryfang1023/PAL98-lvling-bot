# -*- coding: utf-8 -*-
"""回归测试: 走没走过的路 (寻路避重 + 怪点轮转 + 定期探索配额)。

背景 (2026-10-04 用户要求「优先走没有走过的路」):
  原来的寻路是无权 BFS, 代价恒为 1 -> 两条怪点之间**永远走同一条最短路**;
  怪点选择又是纯"最近优先" -> 死盯最近那一两只怪。而且"走过几次"这个信号
  其实只在巡逻兜底层计数, 主模式(要素巡回)压根不更新 -> 所有避重信号都是死的。

本文件覆盖四件事:
  D. visit_cnt 接到真实行走 + **按场景**存 (修跨场景串味)
  A. bfs_path 加权最短路 (等长/近似时长优先走生路), λ=0 可退回老行为
  B. 怪点分数制轮转 + 宝箱同距离 tie-break
  C. 定期探索配额 (每 N 场强制去一次没走过的区域)
  并在最后校验 botcfg 新键存在。

不启动游戏, 不写任何运行标志。
"""
import os
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE)
sys.path.insert(0, BASE)

import botcfg                                                  # noqa: E402
from pal_bot_mem import MemBot                                 # noqa: E402
from pal_bot_mem2 import MemBot2                               # noqa: E402
from pal_bot_mem5 import MemBot5, cheb, chest_face_key         # noqa: E402

OK = []
BAD = []


def check(name, cond, extra=""):
    (OK if cond else BAD).append(name)
    print("  %s %s %s" % ("PASS" if cond else "FAIL", name, extra))


print("=" * 70)
print("D. visit_cnt: 真实踏上才记账 + 按场景隔离")
print("=" * 70)


class SeqMem(object):
    """按序吐出快照; 用完之后一直吐最后一个 (模拟"停在那里不动")。"""

    def __init__(self, seq):
        self.seq = list(seq)
        self.i = 0

    def state(self):
        j = min(self.i, len(self.seq) - 1)
        self.i += 1
        return self.seq[j]


def mkstepper():
    """能跑真实 walk_path 的最小壳 (记账就在 walk_path 里, 不在 step 里)。"""
    o = MemBot.__new__(MemBot)
    o.TAP = 0.0
    o.STEP_TIMEOUT = 0.05
    o.scene = 7
    o.visited = set()
    o.visit_cnt = {}
    o.blocked = set()
    o.steps = 0
    o.steps_since_battle = 0
    o._last_move_t = 0.0          # 刻意设 0 -> blocked 分支跳过"记绕行"
    o._trace = []
    o.last_beat = time.time()
    o.battles_won = 0
    o.t0 = time.time()
    o.log_lines = 0
    o.log = lambda m: None
    o.maybe_fight = lambda: False
    o.g = type("G", (), {"tap": staticmethod(lambda k, t=0: None)})()
    return o


# walk_path 会先读一次 state() 当 cur, 然后 step() 内部再读两次
# (s0 起点 / s1 位移后) -> 三段快照。
o = mkstepper()
o.mem = SeqMem([{"K": (10, 20), "scene": 7},
                {"K": (10, 20), "scene": 7},
                {"K": (10, 20), "scene": 7}])
ok = o.walk_path([(10, 20), (11, 20)])
check("原地不动 -> walk_path 返回 False", ok is False, str(ok))
check("★ blocked 不记访问次数 (没踏上就不算)", o.visit_cnt == {},
      str(o.visit_cnt))
check("blocked 也不进 visited", o.visited == set(), str(o.visited))

o = mkstepper()
o.mem = SeqMem([{"K": (10, 20), "scene": 7},
                {"K": (10, 20), "scene": 7},
                {"K": (11, 20), "scene": 7}])
ok = o.walk_path([(10, 20), (11, 20)])
check("位移成功 -> walk_path 返回 True", ok is True, str(ok))
check("★ 踏上 (11,20) 记 1 次", o.visit_cnt.get((11, 20)) == 1,
      str(o.visit_cnt))
check("visited 同步记 (老行为不变)", (11, 20) in o.visited)

o.mem = SeqMem([{"K": (11, 20), "scene": 7},
                {"K": (11, 20), "scene": 7},
                {"K": (11, 20), "scene": 7}])
o.walk_path([(11, 20)])
check("单格路径 (无位移) 不记账", o.visit_cnt.get((11, 20)) == 1,
      str(o.visit_cnt))
o.mem = SeqMem([{"K": (10, 20), "scene": 7},
                {"K": (10, 20), "scene": 7},
                {"K": (11, 20), "scene": 7}])
o.walk_path([(10, 20), (11, 20)])
check("★ 再走一遍同一条路 -> 同一格累加到 2",
      o.visit_cnt.get((11, 20)) == 2, str(o.visit_cnt))
check("steps 也照常累加", o.steps == 2, str(o.steps))


def mkscene(bot_cls):
    b = bot_cls.__new__(bot_cls)
    b.scene_know = {}
    b.report_doors = {}
    b.report_map = {}
    b.doors, b.blocked, b.visited = set(), set(), set()
    b.startK = None
    b.map = None
    b.kwalk = lambda m=None: set()
    b.log = lambda m: None
    b.mem = type("M", (), {
        "state": staticmethod(lambda: {"K": (0, 0), "scene": 5}),
        "scene_map": {5: 1, 6: 1}})()
    return b


b = mkscene(MemBot)
b.enter_scene(5, "test")
b.visit_cnt[(1, 1)] = 3
check("场景 5 里记下 (1,1)=3", b.visit_cnt.get((1, 1)) == 3, str(b.visit_cnt))
b.enter_scene(6, "test")
check("★ 换到场景 6 后计数不串味 (旧实现是全局字典 -> 会串)",
      b.visit_cnt.get((1, 1)) is None, str(b.visit_cnt))
b.visit_cnt[(1, 1)] = 9
b.enter_scene(5, "test")
check("★ 回到场景 5 -> 自己那份记忆还在 (=3, 不是 6 的 9)",
      b.visit_cnt.get((1, 1)) == 3, str(b.visit_cnt))
check("visit_cnt 绑在 scene_know 里 (可跨往返保留)",
      "visit_cnt" in b.scene_know[5])

print("\n" + "=" * 70)
print("A. 寻路避重: 等长/近似时长优先走没走过的格")
print("=" * 70)


def mkpath(cls, ks, vc=None, lam=0.15):
    o = cls.__new__(cls)
    o.map = None
    o._ks = set(ks)
    o.kwalk = lambda m=None: o._ks
    o.blocked = set()
    o.doors = set()
    o.visit_cnt = dict(vc or {})
    o.REVISIT_LAMBDA = lam
    o.REVISIT_CAP = 4
    return o


# ---- 唯一通路: 再熟也必须走 ----
ks = {(0, 0), (1, 0), (2, 0), (1, 1)}
o = mkpath(MemBot2, ks, vc={(1, 0): 99, (2, 0): 99, (0, 0): 99})
p = o.bfs_path((0, 0), {(2, 0)}, allow_doors=True)
check("★ 唯一通路 -> 再熟也照走", p == [(0, 0), (1, 0), (2, 0)], str(p))

p0 = mkpath(MemBot2, ks).bfs_path((0, 0), {(2, 0)}, allow_doors=True)
check("visit_cnt 全空 -> 与旧版同解", p0 == [(0, 0), (1, 0), (2, 0)], str(p0))

o = mkpath(MemBot2, ks, lam=0.0)
pz = o.bfs_path((0, 0), {(2, 0)}, allow_doors=True)
check("λ=0 -> 退回老 BFS 分支", pz == [(0, 0), (1, 0), (2, 0)], str(pz))

# ---- 等长双路 (K 空间矩形): 熟路 vs 生路 ----
rect = {(0, 0), (1, 0), (1, 1), (0, 1)}
o = mkpath(MemBot2, rect, vc={(1, 0): 8})      # 上边那条路很熟
p = o.bfs_path((0, 0), {(1, 1)}, allow_doors=True)
check("★ 等长时优先走生路 (上下两条都是 2 步)",
      p == [(0, 0), (0, 1), (1, 1)], str(p))

o = mkpath(MemBot2, rect, vc={(0, 1): 8})      # 换生路一侧: 应当走熟的那条
p = o.bfs_path((0, 0), {(1, 1)}, allow_doors=True)
check("★ 对称: 另一边熟了就走这一边", p == [(0, 0), (1, 0), (1, 1)], str(p))

o = mkpath(MemBot2, rect)
p = o.bfs_path((0, 0), {(1, 1)}, allow_doors=True)
check("全生 -> 仍是 2 步最短路", p is not None and len(p) == 3, str(p))

# ---- λ 决定"愿意绕多远" ----
corr = {(0, 0), (1, 0), (2, 0), (3, 0), (4, 0),
        (0, 1), (1, 1), (2, 1), (3, 1), (4, 1)}
hot = {(1, 0): 9, (2, 0): 9, (3, 0): 9}
o = mkpath(MemBot2, corr, vc=hot, lam=0.15)
p = o.bfs_path((0, 0), {(4, 0)}, allow_doors=True)
check("小 λ: 只多 2 步的生路还压不过熟路 (效率优先)",
      p == [(0, 0), (1, 0), (2, 0), (3, 0), (4, 0)], str(p))

o = mkpath(MemBot2, corr, vc=hot, lam=1.0)
p = o.bfs_path((0, 0), {(4, 0)}, allow_doors=True)
check("★ 大 λ: 愿意绕 2 步走生路 (可调)",
      p == [(0, 0), (0, 1), (1, 1), (2, 1), (3, 1), (4, 1), (4, 0)], str(p))

# ---- 语义不变: dsts 顺序优先 + 不可达 ----
o = mkpath(MemBot2, ks)
p = o.bfs_path((0, 0), [(9, 9), (2, 0)], allow_doors=True)
check("dsts 里第一个可达的胜出 (优先级语义没变)",
      p == [(0, 0), (1, 0), (2, 0)], str(p))
p = o.bfs_path((0, 0), {(9, 9)}, allow_doors=True)
check("不可达 -> None", p is None, str(p))

o = mkpath(MemBot2, ks)
p = o.bfs_path((0, 0), {(2, 0)}, extra_blocked={(1, 0)}, allow_doors=True)
check("目标被 extra_blocked 挡住 -> None", p is None, str(p))

o = mkpath(MemBot2, ks, lam=0.15)
w = o._weighted_path((0, 0), (2, 0), bad={(1, 0)}, lam=0.15)
check("_weighted_path: 必经格被禁 -> None", w is None, str(w))
w = o._weighted_path((0, 0), (2, 0), bad=frozenset(), lam=0.15)
check("_weighted_path: bad 传 frozenset 也不炸",
      w == [(0, 0), (1, 0), (2, 0)], str(w))

print("\n" + "=" * 70)
print("B. 选点层: 怪点轮转 + 宝箱同距离 tie-break")
print("=" * 70)


def mkmons(vc=None, mons=((5, 0), (7, 0))):
    o = MemBot5.__new__(MemBot5)
    o.scene = 157
    o.mon_cd = {}
    o.mon_dead = set()
    o._mon_miss = {}
    o._pick_n5 = 0
    o.visit_cnt = dict(vc or {})
    o.logs = []
    o.log = lambda m: o.logs.append(m)
    o.scene_monsters = lambda sc=None: [
        {"K": [k[0], k[1]], "spr": 11, "mode": 5, "state": 2} for k in mons]
    return o


CANDALL = [(a, b) for a in range(-70, 71) for b in range(-70, 71)]
CUR = (0, 0)

o = mkmons()
t, mk = o._pick_monster(CUR, CANDALL)
check("visit_cnt 全空 -> 仍是最近的怪点 (旧行为)",
      mk == (5, 0) and t == (4, 0), "走位%s 怪点%s" % (t, mk))

o = mkmons(vc={(4, 0): 6})
t, mk = o._pick_monster(CUR, CANDALL)
check("★ 最近怪点的站位去过 6 次 -> 轮到旁边那只 (6 格, 2 格更远)",
      mk == (7, 0) and t == (6, 0), "走位%s 怪点%s" % (t, mk))

o = mkmons(vc={(4, 0): 1})
t, mk = o._pick_monster(CUR, CANDALL)
check("只去过 1 次 -> 还压不过 2 格距离 (软代价, 不是硬排除)",
      mk == (5, 0), "怪点%s" % (mk,))

o = mkmons(vc={(4, 0): 100})
t, mk = o._pick_monster(CUR, CANDALL)
check("★ 访问次数封顶 (100 次与 6 次同罚), 不会无限放大",
      mk == (7, 0), "怪点%s" % (mk,))

o = MemBot5.__new__(MemBot5)
o.MON_FRESH_W = 0.0
o.scene = 157
o.mon_cd = {}
o.mon_dead = set()
o._mon_miss = {}
o._pick_n5 = 0
o.visit_cnt = {(4, 0): 100}
o.log = lambda m: None
o.scene_monsters = lambda sc=None: [
    {"K": [5, 0], "spr": 11, "mode": 5, "state": 2},
    {"K": [7, 0], "spr": 11, "mode": 5, "state": 2}]
t, mk = o._pick_monster(CUR, CANDALL)
check("MON_FRESH_W=0 -> 关掉轮转, 回到纯最近", mk == (5, 0), "怪点%s" % (mk,))


def mkchest(vc=None):
    o = MemBot5.__new__(MemBot5)
    o.scene = 157
    o.chest_cd = {}
    o.chest_dead = set()
    o.steps = 0
    o._chest_goal = None
    o.visit_cnt = dict(vc or {})
    o.log = lambda m: None
    o.scene_chests = lambda sc=None: [
        {"K": [3, 0], "spr": 4, "mode": 0},
        {"K": [-3, 0], "spr": 4, "mode": 0}]
    return o


o = mkchest()
t = o._pick_chest(CUR, CANDALL)
check("两个等距宝箱 -> 取清单里先出现的", t == (2, 0), str(t))

o = mkchest(vc={(2, 0): 3})
t = o._pick_chest(CUR, CANDALL)
check("★ 等距时优先没走过的站位 (tie-break, 不牺牲距离)",
      t == (-2, 0), str(t))

o = mkchest(vc={(-2, 0): 3})
t = o._pick_chest(CUR, CANDALL)
check("对称: 另一边熟了就走这一边", t == (2, 0), str(t))

o = mkchest(vc={(2, 0): 3})
t = o._pick_chest(CUR, CANDALL, max_d=1)
check("max_d 过滤仍然生效 (顺路开箱语义没变)", t is None, str(t))

print("\n" + "=" * 70)
print("C. 定期探索配额: 每 N 场强制去一次生区")
print("=" * 70)


def mkexp(vc=None, on=True, every=25):
    o = MemBot5.__new__(MemBot5)
    o.scene = 157
    o.steps = 0
    o.battles_won = 0
    o.visit_cnt = dict(vc or {})
    o.explore = on
    o.EXPLORE_EVERY_BATTLES = every
    o._explore_at = None
    o._explore_done = 0.0
    o._explore_goal = None
    o.logs = []
    o.log = lambda m: o.logs.append(m)
    return o


o = mkexp()
check("未武装的第一轮不触发 (只负责武装)",
      o._explore_pick(CUR, CANDALL) is None and o._explore_at == 25,
      "at=%s" % (o._explore_at,))

o.battles_won = 25
t = o._explore_pick(CUR, CANDALL)
check("★ 攒够 25 场 -> 触发探索", t is not None and cheb(t, CUR) >= 12,
      str(t))
check("触发后重新武装 (下一个周期)", o._explore_at == 50,
      "at=%s" % (o._explore_at,))
check("★ 目标是**远端**的生格 (才叫走出去), 不是脚边",
      cheb(t, CUR) >= MemBot5.EXPLORE_MIN_D, "距 %d" % cheb(t, CUR))

check("在途目标沿用 (不每 tick 重选)",
      o._explore_pick(CUR, CANDALL) == t)

check("冷却期内不重复触发",
      o._explore_pick(CUR, CANDALL) == t or o._explore_goal is None)

t_arrive = o._explore_pick(t, CANDALL)
check("★ 抵达后清空目标", o._explore_goal is None)
check("抵达后不立刻再探索 (配额已顺延)", t_arrive is None, str(t_arrive))

o.battles_won = 400
check("冷却期(EXPLORE_MIN_GAP)内即使场次够了也不触发",
      o._explore_pick(CUR, CANDALL) is None)
o._explore_done = time.time() - o.EXPLORE_MIN_GAP - 5.0
t2 = o._explore_pick(CUR, CANDALL)
check("★ 冷却过后可再触发", t2 is not None, str(t2))

o = mkexp()
o._explore_at = 0
o.battles_won = 99
o._explore_done = time.time()
o._explore_goal = (157, (5, 5), time.time(), 0)
check("在途目标还在 -> 不重选", o._explore_pick(CUR, CANDALL) == (5, 5))

o = mkexp()
o._explore_at = 999999
o._explore_goal = (157, (5, 5), time.time() - 99, 0)
check("★ 目标超时 -> 放弃, 回落常规刷怪",
      o._explore_pick(CUR, CANDALL) is None and o._explore_goal is None)

o = mkexp()
o._explore_goal = (157, (5, 5), time.time() - 20, 0)
o._explore_at = 999999
o.steps = 0
check("★ 目标在途卡死(步数零增长) -> 提前放弃",
      o._explore_pick(CUR, CANDALL) is None and o._explore_goal is None)

o = mkexp()
o._explore_goal = (999, (5, 5), time.time(), 0)
o._explore_at = 999999
check("换场景了 -> 旧探索目标作废",
      o._explore_pick(CUR, CANDALL) is None and o._explore_goal is None)

o = mkexp()
o._explore_at = 0
o.battles_won = 99
o.visit_cnt = dict((c, 1) for c in CANDALL)
check("★ 本场景已走遍 (没有 visit_cnt=0 的格) -> 不探索, 顺延下一周期",
      o._explore_pick(CUR, CANDALL) is None and o._explore_at == 124,
      "at=%s" % (o._explore_at,))

o = mkexp()
o._explore_at = 0
o.battles_won = 99
check("★ 生格都在脚边(距 < EXPLORE_MIN_D) -> 不值得专门跑一趟",
      o._explore_pick(CUR, [(a, b) for a in range(-3, 4)
                            for b in range(-3, 4)]) is None)

check("开关关掉 -> 永远不探索",
      mkexp(on=False)._explore_pick(CUR, CANDALL) is None)
check("EXPLORE_EVERY_BATTLES=0 -> 关掉",
      mkexp(every=0)._explore_pick(CUR, CANDALL) is None)

print("\n" + "=" * 70)
print("配置: botcfg 新键 + 默认值自洽")
print("=" * 70)
for k in ("revisit_lambda", "explore", "explore_every"):
    check("botcfg.DEFAULTS 有 %s" % k, k in botcfg.DEFAULTS)
check("revisit_lambda 默认 >0 (开箱即开)",
      botcfg.as_float(botcfg.DEFAULTS.get("revisit_lambda"), 0) > 0)
check("explore 默认开", botcfg.as_bool(botcfg.DEFAULTS.get("explore"), False))
check("explore_every 是正整数",
      botcfg.as_int(botcfg.DEFAULTS.get("explore_every"), 0) > 0)
check("REVISIT_LAMBDA 类默认与 botcfg 一致",
      abs(MemBot.REVISIT_LAMBDA - botcfg.as_float(
          botcfg.DEFAULTS.get("revisit_lambda"), 0)) < 1e-9)
check("λ=0 是逃生开关 (文档承诺)",
      mkpath(MemBot2, ks, lam=0.0).bfs_path(
          (0, 0), {(2, 0)}, allow_doors=True) == [(0, 0), (1, 0), (2, 0)])

print("\n" + "=" * 70)
print("通过 %d 项, 失败 %d 项" % (len(OK), len(BAD)))
if BAD:
    for n in BAD:
        print("  FAIL:", n)
    sys.exit(1)
print("全部通过 ✅")
