# -*- coding: utf-8 -*-
"""probe_walk.py — 「地图数据 vs 引擎实测」四方向对照。

用途：bot 大量报「实测被挡(数据说可走)」时，用它判定到底是
      ①地图数据(kwalk)错  还是 ②真地形挡路  还是 ③输入被吞。
做法：读内存拿当前 K，对四个方向逐个「查数据 + 发一次方向键」比对。

只读 + 单向试走，试走成功会立刻按键走回原地。
"""
import io
import os
import sys
import time

import numpy as np

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(BASE)
sys.path.insert(0, BASE)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import pal_bot as PB                       # noqa: E402
from pal_bot_mem import Mem, MemBot        # noqa: E402

BACK = {"up": "down", "down": "up", "left": "right", "right": "left"}
DIRS = [("up", (1, 0)), ("down", (-1, 0)), ("left", (0, -1)), ("right", (0, 1))]

m = Mem()
bot = MemBot.__new__(MemBot)
bot.grids = np.load(os.path.join(BASE, "map_walk2.npy"))
bot._kwalk_cache = {}
st = m.state()
scene, K = st["scene"], st["K"]
mp = m.scene_map[scene]
ks = bot.kwalk(mp)
g = PB.Game(PB.find_game_window()[0])

print("场景 %d 地图 %d  起点 K%s  可走格 %d" % (scene, mp, K, len(ks)))
print("%-6s %-13s %-8s %-16s %s" % ("键", "目标K", "数据", "实测", "结论"))
bad = 0
for name, d in DIRS:
    tgt = (K[0] + d[0], K[1] + d[1])
    data_ok = tgt in ks
    g.flush_keys()
    time.sleep(0.2)
    g.key(name, 0.07)
    time.sleep(0.4)
    now = m.state()["K"]
    moved = (now == tgt)
    if data_ok and moved:
        v = "一致(可走)"
    elif not data_ok and not moved:
        v = "一致(不可走)"
    elif data_ok and not moved:
        v = "★数据说可走, 引擎不让走"
        bad += 1
    else:
        v = "★数据说不可走, 却走成了(%s)" % (now,)
        bad += 1
    print("%-6s %-13s %-8s %-16s %s"
          % (name, tgt, "可走" if data_ok else "不可走",
             ("动了 -> %s" % (now,)) if moved else "没动", v))
    if moved:                                   # 走回原地
        g.flush_keys()
        time.sleep(0.15)
        g.key(BACK[name], 0.07)
        time.sleep(0.35)
        K = m.state()["K"]
        print("        (已退回 K%s)" % (K,))

print("\n不一致方向数: %d/4 -> %s"
      % (bad, "地图数据与引擎存在偏差" if bad else "数据与引擎完全一致"))
