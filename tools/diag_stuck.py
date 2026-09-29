# -*- coding: utf-8 -*-
"""诊断: 人物被困原地 / 候选为空。只读, 不发按键。"""
import os
import sys

import numpy as np

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(BASE)
sys.path.insert(0, BASE)

from pal_bot_mem import Mem, MemBot            # noqa: E402

m = Mem()
st = m.state()
scene, K = st["scene"], st["K"]
mp = m.scene_map[scene]
print("内存: 场景 %d -> 地图 %d, K %s, px,py=(%d,%d)" % (scene, mp, K, st["px"], st["py"]))


def findxy(a, b):
    out = []
    for hh in (0, 1):
        s, d = a + b, b - a
        if (s + 1 - hh) % 2 == 0 and (d + 1 - hh) % 2 == 0:
            out.append(((s + 1 - hh) // 2, (d + 1 - hh) // 2, hh))
    return out


print("cur 反推 (x,y,h):", findxy(*K))

# 复现 kwalk 三种口径
bot = MemBot.__new__(MemBot)
bot.grids = np.load(os.path.join(BASE, "map_walk2.npy"))
bot._kwalk_cache = {}
ks_new = bot.kwalk(mp)          # 现行 (含边界剔除)

grid = bot.grids[mp]
ks_old, ks_edge = set(), set()
for x in range(64):
    for y in range(128):
        row = grid[y, x]
        for hh in (0, 1):
            if not row[hh]:
                t = (x - y, x + y - 1 + hh)
                ks_old.add(t)
                if x < 5 or x > 57 or y < 7:
                    ks_edge.add(t)
print("ks_old %d / ks_new %d / 被边界剔除 %d" % (len(ks_old), len(ks_new), len(ks_old - ks_new)))
print("cur 是否可走: old=%s new=%s" % (K in ks_old, K in ks_new))

print("\n=== 周围 9x9 (以 cur 为中心, 行=a 递增, 列=b 递增) ===")
print("    " + "".join("%4d" % (K[1] + db) for db in range(-4, 5)))
for da in range(-4, 5):
    line = ""
    for db in range(-4, 5):
        t = (K[0] + da, K[1] + db)
        if t == K:
            ch = "  ()"
        elif t not in ks_old:
            ch = "   ."
        elif t in ks_new:
            ch = "   o"
        else:
            ch = "   x"     # 被边界剔除
        line += ch
    print("a%+3d%s" % (da, line))
print("图例: () 当前   o 可走   x 被边界剔除   . 数据即不可走")

# BFS reach 复现
bad = set()
prev = {K: None}
from collections import deque
q = deque([K])
while q:
    p = q.popleft()
    for d in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        t = (p[0] + d[0], p[1] + d[1])
        if t in ks_new and t not in bad and t not in prev:
            prev[t] = p
            q.append(t)
print("\nreach(cur) 大小 = %d" % len(prev))
if len(prev) <= 1:
    print("!! 周边完全走不出去 -> 需要看上面的 9x9")
else:
    xs = [c[0] for c in prev]
    ys = [c[1] for c in prev]
    print("可达域 K 范围 a[%d,%d] b[%d,%d]" % (min(xs), max(xs), min(ys), max(ys)))
