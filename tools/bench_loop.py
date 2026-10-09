# -*- coding: utf-8 -*-
"""bench_loop.py —— 主循环耗时剖析 (只读, 零副作用: 不按键/不移动/不写日志)

用途: 定位"每格 1.5s, 引擎上限 0.2s"的钱花在哪。
      把每个 tick 会调的只读函数逐个计时, 直接看谁是瓶颈。

用法:
    python tools/bench_loop.py            # 当前场景
    python tools/bench_loop.py 157        # 指定场景

注意: 本工具不调用 tick()/walk_path()/step() (那些会按键), 只测纯计算部分。
"""
import os
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import pal_bot_mem as M                                  # noqa: E402
import pal_bot_mem5 as M5                                # noqa: E402

# ★ 抑制日志: 绝不能把探针输出混进用户正在写的 bot_log_mem.txt
_SINK = []
M.MemBot.NO_LOGFILE = True          # 双保险: 关掉落盘 (下面还整体换掉 log)
M.MemBot.log = lambda self, msg: _SINK.append(msg)
M5.MemBot5.log = M.MemBot.log


def bench(name, fn, n=20, warm=3):
    for _ in range(warm):
        fn()
    t0 = time.perf_counter()
    for _ in range(n):
        fn()
    dt = (time.perf_counter() - t0) / n
    print("  %-34s %8.3f ms/次" % (name, dt * 1000))
    return dt


def main():
    sc = None
    for a in sys.argv[1:]:
        if a.isdigit():
            sc = int(a)
    bot = M5.MemBot5(max_battles=None, max_seconds=None, stall_limit=10 ** 9)
    st = bot.mem.state()
    if st is None:
        print("读内存失败 (游戏没开?)")
        return 1
    sc = sc or st["scene"]
    bot.scene = sc
    bot.map = bot.mem.scene_map.get(sc)
    ks = bot.kwalk(bot.map)
    cur = st["K"]
    print("=" * 72)
    print("场景 %d  地图 %s  K%s  kwalk=%d 格"
          % (sc, bot.map, cur, len(ks)))
    print("=" * 72)

    print("[单次原语]")
    bench("mem.state()  (每步至少 2 次)", bot.mem.state, n=2000)
    bench("mem.raw()    (转身检测每步 1 次)", bot.mem.raw, n=2000)
    bench("scene_monsters() (实时怪点)", lambda: bot.scene_monsters(sc), n=200)

    print("[主循环重活]")
    bench("kwalk(map)  (带缓存, 应≈0)", lambda: bot.kwalk(bot.map), n=500)
    ban = set(bot.blocked) - {cur}
    bench("reach(cur,ban)  (1 次全图 BFS)", lambda: bot.reach(cur, ban), n=20)
    bench("graded_reach(cur)  (最多 3 次 BFS)",
          lambda: bot.graded_reach(cur), n=20)
    bot._pc_key = None
    bench("plan_ctx(cur)  (含缓存判断)", lambda: bot.plan_ctx(cur), n=20)
    tgt = next(iter(ks))
    bench("bfs_path(cur,{tgt})  (又 1 次 BFS)",
          lambda: bot.bfs_path(cur, {tgt}), n=20)
    bot._pc_key = None
    bot._mon_goal = None
    bench("pick_target()  (mem5 全流程)", lambda: bot.pick_target(), n=20)

    print("[一次 tick 的只读等价量]")
    t0 = time.perf_counter()
    for _ in range(20):
        bot._pc_key = None
        bot.pick_target()
        bot.bfs_path(cur, {tgt})
    one = (time.perf_counter() - t0) / 20
    print("  合计 ≈ %.3f s/tick" % one)
    print("  对照: 引擎单格上限 0.20s -> 若每 tick 只走 1 格,"
          " 节拍 = %.2fs/格" % (0.2 + one))
    print("=" * 72)
    print("本工具只读内存, 不按键、不走路、不写日志。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
