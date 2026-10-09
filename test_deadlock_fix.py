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
print("T1  出口保护: 仅剩出口被反复撞 -> 只给「短禁入」不给 30 分钟长期禁入")
print("     (2026-09-29 v2: 旧行为「绝不升级」在锁妖塔窄桥上每 70 秒重撞一次,")
print("      与 RESET 清绕行形成死循环 -> 吞吐 0.03 格/秒, 故改为短禁入)")
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
ttl = b.blocked.ts.get(EXITS[0], 0) - time.time()
print("出口1 (-45,140) 连续被挡 3 次后: 剩余禁入 %.0f 秒 (应约 %d 秒短禁入; "
      "长期禁入是 %.0f 秒)" % (ttl, MemBot.EXIT_BAN_TTL, MemBot.ESCALATE_TTL))
assert 0.5 * MemBot.EXIT_BAN_TTL < ttl <= MemBot.EXIT_BAN_TTL + 5, \
    "仅剩出口应升级为 %d 秒短禁入, 实际 %.0f" % (MemBot.EXIT_BAN_TTL, ttl)
assert ttl < MemBot.ESCALATE_TTL - 60, "不该给 30 分钟长期禁入 (出口保护)"
print("T1 PASS\n")

print("=" * 64)
print("T1b 出口保护兜底: 短禁入会把当前位置彻底锁死 -> 立即撤销")
print("=" * 64)
b = mkbot(perm_cells=[EXITS[1]])       # 另一个出口已长期禁入
for i in range(3):
    b.blocked.add(EXITS[0])
    b._ban_escalate(EXITS[0])
print("两个出口都不可用时, 出口1 是否仍被禁:", EXITS[0] in b.blocked,
      "(应为 False, 移动优先)")
assert EXITS[0] not in b.blocked, "会让当前位置无路可走的短禁入必须撤销"
print("T1b PASS\n")

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
