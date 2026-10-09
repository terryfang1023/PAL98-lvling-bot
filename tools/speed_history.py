# -*- coding: utf-8 -*-
"""speed_history.py —— 历次运行的「节拍 + 按键健康度」对照 (只读)

用途: 新跑一轮慢了, 先看它跟历史最快那轮差在哪:
      * 每格真实耗时 (引擎响应延迟 "移动Xs/格" 与真实吞吐)
      * 补按 / 异常停机 / 截图 的累计计数 (判断是"引擎慢"还是"外挂白等")

判读:
    "移动Xs/格" 是**引擎响应延迟** (发出按键 -> 坐标变化), 各轮基本恒定 ~0.15s;
    真实吞吐的倒数才是"每格墙钟耗时"。两者差距 = 外挂自己等掉的。
    历史最好的轮次: 真实吞吐 ~2.5 格/秒 (0.40s/格)。

用法:
    python tools/speed_history.py [日志路径]
"""
import io
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(BASE, "bot_log_mem.txt")

lines = io.open(path, encoding="utf-8", errors="ignore").read().splitlines()
starts = [i for i, ln in enumerate(lines) if "[v13] 战斗判定" in ln]
print("日志 %s   共 %d 行, %d 段运行"
      % (os.path.basename(path), len(lines), len(starts)))

pat = re.compile(r"^(\d\d:\d\d:\d\d) \[速度\] 移动([\d.]+)s/格, 真实吞吐 "
                 r"([\d.]+) 格/秒 \(近(\d+)s (\d+)步\) 补按(\d+) 转身(\d+) "
                 r"异常(\d+) 截图(\d+)")

print()
print("段  行起   时刻跨度            总步数/窗口总时长 = 真实吞吐  "
      "移动延迟  补按   异常   截图   转身")
for n, s in enumerate(starts):
    e = starts[n + 1] if n + 1 < len(starts) else len(lines)
    mv, thr, tot_el, tot_st = [], [], 0, 0
    last = None
    for ln in lines[s:e]:
        m = pat.match(ln)
        if not m:
            continue
        last = m
        mv.append(float(m.group(2)))
        thr.append(float(m.group(3)))
        tot_el += int(m.group(4))
        tot_st += int(m.group(5))
    if not last:
        continue
    mv.sort()
    print("#%-2d %6d  %s->%s  %7d步/%-6ds = %5.2f 格/秒  %.3fs  %6s %6s %6s %6s"
          % (n, s, last.group(1), "", tot_st, tot_el,
             tot_st / float(tot_el) if tot_el else 0,
             mv[len(mv) // 2], last.group(6), last.group(8), last.group(9),
             last.group(7)))
print()
print("说明: 真实吞吐 = 总步数 / 窗口总时长; '移动延迟' 是本轮所有窗口的中位数。")
print("      全轮均速最高的一行即历史最佳节拍, 用它当基准比较新跑的轮次。")
print("本工具只读, 不按键、不走路、不写日志。")
