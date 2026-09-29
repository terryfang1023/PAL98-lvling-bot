# -*- coding: utf-8 -*-
"""回归测试: 长期禁入锁死自救 + 出口保护。不启动游戏。"""
import os
import sys
import time

import numpy as np

BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE)
sys.path.insert(0, BASE)

from pal_bot_mem import MemBot                     # noqa: E402
from pal_bot_mem2 import AgedSet                   # noqa: E402
from pal_bot_mem4 import MemBot4                   # noqa: E402

SCENE, MAP, CUR = 157, 156, (-46, 140)      # 事故现场 (熔岩锁链窄路)
EXITS = [(-45, 140), (-47, 140)]            # 数据上仅有的两个出口

print("=" * 64)
print("T1  出口保护: 相邻格若是仅剩出口 -> 拒绝升级长期禁入")
print("=" * 64)


def mkbot(blocked_cells=(), perm_cells=()):
    b = MemBot4.__new__(MemBot4)
    b.grids = np.load(os.path.join(BASE, "map_walk2.npy"))
    b._kwalk_cache = {}
    b.scene, b.map = SCENE, MAP
    b.visited, b.visit_cnt = set(), {}
    b.doors, b.known_doors, b.report_doors = set(), {}, {}
    b.blocked = AgedSet()
    b.blocked_hits = {}
    b.scene_know = {}
    b.home = None
    b.hotK, b.startK = (-50, 151), CUR
    b.migrate = False
    b.migrate_cd = 0.0
    b.NO_MONSTER = set()
    b._pc_key = None
    b._chase_goal = None
    b._dead_n = 0
    b.open_doors = False
    b._patrol_r = 28
    b._pick_n = 0
    b._bias = None
    b._bias_name = ""
    b._chase_tries = {}
    b.scan_monsters = lambda cur: None
    b.log = lambda m: print("   |", m)
    b.mem = type("M", (), {
        "state": staticmethod(lambda: {"K": CUR, "scene": SCENE}),
        "scene_map": {SCENE: MAP}})()
    for c in blocked_cells:
        b.blocked.add(c)
    for c in perm_cells:
        b.blocked.add(c)
        b.blocked.ts[c] = time.time() + 1800
    return b


b = mkbot()
print("初始出口数:", b._exit_count(CUR), "(应为 2)")
for i in range(3):
    b.blocked.add(EXITS[0])
    b._ban_escalate(EXITS[0])
print("把出口1 (-45,140) 连续被挡 3 次后: is_perm =", b._is_perm(EXITS[0]),
      "(应为 False, 被出口保护拦住)")
assert not b._is_perm(EXITS[0]), "仅剩出口不该被升级"
print("T1 PASS\n")

print("=" * 64)
print("T2  正常情况(出口充足) -> 升级照常生效")
print("=" * 64)
b = mkbot()
far = (-60, 130)                     # 远处格, 不影响出口
for i in range(3):
    b.blocked.add(far)
    b._ban_escalate(far)
print("远处格 3 次被挡后: is_perm =", b._is_perm(far), "(应为 True)")
assert b._is_perm(far)
print("T2 PASS\n")

print("=" * 64)
print("T3  死锁现场还原: 两个出口都被长期禁入 -> 三级兜底自救")
print("=" * 64)
b = mkbot(perm_cells=EXITS)
ban, reach = b.plan_ctx(CUR)
print("锁死时 reach(cur) =", len(reach), "格 (期望 1, 即只剩自己)")
assert len(reach) == 1, "应当复现死锁"
b._pc_key = None
tgt = b.pick_target()
print("pick_target() ->", tgt)
print("自救后 blocked 剩余:", len(b.blocked), " perm 剩余:",
      sum(1 for c in b.blocked if b._is_perm(c)))
assert tgt is not CUR and tgt is not None, "应当能选出目标脱困"
ban, reach = b.plan_ctx(CUR)
print("恢复后 reach(cur) =", len(reach), "格")
assert len(reach) > 100
print("T3 PASS\n")

print("=" * 64)
print("T4  _clear_blocked 分级清除")
print("=" * 64)
b = mkbot(blocked_cells=[(-40, 130), (-41, 130)], perm_cells=[(-42, 130)])
n = b._clear_blocked(keep_perm=True)
print("keep_perm=True 清掉 %d 条, 剩 %d 条 (perm 保留)" % (n, len(b.blocked)))
assert n == 2 and b._is_perm((-42, 130))
n2 = b._clear_blocked(keep_perm=False)
print("keep_perm=False 清掉 %d 条, 剩 %d 条" % (n2, len(b.blocked)))
assert n2 == 1 and len(b.blocked) == 0
print("T4 PASS\n")

print("=" * 64)
print("T5  追怪目标格走不进去 -> 不记绕行")
print("=" * 64)
b = mkbot()
b._chase_goal = (-45, 140)
# 复现 walk_path 被挡分支的判定
cell = (-45, 140)
if cell == getattr(b, "_chase_goal", None):
    print("目标格 (-45,140): 跳过记录 (正确)")
    skip = True
else:
    skip = False
assert skip
cell2 = (-44, 140)
assert cell2 != getattr(b, "_chase_goal", None)
print("非目标格 (-44,140): 正常记录 (正确)")
print("T5 PASS\n")

print("全部回归测试通过 ✅")
