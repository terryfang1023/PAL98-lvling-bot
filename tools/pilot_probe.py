# -*- coding: utf-8 -*-
"""pilot_probe.py —— 自动换场景的只读诊断 (不按任何键, 不移动队伍)

回答三个问题:
  ① 当前场景的评分/耐心系数是多少? 现在会不会决定换场景?
  ② 如果决定换, 它能不能找到门? (pick_migrate_door)
  ③ 当前场景"离该走还有多久"? (把「已多久没战斗」调成不同值, 看哪个时刻会触发)

用法:
    python tools/pilot_probe.py             # 当前场景, 用真实空闲时长
    python tools/pilot_probe.py 400         # 假设「已 400 秒没战斗」看会不会换
    python tools/pilot_probe.py 400 157     # 指定场景
"""
import os
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from pal_bot_mem5 import MemBot5, cheb              # noqa: E402

# ★ 2026-09-30: 只读诊断必须连"日志副作用"也封掉。MemBot.log() 会懒加载打开
#   bot_log_mem.txt 追加, 于是光是实例化 + enter_scene 就会往**正在跑的机器人**
#   的日志里塞一整块头部 (含「[v13] 战斗判定」横幅) —— 那行正是 run_report.py
#   用来定位"运行段"的标记, 会多出假运行段并把效率归因带偏。
MemBot5.NO_LOGFILE = True


def main():
    fake_idle = None
    scene = None
    for a in sys.argv[1:]:
        if a.isdigit():
            if fake_idle is None:
                fake_idle = float(a)
            else:
                scene = int(a)

    bot = MemBot5(max_battles=None, max_seconds=None, stall_limit=10 ** 9)
    st = bot.mem.state()
    if st is None:
        print("读内存失败 (游戏没开?)")
        return 1
    sc = scene or st["scene"]
    cur = st["K"]
    bot.enter_scene(sc, "(probe)")

    p = bot.pilot
    v = p.info(sc)
    print("=" * 76)
    print("场景 %d   队伍 K%s" % (sc, cur))
    if v:
        print("评分: 可达怪点 %d/共%d  巡回 %d 格  接入 %d 格  可达域 %d"
              % (v["n_mon"], v["n_mon_all"], v["ring"], v["lead"], v["reach"]))
        print("      几何上限 %.2f 场/分  ->  预期实测 %.2f 场/分  %s"
              % (v["ceil"], v["expect"], "【过路场景】" if v["pass"] else ""))
    else:
        print("评分: 无记录 (scene_score.json 里没有这个场景)")
    print("耐心系数 x%.2f   过路阈值 %.0fs   常规阈值 %.0fs   换场景冷却 %.0fs"
          % (p.patience(now=time.time()), p.PASS_IDLE * p.patience(now=time.time()),
             p.IDLE_LIMIT * p.patience(now=time.time()), p.CHANGE_CD))
    if p.dud:
        print("低效标记: %s" % p.dud)

    # ① 决策 (把「上次战斗」按需往前挪, 模拟不同空闲时长)
    now = time.time()
    for idle in ([fake_idle] if fake_idle is not None
                 else [30, 120, 240, 360, 600]):
        p.t_arrive = now - max(idle, p.MIN_STAY + 1)
        p.t_last_battle = now - idle
        p.t_last_change = 0.0
        need, why = p.decide(now, getattr(bot, "battles_won", 0))
        print("  空闲 %4ds -> %s %s" % (idle, "★ 换场景" if need else "留下  ",
                                        why))

    # ② 能不能找到门
    print("-" * 76)
    door = bot.pick_migrate_door(cur)
    if door is None:
        print("门: 当前场景没有可用门 (预取门位+历史门都取不到) -> 只会留在原地")
        print("    预取门位 %d 个, 历史学到 %d 个"
              % (len(bot.report_doors.get(sc, set())),
                 len(bot.known_doors.get(sc, set()))))
    else:
        print("门: 最近可用门 K%s (距 %d 格)  -> 决定换场景时会走过去踩它"
              % (door, cheb(door, cur)))
    if bot._scene_links:
        print("已学门路 %d 条: %s" % (len(bot._scene_links),
                                    dict(list(bot._scene_links.items())[:6])))
    else:
        print("已学门路: 无 (第一次穿门后就会记下来: 场景x 门K -> 场景y)")
    print("=" * 76)
    print("本工具只读内存, 不会按键/走路。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
