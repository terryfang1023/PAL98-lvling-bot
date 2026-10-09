# -*- coding: utf-8 -*-
"""test_rope.py — 绳子攀爬 (pal_bot_mem5.rope_probe / 迁移接入) 离线回归测试。

不需要游戏窗口: 用 shadow() 造 MemBot5 空Shell + 假 mem/g/寻路, 覆盖:
  ① 绳子识别回退 (实时读失败 -> 静态 scene_info.json ropes)
  ② _pick_rope 选绳 (最近/冷却/可站格)
  ③ pick_migrate_door 无门时的绳子回退 + _mig_is_rope 标记
  ④ migrate_probe 分派 (绳子走 rope_probe, 门走原逻辑)
  ⑤ rope_probe: 成功(按空格换层)/走位途中触发/全失败进冷却/无可站格
  ⑥ face_key 朝向映射 / botcfg.rope 默认值与环境变量
  ⑦ ★v18.2「绳子优先级最高, 遇到就爬」(_rope_hunt / _rope_goal_target /
     pick_target 让位 / _after_arrive 到位就爬)
  ⑧ ★v18.3 走位失败≠空格没反应 (短冷却不锁死) / 放宽 BFS / 绕行软惩罚 /
     失败现场截图
"""
import json
import os
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import pal_bot_mem5 as M5                                   # noqa: E402
from pal_bot_mem5 import (MemBot5, face_key,                     # noqa: E402
                          chest_face_key)
import botcfg                                               # noqa: E402

OK = []
BAD = []


def check(name, cond, extra=""):
    (OK if cond else BAD).append(name)
    print("  %s %s %s" % ("PASS" if cond else "FAIL", name, extra))


class FakeObjMem(object):
    """open() 失败 -> scene_ropes 走静态回退。"""

    def __init__(self, ropes=None):
        self._ropes = ropes or []
        self.open_calls = 0

    def open(self):
        self.open_calls += 1
        return False

    def scene_ropes(self, sc):
        return list(self._ropes)


class FakeMem(object):

    def __init__(self, K, scene, seq=None):
        self.K = K
        self.scene = scene
        self.seq = list(seq or [])          # 每次 state() 弹出的 (K, scene)

    def state(self):
        if self.seq:
            k, sc = self.seq.pop(0)
            self.K, self.scene = k, sc
        return {"K": tuple(self.K), "scene": self.scene}


class FakeGame(object):
    """记录按键的假手柄。"""

    def __init__(self):
        self.keys = []

    def flush_keys(self):
        pass

    def key(self, name, hold=0.0):
        self.keys.append(name)


def shadow(scene=220, K=(-53, 152), ropes=None, seq=None, scene_info=None,
           hunt=True):
    o = MemBot5.__new__(MemBot5)
    o.scene = scene
    o.elem_mode = True
    o.use_ropes = True
    o.rope_hunt = hunt                # ★ v18.2 遇到就爬
    o.migrate = False
    o.not_battle_count = 99           # 非战斗帧
    o._trap_goal = None
    o._rope_goal = None
    o._rope_hunt_t = 0.0
    o._rope_ok_t = 0.0
    o._mon_goal = None
    o._chest_goal = None
    o.pilot = None
    # MemBot.pick_migrate_door 需要的门知识字段 (默认无门)
    o.report_doors = {}
    o.known_doors = {}
    o._door_fail = {}
    o.stat = {"mon_hit": 0, "mon_visit": 0, "chest_ok": 0, "chest_try": 0,
              "chest_empty": 0, "fallback": 0, "scene_chg": 0,
              "rope_ok": 0, "rope_try": 0}
    o.rope_cd = {}
    o.rope_fail = {}
    o._rope_cache = None
    o._mig_is_rope = False
    o.blocked = set()
    o.mem = FakeMem(K, scene, seq)
    o.g = FakeGame()
    o._objmem = FakeObjMem(ropes)
    o.scene_info = scene_info if scene_info is not None else {
        str(scene): {"ropes": [dict(r) for r in (ropes or [])]}}
    o.logs = []
    o.log = lambda m: o.logs.append(m)
    o.entered = []

    def enter_scene(sc, why=""):
        o.entered.append((sc, why))
        o.scene = sc
    o.enter_scene = enter_scene
    # 可走格: 一片平台 (含绳格与邻格)
    o.map = None                        # kwalk 被覆写, 不真用 map
    o._ks = {(0, 0), (1, 0), (0, 1), (1, 1), (2, 1), (0, 2)}
    o.kwalk = lambda m=None: o._ks
    o.bfs_path = lambda cur, goals, allow_doors=False: (
        [tuple(cur), tuple(next(iter(goals)))])
    o.walk_path = lambda path: True
    return o


ROPE_A = {"K": [0, 0], "spr": 0, "trig": 101, "mode": 1}
ROPE_B = {"K": [5, 5], "spr": 0, "trig": 102, "mode": 1}


def t_face_key():
    print("[face_key] K 空间朝向真值 (与 pal_bot_mem.KEYDK 一致)")
    check("up (+1,0)", face_key(1, 0) == "up")
    check("down (-1,0)", face_key(-1, 0) == "down")
    check("left (0,-1)", face_key(0, -1) == "left")
    check("right (0,+1)", face_key(0, 1) == "right")
    # ★ 2026-10-02 修反的回归锁: 全项目真值表是 up=+a/down=-a/left=-b/right=+b
    from pal_bot_mem import KEYDK as _KD
    check("与 KEYDK 互逆 (up=+a, down=-a, left=-b, right=+b)",
          all(face_key(d[0], d[1]) == k for k, d in _KD.items()), str(_KD))
    check("chest_face_key 是 face_key 的反向偏移口径",
          chest_face_key(1, 0) == face_key(-1, 0) == "down"
          and chest_face_key(0, 1) == face_key(0, -1) == "left")


def t_botcfg_rope():
    print("[botcfg] rope 开关默认值与环境变量")
    check("DEFAULTS.rope = True", botcfg.DEFAULTS.get("rope") is True)
    check("DEFAULTS.rope_hunt = True (遇到就爬默认开)",
          botcfg.DEFAULTS.get("rope_hunt") is True)
    check("DEFAULTS.rope_hunt_d / cd 存在",
          int(botcfg.DEFAULTS.get("rope_hunt_d")) == 10
          and int(botcfg.DEFAULTS.get("rope_hunt_cd")) == 120)
    old = os.environ.get("PAL_ROPE")
    oldh = os.environ.get("PAL_ROPE_HUNT")
    try:
        os.environ.pop("PAL_ROPE", None)
        v, src = botcfg.resolve_bool("rope")
        check("无环境变量 -> 默认开", v is True, src)
        os.environ["PAL_ROPE"] = "0"
        v, _ = botcfg.resolve_bool("rope")
        check("PAL_ROPE=0 -> 关", v is False)
        os.environ["PAL_ROPE"] = "1"
        v, _ = botcfg.resolve_bool("rope")
        check("PAL_ROPE=1 -> 开", v is True)
        os.environ.pop("PAL_ROPE_HUNT", None)
        v, _ = botcfg.resolve_bool("rope_hunt")
        check("rope_hunt 默认开", v is True)
        os.environ["PAL_ROPE_HUNT"] = "0"
        v, _ = botcfg.resolve_bool("rope_hunt")
        check("PAL_ROPE_HUNT=0 -> 关", v is False)
    finally:
        for k, v in (("PAL_ROPE", old), ("PAL_ROPE_HUNT", oldh)):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def t_cfg_int():
    print("[cfg_int] 整数配置读取 (环境变量 > json > 默认; lo 钳位)")
    check("默认值", M5.cfg_int("chest_try_n", 1, lo=1) == 1)
    old = os.environ.get("PAL_CHEST_TRY_N")
    try:
        os.environ["PAL_CHEST_TRY_N"] = "3"
        check("环境变量生效", M5.cfg_int("chest_try_n", 1, lo=1) == 3)
        os.environ["PAL_CHEST_TRY_N"] = "0"
        check("lo 钳位 (0 -> 1)", M5.cfg_int("chest_try_n", 1, lo=1) == 1)
        os.environ["PAL_CHEST_TRY_N"] = "abc"
        check("非法值 -> 默认", M5.cfg_int("chest_try_n", 1, lo=1) == 1)
    finally:
        if old is None:
            os.environ.pop("PAL_CHEST_TRY_N", None)
        else:
            os.environ["PAL_CHEST_TRY_N"] = old
    check("CHEST_TRY_N 默认 1 (碰到就记)", M5.MemBot5.CHEST_TRY_N == 1)


def t_scene_ropes_fallback():
    print("[scene_ropes] 实时读失败 -> 静态回退 + 缓存")
    o = shadow(ropes=[ROPE_A])
    lst = o.scene_ropes()
    check("回退到静态清单", len(lst) == 1 and tuple(lst[0]["K"]) == (0, 0))
    lst2 = o.scene_ropes()
    check("120s 内走缓存 (同一对象)", lst2 is lst)
    o._rope_cache = None
    o._objmem = FakeObjMem([])          # 实时/静态都空
    o.scene_info = {str(o.scene): {}}
    check("两头都空 -> 空表不算错", o.scene_ropes() == [])


def t_pick_rope():
    print("[_pick_rope] 选绳")
    o = shadow(ropes=[ROPE_A, ROPE_B])
    o.mem = FakeMem((2, 1), o.scene)    # 队伍在 (2,1)
    r = o._pick_rope((2, 1))
    check("取最近且旁侧可站的绳", r == (0, 0), str(r))
    o.rope_cd[(o.scene, 0, 0)] = time.time() + 999
    r = o._pick_rope((2, 1))
    check("冷却中的绳被跳过", r is None, str(r))
    o.rope_cd.clear()
    o._ks = {(9, 9)}                    # 绳子旁全不可站
    r = o._pick_rope((2, 1))
    check("无可站格 -> 不选", r is None)
    o.use_ropes = False
    o._ks = {(0, 0), (1, 0), (0, 1), (1, 1), (2, 1), (0, 2)}
    o._rope_cache = None
    # _pick_rope 不看开关 (开关在 pick_migrate_door), 这里只验证关掉后不回退
    o2 = shadow(ropes=[ROPE_A])
    o2._ks = set(o._ks)
    o2.use_ropes = False
    d = o2.pick_migrate_door((0, 0))
    check("use_ropes=False -> 不用绳", d is None and o2._mig_is_rope is False)


def t_migrate_integration():
    print("[迁移接入] 无门 -> 绳子; 有门 -> 门")
    # ① 有已知门: 原逻辑优先
    o = shadow(ropes=[ROPE_A])
    o.report_doors = {o.scene: {(1, 0)}}
    o.known_doors = {}
    o._door_fail = {}
    d = o.pick_migrate_door((2, 1))
    check("有门时不碰绳子", d == (1, 0) and o._mig_is_rope is False, str(d))
    # ② 无门: 回退绳子
    o.report_doors = {}
    d = o.pick_migrate_door((2, 1))
    check("无门 -> 选绳并打标记", d == (0, 0) and o._mig_is_rope is True,
          str(d))
    # ③ migrate_probe 分派
    called = {}

    def fake_rope(k):
        called["rope"] = k
        return True
    o.rope_probe = fake_rope
    ok = o.migrate_probe((0, 0))
    check("绳子分支走 rope_probe", ok is True and called.get("rope") == (0, 0))
    check("分派后清标记", o._mig_is_rope is False)
    o._mig_is_rope = False
    real = M5.MemBot.migrate_probe
    M5.MemBot.migrate_probe = lambda self, k: called.setdefault("door", k) or True
    try:
        o.migrate_probe((1, 0))
        check("门分支走原逻辑", called.get("door") == (1, 0))
    finally:
        M5.MemBot.migrate_probe = real


def t_migrate_door_is_rope():
    """★ 2026-10-02 真机事故: 试炼窟的绳子被学成了 door 表里的"门"。"""
    print("[迁移接入] door 表里的 K 其实是绳子 -> 走空格攀爬")
    o = shadow(ropes=[ROPE_A])                     # 绳在 (0,0)
    o.report_doors = {o.scene: {(0, 0)}}           # door 表里就是这根绳
    o.known_doors = {}
    o._door_fail = {}
    d = o.pick_migrate_door((2, 1))
    check("识破: 目标仍是 (0,0) 但标记成绳子",
          d == (0, 0) and o._mig_is_rope is True, str((d, o._mig_is_rope)))
    check("日志说明识破了", any("搜索型换景" in m for m in o.logs), str(o.logs))
    called = {}

    def fake_rope(k):
        called["rope"] = k
        return True
    o.rope_probe = fake_rope
    check("就该走 rope_probe", o.migrate_probe((0, 0)) is True
          and called.get("rope") == (0, 0))


def t_migrate_door_fail_fallback():
    """★ 门走不通 (不可达/踩不响) -> 轮到绳子, 而不是"放弃本轮"空转。"""
    print("[迁移接入] 门失败 -> 绳子兜底")
    o = shadow(K=(2, 1), ropes=[ROPE_A])
    o.report_doors = {o.scene: {(9, 9)}}           # 一道真门
    o.known_doors = {}
    o._door_fail = {}
    o._mig_why = "门外 11 格全走不进去 (无路/被挡)"
    logs = []
    o.log = lambda m: (logs.append(m), o.logs.append(m))
    real = M5.MemBot.migrate_probe
    M5.MemBot.migrate_probe = lambda self, k: False      # 门失败
    called = {}

    def fake_rope(k):
        called["rope"] = k
        return True
    o.rope_probe = fake_rope
    try:
        ok = o.migrate_probe((9, 9))
    finally:
        M5.MemBot.migrate_probe = real
    check("门失败后自动改用绳子", ok is True and called.get("rope") == (0, 0),
          str((ok, called)))
    check("日志写明门为什么走不通",
          any("走不通" in m and "门外 11 格" in m for m in logs), str(logs))
    # 关掉绳子开关 -> 门失败就是失败 (不硬来)
    o2 = shadow(K=(2, 1), ropes=[ROPE_A])
    o2.use_ropes = False
    called2 = {}

    def fake_rope2(k):
        called2["rope"] = k
        return True
    o2.rope_probe = fake_rope2
    M5.MemBot.migrate_probe = lambda self, k: False
    try:
        check("绳子关掉 -> 不兜底", o2.migrate_probe((9, 9)) is False
              and not called2)
    finally:
        M5.MemBot.migrate_probe = real
    # 场景里根本没绳子 -> 失败就是失败 (不报假成功)
    o3 = shadow(K=(2, 1), ropes=[])
    o3.scene_info = {str(o3.scene): {}}
    o3.rope_probe = lambda k: True
    M5.MemBot.migrate_probe = lambda self, k: False
    try:
        check("无绳可退 -> 返回失败", o3.migrate_probe((9, 9)) is False)
    finally:
        M5.MemBot.migrate_probe = real


def t_rope_hunt_gates():
    """★ v18.2 用户要求「绳子的优先级最高, 遇到就爬」的四道闸门。"""
    print("[_rope_hunt] 闸门: 开关 / 忙 / 冷却")
    o = shadow(K=(2, 1), ropes=[ROPE_A])
    o.use_ropes = False
    check("绳子总开关关 -> 不主动爬",
          o._rope_hunt() is False and o._rope_goal is None)
    o2 = shadow(K=(2, 1), ropes=[ROPE_A], hunt=False)
    check("「遇到就爬」关 -> 不主动爬", o2._rope_hunt() is False)
    o3 = shadow(K=(2, 1), ropes=[ROPE_A])
    o3.migrate = True
    check("迁移途中(去门) -> 不抢", o3._rope_hunt() is False)
    o3.migrate = False
    o3._trap_goal = (o3.scene, (1, 1), time.time() + 99)
    check("困区突围中 -> 不抢", o3._rope_hunt() is False)
    o3._trap_goal = None
    o3.not_battle_count = 0
    check("战斗中 -> 不抢", o3._rope_hunt() is False)
    o3.not_battle_count = 99
    o4 = shadow(K=(2, 1), ropes=[ROPE_A])
    o4._rope_ok_t = time.time()              # 刚爬成功
    check("刚爬成功 ROPE_HUNT_CD 内 -> 不爬 (防横跳)",
          o4._rope_hunt() is False and o4._rope_goal is None)
    o4._rope_ok_t = 0.0
    o4._rope_hunt_t = time.time()            # 刚尝试过
    check("两次尝试间隔不足 GAP -> 不爬", o4._rope_hunt() is False)
    o4._rope_hunt_t = 0.0
    check("闸门全开 -> 去爬", o4._rope_hunt() is True
          and o4._rope_goal is not None)
    check("日志写明「遇到」",
          any("遇到绳子" in m for m in o4.logs), str(o4.logs))


def t_rope_hunt_range_and_stand():
    print("[_rope_hunt] 距离上限 / 已在可爬位直接按空格")
    o = shadow(K=(2, 1), ropes=[ROPE_B])       # 绳在 (5,5)
    o._ks = {(0, 0), (1, 0), (0, 1), (1, 1), (2, 1), (0, 2),
             (5, 5), (4, 5), (5, 4)}
    o.ROPE_HUNT_D = 2
    check("超出「遇到」距离 -> 不去", o._rope_hunt() is False)
    o.ROPE_HUNT_D = 10
    check("进入距离 -> 去爬", o._rope_hunt() is True)
    check("记下在途目标", o._rope_goal is not None
          and o._rope_goal[1] == (5, 5), str(o._rope_goal))
    called = {}

    def fake(k):
        called["k"] = k
        return True
    o2 = shadow(K=(1, 0), ropes=[ROPE_A])
    o2.rope_probe = fake
    check("已站在可爬位 -> 立刻爬(不先走位)",
          o2._rope_hunt() is True and called.get("k") == (0, 0)
          and o2._rope_goal is None)
    # 绳子在冷却里 -> 不算「遇到」
    o3 = shadow(K=(2, 1), ropes=[ROPE_A])
    o3.rope_cd[(o3.scene, 0, 0)] = time.time() + 999
    check("绳子在冷却 -> 不选它", o3._rope_hunt() is False)


def t_rope_goal_target():
    print("[_rope_goal_target] 在途目标 -> 站位; 失效自动清")
    o = shadow(K=(2, 1), ropes=[ROPE_A])
    check("没有目标 -> None", o._rope_goal_target((2, 1)) is None)
    o._rope_goal = (o.scene, (0, 0), time.time())
    check("有目标 -> 给最近的站位", o._rope_goal_target((2, 1)) == (1, 0),
          str(o._rope_goal_target((2, 1))))
    o._rope_goal = (999, (0, 0), time.time())
    check("换场景 -> 放弃并清目标",
          o._rope_goal_target((2, 1)) is None and o._rope_goal is None)
    check("日志说明换了场景", any("已换场景" in m for m in o.logs))
    o._rope_goal = (o.scene, (0, 0), time.time())
    o.rope_cd[(o.scene, 0, 0)] = time.time() + 999
    check("绳子进冷却 -> 放弃", o._rope_goal_target((2, 1)) is None
          and o._rope_goal is None)
    o.rope_cd.clear()
    o._rope_goal = (o.scene, (0, 0), time.time() - o.ROPE_GOAL_TTL - 1)
    check("走太久没到 -> 放弃", o._rope_goal_target((2, 1)) is None
          and o._rope_goal is None)
    o._rope_goal = (o.scene, (0, 0), time.time())
    o._ks = {(9, 9)}
    check("旁无可站格 -> 放弃", o._rope_goal_target((2, 1)) is None
          and o._rope_goal is None)


def t_pick_target_rope_first():
    print("[pick_target] 绳子目标压过怪点/宝箱")
    o = shadow(K=(2, 1), ropes=[ROPE_A])
    o.home_active = lambda: False
    o._settle_goal = lambda cur: None
    o._rope_goal = (o.scene, (0, 0), time.time())
    check("优先返回绳位 (不看怪点/宝箱)", o.pick_target() == (1, 0),
          str(o.pick_target()))
    # 失效 -> 落回常规 (这里把常规入口打桩, 只验证"不再返回绳位")
    o._ks = {(9, 9)}                            # 绳位失效
    o.scene_monsters = lambda *a: []
    o.scene_chests = lambda *a: []
    real = M5.MemBot4.pick_target
    M5.MemBot4.pick_target = lambda self, exclude=None: "FALLBACK"
    try:
        check("目标失效 -> 落回常规且清目标",
              o.pick_target() == "FALLBACK" and o._rope_goal is None)
    finally:
        M5.MemBot4.pick_target = real


def t_after_arrive_rope():
    print("[_after_arrive] 站到绳位 -> 按空格攀爬")
    o = shadow(K=(1, 0), ropes=[ROPE_A])
    o._rope_goal = (o.scene, (0, 0), time.time())
    called = {}

    def fake(k):
        called["k"] = k
        return True
    o.rope_probe = fake
    o._after_arrive()
    check("到位就爬", called.get("k") == (0, 0))
    check("爬完清在途目标", o._rope_goal is None)
    # 还在路上 -> 既不动手也不顺手开箱
    o2 = shadow(K=(2, 1), ropes=[ROPE_A])
    o2._rope_goal = (o2.scene, (0, 0), time.time())
    o2._chest_goal = (o2.scene, (0, 0), (2, 1), "up", time.time(), 0)
    hit = {}

    def fake2(k):
        hit["k"] = k
        return True
    o2.rope_probe = fake2
    o2._after_arrive()
    check("去绳子的路上不动手", not hit)
    check("也不顺手开箱", o2._chest_goal is not None
          and o2.stat["chest_try"] == 0)
    # 战斗中 -> 一律不动手 (按空格会吞掉攻击键)
    o3 = shadow(K=(1, 0), ropes=[ROPE_A])
    o3.not_battle_count = 0
    o3._rope_goal = (o3.scene, (0, 0), time.time())
    hit3 = {}

    def fake3(k):
        hit3["k"] = k
        return True
    o3.rope_probe = fake3
    o3._after_arrive()
    check("战斗中不按空格", not hit3 and o3._rope_goal is not None)
    # 爬成功 -> 通知调度器 "刚到新场景" (别立刻又判该走)
    class FakePilot(object):
        def __init__(self):
            self.n = 0

        def note_change(self, now):
            self.n += 1
    o4 = shadow(K=(1, 0), ropes=[ROPE_A])
    o4.pilot = FakePilot()
    o4._rope_goal = (o4.scene, (0, 0), time.time())
    o4.rope_probe = lambda k: True
    o4._after_arrive()
    check("爬成功后通知 pilot.note_change", o4.pilot.n == 1, str(o4.pilot.n))
    o5 = shadow(K=(1, 0), ropes=[ROPE_A])
    o5.pilot = FakePilot()
    o5._rope_goal = (o5.scene, (0, 0), time.time())
    o5.rope_probe = lambda k: False
    o5._after_arrive()
    check("没爬成 -> 不动调度器", o5.pilot.n == 0)


def t_pilot_tick_lets_rope():
    print("[_pilot_tick] 绳子在途时不再发起换场景")
    o = shadow(K=(2, 1), ropes=[ROPE_A])
    o.auto_scene = True
    o._rope_goal = (o.scene, (0, 0), time.time())

    class BoomPilot(object):
        def decide(self, *a, **k):
            raise AssertionError("绳子在途时不该问调度器")
    o.pilot = BoomPilot()
    o.migrate_cd = 0.0

    def boom():
        raise AssertionError("不该走到发改门这步")
    o.pick_migrate_door = boom
    try:
        o._pilot_tick()
        check("直接 return (绳子优先)", True)
    except AssertionError as e:
        check("直接 return (绳子优先)", False, str(e))


def t_rope_attrs_missing():
    """★ 血案回归: 别的测试(如 test_mem5_logic)造的影子对象没有 v18.2 新属性
    (_rope_goal / rope_hunt / rope_cd ...) —— 新代码必须 getattr 兜底,
    否则一跑全量回归就 AttributeError (2026-10-02 真的踩到过)。"""
    print("[健壮性] 属性缺失的裸影子对象不炸")
    o = MemBot5.__new__(MemBot5)
    o.scene = 220
    o.migrate = False
    o.mem = FakeMem((0, 0), 220)
    o.log = lambda m: None
    try:
        r1 = o._rope_hunt()
        r2 = o._rope_goal_target((0, 0))
        check("裸对象: _rope_hunt / _rope_goal_target 安全返回",
              r1 is False and r2 is None, str((r1, r2)))
    except Exception as ex:
        check("裸对象: _rope_hunt / _rope_goal_target 安全返回", False, str(ex))

    class P(object):
        def decide(self, *a):
            return False, "测试用例"
    o.pilot = P()
    o.auto_scene = True
    o.elem_mode = True
    o.battles_won = 0
    try:
        o._pilot_tick()
        check("裸对象: _pilot_tick 不炸 (没有 _rope_goal)", True)
    except Exception as ex:
        check("裸对象: _pilot_tick 不炸 (没有 _rope_goal)", False, str(ex))
    o._chest_goal = None
    try:
        o._after_arrive()
        check("裸对象: _after_arrive 不炸", True)
    except Exception as ex:
        check("裸对象: _after_arrive 不炸", False, str(ex))


def t_rope_probe_success():
    print("[rope_probe] 按空格 -> 换层成功")
    # state() 调用序: 顶部cur / 站位判断 / 触摸检查 / 轮询(变221) / 落点
    o = shadow(K=(0, 0), ropes=[ROPE_A],
               seq=[((0, 0), 220), ((0, 0), 220), ((0, 0), 220),
                    ((0, 0), 221), ((0, 0), 221)])
    o.ROPE_DLG_WAIT = 0.01
    o.ROPE_SC_WAIT = 0.05
    ok = o.rope_probe((0, 0))
    check("返回成功", ok is True)
    check("enter_scene(221, 绳子)", o.entered and o.entered[0][0] == 221
          and "绳" in o.entered[0][1], str(o.entered))
    check("记 _last_door_K (攒门路)", getattr(o, "_last_door_K", None) == (0, 0))
    check("统计 rope_ok/rope_try", o.stat["rope_ok"] == 1
          and o.stat["rope_try"] == 1)
    check("按过空格", "space" in o.g.keys)
    check("记下成功时刻 -> 「遇到就爬」的防横跳 CD", o._rope_ok_t > 0,
          str(o._rope_ok_t))
    check("落点 ±1 禁入 (防回头)", (0, 0) in o.blocked and (1, 0) in o.blocked)


def t_rope_probe_touch_on_walk():
    print("[rope_probe] 走位途中踩触发 (touch 型)")
    # 队伍从 (2,1) 走向绳旁 (0,1); walk_path 落地时场景已变
    o = shadow(K=(2, 1), ropes=[ROPE_A],
               seq=[((0, 1), 221)])
    o.ROPE_DLG_WAIT = 0.01
    o.ROPE_SC_WAIT = 0.05
    ok = o.rope_probe((0, 0))
    check("走位即触发也算成功", ok is True)
    check("不再按空格 (没进按键阶段)", "space" not in o.g.keys)
    check("统计 +1", o.stat["rope_ok"] == 1)


def t_rope_probe_fail():
    print("[rope_probe] 全失败 -> 冷却")
    o = shadow(K=(2, 1), ropes=[ROPE_A])       # seq 空: 场景一直不变
    o.ROPE_DLG_WAIT = 0.01
    o.ROPE_SC_WAIT = 0.05
    t0 = time.time()
    ok = o.rope_probe((0, 0))
    check("返回失败", ok is False)
    check("记冷却", o.rope_cd.get((o.scene, 0, 0), 0) > time.time())
    check("记失败次数", o.rope_fail.get((o.scene, 0, 0)) == 1)
    check("试过空格也补过 esc", "space" in o.g.keys and "esc" in o.g.keys)
    check("失败耗时可接受 (<20s)", time.time() - t0 < 20)
    # 连败 ROPE_FAIL_MAX 次 -> 长冷却
    o2 = shadow(K=(2, 1), ropes=[ROPE_A])
    o2.ROPE_DLG_WAIT = 0.01
    o2.ROPE_SC_WAIT = 0.05
    for _ in range(o2.ROPE_FAIL_MAX):
        o2.rope_probe((0, 0))
    cd = o2.rope_cd[(o2.scene, 0, 0)] - time.time()
    check("连败拉长冷却", cd > o2.ROPE_CD, "%.0fs" % cd)


def t_rope_probe_no_stand():
    print("[rope_probe] 无可站格")
    o = shadow(K=(9, 9), ropes=[ROPE_A])
    o._ks = {(9, 9)}                    # 绳子旁全不可站
    o.ROPE_DLG_WAIT = 0.01
    ok = o.rope_probe((0, 0))
    check("直接失败且不按键", ok is False and o.g.keys == [])
    check("也进冷却 (短冷却)", o.rope_cd.get((o.scene, 0, 0), 0) > time.time())


def t_pick_hole():
    """★ v18.7 黑洞传送: scene_info["portals"] 里隐形、踩上自动换景的洞。

    真机指认 (2026-10-04 场景221): 地面黑洞 spr=0 / mode=5(touchNormal) /
    trig 脚本换景 —— 走到洞格就是进去了, 不需要按空格。
    """
    print("[迁移接入] 无门 -> 黑洞兜底 (踩洞自动换景)")
    HOLE_A = {"K": [0, 1], "spr": 0, "mode": 5, "state": 1,
              "trig": 201, "auto": 0}
    HOLE_B = {"K": [0, 2], "spr": 0, "mode": 4, "state": 1,
              "trig": 202, "auto": 0}
    si = {"220": {"portals": [HOLE_A, HOLE_B]}}
    o = shadow(K=(0, 0), scene_info=si)
    d = o.pick_migrate_door((0, 0))
    check("无门 -> 最近的黑洞 (0,1) 被选中",
          d == (0, 1) and o._mig_is_hole is True, str((d, o._mig_is_hole)))
    check("日志说明踩洞自动触发",
          any("黑洞" in m and "踩洞" in m for m in o.logs), str(o.logs))
    check("没标记成绳子", o._mig_is_rope is False)
    # 门表有门 -> 仍然门优先
    o2 = shadow(K=(0, 0), scene_info=si)
    o2.report_doors = {o2.scene: {(1, 2)}}     # 旁有可走格的真门
    d2 = o2.pick_migrate_door((0, 0))
    check("有门时门优先, 不标黑洞",
          d2 == (1, 2) and o2._mig_is_hole is False, str((d2,)))
    # 过滤: 有贴图(普通门)/已消失/超距 都不算洞
    o3 = shadow(K=(0, 0), scene_info={"220": {"portals": [
        {"K": [9, 9], "spr": 3, "mode": 5, "state": 1, "trig": 1, "auto": 0},
        {"K": [9, 8], "spr": 0, "mode": 5, "state": 0, "trig": 1, "auto": 0},
        {"K": [150, 150], "spr": 0, "mode": 5, "state": 1,
         "trig": 1, "auto": 0},
        HOLE_A]}})
    check("过滤后仍选中真洞", o3._pick_hole((0, 0)) == (0, 1),
          str(o3._pick_hole((0, 0))))
    # 连败拉黑 (复用 _door_fail, 与门同账户)
    o4 = shadow(K=(0, 0), scene_info={"220": {"portals": [HOLE_A]}})
    o4._door_fail = {(0, 1): 2}          # DOOR_FAIL_MAX=2 -> 拉黑
    check("连败洞被拉黑", o4._pick_hole((0, 0)) is None,
          str(o4._pick_hole((0, 0))))
    # 没洞 -> 照旧落绳子
    o5 = shadow(K=(2, 1), ropes=[ROPE_A])
    d5 = o5.pick_migrate_door((2, 1))
    check("无门无洞 -> 绳子兜底不变",
          d5 == (0, 0) and o5._mig_is_rope is True, str((d5,)))


def t_migrate_probe_hole():
    """★ v18.7: 黑洞走基类 migrate_probe (踩上换景), 绝不按空格。"""
    print("[迁移接入] 黑洞 = 基类踩门探测, 失败再落绳子")
    called = {}
    real = M5.MemBot.migrate_probe

    def fake_base(self, k):
        called["base"] = k
        return called.get("base_ok", True)
    M5.MemBot.migrate_probe = fake_base

    def fake_rope(k):
        called["rope"] = k
        return True
    try:
        o = shadow(K=(2, 1))
        o.rope_probe = fake_rope
        o._mig_is_hole = True
        ok = o.migrate_probe((0, 1))
        check("踩洞走基类探测 (基类 step 判换景)",
              called.get("base") == (0, 1) and ok is True, str(called))
        check("踩洞不需要按空格 (没碰绳子)", "rope" not in called)
        # 黑洞走不通 (无路/没换景) -> 绳子兜底
        o2 = shadow(K=(2, 1), ropes=[ROPE_A])
        o2.rope_probe = fake_rope
        called.clear()
        called["base_ok"] = False
        o2._mig_is_hole = True
        o2._mig_why = "门外 3 格全走不进去 (无路/被挡)"
        ok2 = o2.migrate_probe((0, 1))
        check("洞失败 -> 绳子兜底",
              ok2 is True and called.get("rope") == (0, 0), str(called))
    finally:
        M5.MemBot.migrate_probe = real


def t_rope_probe_walkfail():
    print("[rope_probe] ★v18.3 走位失败 ≠ 空格没反应 (真机谎报事故回归锁)")
    # 真机事故: 通往绳子的走廊被怪挡几次 -> 绕行标记把 bfs 压塌 ->
    # 旧版一次空格都没按却上 300/900s 冷却, 把「遇到就爬」也锁死
    def bfs_none(cur, goals, allow_doors=False, relax_blocked=False):
        return None
    o = shadow(K=(2, 1), ropes=[ROPE_A])
    o.bfs_path = bfs_none
    o.ROPE_DLG_WAIT = 0.01
    ok = o.rope_probe((0, 0))
    check("返回失败", ok is False)
    check("一次空格都没按", "space" not in o.g.keys)
    check("日志明说「没走到」而不是「空格没反应」",
          any("没走到可攀爬位置" in m for m in o.logs), str(o.logs[-1:]))
    check("只给短冷却 (≤ROPE_CD_WALKFAIL)",
          0 < o.rope_cd[(o.scene, 0, 0)] - time.time()
          <= M5.MemBot5.ROPE_CD_WALKFAIL + 1,
          "%.0fs" % (o.rope_cd[(o.scene, 0, 0)] - time.time()))
    check("不计 rope_fail (不会升级 900s 长冷却)", o.rope_fail == {})
    check("stat.rope_try 仍计数", o.stat["rope_try"] == 1)
    o.mem = FakeMem((2, 1), o.scene)
    o.rope_probe((0, 0))
    check("连走位失败也不升级冷却",
          o.rope_cd[(o.scene, 0, 0)] - time.time()
          <= M5.MemBot5.ROPE_CD_WALKFAIL + 1)


def t_rope_probe_relax_bfs():
    print("[rope_probe] 绕行标记压塌路径 -> 放宽 BFS 再试一次")
    calls = []

    def bfs(cur, goals, allow_doors=False, relax_blocked=False):
        calls.append(relax_blocked)
        if relax_blocked:
            return [tuple(cur), tuple(next(iter(goals)))]
        return None
    o = shadow(K=(2, 1), ropes=[ROPE_A])
    o.bfs_path = bfs
    o.blocked = {(0, 1)}                # 模拟走廊被记绕行
    o.ROPE_DLG_WAIT = 0.01
    o.ROPE_SC_WAIT = 0.05
    o.rope_probe((0, 0))
    check("发生过 relax_blocked=True 的放宽调用", True in calls, str(calls))
    check("放宽后按了空格", "space" in o.g.keys)


def t_rope_probe_blocked_soft():
    print("[rope_probe] 绕行标记只降序软惩罚, 不再一票否决")
    o = shadow(K=(0, 2), ropes=[ROPE_A])
    o.blocked = {(0, 1)}                # 唯一邻格被标绕行, 但仍在可走域
    o.ROPE_DLG_WAIT = 0.01
    o.ROPE_SC_WAIT = 0.05
    o.rope_probe((0, 0))
    check("被标绕行的邻格仍被尝试 (按了空格)", "space" in o.g.keys)
    check("_rope_stands 同口径 (含被标绕行格)",
          (0, 1) in o._rope_stands((0, 0)))


def t_rope_diag_save():
    print("[rope_probe] 失败现场截图 (_diag_rope/, 上限 6 张)")
    import shutil

    import numpy as np
    tmp = os.path.join(BASE, "_tmp_diag_rope_test")
    old_base = M5.BASE
    M5.BASE = tmp
    try:
        o = shadow(K=(2, 1), ropes=[ROPE_A])
        o.ROPE_DLG_WAIT = 0.01
        o.ROPE_SC_WAIT = 0.05
        o.g.shot = lambda: np.zeros((4, 4, 3), dtype=np.int16)
        os.makedirs(os.path.join(tmp, "_diag_rope"), exist_ok=True)
        for i in range(6):              # 预置 6 张旧图 -> 应被新图挤掉
            open(os.path.join(tmp, "_diag_rope", "old%d.png" % i), "w").close()
        o.rope_probe((0, 0))
        d = os.path.join(tmp, "_diag_rope")
        fs = os.listdir(d)
        check("留了现场截图 (总数保持 ≤6)", len(fs) == 6, str(fs))
        check("新图进来了 (s220_K0_0 前缀)",
              any(f.startswith("s220_K0_0") for f in fs), str(fs))
        check("旧图被挤掉", not any(f.startswith("old") for f in fs))
        # shot 坏掉不炸主流程
        o.g.shot = lambda: None
        o.rope_cd.clear()
        o.rope_fail.clear()
        o.mem = FakeMem((2, 1), o.scene)
        try:
            o.rope_probe((0, 0))
            check("shot 返回 None 也不炸", True)
        except Exception as ex:
            check("shot 返回 None 也不炸", False, str(ex))
    finally:
        M5.BASE = old_base
        shutil.rmtree(tmp, ignore_errors=True)


def t_bfs_relax_blocked():
    print("[bfs_path] relax_blocked 参数 (mem 与 mem2 两份实现)")
    from pal_bot_mem import MemBot as MB
    from pal_bot_mem2 import MemBot2 as MB2
    for cls in (MB, MB2):
        o = cls.__new__(cls)
        o.map = None
        o._ks = {(0, 0), (1, 0), (2, 0)}
        o.kwalk = lambda m=None: o._ks
        o.blocked = {(1, 0)}
        o.doors = set()
        tag = cls.__name__
        check("%s: 默认绕行标记一票否决 -> 无路" % tag,
              o.bfs_path((0, 0), {(2, 0)}, allow_doors=True) is None)
        p = o.bfs_path((0, 0), {(2, 0)}, allow_doors=True, relax_blocked=True)
        check("%s: relax_blocked=True -> 放行" % tag,
              p == [(0, 0), (1, 0), (2, 0)], str(p))


def t_build_scene_info_static():
    print("[build_scene_info] 语法/字段检查 (不碰游戏)")
    src = open(os.path.join(BASE, "tools", "build_scene_info.py"),
               encoding="utf-8").read()
    check("含 ropes 分类", '"ropes"' in src and "wTriggerMode" in src)
    p = os.path.join(BASE, "scene_info.json")
    if not os.path.exists(p):
        print("SKIP  scene_info.json 不在本地 (衍生数据不入库; 在母目录跑全量)")
        return
    check("scene_info.json 有 ropes 字段 (已按新版重新生成)",
          any(v.get("ropes") for v in json.load(
              open(p, encoding="utf-8")).values()))


def main():
    t_face_key()
    t_botcfg_rope()
    t_cfg_int()
    t_scene_ropes_fallback()
    t_pick_rope()
    t_migrate_integration()
    t_migrate_door_is_rope()
    t_migrate_door_fail_fallback()
    t_pick_hole()
    t_migrate_probe_hole()
    t_rope_hunt_gates()
    t_rope_hunt_range_and_stand()
    t_rope_goal_target()
    t_pick_target_rope_first()
    t_after_arrive_rope()
    t_pilot_tick_lets_rope()
    t_rope_attrs_missing()
    t_rope_probe_success()
    t_rope_probe_touch_on_walk()
    t_rope_probe_fail()
    t_rope_probe_no_stand()
    t_rope_probe_walkfail()
    t_rope_probe_relax_bfs()
    t_rope_probe_blocked_soft()
    t_rope_diag_save()
    t_bfs_relax_blocked()
    t_build_scene_info_static()
    print("\n===== test_rope: %d PASS / %d FAIL =====" % (len(OK), len(BAD)))
    if BAD:
        for n in BAD:
            print("  FAIL:", n)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
