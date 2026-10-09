# -*- coding: utf-8 -*-
"""run_report.py —— 一次运行的「效果归因」报告 (只读, 零副作用)

用途: 跑完一轮之后回答"这一小时到底慢在哪"。
      不碰游戏、不按键、不写任何文件。

用法:
    python tools/run_report.py                  # 取 bot_log_mem.txt 里最后一次运行
    python tools/run_report.py <日志> [运行序号]
    python tools/run_report.py --all            # 列出日志里所有运行段

判读要点:
    * 「场/分」= 效率总指标; 标定战绩 1.05~1.22 场/分
    * 「有效行走」只统计"该动就动了"的步; 被挡/脱困/迁移/停机都不计入
      -> 若它远小于运行时长, 说明时间被"没产出"的机制吃掉了
    * 机制抖动: 「实测被挡」与「自愈清除」交替出现 = 知识库自我打架
"""
from __future__ import print_function

import io
import os
import re
import sys
import collections

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)

BANNER = "[v13] 战斗判定 = is_battle"
OK, WARN, BAD = "[OK]", "[!]", "[X]"


def hr(t):
    print()
    print("=" * 70)
    print(t)
    print("=" * 70)


def load(path):
    with io.open(path, encoding="utf-8", errors="ignore") as f:
        return f.read().splitlines()


def find_runs(lines):
    """返回每个运行段的起始行号 (启动横幅所在行)。

    ★ 2026-09-30: 只读诊断工具 (pilot_probe / door_audit / dryrun5) 曾会往日志
      追加一整块含同样启动横幅的头部, 于是被误认成「运行段」, 把归因带偏
      (根因已修: MemBot.NO_LOGFILE)。这里再加一道防线:

        * 真运行段一定含「[心跳]」行 (主循环每 ~30s 一条), 诊断块没有;
        * 兜底: 最后一段若既无心跳、时长又 <120s, 同样判为非运行段。
    """
    raw = [i for i, ln in enumerate(lines) if BANNER in ln]
    out = []
    for n, s in enumerate(raw):
        e = raw[n + 1] if n + 1 < len(raw) else len(lines)
        if any("[心跳]" in lines[k] for k in range(s, e)):
            out.append(s)
            continue
        if n == len(raw) - 1 and _seg_secs(lines, s, e) >= 120:
            out.append(s)
    return out


def _seg_secs(lines, s, e):
    """段内首尾时间戳之差 (秒); 解析不出来就给 0。"""
    ts = [m.group(0) for m in (TS.match(lines[k]) for k in range(s, e)) if m]
    if len(ts) < 2:
        return 0.0
    a, b = ts[0].split(":"), ts[-1].split(":")
    d = (secs(*b) - secs(*a)) % 86400
    return float(d)


def secs(hh, mm, ss):
    return int(hh) * 3600 + int(mm) * 60 + int(ss)


TS = re.compile(r"^(\d\d):(\d\d):(\d\d) ")


def parse(lines, i0, i1):
    """把一段日志解析成结构化事件列表 + 统计。"""
    ev = []
    for i in range(i0, min(i1, len(lines))):
        ln = lines[i]
        m = TS.match(ln)
        if not m:
            continue
        t = secs(*m.groups())
        ev.append((t, ln))
    if not ev:
        return None
    # 跨午夜: 时间回退就补一天
    off = 0
    prev = ev[0][0]
    fixed = []
    for t, ln in ev:
        if t < prev - 600:
            off += 86400
        prev = t
        fixed.append((t + off, ln))
    ev = fixed
    return ev


def buckets(ev, span):
    d = {}
    t0 = ev[0][0]
    for t, ln in ev:
        k = (t - t0) // span
        d.setdefault(k, []).append((t, ln))
    return t0, d


def classify(ln):
    """把一行归到一个「时间去向」桶。"""
    if "[效率]" in ln:
        return "meta"
    if "[心跳]" in ln or "[速度]" in ln:
        return "meta"
    for k, tag in (
            ("[MEM] 实测被挡", "被挡/绕行"),
            ("[绕行]", "被挡/绕行"),
            ("[快] 停机", "停机判定"),
            ("[自愈]", "自愈/重测绘"),
            ("[PT] 可达域", "自愈/重测绘"),
            ("[MEM] RESET", "自愈/重测绘"),
            ("[MEM] 迁移", "跨场景迁移"),
            ("[调度]", "跨场景迁移"),
            ("[追怪]", "追怪"),
            ("[巡逻]", "巡逻选点"),
            ("[怪点]", "怪点巡回"),
            ("[宝箱]", "宝箱"),
            ("[战斗", "战斗"),
            ("[要素]", "meta"),
            ("[MENU]", "菜单/对话自救"),
            ("[自救]", "菜单/对话自救"),
            ("[菜单]", "菜单/对话自救"),
    ):
        if k in ln:
            return tag
    return "其他"


def main():
    args = [a for a in sys.argv[1:]]
    path = os.path.join(BASE, "bot_log_mem.txt")
    idx = None
    if args and not args[0].startswith("-"):
        if os.path.exists(args[0]):
            path = args[0]
            args = args[1:]
        elif args[0].isdigit():
            idx = int(args[0])
            args = args[1:]
    which = args[0] if args else ""
    if not os.path.exists(path):
        print("%s 找不到日志: %s" % (BAD, path))
        return 2
    lines = load(path)
    all_banners = [i for i, ln in enumerate(lines) if BANNER in ln]
    starts = find_runs(lines)
    skipped = len(all_banners) - len(starts)
    if skipped > 0:
        print("(提示) 日志里有 %d 处启动横幅被判为**非运行段**并跳过 —— "
              "多为只读诊断工具 (pilot_probe/door_audit/dryrun5) 留下的头部。"
              % skipped)
    if not starts:
        print("%s 日志里没有启动横幅, 无法定位运行段" % BAD)
        return 2
    if which == "--all":
        print("共 %d 段运行:" % len(starts))
        for n, s in enumerate(starts):
            # 段的**结束边界**用"下一处任意启动横幅"(含被跳过的诊断块),
            # 否则诊断块的行会被算进这一段的时长与归因里。
            e = len(lines)
            for b in all_banners:
                if b > s:
                    e = b
                    break
            sub = parse(lines, s, e)
            if not sub:
                continue
            dur = sub[-1][0] - sub[0][0]
            wins = max([int(m.group(1)) for _, ln in sub
                        for m in [re.search(r"累计胜场=(\d+)", ln)] if m] or [0])
            print("  #%d  行 %6d  时长 %5.1f 分  胜场 %d  %s"
                  % (n, s, dur / 60.0, wins, lines[s + 6][:9] if s + 6 < len(lines) else ""))
        return 0
    i0 = starts[-1]
    if idx is not None and 0 <= idx < len(starts):
        i0 = starts[idx]
    i1 = len(lines)
    for b in all_banners:                 # 同 --all: 结束边界用原始横幅
        if b > i0:
            i1 = b
            break
    ev = parse(lines, i0, i1)
    if not ev:
        print("%s 该段为空" % BAD)
        return 2
    return report(ev, lines, i0, i1)


def report(ev, lines, i0, i1):
    t0, t1 = ev[0][0], ev[-1][0]
    dur = t1 - t0

    hr("① 总览")
    print("  行 %d..%d   时长 %.1f 分 (%.0fs)"
          % (i0, i1, dur / 60.0, dur))

    # 心跳: 步数/胜场/场景
    steps = wins = 0
    scenes = collections.Counter()
    scene_seq = []
    for t, ln in ev:
        m = re.search(r"\[心跳\] 场景(\d+) K\((-?\d+), (-?\d+)\) 步数(\d+) 胜(\d+)场",
                      ln)
        if m:
            scenes[int(m.group(1))] += 1
            s = int(m.group(4))
            w = int(m.group(5))
            if s >= steps:
                steps = s
            if w >= wins:
                wins = w
            if not scene_seq or scene_seq[-1] != m.group(1):
                scene_seq.append(m.group(1))
    # 胜场兜底: 从 [战斗结束] 取
    bw = [int(m.group(1)) for _, ln in ev
          for m in [re.search(r"累计胜场=(\d+)", ln)] if m]
    if bw:
        wins = max(wins, max(bw))
    print("  步数 %d   胜场 %d   效率 %.2f 场/分"
          % (steps, wins, wins / (dur / 60.0) if dur else 0))
    print("  场景出现: %s"
          % ", ".join("%d(%d)" % (k, v) for k, v in scenes.most_common()))
    if scene_seq:
        print("  场景轨迹: %s" % " -> ".join(scene_seq[:24]))

    # 机制计数
    cnt = collections.Counter()
    for t, ln in ev:
        cnt[classify(ln)] += 1
    key = collections.Counter()
    for t, ln in ev:
        for k in ("实测被挡(数据说可走)", "停机=动态图块", "停机=真墙",
                  "停机=战斗", "停机=换图淡入", "停机=黑屏", "停机=持续动画",
                  "无路可规划 -> 四方向硬试突围", "秒无位移", "自愈",
                  "RESET 局部绕行知识", "门未触发", "尝试", "换场景",
                  "撤销", "走不进去", "连续", "超时没走到"):
            if k in ln:
                key[k] += 1

    hr("② 时间去向 (按日志条数归因)")
    tot = sum(cnt.values())
    for k, v in cnt.most_common():
        if k == "meta":
            continue
        print("  %5d  (%4.1f%%)  %s" % (v, v * 100.0 / tot, k))
    print("  %5d  (%4.1f%%)  %s" % (cnt["meta"], cnt["meta"] * 100.0 / tot,
                                    "meta(心跳/速度/效率, 反映节奏)"))

    hr("③ 关键机制计数")
    for k, v in key.most_common():
        print("  %5d  包含 '%s'" % (v, k))

    # 效率累计
    eff = None
    for t, ln in ev:
        m = re.search(r"\[效率\].*走(\d+)s 战(\d+)s 误(\d+)s", ln)
        if m:
            eff = tuple(int(x) for x in m.groups())
    if eff:
        w, f, fa = eff
        acc = w + f + fa
        print()
        print("  有效行走 %ds / 战斗 %ds / 误抓 %ds  = 合计 %ds"
              % (w, f, fa, acc))
        print("  占运行时长 %.1f%%   -> 其余 %.1f 分去向见下"
              % (acc * 100.0 / dur, (dur - acc) / 60.0))

    # 分时段
    hr("④ 分时段 (每 5 分钟)")
    print("  %-11s %6s %5s %6s %6s %6s %6s %6s"
          % ("时刻", "步数", "胜场", "被挡", "停机", "自愈", "迁移", "吞吐"))
    t0b, bk = buckets(ev, 300)
    spans = []
    for t, ln in ev:
        m = re.search(r"\(近(\d+)s (\d+)步\)", ln)
        if m:
            spans.append((t, float(m.group(2)) / max(1, int(m.group(1)))))
    for k in sorted(bk):
        sub = bk[k]
        # 心跳里的步数/胜场是累计值 -> 取本时段最大值
        stv = [int(mm.group(1)) for _, ln in sub
               for mm in [re.search(r"\[心跳\].*步数(\d+)", ln)] if mm]
        st = max(stv) if stv else "-"
        wn = [int(re.search(r"胜(\d+)场", ln).group(1)) for _, ln in sub
              if re.search(r"\[心跳\].*胜(\d+)场", ln)]
        bb = sum(1 for _, ln in sub if "实测被挡" in ln)
        sl = sum(1 for _, ln in sub if "[快] 停机" in ln)
        hl = sum(1 for _, ln in sub if "[自愈]" in ln)
        mg = sum(1 for _, ln in sub if "[MEM] 迁移" in ln)
        thr = [v for t, v in spans if t0b + k * 300 <= t < t0b + (k + 1) * 300]
        print("  %-11s %6s %5s %6d %6d %6d %6d %6s"
              % (fmt_clock(t0b + k * 300), st, max(wn) if wn else "-", bb, sl,
                 hl, mg, ("%.2f" % (sum(thr) / len(thr))) if thr else "-"))

    # 步进节拍分布
    spans = []
    for t, ln in ev:
        m = re.search(r"\[速度\] 移动([\d.]+)s/格, 真实吞吐 ([\d.]+) 格/秒 "
                      r"\(近(\d+)s (\d+)步\)", ln)
        if m:
            spans.append((t, float(m.group(1)), float(m.group(2)),
                          int(m.group(3)), int(m.group(4))))
    if spans:
        hr("⑤ 步进节拍 (每格子真实耗时; 引擎上限 0.20s/格)")
        sp = sorted(s / float(n) for _, _, _, s, n in spans if n)
        print("  样本 %d 个窗口,  中位 %.2fs/格  最慢 %.2fs/格  最快 %.2fs/格"
              % (len(sp), sp[len(sp) // 2], sp[-1], sp[0]))
        slow = sum(1 for x in sp if x > 1.0)
        print("  %.0f%% 的窗口每格 >1.0s (引擎上限的 5 倍以上)"
              % (slow * 100.0 / len(sp)))
        print("  最慢 8 个窗口:")
        for t, mv, thr, s, n in sorted(spans, key=lambda r: r[3] / float(r[4])
                                       if r[4] else 0)[-8:]:
            print("     %s  %.1fs/步  (近%ds %d步, 吞吐%.2f)"
                  % (fmt_clock(t), s / float(n) if n else 0, s, n, thr))

    # 卡点坐标聚类
    hr("⑥ 卡点坐标聚类 (被挡 / 停机 各自的热点)")
    for tag in ("实测被挡(数据说可走)", "停机=", "门未触发"):
        pts = collections.Counter()
        for t, ln in ev:
            if tag not in ln:
                continue
            m = re.search(r"\((-?\d+), (-?\d+)\)", ln)
            if m:
                pts[(int(m.group(1)), int(m.group(2)))] += 1
        if not pts:
            continue
        print("  --- %s  (共 %d 处, 前 8) ---" % (tag, len(pts)))
        for (a, b), c in pts.most_common(8):
            print("      K(%d, %d) x%d" % (a, b, c))

    # 抖动检测
    hr("⑥ 机制冲突检测")
    n_block = sum(1 for _, ln in ev if "实测被挡(数据说可走)" in ln)
    n_heal = sum(1 for _, ln in ev if "[自愈]" in ln and "清除身边" in ln)
    n_undo = sum(1 for _, ln in ev if "撤销" in ln)
    print("  「实测被挡」%d 次   「自愈清除身边记录」%d 次   「绕行被撤销」%d 次"
          % (n_block, n_heal, n_undo))
    if n_block and n_heal >= n_block * 0.25:
        print("  %s 抖动明显: 自愈频率达被挡的 %.0f%% -> 知识库在自我打架,"
              % (WARN, n_heal * 100.0 / n_block))
        print("     值得检查「自愈阈值」是否过于激进 (把真实阻挡当噪声清掉)")
    if n_undo:
        print("  %s 「被挡记录」有 %d 次因'会让当前位置无路可走'被撤销"
              % (WARN, n_undo))
    n_stall = sum(1 for _, ln in ev if "[快] 停机" in ln)
    print("  停机 %d 次 = 每 %.1f 秒一次" % (n_stall, dur / max(1, n_stall)))
    return 0


def fmt_clock(t):
    t = int(t) % 86400
    return "%02d:%02d:%02d" % (t // 3600, (t % 3600) // 60, t % 60)


if __name__ == "__main__":
    sys.exit(main())
