# -*- coding: utf-8 -*-
"""scene_who.py —— 场景号对照表: 「这个号是哪张图?」

为什么需要它: 仙剑引擎**没有地名表** (地图名从不显示, MAP.MKF 只有块号),
所以"场景 7 是哪"没法从数据里直接读出来。但每张图的怪、箱子、出口数量,
以及你在这张图上打过多少场, 都是确定的 —— 靠这些"指纹"就能认出地名。

    python tools/scene_who.py            # 只列打过的 (胜场 > 0)
    python tools/scene_who.py --all      # 全部 156 张
    python tools/scene_who.py 7          # 只看场景 7

纯只读: 只读 scene_info.json 和 hotspot.json, 不碰游戏进程, 不影响机器人。
"""
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(name):
    """读一份派生的数据文件。★ 仓库目录与母目录 (_bot/) 各有一份, 而且**内容
    不一样** —— 机器人实际是在母目录跑, 台账也写在那儿; 仓库那份可能是旧的。
    所以两边都看, 取修改时间更新的那份 (并打出来源, 免得看错表)。"""
    best = None
    for d in (BASE, os.path.dirname(BASE)):
        p = os.path.join(d, name)
        if not os.path.exists(p):
            continue
        try:
            m = os.path.getmtime(p)
        except Exception:
            continue
        if best is None or m > best[0]:
            best = (m, p)
    if best is None:
        return {}, None
    try:
        with open(best[1], encoding="utf-8") as f:
            return json.load(f), best[1]
    except Exception:
        return {}, best[1]


def main(argv):
    show_all = "--all" in argv
    want = [a for a in argv[1:] if a.isdigit()]
    si, si_p = load("scene_info.json")
    hot, hot_p = load("hotspot.json")
    hot = hot.get("battles") or {}
    if not si:
        print("缺少 scene_info.json —— 先跑: python tools/build_scene_info.py")
        return 1
    print("(清单: %s)" % si_p)
    print("(胜场: %s)" % hot_p)
    rows = []
    for sc, v in si.items():
        if want and sc not in want:
            continue
        won = int(hot.get(sc, 0) or 0)
        if not want and not show_all and won <= 0:
            continue
        mons = v.get("monsters") or []
        names = []
        for m in mons:
            n = (m.get("ename") or "").split("×")[0].strip()
            if n and n not in names:
                names.append(n)
        rows.append((int(sc), len(mons), len(v.get("chests") or []),
                     len(v.get("portals") or []), won, "/".join(names[:4])))
    rows.sort(key=lambda r: -r[4])
    if not rows:
        print("没有符合条件的场景 (试试 --all)")
        return 0
    print("%-5s %-5s %-5s %-5s %-7s %s"
          % ("场景", "怪点", "宝箱", "出口", "胜场", "常见怪 / 采集物名"))
    print("-" * 74)
    for sc, nm, nc, np_, won, names in rows:
        print("%-6d %-6d %-6d %-6d %-8d %s"
              % (sc, nm, nc, np_, won, names or "-"))
    print("-" * 74)
    print("说明: 「出口」= scene_info 静态快照里的换景对象数, 可能少于真实值")
    print("      (脚本动态创建的出口它看不见) —— 驻留模式是实时扫的, 不信这张表。")
    print("      认图靠最后一列: 怪名对得上, 就是那张图。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
