# -*- coding: utf-8 -*-
"""test_key.py — 按键通道诊断：向游戏发一个方向键，看队伍有没有真的移动。

用途：当 bot 大量「实测被挡(数据说可走)」时，先跑这个确认按键通道是否正常。
按键真值（K 空间，见项目记忆）：up=(+1,0) down=(-1,0) left=(0,-1) right=(0,+1)
"""
import io
import os
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import pal_bot as PB          # noqa: E402
from pal_bot_mem import Mem   # noqa: E402

EXPECT = {"up": (1, 0), "down": (-1, 0),
          "left": (0, -1), "right": (0, 1)}

hwnd = PB.find_game_window()[0]
g = PB.Game(hwnd)
mem = Mem()
print("窗口 hwnd=%s" % hwnd)

cur = mem.state()["K"]
print("起点 K=%s" % (cur,))
ok_n = 0
try_n = 0
for name in ("up", "down", "up", "down", "right", "left"):
    expect = EXPECT[name]
    g.flush_keys()
    time.sleep(0.2)
    g.key(name, 0.07)
    time.sleep(0.4)
    nxt = mem.state()["K"]
    d = (nxt[0] - cur[0], nxt[1] - cur[1])
    ok = d == expect
    try_n += 1
    ok_n += ok
    print("  %-5s K %s -> %s Δ=%s 期望%s  %s"
          % (name, cur, nxt, d, expect, "OK" if ok else "!! 未动/方向不符"))
    cur = nxt
    time.sleep(0.15)
print("结论: %d/%d 次按键生效 -> %s"
      % (ok_n, try_n, "通道正常" if ok_n >= try_n - 1
         else "★按键被大量丢弃(游戏失焦/被遮挡/有对话框?)"))
