# -*- coding: utf-8 -*-
"""route_odometer.py —— 路线里程计（只读分析工具, 不改 bot 行为）

回答一个问题: 把某场景的「怪点(+宝箱)」串成一条巡回路线, 要走多少格?
以及: 最优顺序 vs 现在用的「最近邻贪心」差多少?

用法:
    python tools/route_odometer.py [场景号] [起点K]
       场景号 默认 157; 起点K 形如 -87,105, 不给则用第一个怪点

结论口径:
  · 可走域 = map_walk2.npy 解码 + 引擎边界检查(x<5/x>57/y<7), 与 bot 的 kwalk 一致
  · 不含运行时 blocked/门禁(那会让可达域更小), 故本工具算的是「乐观下界」
  · 步数 = K 空间最短路的格数; 时间按 1.2 格/秒 折算(实测有效走速)
"""
import json
import os
import sys
from collections import deque

import numpy as np

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_grids():
    for name in ("map_walk2.npy", "map_walk.npy"):
        p = os.path.join(BASE, name)
        if os.path.exists(p):
            return np.load(p), name
    raise SystemExit("找不到 map_walk*.npy")


def kwalk(grids, m):
    """与 pal_bot_mem.py:164 kwalk 完全一致的解码。"""
    s = set()
    for x in range(64):
        for y in range(128):
            if x < 5 or x > 57 or y < 7:
                continue
            row = grids[m][y, x]
            for hh in (0, 1):
                if not row[hh]:
                    s.add((x - y, x + y - 1 + hh))
    return s


def bfs_from(walk, src):
    """从 src 出发的 BFS, 返回 {格: 距离}。"""
    dist = {src: 0}
    q = deque([src])
    while q:
        p = q.popleft()
        d = dist[p] + 1
        for dd in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            t = (p[0] + dd[0], p[1] + dd[1])
            if t in walk and t not in dist:
                dist[t] = d
                q.append(t)
    return dist


def route_len(order, D, start_i):
    total = 0
    cur = start_i
    for nxt in order:
        if D[cur][nxt] is None:
            return None
        total += D[cur][nxt]
        cur = nxt
    return total


def two_opt(order, D, start_i, iters=400):
    n = len(order)
    best = list(order)
    bl = route_len(best, D, start_i)
    if bl is None:
        return best, None
    improved = True
    it = 0
    while improved and it < iters:
        improved = False
        it += 1
        for i in range(n - 1):
            for j in range(i + 1, n):
                cand = best[:i] + best[i:j + 1][::-1] + best[j + 1:]
                cl = route_len(cand, D, start_i)
                if cl is not None and cl < bl - 1e-9:
                    best, bl, improved = cand, cl, True
        if not improved:
            break
    return best, bl


def scan_all():
    """全场景粗筛: 用 K 空间直线距离(不算 BFS, 秒级) 找「怪点密集」的场景。

    直线距离是真实步数的下界(走廊地图 BFS ≈ 直线 x 3~4), 用来排序足够。
    指标 「最近邻间距中位」 = 每个怪点到最近另一怪点的距离(直线), 越小越适合
    「小范围循环刷」。
    """
    info = json.load(open(os.path.join(BASE, "scene_info.json"), encoding="utf-8"))
    rows = []
    for sc, si in info.items():
        ms = si.get("monsters") or []
        if len(ms) < 3:
            continue
        P = [(x["K"][0], x["K"][1]) for x in ms]
        n = len(P)
        A = np.array(P, dtype=np.int64)
        D = np.abs(A[:, 0, None] - A[None, :, 0]) + \
            np.abs(A[:, 1, None] - A[None, :, 1])
        np.fill_diagonal(D, 10 ** 9)
        nnd = np.min(D, axis=1)                    # 每点到最近邻的距离
        # 最近邻巡回(直线口径)
        rem = set(range(n))
        cur = 0
        rem.discard(0)
        tot = 0
        while rem:
            nxt = min(rem, key=lambda j: D[cur, j])
            tot += int(D[cur, nxt])
            rem.discard(nxt)
            cur = nxt
        rows.append({
            "sc": sc, "n": n, "nnd_med": int(np.median(nnd)),
            "nnd_min": int(nnd.min()), "tour": tot,
            "mon_per_tour": 1000.0 * n / max(tot, 1),   # 每千格能碰到几个怪点
        })
    rows.sort(key=lambda r: (r["nnd_med"], -r["n"]))
    print("=" * 84)
    print("全场景怪点密度排名 (只看怪点 >=3 个的场景, 共 %d 个)" % len(rows))
    print("  nnd_med = 到最近怪点的直线距离中位(格); 越小越好刷")
    print("  M/T    = 每 1000 格巡回能覆盖几个怪点 (越大越划算)")
    print("=" * 84)
    print("%-5s %3s %8s %8s %8s %8s" % ("场景", "怪数", "最近邻距中位", "最近邻距最小",
                                        "巡回格数", "M/T"))
    for r in rows[:20]:
        print("%-5s %3d %8d %8d %8d %8.1f"
              % (r["sc"], r["n"], r["nnd_med"], r["nnd_min"], r["tour"],
                 r["mon_per_tour"]))
    print("...")
    for r in rows[-5:]:
        print("%-5s %3d %8d %8d %8d %8.1f"
              % (r["sc"], r["n"], r["nnd_med"], r["nnd_min"], r["tour"],
                 r["mon_per_tour"]))
    print("\n按「每千格覆盖怪点数」排名前 12:")
    top = sorted(rows, key=lambda r: -r["mon_per_tour"])[:12]
    for r in top:
        print("  场景 %-4s 怪 %2d 个   最近邻距中位 %3d 格   巡回 %4d 格   M/T %.1f"
              % (r["sc"], r["n"], r["nnd_med"], r["tour"], r["mon_per_tour"]))
    s157 = [r for r in rows if r["sc"] == "157"]
    if s157:
        r = s157[0]
        rank_nd = rows.index(r) + 1
        rank_mt = sorted(rows, key=lambda x: -x["mon_per_tour"]).index(r) + 1
        print("\n场景 157 (当前所在): 怪 %d, 最近邻距中位 %d 格, 直线巡回 %d 格, M/T %.1f"
              % (r["n"], r["nnd_med"], r["tour"], r["mon_per_tour"]))
        print("  密度排名 %d / %d ; 性价比排名 %d / %d"
              % (rank_nd, len(rows), rank_mt, len(rows)))


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--scan":
        scan_all()
        return
    sc = sys.argv[1] if len(sys.argv) > 1 else "157"
    info = json.load(open(os.path.join(BASE, "scene_info.json"), encoding="utf-8"))
    si = info.get(str(sc))
    if not si:
        raise SystemExit("scene_info.json 里没有场景 %s" % sc)
    m = si["map"]
    grids, gname = load_grids()
    if m >= len(grids):
        raise SystemExit("map %d 超出 %s 范围" % (m, gname))

    walk = kwalk(grids, m)
    mons = [(tuple(x["K"]), x) for x in (si.get("monsters") or [])]
    chests = [(tuple(x["K"]), x) for x in (si.get("chests") or [])]

    # 站格: 怪点取正交 1 格邻格中可走的(触发圈就是正交 1 格); 没有则退而取怪点自身
    ORTHO = ((1, 0), (-1, 0), (0, 1), (0, -1))

    def stand_of(k):
        cand = [(k[0] + a, k[1] + b) for a, b in ORTHO
                if (k[0] + a, k[1] + b) in walk]
        return cand[0] if cand else (k if k in walk else None)

    pts, names = [], []
    reach0 = None
    if len(sys.argv) > 2:
        a, b = sys.argv[2].split(",")
        reach0 = bfs_from(walk, (int(a), int(b)))
    # 主连通块 = 从第一个可站格出发能到的集合
    anchor = None
    for k, _ in mons:
        s = stand_of(k)
        if s:
            anchor = s
            break
    if anchor is None:
        for k, _ in chests:
            s = stand_of(k)
            if s:
                anchor = s
                break
    main_cc = bfs_from(walk, anchor) if anchor else {}

    for k, x in mons:
        s = stand_of(k)
        pts.append(s)
        names.append("怪%s" % (k,))
    n_mon = len(pts)
    for k, x in chests:
        s = stand_of(k)
        pts.append(s)
        names.append("箱%s" % (k,))

    ok = [(p, nm) for p, nm in zip(pts, names) if p is not None]
    unreachable = [nm for p, nm in zip(pts, names) if p is None]
    out_of_cc = [nm for p, nm in zip(pts, names)
                 if p is not None and p not in main_cc]
    print("场景 %s (map %d):  可走域 %d 格   主连通块 %d 格"
          % (sc, m, len(walk), len(main_cc)))
    print("要素: 怪 %d  宝箱 %d   站格不可走 %d   不在主连通块 %d"
          % (n_mon, len(chests), len(unreachable), len(out_of_cc)))
    if unreachable:
        print("  不可走站格: %s" % ", ".join(unreachable))
    if out_of_cc:
        print("  另一连通块: %s" % ", ".join(out_of_cc))

    # 只保留主连通块内的点
    use = [(p, nm) for p, nm in zip(pts, names)
           if p is not None and p in main_cc]
    if len(use) < 2:
        raise SystemExit("可用点太少")
    P = [p for p, _ in use]
    NM = [nm for _, nm in use]

    # 起点
    if reach0 is not None and tuple(int(x) for x in sys.argv[2].split(",")) in walk:
        start_pt = tuple(int(x) for x in sys.argv[2].split(","))
        print("起点: K%s (命令行指定)" % (start_pt,))
    else:
        start_pt = P[0]
        print("起点: K%s (= %s, 默认用第一个怪点)" % (start_pt, NM[0]))

    # 距离矩阵: 从每个点(含起点)出发 BFS
    nodes = P + ([start_pt] if start_pt not in P else [])
    D = {}
    for i, p in enumerate(nodes):
        d = bfs_from(walk, p)
        D[p] = d
    si_i = nodes.index(start_pt)

    def Dof(i, j):
        return D[nodes[i]].get(nodes[j])

    # 只保留主连通块内的怪(前面的 use 已过滤, 这里按名字还原分组)
    mon_idx = [i for i, nm in enumerate(NM) if nm.startswith("怪")]
    all_idx = list(range(len(P)))

    print("\n--- 只刷怪 (%d 个点) ---" % len(mon_idx))
    report(mon_idx, P, NM, Dof, si_i, nodes, start_pt)

    print("\n--- 怪 + 宝箱 (%d 个点) ---" % len(all_idx))
    report(all_idx, P, NM, Dof, si_i, nodes, start_pt)

    density(P, NM, Dof)


def density(P, NM, Dof):
    """局部密度: 每个点周围 R 格内有多少要素 —— 决定「怪窝循环」能否成立。"""
    print("\n--- 局部密度 (决定「小范围循环」值不值得) ---")
    n = len(P)
    for R in (10, 18, 28):
        best = []
        for i in range(n):
            grp = [j for j in range(n)
                   if j != i and Dof(i, j) is not None and Dof(i, j) <= R]
            best.append((len(grp) + 1, i, grp))
        best.sort(reverse=True)
        cnt, i, grp = best[0]
        # 该簇内部巡回里程(从 i 出发)
        sub = [i] + grp
        tot = 0
        cur = i
        rem = list(grp)
        while rem:
            nxt = min(rem, key=lambda j: (Dof(cur, j) if Dof(cur, j) is not None
                                         else 10 ** 9))
            tot += Dof(cur, nxt) or 0
            rem.remove(nxt)
            cur = nxt
        cyc = tot / 1.2 + cnt * 11.2
        print("  R=%2d 格: 最密簇 %d 个点 (中心 K%s) 内部巡回 %d 格 -> 一圈 %.0f 秒, "
              "吞吐 %.2f 场/分" % (R, cnt, P[i], tot, cyc, 60.0 * cnt / cyc))
        dens = sum(1 for c, _, _ in best if c >= 3)
        print("           全场景有 %d 个点能凑出 ≥3 点的簇" % dens)
    # 全场景平均点间距
    ds = [Dof(i, j) for i in range(n) for j in range(i + 1, n)
          if Dof(i, j) is not None]
    if ds:
        ds.sort()
        print("  两两最短路距离: 中位 %d 格 均值 %.0f 格 最大 %d 格"
              % (ds[len(ds) // 2], sum(ds) / len(ds), ds[-1]))


def report(idxs, P, NM, Dof, si_i, nodes, start_pt):
    n = len(idxs)
    # 贪心最近邻
    rem = list(idxs)
    order, cur = [], si_i
    while rem:
        nxt = min(rem, key=lambda j: (Dof(cur, j) if Dof(cur, j) is not None else 10 ** 9))
        if Dof(cur, nxt) is None:
            order.append(nxt)
            rem.remove(nxt)
            continue
        order.append(nxt)
        rem.remove(nxt)
        cur = nxt
    gl = route_len(order, {(): 0}, 0) if False else None
    # 用统一口径算长度
    def rl(ord_):
        tot, c = 0, si_i
        for j in ord_:
            d = Dof(c, j)
            if d is None:
                return None
            tot += d
            c = j
        return tot
    gl = rl(order)
    best_order, bl = two_opt(order, None, si_i) if False else (None, None)
    # two_opt 需要 Dof 版: 就地实现
    best_order = list(order)
    bl = gl
    if gl is not None:
        improved, it = True, 0
        while improved and it < 60:
            improved = False
            it += 1
            for i in range(n - 1):
                for j in range(i + 1, n):
                    cand = best_order[:i] + best_order[i:j + 1][::-1] + best_order[j + 1:]
                    cl = rl(cand)
                    if cl is not None and cl < bl:
                        best_order, bl, improved = cand, cl, True
            if not improved:
                break
    print("  最近邻(现在用的贪心)  总里程 %s 格" % (gl if gl is not None else "含不可达"))
    print("  2-opt 优化后          总里程 %s 格%s"
          % (bl if bl is not None else "含不可达",
             ("  (省 %d 格, %.0f%%)" % (gl - bl, 100.0 * (gl - bl) / gl))
             if (gl and bl and gl > 0) else ""))
    if bl is not None:
        print("  接入起点 K%s 的第一段再加 %s 格"
              % (start_pt, Dof(si_i, best_order[0])))
        sec_walk = bl / 1.2
        print("  折算: 走路 ~%.0f 秒 (按 1.2 格/秒)  + %d 场战斗 x 11.2 秒 = %.0f 秒"
              % (sec_walk, len(order), sec_walk + len(order) * 11.2))
        cyc = sec_walk + len(order) * 11.2
        print("  → 一圈 %.0f 秒 (%.1f 分钟), 理论吞吐 %.2f 场/分"
              % (cyc, cyc / 60.0, 60.0 * len(order) / cyc))
    print("  顺序: %s" % " -> ".join(str(P[j]) for j in best_order[:24]))


if __name__ == "__main__":
    main()
