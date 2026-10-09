# -*- coding: utf-8 -*-
"""sample_moves.py —— 高频采样队伍坐标, 量出真实的"每格间隔"分布 (只读)

用途: 每格 1.5s 到底是"均匀地慢"还是"卡在少数几次长时间停摆"?
      只读内存, 不按键、不移动、不写日志。

用法:
    python tools/sample_moves.py [采样秒数, 默认 45]
"""
import os
import sys
import time
import collections

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from pal_bot_mem import Mem                            # noqa: E402

DT = 0.004
DIRNAME = {0: "down", 1: "left", 2: "up", 3: "right", 255: "?"}


def main():
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 45.0
    mem = Mem()
    s = mem.state()
    if s is None:
        print("读内存失败 (游戏没开?)")
        return 1
    print("场景 %d  起始 K%s  采样 %.0fs (%.0f Hz)"
          % (s["scene"], s["K"], secs, 1.0 / DT))
    t_end = time.time() + secs
    prev = (s["K"], mem.raw()[51] if mem.raw() else -1)
    last_move = time.time()
    moves = []          # (时刻, 从上一格到这一格的间隔, K)
    turns = 0
    n = 0
    scene_chg = 0
    while time.time() < t_end:
        st = mem.state()
        n += 1
        if st is not None:
            if st["scene"] != s["scene"]:
                scene_chg += 1
                s["scene"] = st["scene"]
            K = st["K"]
            W = mem.raw()
            d = W[51] if W else -1
            if K != prev[0]:
                now = time.time()
                moves.append((now, now - last_move, K))
                last_move = now
            elif d != prev[1]:
                turns += 1
            prev = (K, d)
        time.sleep(DT)
    now = time.time()
    print("采样 %d 次, 换场景 %d 次, 转身 %d 次" % (n, scene_chg, turns))
    if not moves:
        print("!! 整段一次都没动")
        return 0
    iv = sorted(m[1] for m in moves)
    tot = sum(iv)
    print("移动 %d 格, 总耗时 %.1fs -> %.2f 格/秒 (每格均值 %.3fs)"
          % (len(iv), tot, len(iv) / tot, tot / len(iv)))
    print("间隔分布: 最小 %.3f  25%% %.3f  中位 %.3f  75%% %.3f  最大 %.3f"
          % (iv[0], iv[len(iv) // 4], iv[len(iv) // 2],
             iv[len(iv) * 3 // 4], iv[-1]))
    buckets = collections.Counter()
    for x in iv:
        if x < 0.30:
            buckets["<0.30s (正常)"] += 1
        elif x < 0.60:
            buckets["0.30-0.6s"] += 1
        elif x < 1.5:
            buckets["0.6-1.5s"] += 1
        elif x < 5:
            buckets["1.5-5s"] += 1
        else:
            buckets[">=5s (停摆)"] += 1
    print("间隔直方图 (格数 / 占时):")
    for k in ("<0.30s (正常)", "0.30-0.6s", "0.6-1.5s", "1.5-5s",
              ">=5s (停摆)"):
        if buckets[k]:
            share = sum(x for x in iv if _in(k, x))
            print("   %-16s %4d 格   %6.1fs (%.0f%%)"
                  % (k, buckets[k], share, share * 100 / tot))
    if now - last_move > 3:
        print("   (采样结束时已 %.1fs 未移动)" % (now - last_move))
    print("最长的 6 段停顿:")
    for t, dt, K in sorted(moves, key=lambda m: -m[1])[:6]:
        print("   %s 停 %.2fs 后走到 K%s"
              % (time.strftime("%H:%M:%S", time.localtime(t)), dt, K))
    print("本工具只读, 不按键、不走路、不写日志。")
    return 0


def _in(k, x):
    if k.startswith("<0.30"):
        return x < 0.30
    if k.startswith("0.30"):
        return 0.30 <= x < 0.60
    if k.startswith("0.6"):
        return 0.60 <= x < 1.5
    if k.startswith("1.5"):
        return 1.5 <= x < 5
    return x >= 5


if __name__ == "__main__":
    sys.exit(main())
