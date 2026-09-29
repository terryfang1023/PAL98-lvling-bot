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

OK = []
BAD = []


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
              "fallback": 0}
    o.logs = []
    o.log = lambda m: o.logs.append(m)
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
o3._mon_goal = (157, t, mk, time.time() - 10)  # 10 秒前设的目标(已过驻留期)
o3._settle_goal(mk)                            # 人已站在怪点上
key = (157,) + mk
check("第1次: 进冷却(怪可能在恢复期)", o3.mon_cd.get(key, 0) > time.time(),
      "cd=+%.0fs" % (o3.mon_cd.get(key, 0) - time.time()))
check("第1次不进放弃名单", key not in o3.mon_dead)
check("计数 +1", o3.mon_visit.get(key) == 1)
check("只记 1 条日志(不刷屏)", len([x for x in o3.logs if "未触发" in x]) == 1,
      "logs=%d" % len(o3.logs))
for _ in range(MemBot5.MON_TRY_N - 1):         # 再试 3 次
    o3._mon_goal = (157, t, mk, time.time() - 10)
    o3._settle_goal(mk)
check("连试 %d 次后本局放弃" % MemBot5.MON_TRY_N, key in o3.mon_dead,
      "visits=%d" % o3.mon_visit.get(key, 0))

print("\n=== 4. 刚打过 -> 记命中 + 长冷却 ===")
o4 = shadow()
r = o4._pick_monster(CUR, CAND)
t, mk = r
o4._last_fight_end = time.time()               # 刚刚开过打
o4._mon_goal = (157, t, mk, time.time() - 10)
o4._settle_goal(mk)
cd = o4.mon_cd[(157,) + mk] - time.time()
check("命中写长冷却 %.0fs" % cd, 60 < cd <= MemBot5.MON_CD + 1)
check("命中不进放弃名单", (157,) + mk not in o4.mon_dead)

print("\n=== 5. ★ 最小驻留: 刚设的目标不结算(防每 tick 重复判) ===")
o5 = shadow()
r = o5._pick_monster(CUR, CAND)
t, mk = r
o5._mon_goal = (157, t, mk, time.time())        # 刚刚设置
o5._settle_goal(mk)
check("驻留期未到 -> 目标保持", o5._mon_goal is not None)
check("也没被误弃", (157,) + mk not in o5.mon_dead)

print("\n=== 6. 没走到(在途) -> 继续走; 超时才记 miss, 连续多次才罚 ===")
o6 = shadow()
r = o6._pick_monster(CUR, CAND)
t, mk = r
FAR = (mk[0] + 9, mk[1] + 9)
o6._mon_goal = (157, t, mk, time.time() - 2)    # 在途, 才 2 秒
o6._settle_goal(FAR)
check("★ 在途未超时 -> 目标保持(不误判 miss)", o6._mon_goal is not None)
check("也没记 miss", o6._mon_miss.get((157,) + mk, 0) == 0)
o6._mon_goal = (157, t, mk, time.time() - 999)  # 远超时限
o6._settle_goal(FAR)
check("超时 -> 目标清空以重选", o6._mon_goal is None)
check("第1次超时不罚", (157,) + mk not in o6.mon_dead)
check("miss 计数 =1", o6._mon_miss.get((157,) + mk) == 1)
for _ in range(MemBot5.MON_MISS_N - 1):
    o6._mon_goal = (157, t, mk, time.time() - 999)
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
check("宝箱目标带时间戳(防站位抖动)", len(o7._chest_goal) == 5,
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
o10._mon_goal = (157, goal, (50, 50), time.time())
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

print("\n" + "=" * 56)
print("通过 %d 项, 失败 %d 项" % (len(OK), len(BAD)))
for b in BAD:
    print("  !! %s" % b)
sys.exit(1 if BAD else 0)
