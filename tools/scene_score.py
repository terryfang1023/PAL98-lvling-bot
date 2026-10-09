# -*- coding: utf-8 -*-
"""scene_score.py —— 全场景「刷怪性价比」评分表 (只读分析工具)

回答一个问题: 全地图 100 多个场景里, 哪个值得刷、哪个只是过路?

口径 (与 tools/route_odometer.py 完全一致, 保证两处数据可比):
  · 可走域 = map_walk2.npy 解码 + 引擎边界检查(x<5/x>57/y<7), 与 bot 的 kwalk 一致
  · 落点   = 从场景传送阵(CHANGE_SCENE 对象)的邻格估计 —— 因为 bot 是穿门进来的,
             落点决定"哪些怪点真的够得着"(锁妖塔分层: 同一个场景可能分成两块)
  · 巡回里程 = 主连通块内怪点(站格)的最近邻巡回 + 2-opt, K 空间 BFS 最短路
  · 时间   = 走路 1/1.2 秒每格(实测纯走速) + 每场 11.2 秒战斗
  · ceil   = 几何上限吞吐 场/分 (乐观: 不含停顿/被挡/目标失败)
  · expect = ceil x EFF_TOTAL(0.50) —— 与实测对得上(场景157 实测 0.50 场/分)

★ 不修改任何 bot 行为。产物 scene_score.json 供 bot 的「自动换场景」读取。

用法:
    python tools/scene_score.py                # 全场景评分, 写 scene_score.json
    python tools/scene_score.py --top 30       # 只看前 30 名
    python tools/scene_score.py 157 229 230    # 只看指定场景
"""
import json
import os
import sys
from collections import deque

import numpy as np

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from route_odometer import kwalk, bfs_from, load_grids       # noqa: E402

FIGHT_S = 11.2            # 一场战斗的耗时 (实测中位 11.2s)
WALK_CELL_S = 1.0 / 1.2   # 纯走路每格耗时 (实测有效走速 1.2 格/秒)
MON_CD = 70.0             # 怪被打掉后的重生周期 (实测隐藏 ~800 帧≈45s, bot 冷却 70s)
                          # ★ 没有这一项, "只有 1 个怪点"的场景会被算成 5.4 场/分
EFF_TOTAL = 0.50          # 端到端效率: 几何上限 -> 实测吞吐 的折算系数
MAX_PTS = 48              # 单场景参与巡回计算的要素上限(性能保护)
MAX_ENTRY = 8             # 最多试几个"落点候选"
ORTHO = ((1, 0), (-1, 0), (0, 1), (0, -1))
PASS_REACH = 200          # 落点可达域小于此 -> 过路场景
PASS_MIN_MON = 3          # 可达怪点少于此 -> 过路场景


def stand_of(k, walk):
    """怪点/宝箱的站格: 优先正交 1 格邻格(触发圈), 否则自身。"""
    cand = [(k[0] + a, k[1] + b) for a, b in ORTHO
            if (k[0] + a, k[1] + b) in walk]
    if cand:
        return min(cand)
    return k if k in walk else None


def ring_len(pts, dist):
    """最近邻巡回里程 + 2-opt 改良。dist(i,j) -> 最短路格数或 None。"""
    n = len(pts)
    if n < 2:
        return 0, 0
    rem = set(range(1, n))
    order, cur = [0], 0
    while rem:
        nxt = min(rem, key=lambda j: (dist(cur, j) if dist(cur, j) is not None
                                     else 10 ** 9))
        if dist(cur, nxt) is None:
            rem.discard(nxt)                       # 不可达: 不参与里程
            continue
        order.append(nxt)
        rem.discard(nxt)
        cur = nxt

    def rl(o):
        tot, c = 0, o[0]
        for j in o[1:]:
            d = dist(c, j)
            if d is None:
                return None
            tot += d
            c = j
        return tot

    greedy = rl(order) or 0
    best, bl = list(order), greedy
    improved, it = True, 0
    while improved and it < 40:
        improved = False
        it += 1
        m = len(best)
        for i in range(1, m - 1):
            for j in range(i + 1, m):
                cand = best[:i] + best[i:j + 1][::-1] + best[j + 1:]
                cl = rl(cand)
                if cl is not None and cl < bl:
                    best, bl, improved = cand, cl, True
        if not improved:
            break
    return greedy, bl


def score_scene(sc, si, grids):
    m = si.get("map")
    if m is None or m <= 0 or m >= len(grids):
        return None
    walk = kwalk(grids, m)
    if not walk:
        return None
    mons = [(tuple(x["K"]), x) for x in (si.get("monsters") or [])]
    chests = [(tuple(x["K"]), x) for x in (si.get("chests") or [])]

    # ---- 落点候选: 传送阵邻格(穿门进来的地方) + 要素站格 ----
    entries = []
    for p in (si.get("portals") or []):
        k = tuple(p["K"])
        near = [(k[0] + a, k[1] + b) for a in range(-2, 3) for b in range(-2, 3)
                if (k[0] + a, k[1] + b) in walk]
        if near:
            near.sort(key=lambda c: abs(c[0] - k[0]) + abs(c[1] - k[1]))
            entries.append(near[0])

    def in_reach(p, reach):
        s = stand_of(p, walk)
        return s is not None and s in reach

    # 选"能覆盖最多怪点"的落点。
    # ① 先只用门位(最有把握: bot 就是穿门进来的)
    best = None
    for e in entries[:MAX_ENTRY]:
        reach = set(bfs_from(walk, e))
        cnt = sum(1 for k, _ in mons if in_reach(k, reach))
        if best is None or cnt > best[0]:
            best = (cnt, e, reach)
    # ② 门位覆盖不足(很多场景没有传送阵对象) -> 改从每个怪点试,
    #    取"同块怪点最多"的那一块 = 这个场景最多能连着刷几只
    if best is None or best[0] < PASS_MIN_MON:
        seen_e = set(entries[:MAX_ENTRY])
        for k, _ in mons[:MAX_ENTRY + 4]:
            s = stand_of(k, walk)
            if s is None or s in seen_e:
                continue
            seen_e.add(s)
            reach = set(bfs_from(walk, s))
            cnt = sum(1 for k2, _ in mons if in_reach(k2, reach))
            if best is None or cnt > best[0]:
                best = (cnt, s, reach)
    if best is None:
        return None
    _, entry, reach = best

    mon_ok = [(k, x) for k, x in mons if in_reach(k, reach)]
    chest_ok = [(k, x) for k, x in chests if in_reach(k, reach)]

    pts, tags = [], []
    for k, _ in mon_ok[:MAX_PTS]:
        pts.append(stand_of(k, walk))
        tags.append("mon")
    for k, _ in chest_ok[:max(0, MAX_PTS - len(pts))]:
        pts.append(stand_of(k, walk))
        tags.append("chest")
    pts = [p for p in pts if p is not None]
    if len(pts) > MAX_PTS:
        pts = pts[:MAX_PTS]

    # ---- 距离矩阵 (每个点 BFS 一次) ----
    D = {}
    for p in pts:
        D[p] = bfs_from(walk, p)

    def dist(i, j):
        return D[pts[i]].get(pts[j])

    mon_idx = [i for i, t in enumerate(tags) if t == "mon"][:len(pts)]
    g_all, o_all = ring_len(pts, dist)
    g_mon, o_mon = (ring_len([pts[i] for i in mon_idx],
                             lambda a, b: dist(mon_idx[a], mon_idx[b]))
                    if len(mon_idx) >= 2 else (0, 0))

    n = len(mon_idx)
    if n < 1:
        ceil_per_min = 0.0
        ring = 0
        lead = 0
    else:
        ring = o_mon
        # 接入段: 从落点走到第一个怪点的距离(只算一次)
        lead = D[pts[mon_idx[0]]].get(entry, 0) or 0
        cycle = (ring + lead) * WALK_CELL_S + n * FIGHT_S
        # ★ 怪的重生周期是硬下限: 一圈走太快, 回头怪还没重生, 只能等
        cycle_eff = max(cycle, MON_CD)
        ceil_per_min = 60.0 * n / cycle_eff if cycle_eff > 0 else 0.0

    is_pass = (n < PASS_MIN_MON) or (len(reach) < PASS_REACH)
    return {
        "map": int(m),
        "n_mon_all": len(mons),
        "n_mon": n,                       # 落点可达的怪点数
        "n_chest": len(chest_ok),
        "reach": len(reach),
        "entry": list(entry),
        "ring": int(ring),                # 只刷怪的巡回里程(2-opt)
        "lead": int(lead),                # 落点 -> 第一个怪点 的距离
        "ring_all": int(o_all),           # 怪+箱
        "ring_line": int(g_mon),          # 最近邻(未优化)
        "cycle_s": round(max((ring + lead) * WALK_CELL_S + n * FIGHT_S,
                             MON_CD if n else 0), 1),
        "ceil": round(ceil_per_min, 3),   # 几何上限 场/分 (含重生周期限制)
        "expect": round(ceil_per_min * EFF_TOTAL, 3),   # 预期实测 场/分
        "pass": bool(is_pass),
    }


def main():
    info = json.load(open(os.path.join(BASE, "scene_info.json"), encoding="utf-8"))
    grids, gname = load_grids()
    argv = list(sys.argv[1:])
    top = 25
    if "--top" in argv:
        i = argv.index("--top")
        try:
            top = int(argv[i + 1])
        except (IndexError, ValueError):
            top = 25
        del argv[i:i + 2]
    only = [a for a in argv if a.isdigit()]

    scenes = {}
    skipped = []
    for sc, si in info.items():
        if only and sc not in only:
            continue
        r = score_scene(sc, si, grids)
        if r is None:
            skipped.append(sc)
            continue
        scenes[sc] = r

    out = {"meta": {"grid": gname, "fight_s": FIGHT_S,
                    "walk_cell_s": round(WALK_CELL_S, 4),
                    "eff_total": EFF_TOTAL,
                    "note": "ceil=几何上限场/分; expect=折算实测; pass=过路场景"},
            "scenes": scenes}

    if not only:
        p = os.path.join(BASE, "scene_score.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
        print("已写出 %s (%.1f KB)" % (p, os.path.getsize(p) / 1024.0))

    rows = sorted(scenes.items(), key=lambda kv: -kv[1]["expect"])
    n_pass = sum(1 for _, v in scenes.items() if v["pass"])
    print("=" * 96)
    print("全场景刷怪性价比评分  (共 %d 个场景, 其中 %d 个判为「过路」"
          " | 跳过 %d 个无有效地图)" % (len(scenes), n_pass, len(skipped)))
    print("  n    = 落点可达的怪点数   ring = 只刷怪巡回里程(格)"
          "   ceil = 几何上限 场/分   expect = 预期实测 场/分")
    print("=" * 96)
    print("%-6s %5s %5s %6s %7s %7s %8s %8s  %s"
          % ("场景", "map", "n怪", "可达怪", "巡回格", "可达域", "ceil", "expect",
             "备注"))
    for sc, v in (rows if only else rows[:top]):
        print("%-6s %5d %5d %6d %7d %7d %8.2f %8.2f  %s"
              % (sc, v["map"], v["n_mon_all"], v["n_mon"], v["ring"],
                 v["reach"], v["ceil"], v["expect"],
                 "过路" if v["pass"] else ""))
    if not only and len(rows) > top:
        print("...  (共 %d 个, 用 --top N 调整)" % len(rows))
        print("    最差 5 名:")
        for sc, v in rows[-5:]:
            print("%-6s %5d %5d %6d %7d %7d %8.2f %8.2f  %s"
                  % (sc, v["map"], v["n_mon_all"], v["n_mon"], v["ring"],
                     v["reach"], v["ceil"], v["expect"],
                     "过路" if v["pass"] else ""))
    if skipped:
        print("跳过(无有效地图): %s" % ", ".join(sorted(skipped, key=int)))
    print("-" * 96)
    print("判读: expect 排名就是「换场景」的目标优先级; pass=过路场景(进入后不必停留)。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
