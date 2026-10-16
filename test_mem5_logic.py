# -*- coding: utf-8 -*-
"""test_mem5_logic.py — 怪点/宝箱巡回的状态机回归测试 (不需要游戏窗口)。

覆盖 2026-09-29 首跑暴露的 bug: 队伍站在目标身侧 1 格时每 tick 重复判「到达」,
174 次空转刷屏。修复后: 目标有最小驻留时间; 走到 1 格内仍不触发 -> 该点立即失效。
"""
import json
import os
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

from pal_bot_mem5 import MemBot5, cheb, kof      # noqa: E402
from pal_bot_mem2 import AgedSet                 # noqa: E402
from pal_bot_mem4 import MemBot4                 # noqa: E402
from scene_pilot import ScenePilot               # noqa: E402

OK = []
BAD = []

# 测试用评分表: 100=好场景, 200=过路场景(1个怪点), 300=中等
SCORES = {
    "100": {"expect": 1.50, "pass": False, "n_mon": 15, "ring": 300,
            "n_mon_all": 15, "ceil": 3.0},
    "200": {"expect": 0.43, "pass": True, "n_mon": 1, "ring": 0,
            "n_mon_all": 1, "ceil": 0.86},
    "300": {"expect": 0.40, "pass": False, "n_mon": 8, "ring": 600,
            "n_mon_all": 8, "ceil": 0.80},
}


class FakeObjMem(object):
    """离线替身: open() 直接失败 -> 走「回退静态坐标」分支。"""

    def open(self):
        return False


def check(name, cond, extra=""):
    (OK if cond else BAD).append(name)
    print("  %s %s %s" % ("PASS" if cond else "FAIL", name, extra))


def shadow(scene=157):
    o = MemBot5.__new__(MemBot5)
    with open(os.path.join(BASE, "scene_info.json"), encoding="utf-8") as f:
        o.scene_info = json.load(f)
    o.elem_mode = True
    o.scene = scene
    o.mon_cd = {}
    o.mon_visit = {}
    o._mon_miss = {}
    o.mon_dead = set()
    o.chest_cd = {}
    o.chest_try = {}
    o.chest_dead = set()
    o._mon_goal = None
    o._chest_goal = None
    o._trap_goal = None         # 困区突围目标
    o.steps = 0                 # 在途卡死判定用的步数快照
    o._last_fight_end = 0.0
    o._pick_n5 = 0
    o._slog_t = {}
    o.migrate = False
    o.home_active = lambda: False
    o._objmem = FakeObjMem()
    o._live_t = 0.0
    o._live_cache = None
    o._live_moved = {}
    o._live_warned = False
    o.stat = {"mon_hit": 0, "mon_visit": 0, "chest_ok": 0, "chest_try": 0,
              "fallback": 0, "scene_chg": 0}
    o.logs = []
    o.log = lambda m: o.logs.append(m)
    # ---- 场景调度 (自动换场景) ----
    o.auto_scene = True
    o.battles_won = 0
    o.open_doors = False
    o.pick_migrate_door = lambda cur: None
    o._open_doors_user = False
    o._pilot_open_tmp = False
    o._pilot_changing = False
    o._scene_links = {}
    o.pilot = ScenePilot(SCORES, log=o.log)
    return o


class FakeMem(object):
    """只提供 pick_target 需要的 K。"""

    def __init__(self, K):
        self.K = K

    def state(self):
        return {"K": self.K}


CUR = (-28, 120)
CAND = [(a, b) for a in range(CUR[0] - 70, CUR[0] + 70)
        for b in range(CUR[1] - 70, CUR[1] + 70)]

print("=== 1. 怪点选择: 最近怪点的正交邻域 ===")
o = shadow()
r = o._pick_monster(CUR, CAND)
check("返回 (走位, 怪点) 二元组", isinstance(r, tuple) and len(r) == 2, str(r))
t, mk = r
near = min(o.scene_monsters(), key=lambda m: cheb(tuple(m["K"]), CUR))
check("挑的是最近的怪点", cheb(mk, tuple(near["K"])) <= 1,
      "怪点%s 最近%s" % (mk, near["K"]))
check("★走位在怪点正交邻域(真触发圈, 非斜角)",
      (t[0] - mk[0], t[1] - mk[1]) in MemBot5.ORTHO_SET, "走位%s 怪点%s" % (t, mk))
check("走位不是怪点本身时距它正好 1 格",
      t == mk or abs(t[0] - mk[0]) + abs(t[1] - mk[1]) == 1)

print("\n=== 2. 冷却中的怪点被跳过 ===")
o.mon_cd[(157,) + mk] = time.time() + 60
t2, mk2 = o._pick_monster(CUR, CAND)
check("冷却点不被选", cheb(mk2, mk) > 1, "新怪点%s" % (mk2,))
check("新目标更远或相等", cheb(t2, CUR) >= cheb(t, CUR),
      "旧%d 新%d" % (cheb(t, CUR), cheb(t2, CUR)))

print("\n=== 3. ★ 走到 1 格内仍不触发 -> 先冷却, 连试才放弃 ===")
o3 = shadow()
r = o3._pick_monster(CUR, CAND)
t, mk = r
o3._last_fight_end = 0.0                      # 从没打过
o3._mon_goal = (157, t, mk, time.time() - 10, 0)  # 10 秒前设的目标(已过驻留期)
o3._settle_goal(mk)                            # 人已站在怪点上
key = (157,) + mk
check("第1次: 进冷却(怪可能在恢复期)", o3.mon_cd.get(key, 0) > time.time(),
      "cd=+%.0fs" % (o3.mon_cd.get(key, 0) - time.time()))
check("第1次不进放弃名单", key not in o3.mon_dead)
check("计数 +1", o3.mon_visit.get(key) == 1)
check("只记 1 条日志(不刷屏)", len([x for x in o3.logs if "未触发" in x]) == 1,
      "logs=%d" % len(o3.logs))
for _ in range(MemBot5.MON_TRY_N - 1):         # 再试 3 次
    o3._mon_goal = (157, t, mk, time.time() - 10, 0)
    o3._settle_goal(mk)
check("连试 %d 次后本局放弃" % MemBot5.MON_TRY_N, key in o3.mon_dead,
      "visits=%d" % o3.mon_visit.get(key, 0))

print("\n=== 4. 刚打过 -> 记命中 + 长冷却 ===")
o4 = shadow()
r = o4._pick_monster(CUR, CAND)
t, mk = r
o4._last_fight_end = time.time()               # 刚刚开过打
o4._mon_goal = (157, t, mk, time.time() - 10, 0)
o4._settle_goal(mk)
cd = o4.mon_cd[(157,) + mk] - time.time()
check("命中写长冷却 %.0fs" % cd, 60 < cd <= MemBot5.MON_CD + 1)
check("命中不进放弃名单", (157,) + mk not in o4.mon_dead)

print("\n=== 5. ★ 最小驻留: 刚设的目标不结算(防每 tick 重复判) ===")
o5 = shadow()
r = o5._pick_monster(CUR, CAND)
t, mk = r
o5._mon_goal = (157, t, mk, time.time(), 0)        # 刚刚设置
o5._settle_goal(mk)
check("驻留期未到 -> 目标保持", o5._mon_goal is not None)
check("也没被误弃", (157,) + mk not in o5.mon_dead)

print("\n=== 6. 没走到(在途) -> 继续走; 超时才记 miss, 连续多次才罚 ===")
o6 = shadow()
r = o6._pick_monster(CUR, CAND)
t, mk = r
FAR = (mk[0] + 9, mk[1] + 9)
o6._mon_goal = (157, t, mk, time.time() - 2, 0)    # 在途, 才 2 秒
o6._settle_goal(FAR)
check("★ 在途未超时 -> 目标保持(不误判 miss)", o6._mon_goal is not None)
check("也没记 miss", o6._mon_miss.get((157,) + mk, 0) == 0)
o6._mon_goal = (157, t, mk, time.time() - 999, 0)  # 远超时限
o6._settle_goal(FAR)
check("超时 -> 目标清空以重选", o6._mon_goal is None)
check("第1次超时不罚", (157,) + mk not in o6.mon_dead)
check("miss 计数 =1", o6._mon_miss.get((157,) + mk) == 1)
for _ in range(MemBot5.MON_MISS_N - 1):
    o6._mon_goal = (157, t, mk, time.time() - 999, 0)
    o6._settle_goal(FAR)
check("连续 %d 次超时 -> 判长冷却(不永久放弃)" % MemBot5.MON_MISS_N,
      o6.mon_cd.get((157,) + mk, 0) > time.time() + 300,
      "miss=%d dead=%s" % (o6._mon_miss.get((157,) + mk, 0),
                           (157,) + mk in o6.mon_dead))
check("长冷却后 miss 计数已重置", o6._mon_miss.get((157,) + mk) == 0)

print("\n=== 7. 宝箱: 站位与朝向映射 ===")
o7 = shadow()
tb = o7._pick_chest(CUR, CAND)
check("选到宝箱站位", tb is not None and tb in set(CAND), "站位%s" % (tb,))
sc, ck, stand, kd = o7._chest_goal[0], o7._chest_goal[1], \
    o7._chest_goal[2], o7._chest_goal[3]
check("宝箱目标带时间戳与步数快照(防抖动/防卡死)", len(o7._chest_goal) == 6,
      "len=%d" % len(o7._chest_goal))
da, db = ck[0] - stand[0], ck[1] - stand[1]
want = {(1, 0): "up", (-1, 0): "down", (0, -1): "left", (0, 1): "right"}
check("朝向键正确", want.get((da, db)) == kd,
      "箱%s 站%s 偏移(%d,%d) 键%s" % (ck, stand, da, db, kd))
cx = min(o7.scene_chests(), key=lambda c: cheb(tuple(c["K"]), CUR))
check("选最近宝箱", cheb(ck, tuple(cx["K"])) <= 1, "选中%s 最近%s" % (ck, cx["K"]))

print("\n=== 8. 全冷却 / 全弃 / 无数据 -> 交回 v15 巡逻 ===")
o8 = shadow()
now = time.time()
for m in o8.scene_monsters():
    o8.mon_cd[(157, m["K"][0], m["K"][1])] = now + 3600
check("怪点全冷却 -> None", o8._pick_monster(CUR, CAND) is None)
o8b = shadow()
for c in o8b.scene_chests():
    o8b.chest_dead.add((157, c["K"][0], c["K"][1]))
check("宝箱全弃 -> None", o8b._pick_chest(CUR, CAND) is None)
o8c = shadow(scene=99999)
check("无要素数据 -> None", o8c._pick_monster(CUR, CAND) is None
      and o8c._pick_chest(CUR, CAND) is None)

print("\n=== 9. 日志节流: 同一点 20 秒内只打一条 ===")
o9 = shadow()
k = tuple(o9.scene_monsters()[0]["K"])
o9.logs = []            # 清掉上面取清单时可能打的「实时不可用」提示
o9._slog(k, "msg1")
o9._slog(k, "msg2")
check("同 key 第二条被节流", len(o9.logs) == 1, "logs=%d" % len(o9.logs))
o9._slog((1, 2), "other")
check("不同 key 不互相影响", len(o9.logs) == 2)

print("\n=== 10. ★ 目标在途直接沿用(防每 tick 重设 -> 摆动) ===")
o10 = shadow()
o10.mem = FakeMem(CUR)
goal = (77, 88)
o10._mon_goal = (157, goal, (50, 50), time.time(), 0)
check("在途目标沿用, 不重选", o10.pick_target() == goal, str(o10.pick_target()))

print("\n=== 11. ★ 已在触发圈内的怪点不作为目标(防来回摆) ===")
lst = o10.scene_monsters()
body = tuple(lst[0]["K"])
t11 = o10._pick_monster(body, CAND)
check("不把脚下这只怪当目标",
      t11 is None or not o10._in_trigger(t11[1], body),
      "选中%s (%d 个怪点)" % (t11[1] if t11 else None, len(lst)))

print("\n=== 12. 常量的自洽性(阈值必须存在且有序) ===")
for nm in ("MON_CD", "MON_SETTLE_MIN", "MON_GOAL_TTL", "MON_HIT_NEAR",
           "MON_TRY_N", "MON_MISS_N", "MON_LOG_EVERY", "CHEST_BIAS"):
    check("常量 %s 已定义" % nm, isinstance(getattr(MemBot5, nm, None),
                                          (int, float)),
          "=%s" % getattr(MemBot5, nm, None))
check("MON_GOAL_TTL > MON_SETTLE_MIN",
      MemBot5.MON_GOAL_TTL > MemBot5.MON_SETTLE_MIN)

print("\n=== 13. ★ 触发圈几何: 正交算、斜角不算 ===")
o13 = shadow()
m0 = tuple(o13.scene_monsters()[0]["K"])
check("怪点本身在触发圈", o13._in_trigger(m0, m0))
check("正交邻格(4个)在触发圈",
      all(o13._in_trigger(m0, (m0[0] + da, m0[1] + db))
          for da, db in MemBot5.ORTHO))
check("★斜角格(4个)不在触发圈",
      not any(o13._in_trigger(m0, (m0[0] + da, m0[1] + db))
              for da, db in MemBot5.DIAG))
check("2 格外不在触发圈", not o13._in_trigger(m0, (m0[0] + 2, m0[1])))

print("\n=== 14. ★ 顺路开箱: max_d 限制与无限制 ===")
o14 = shadow()
check("max_d=0 -> 不开箱(怪点近时不去)", o14._pick_chest(CUR, CAND, max_d=0)
      is None)
tb14 = o14._pick_chest(CUR, CAND, max_d=10 ** 6)
check("max_d 放开 -> 能选到箱子", tb14 is not None, "站位%s" % (tb14,))
o14b = shadow()
check("max_d 过滤时不会误设目标", o14b._pick_chest(CUR, CAND, max_d=0) is None
      and o14b._chest_goal is None)

print("\n=== 15. ★ 实时怪点坐标: 读不到时安全回退 ===")
check("kof 换算正确", kof(400, 1480) == (-80, 104), str(kof(400, 1480)))
o15 = shadow()
static_n = len(o15.scene_info["157"]["monsters"])
check("读不到内存 -> 回退静态清单",
      len(o15.scene_monsters()) == static_n,
      "实时%d 静态%d" % (len(o15.scene_monsters()), static_n))
check("跨场景查询仍走静态清单",
      len(o15.scene_monsters(99999)) == 0)
o15c = shadow()
o15c.elem_mode = False
check("要素模式关闭时不读实时", hasattr(o15c, "_objmem")
      and o15c.scene_monsters() == o15c.scene_info["157"]["monsters"])

print("\n=== 16. ★ 在途卡死(步数零增长) -> 提前放弃, 不干等 TTL ===")
o16 = shadow()
r = o16._pick_monster(CUR, CAND)
t, mk = r
# 目标设了 20 秒, 步数零增长(仍等于设定时的 0) -> 12 秒就该放弃
o16._mon_goal = (157, t, mk, time.time() - 20, 0)
o16._settle_goal((mk[0] + 30, mk[1] + 30))     # 人还在 30 格外
check("卡死目标被提前清空", o16._mon_goal is None)
check("卡死记 miss 一次", o16._mon_miss.get((157,) + mk) == 1,
      str(o16._mon_miss))
# 对照: 步数有进展 -> 不算卡死, 继续沿用
o16b = shadow()
r = o16b._pick_monster(CUR, CAND)
t, mk = r
o16b._mon_goal = (157, t, mk, time.time() - 20, 5)   # 设定时已有 5 步
o16b.steps = 8                                        # 之后又走了 3 步
o16b._settle_goal((mk[0] + 30, mk[1] + 30))
check("有进展的目标继续沿用", o16b._mon_goal is not None)

print("\n=== 17. 常量自洽: 卡死阈值 < TTL 基准 ===")
check("MON_MOVE_STALL < MON_GOAL_TTL",
      MemBot5.MON_MOVE_STALL < MemBot5.MON_GOAL_TTL,
      "%s < %s" % (MemBot5.MON_MOVE_STALL, MemBot5.MON_GOAL_TTL))
check("MON_MOVE_STALL 合理(5~20 秒)",
      5 <= MemBot5.MON_MOVE_STALL <= 20)

print("\n=== 18. 场景调度: 耐心系数由评分驱动 ===")


def mk(sc, t0=1000.0, battles=0):
    q = ScenePilot(SCORES)
    q.on_scene(sc, t0, battles, "测试")
    return q


p100 = mk(100).patience(now=1000.0)
p200 = mk(200).patience(now=1000.0)
check("好场景耐心 > 过路场景耐心", p100 > p200, "%.2f vs %.2f" % (p100, p200))
check("过路场景耐心压到 0.45 倍", abs(p200 - 0.45) < 0.01, "%.3f" % p200)

print("\n=== 19. 场景调度: 三条换场景判据 ===")
q = mk(200)
check("MIN_STAY(150s) 内不换", not q.decide(1000.0 + 100, 0)[0], "过路场景 100s")
ok, why = q.decide(1000.0 + 160, 0)
check("过路场景空闲超阈值 -> 换", ok and "过路" in why, why)

q = mk(100)
check("好场景 200s 无战斗 -> 不换", not q.decide(1000.0 + 200, 0)[0])
ok, why = q.decide(1000.0 + 300, 0)
check("好场景 300s 无战斗 -> 换", ok and "无战斗" in why, why)

q = mk(100)
q.on_battle(1000.0 + 498, 1)
ok, why = q.decide(1000.0 + 500, 1)
check("低效(8分钟赢1场) -> 换", ok and "场/分" in why, why)

q = mk(200)
q.note_change(1100.0)
check("换场景冷却期内不再换", not q.decide(1100.0 + 100, 0)[0], "CD 内")
ok, why = q.decide(1100.0 + 190, 0)
check("冷却过后允许再换", ok, why)

print("\n=== 20. 场景调度: 回流惩罚 / 低效累积 / 总开关 ===")
q = ScenePilot(SCORES)
q.on_scene(100, 1000.0, 0)
q.on_scene(200, 1100.0, 0)
q.on_scene(100, 1200.0, 0)
rp = q.patience(now=1200.0)
check("刚离开又回来 -> 耐心砍半", abs(rp - 0.60) < 0.02, "%.3f" % rp)

q = ScenePilot(SCORES)
q.on_scene(100, 1000.0, 0)
base = q.patience(now=1000.0)
for _ in range(3):
    q.note_dud(100)
check("连续低效 3 次 -> 耐心降到 0.35 倍",
      q.patience(now=1000.0) < base * 0.4,
      "%.3f -> %.3f" % (base, q.patience(now=1000.0)))

q = ScenePilot(SCORES)
q.enabled = False
q.on_scene(200, 1000.0, 0)
check("关闭调度后从不换场景", not q.decide(1000.0 + 9999, 0)[0])

print("\n=== 21. 换场景执行: 无门 / 有门 / 关闭 / 穿门还原 ===")
t1 = shadow()
t1.mem = FakeMem((-30, 120))
now = time.time()
t1.pilot.on_scene(200, now - 400, 0, "测试")
t1.pilot.t_arrive = now - 400
t1.pilot.t_last_battle = now - 400
t1.pick_migrate_door = lambda cur: None
t1._pilot_tick()
check("决定该走但本场景无门 -> 不切迁移, 记低效",
      t1.migrate is False and t1.pilot.dud.get(200, 0) == 1, str(t1.pilot.dud))
check("无门时给出日志", any("无已知门" in m for m in t1.logs))

t2 = shadow()
t2.mem = FakeMem((-30, 120))
t2.pick_migrate_door = lambda cur: (-40, 118)
t2.pilot.on_scene(200, now - 400, 0, "测试")
t2.pilot.t_arrive = now - 400
t2.pilot.t_last_battle = now - 400
t2._pilot_tick()
check("有门 -> 进入迁移并临时打开穿门",
      t2.migrate is True and t2.open_doors is True
      and t2._pilot_open_tmp is True,
      "migrate=%s open=%s tmp=%s" % (t2.migrate, t2.open_doors,
                                     t2._pilot_open_tmp))
check("换场景计数 +1", t2.stat["scene_chg"] == 1)
t2._pilot_finish_change()
check("到新场景后临时穿门被还原",
      t2.open_doors is False and t2._pilot_open_tmp is False)

t3 = shadow()
t3.mem = FakeMem((-30, 120))
t3.auto_scene = False
t3._pilot_tick()
check("关闭自动换场景 -> 什么都不做",
      t3.migrate is False and t3.stat["scene_chg"] == 0)

t4 = shadow()
t4.mem = FakeMem((-30, 120))
t4.pick_migrate_door = lambda cur: (-40, 118)
t4.open_doors = True                     # 用户自己开的穿门
t4.pilot.on_scene(200, now - 400, 0, "测试")
t4.pilot.t_arrive = now - 400
t4.pilot.t_last_battle = now - 400
t4._pilot_tick()
t4._pilot_finish_change()
check("用户自己开的穿门不会被还原", t4.open_doors is True)

print("\n=== 22. 评分表: 与实测吻合 (场景157 实测 0.50 场/分) ===")
try:
    with open(os.path.join(BASE, "scene_score.json"), encoding="utf-8") as f:
        _sc = json.load(f)["scenes"]
    v = _sc.get("157")
    check("scene_score.json 含场景157", bool(v))
    if v:
        check("157 评分与实测吻合(0.3~0.7 场/分)", 0.3 <= v["expect"] <= 0.7,
              "expect=%.2f ceil=%.2f ring=%d" % (v["expect"], v["ceil"],
                                                 v["ring"]))
        top = sorted(_sc.items(), key=lambda kv: -kv[1]["expect"])[0]
        check("榜首场景评分显著高于 157", top[1]["expect"] > v["expect"] * 1.5,
              "场景%s %.2f" % (top[0], top[1]["expect"]))
        check("过路场景数合理(30~90)",
              30 <= sum(1 for x in _sc.values() if x["pass"]) <= 90,
              "%d 个" % sum(1 for x in _sc.values() if x["pass"]))
except Exception as e:
    check("scene_score.json 可读", False, str(e))

print("\n=== 23. 困区逃逸: 小圈徘徊 + 高频被挡 -> 清记录突围 / 换场景 ===")

def trap_shadow():
    o = shadow()
    o._trace = []
    o._trap_last = 0.0
    o._trap_cd_until = 0.0
    o._trap_goal = None
    o.blocked = AgedSet()
    o.blocked_hits = {}
    o._animK = {157: {}}
    o._pc_key = None
    o.doors = set()
    o.known_doors = {}
    o.report_doors = {}
    o.visited = set()
    o.reach = lambda src, bad: set()          # 默认: 四面是墙
    return o

now = time.time()

# 23.1 正常大范围活动 -> 不触发
o = trap_shadow()
o._trace = [(now - i * 0.4, (-100 + (i * 7) % 60, 100 + (i * 11) % 60))
            for i in range(300)]
o.blocked_hits = {(-100 + i, 100 + i): (3, now) for i in range(20)}
o._trap_check()
check("大范围活动不触发困区", o._trap_goal is None and not o.migrate)

# 23.2 小圈徘徊但被挡少 -> 不触发 (可能只是怪点密集, 不算受困)
o = trap_shadow()
o._trace = [(now - i * 0.2, (10 + i % 3, 20 + i % 3)) for i in range(300)]
o.blocked_hits = {(9, 19): (2, now), (11, 21): (3, now)}
o._trap_check()
check("小圈但被挡少 -> 不触发", o._trap_goal is None and not o.migrate)

# 23.3 小圈 + 高频被挡 + 清完有路 -> 突围目标 + 清记录
o = trap_shadow()
o.mem = FakeMem((10, 20))
o._trace = [(now - i * 0.2, (10 + i % 3, 20 + i % 3)) for i in range(300)]
o.blocked_hits = {(9, 19): (6, now), (10, 19): (8, now), (11, 21): (9, now),
                  (9, 21): (5, now)}
far = [(50, 90)] + [(50 - i, 90) for i in range(1, 80)]   # 80 格可达域
o.blocked = AgedSet([(10, 19), (9, 19), (11, 21), (-90, -90)])
o.reach = lambda src, bad: set(far)
o._trap_check()
check("受困 -> 设突围目标", o._trap_goal is not None
      and o._trap_goal[1] == (50, 90), str(o._trap_goal))
check("突围目标 = 最远可达格", o._trap_goal is not None
      and o._trap_goal[0] == 157)
check("身边被挡记录被清", (10, 19) not in o.blocked and (9, 19) not in o.blocked)
check("远处被挡记录保留", (-90, -90) in o.blocked)
check("命中计数同步清", (10, 19) not in o.blocked_hits)
check("突围期内不重复触发", o._trap_cd_until > now)

# 23.4 小圈 + 高频被挡 + 清完无路 -> 换场景
o = trap_shadow()
o.mem = FakeMem((10, 20))
o._trace = [(now - i * 0.2, (10 + i % 3, 20 + i % 3)) for i in range(300)]
o.blocked_hits = {(9, 19): (30, now)}
o.reach = lambda src, bad: {(10, 20)}
o._trap_check()
check("清完仍无路 -> 启动换场景", o.migrate is True and o._trap_goal is None,
      "migrate=%s" % o.migrate)

# 23.5 冷却期内不再触发
o2 = o
o2.migrate = False
o2.reach = lambda src, bad: set(far)
n_before = len([m for m in o2.logs if "困区" in m])
o2._trap_last = 0.0
o2._trap_check()
check("冷却期内不重复逃逸",
      len([m for m in o2.logs if "困区" in m]) == n_before)

# 23.6 pick_target: 突围目标优先于怪点/宝箱
o = trap_shadow()
o.mem = FakeMem((10, 20))
o._trap_goal = (157, (50, 90), now + 100)
t = o.pick_target()
check("突围目标优先返回", t == (50, 90), "返回 %s" % (t,))

# 23.7 突围目标超时清除
o = trap_shadow()
o.mem = FakeMem((10, 20))
o._trap_goal = (157, (50, 90), now - 1)
_orig4 = MemBot4.pick_target
MemBot4.pick_target = lambda self, exclude=None: "fallback"
try:
    t = o.pick_target()
finally:
    MemBot4.pick_target = _orig4
check("突围目标超时 -> 清除并回退", o._trap_goal is None and t == "fallback",
      "返回 %s" % (t,))

# 23.8 到达突围点 -> 清除
o = trap_shadow()
o.mem = FakeMem((50, 90))
o._trap_goal = (157, (50, 90), now + 100)
MemBot4.pick_target = lambda self, exclude=None: "fallback"
try:
    t = o.pick_target()
finally:
    MemBot4.pick_target = _orig4
check("到达突围点 -> 清除", o._trap_goal is None and t == "fallback",
      "日志 %s" % [m for m in o.logs if "困区" in m])

# ----------------------------------------------------------------------
# 24. 内存基址自检 (换 exe / 换版本的第一道闸)
#     背景: 换 exe 后 ReadProcessMemory 依然"成功", 但读到的是垃圾 ->
#     机器人会拿着假坐标瞎走。selfcheck 用 4 组独立证据交叉验证。
# ----------------------------------------------------------------------
print("\n--- 24. 内存基址自检 ---")
import pal_bot_mem as PM                                # noqa: E402


def stub_mem(scene=77, mapv=72, viewport=(1000, 800), table=None):
    """造一个"内存内容可控"的假 Mem (不走 __init__, 不真读进程)。"""
    m = PM.Mem.__new__(PM.Mem)
    m.pid = 999999                       # 不存在的 pid -> psutil 分支走异常
    m.slide = 0                          # ★ 本机基址 == 标定基址 (真机另算)
    W = [0] * (PM.READ_N // 2)
    W[PM.IVP] = viewport[0] & 0xFFFF
    W[PM.IVP + 1] = viewport[1] & 0xFFFF
    W[PM.IPO] = 160
    W[PM.IPO + 1] = 112
    si = (PM.SCENE_ADDR - PM.READ_AT) // 2
    W[si] = scene
    W[si + 1] = 0
    m.raw = lambda: tuple(W)
    off = (PM.TABLE_ADDR - PM.TABLE_REGION) // 2
    tw = [0] * (off + 4 * 300)
    for s in range(1, 297):
        i = off + (s - 1) * 4
        tw[i] = (table or {}).get(s, mapv)
        tw[i + 1] = 0
        tw[i + 2] = 0xFFFF
    m._tw = tw
    m.scene_map = {s: tw[off + (s - 1) * 4] for s in range(1, 297)}
    return m


ok, det = stub_mem().selfcheck({77: 72})
check("基址正常 -> ok=True", ok, "; ".join(det))
check("自检报出场景/坐标", any("场景号=77" in d for d in det), "; ".join(det))
check("自检报出地图号与报告一致", any("地图=72" in d for d in det), "; ".join(det))

ok, det = stub_mem(scene=999).selfcheck({})
check("场景号越界 -> ok=False", not ok, "; ".join(det))
ok, det = stub_mem(viewport=(60000, 60000)).selfcheck({})
check("队伍像素越界 -> ok=False", not ok, "; ".join(det))
ok, det = stub_mem().selfcheck({77: 5})
check("地图号与解码报告不一致 -> ok=False", not ok, "; ".join(det))
ok, det = stub_mem(table={s: 4000 for s in range(1, 297)}).selfcheck({})
check("场景表整体离谱 (基址错位的典型样子) -> ok=False", not ok, "; ".join(det))
ok, det = stub_mem().selfcheck(None)
check("无报告表也能判 (不崩)", ok, "; ".join(det))

# ★ 2026-10-16 地址平移 (ASLR 每次开机随机基址) —— 自检必须把平移量写进日志,
#   并复核"累积末序号 == nEventObject"这条防错位判据 (没句柄的桩退化为提示)。
ok, det = stub_mem().selfcheck({77: 72})
check("自检报出地址平移量", any("地址平移" in d for d in det), "; ".join(det))
check("自检报出累积末序号与 nEventObject",
      any("nEventObject" in d for d in det), "; ".join(det))
check("桩没有进程句柄 -> 只是提示, 不判失败", ok, "; ".join(det))
m_stub = stub_mem()
check("桩的 slide 显式置 0 (与标定基址一致)", m_stub.slide == 0)
check("Mem 的地址平移入口是 game_addr.slide (唯一来源)",
      PM.game_addr.slide is not None and hasattr(PM, "game_addr"))

print("\n" + "=" * 56)
print("通过 %d 项, 失败 %d 项" % (len(OK), len(BAD)))
for b in BAD:
    print("  !! %s" % b)
sys.exit(1 if BAD else 0)
