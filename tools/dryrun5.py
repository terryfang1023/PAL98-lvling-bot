# -*- coding: utf-8 -*-
"""dryrun5.py — 真机干跑: 初始化 MemBot5 并模拟选点, 但不操作游戏。

验证: 清单载入 -> 当前场景识别 -> 怪点/宝箱目标选择是否合理。
"""
import os
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from pal_bot_mem5 import MemBot5, cheb            # noqa: E402

# ★ 2026-09-30: 干跑也算"只读", 同样不能往 bot_log_mem.txt 追加 (那会污染
#   正在跑的机器人的日志, 并让 run_report.py 多认出一条假运行段)。
MemBot5.NO_LOGFILE = True


def main():
    bot = MemBot5(max_battles=None, max_seconds=None, stall_limit=10 ** 9)
    st = bot.mem.state()
    print("当前内存状态:", st)
    if st is None:
        print("读内存失败")
        return 1
    bot.enter_scene(st["scene"], "(dry-run)")
    print("场景 %d 地图 %s 队伍 K%s" % (bot.scene, getattr(bot, "map", "?"),
                                      st["K"]))
    mon = bot.scene_monsters()
    chs = bot.scene_chests()
    print("本场景 怪点 %d 个 / 宝箱 %d 个" % (len(mon), len(chs)))
    if mon:
        print("  怪点 K 列表:", [tuple(m["K"]) for m in mon])
        near = min(mon, key=lambda m: cheb(tuple(m["K"]), st["K"]))
        print("  最近的怪点:", near["K"], "距离",
              cheb(tuple(near["K"]), st["K"]))
    if chs:
        print("  宝箱 K 列表:", [tuple(c["K"]) for c in chs])

    print("\n模拟 6 轮 pick_target:")
    for i in range(6):
        t = bot.pick_target()
        print("  #%d -> %s %s" % (i + 1, t,
                                  "(怪点)" if bot._mon_goal else ""))
        if t is None:
            break
    print("\n要素统计:", bot.stat)
    print("干跑结束 (未操作游戏)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
