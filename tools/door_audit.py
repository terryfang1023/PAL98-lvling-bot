# -*- coding: utf-8 -*-
"""door_audit.py —— 换场景用门的只读审计 (不按键, 不移动队伍)

回答「为什么它一直在原地换不出去」这个问题:
  ① 当前场景到底有几扇门? 各在哪 (K 坐标)?
  ② 每扇门: 附近有没有可走格 / 从当前位置是否可达 / 要多少步?
  ③ pick_migrate_door 现在会选哪一扇? 为什么不是别的?
  ④ 对照: 旧的 80 格上限会不会把你真正想去的门排除掉?
  ⑤ 日志里的「实测被挡」热点各自离哪扇门最近 (卡在哪一段路上)

用法:
    python tools/door_audit.py              # 当前场景
    python tools/door_audit.py --log 路径    # 指定日志 (默认 ./bot_log_mem.txt)

本工具只读内存 + 只读日志, 零副作用。
"""
import io
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from pal_bot_mem5 import MemBot5, cheb              # noqa: E402

# ★ 只读: 连 MemBot 的日志副作用也封掉 (它会 append 到 bot_log_mem.txt,
#   把诊断头部写进正在跑的机器人的日志, 并制造假的"运行段")。
MemBot5.NO_LOGFILE = True

OLD_DOOR_MAX = 80          # 2026-09-30 之前的写死上限 (会把远处出口门静默排除)


def px2k(px, py):
    """地图像素 -> K 坐标 (与 pal_bot_mem 同式)。"""
    return ((px - 2 * py) // 32, (px + 2 * py - 32) // 32)


def parse_hotspots(path, top=8):
    """从日志里取「实测被挡(数据说可走): K(a, b)」的热点聚类。只读。"""
    if not path or not os.path.isfile(path):
        return []
    pat = re.compile(r"实测被挡\(数据说可走\):\s*\((-?\d+),\s*(-?\d+)\)")
    cnt = {}
    with io.open(path, encoding="utf-8", errors="ignore") as f:
        for ln in f:
            m = pat.search(ln)
            if m:
                k = (int(m.group(1)), int(m.group(2)))
                cnt[k] = cnt.get(k, 0) + 1
    return sorted(cnt.items(), key=lambda kv: -kv[1])[:top]


def main():
    logpath = None
    argv = sys.argv[1:]
    for i, a in enumerate(argv):
        if a == "--log" and i + 1 < len(argv):
            logpath = argv[i + 1]
    if logpath is None:
        logpath = os.path.join(BASE, "bot_log_mem.txt")

    bot = MemBot5(max_battles=None, max_seconds=None, stall_limit=10 ** 9)
    st = bot.mem.state()
    if st is None:
        print("读内存失败 (游戏没开?)")
        return 1
    sc, cur = st["scene"], st["K"]
    bot.enter_scene(sc, "(door_audit)")

    ks = bot.kwalk(bot.map)
    ban, tag, rr = bot.graded_reach(cur)
    ban = ban - {cur}

    doors = set(bot.report_doors.get(sc, ())) | set(bot.known_doors.get(sc, ()))
    fail = getattr(bot, "_door_fail", {}) or {}

    print("=" * 78)
    print("场景 %d   队伍 K%s   可走域 %d 格   可达域 %d 格 (门禁: %s)"
          % (sc, cur, len(ks), len(rr), tag))
    print("门来源: 报告预取 %d 个 + 历史学到 %d 个 = 去重后 %d 个"
          % (len(bot.report_doors.get(sc, set())),
             len(bot.known_doors.get(sc, set())), len(doors)))
    print("=" * 78)

    if not doors:
        print("这个场景没有任何门记录 -> 只能原地刷, 换不出去。")
        print("（gyt_scene_report.txt 里没有这个场景的门, 且从没成功穿过门）")
        return 0

    rows = []
    for d in sorted(doors, key=lambda x: cheb(x, cur)):
        nb = [(d[0] + da, d[1] + db) for da in (-1, 0, 1) for db in (-1, 0, 1)]
        nb_walk = [c for c in nb if c in ks]
        reach_nb = [c for c in nb_walk if c in rr]
        dist = cheb(d, cur)
        path_len = None
        if reach_nb:
            near = min(reach_nb, key=lambda c: cheb(c, cur))
            p = bot.bfs_path(cur, {near}, allow_doors=True)
            path_len = len(p) if p else None
        rows.append((d, dist, nb_walk, reach_nb, path_len))

        mark = []
        if not nb_walk:
            mark.append("☆ 门外无可走格")
        elif not reach_nb:
            mark.append("★ 可达域里够不到")
        if fail.get(d, 0) >= bot.DOOR_FAIL_MAX:
            mark.append("失败史 %d -> 暂时跳过" % fail.get(d, 0))
        elif fail.get(d, 0):
            mark.append("失败过 %d 次" % fail.get(d, 0))
        if dist > bot.MIGRATE_DOOR_MAX:
            mark.append("超出当前上限 %d" % bot.MIGRATE_DOOR_MAX)
        over_old = "  ← 旧上限 %d 会排除" % OLD_DOOR_MAX if dist > OLD_DOOR_MAX else ""
        print("K%-12s 距 %4d 格   邻格可走 %2d/9   可达邻格 %2d/9   最短路 %s  %s%s"
              % (str(d), dist, len(nb_walk), len(reach_nb),
                 ("%d 步" % path_len) if path_len else "  无  ",
                 " | ".join(mark), over_old))

    print("-" * 78)
    pick = bot.pick_migrate_door(cur)
    print("pick_migrate_door 现在会选: %s" % (("K%s (距 %d 格)" % (pick, cheb(pick, cur)))
                                          if pick else "None (没有可用门)"))
    if pick is None:
        print("  ★ 返回 None 就意味着 migrate 分支会直接放弃 -> 原地空转。")
    else:
        row = [r for r in rows if r[0] == pick][0]
        print("  该门: 邻格可走 %d, 可达邻格 %d, 最短路 %s"
              % (len(row[2]), len(row[3]),
                 ("%d 步" % row[4]) if row[4] else "无"))

    # ---- 对照: 旧上限 80 ----
    print("-" * 78)
    old_ok = [r[0] for r in rows if r[1] <= OLD_DOOR_MAX and r[3]]
    new_ok = [r[0] for r in rows if r[1] <= bot.MIGRATE_DOOR_MAX and r[3]]
    lost = [r for r in rows if r[3] and OLD_DOOR_MAX < r[1] <= bot.MIGRATE_DOOR_MAX]
    print("对照: 旧上限 %d 可及门 %d 个 %s" % (OLD_DOOR_MAX, len(old_ok), old_ok))
    print("      新上限 %d 可及门 %d 个 %s" % (bot.MIGRATE_DOOR_MAX, len(new_ok), new_ok))
    if lost:
        for d, dist, _nw, _rn, pl in lost:
            print("      ★ 旧上限把 K%s (距 %d 格, 最短路 %s) 静默排除 —— 这就是"
                  "「地图右下角的出口门一直不去」的原因"
                  % (d, dist, ("%d 步" % pl) if pl else "无"))
    else:
        print("      旧上限没有排除任何可达门 (这套参数下不构成问题)")

    # ---- 卡点热点 vs 门 ----
    hs = parse_hotspots(logpath)
    if hs:
        print("-" * 78)
        print("日志「实测被挡」热点 -> 离哪扇门最近   (%s)"
              % os.path.basename(logpath))
        for k, n in hs:
            dd = sorted(rows, key=lambda r: cheb(r[0], k))[:2]
            desc = ", ".join("K%s 距%d格" % (r[0], cheb(r[0], k)) for r in dd)
            print("  x%-4d K%-12s 最近门: %s" % (n, str(k), desc))
        print("  提示: 热点紧贴某扇门 (≤5 格) 却仍报「门未触发」-> 不是路的问题,")
        print("        是那扇门本身不触发 (条件门/单向门), 该换下一扇门试。")
    print("=" * 78)
    print("本工具只读内存与日志, 不按键、不走路、不写文件。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
