# -*- coding: utf-8 -*-
"""场景体检 (scene_check.py) —— 换地图前先跑这个, 一秒知道"能不能直接上"。

底图全量: map_walk2.npy 已解码 MAP.MKF 全部 226 块 (与场景表 1:1 对齐),
         所以换地图 BFS 自动生效, 不需要重新解码。
本工具回答的是"新场景的地形有没有坑":
    ① 场景 -> 地图号 (内存 rgScene 实时读, 不用猜)
    ② 可走域 / 最大连通分量 (BFS 有没有路可走)
    ③ 报告门位 -> ±2 圈门禁后可达域  ← 唯一需要人工调参的地方
    ④ 与 REACH_MIN 比较, 给出建议

用法:
    python scene_check.py            # 体检当前所在场景
    python scene_check.py 70 1 100   # 体检指定场景
"""
import os
import re
import sys
from collections import deque

import numpy as np

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

REACH_MIN = 2000        # 与 pal_bot_mem2.MemBot2.REACH_MIN 保持一致
PAD = 2                 # 报告门位外扩圈数 (与 enter_scene 一致)
DIRS = ((1, 0), (-1, 0), (0, 1), (0, -1))


def load_report():
    p = os.path.join(BASE, "gyt_scene_report.txt")
    txt = open(p, encoding="utf-8", errors="ignore").read()
    rep_map = {int(a): int(b) for a, b in
               re.findall(r"场景\s*(\d+)\s+地图\s*(\d+)", txt)}
    rep_doors = {}
    for m in re.finditer(r"场景\s*(\d+)\s+对象\s*\d+\s+px\(\s*(\d+),\s*(\d+)\)"
                         r"\s+mode=\d+", txt):
        sc, px, py = int(m.group(1)), int(m.group(2)), int(m.group(3))
        rep_doors.setdefault(sc, set()).add(
            ((px - 2 * py) // 32, (px + 2 * py - 32) // 32))
    return rep_map, rep_doors


def kwalk(grid):
    s = set()
    for x in range(64):
        for y in range(128):
            r = grid[y, x]
            for hh in (0, 1):
                if not r[hh]:
                    s.add((x - y, x + y - 1 + hh))
    return s


def bfs(ks, src, bad=()):
    bad = set(bad)
    if src not in ks:
        return set()
    prev = {src}
    q = deque([src])
    while q:
        p = q.popleft()
        for d in DIRS:
            t = (p[0] + d[0], p[1] + d[1])
            if t in ks and t not in bad and t not in prev:
                prev.add(t)
                q.append(t)
    return prev


def components(ks):
    """最大连通分量大小。"""
    seen, best = set(), 0
    for c in ks:
        if c in seen:
            continue
        comp = bfs(ks, c)
        seen |= comp
        best = max(best, len(comp))
    return best


def check(sc, ks, rep_doors, cur=None):
    d_raw = rep_doors.get(sc, set())
    doors = set()
    for (a, b) in d_raw:
        for ua in range(-PAD, PAD + 1):
            for ub in range(-PAD, PAD + 1):
                doors.add((a + ua, b + ub))
    reach_center = cur if cur else None
    if reach_center is None:
        # 没有落点信息 -> 用"可走域里最靠中间"的格当估计
        if not ks:
            reach_center = (0, 0)
        else:
            mid = np.median(np.array(sorted(ks)), axis=0)
            reach_center = min(ks, key=lambda c: (abs(c[0] - mid[0])
                                                 + abs(c[1] - mid[1])))
    r_noban = bfs(ks, reach_center)
    r_ban = bfs(ks, reach_center, doors)
    return {"doors_raw": d_raw, "doors": doors, "n_kwalk": len(ks),
            "n_comp": components(ks), "center": reach_center,
            "n_noban": len(r_noban), "n_ban": len(r_ban),
            "center_ok": reach_center in ks}


def main():
    mem = None
    cur_scene = None
    try:
        from pal_bot_mem import Mem
        mem = Mem()
        cur_scene = int(mem.state()["scene"])
    except Exception as e:
        print("!! 读游戏内存失败(游戏没开?): %s" % e)

    args = [int(a) for a in sys.argv[1:]]
    targets = args if args else ([cur_scene] if cur_scene else [])
    if not targets:
        print("用法: python scene_check.py [场景号 ...]  (不给参数=当前场景)")
        return

    grids = np.load(os.path.join(BASE, "map_walk2.npy"))
    rep_map, rep_doors = load_report()

    print("底图: map_walk2.npy %s (全量解码, 换地图自动生效)" % (grids.shape,))
    print("当前场景: %s" % cur_scene)
    print("-" * 78)
    for sc in targets:
        m = (mem.scene_map.get(sc) if mem else None) or rep_map.get(sc)
        if m is None:
            print("场景 %-4d -> 无地图映射 (内存无此场景且报告也没记录)" % sc)
            continue
        if not (0 <= m < grids.shape[0]):
            print("场景 %-4d -> 地图 %s 超出底图范围!" % (sc, m))
            continue
        ks = kwalk(grids[m])
        cur = None
        if mem and sc == cur_scene:
            cur = mem.state()["K"]
        info = check(sc, ks, rep_doors, cur)
        verdict = []
        if not info["center_ok"]:
            verdict.append("落点不在可走域(!!)")
        if info["n_ban"] < 200:
            verdict.append("门禁后几乎无处可走(门锁死了地形)")
        elif info["n_ban"] < REACH_MIN:
            verdict.append("门禁后 %d < REACH_MIN=%d -> 会触发逐级放宽"
                           % (info["n_ban"], REACH_MIN))
        print("场景 %-4d (地图 %-4d) 落点%-12s 可走域 %-6d 最大连通 %-6d"
              % (sc, m, str(info["center"]), info["n_kwalk"], info["n_comp"]))
        print("          门位: 报告 %d 个 -> 外扩±%d 圈 %d 格 ; "
              "无门禁可达 %-6d 门禁后可达 %-6d%s"
              % (len(info["doors_raw"]), PAD, len(info["doors"]),
                 info["n_noban"], info["n_ban"],
                 ("  <== " + "; ".join(verdict)) if verdict else "   OK"))
    print("-" * 78)
    print("判读: 可走域>0 且连通分量够大 = BFS/探索能直接生效;")
    print("      门禁后可达 < %d 才需要调 REACH_MIN 或把该场景放进黑名单。" % REACH_MIN)


if __name__ == "__main__":
    main()
