# -*- coding: utf-8 -*-
"""窗口几何探针: 打印窗口/客户区矩形 + 抓帧的逐行/逐列亮度剖面 + 各种判定结果。

只读, 零副作用 (不按键)。用于核对 wingeom.choose() 在真实窗口上的判定是否可信,
换 exe / 缩放窗口后如果 ②窗口几何 一节看着不对, 就跑这个看原始剖面。

用法:
    python tools/probe_geom.py            # 自动挑标题含 'Pal Win95' 的窗口
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import win32gui                                          # noqa: E402
import pal_bot as PB                                     # noqa: E402
import wingeom                                           # noqa: E402


def _prof(name, arr):
    a = np.asarray(arr, dtype=np.float64).ravel()
    lo = float(np.percentile(a, 5))
    hi = float(a.max())
    mid = float(np.percentile(a, 50))
    print("  %-6s n=%d  5%%=%.0f  50%%=%.0f  max=%.0f  (hi-lo=%.0f)"
          % (name, a.size, lo, mid, hi, hi - lo))
    return a


def _show_runs(a, label, n=8):
    """把剖面按"连续同段"压缩显示: 前 n 段 + 后 n 段"""
    mid = float(np.percentile(a, 50))
    hi = a.max()
    th = float(np.percentile(a, 5)) + max(12.0, 0.25 * (hi - float(np.percentile(a, 5))))
    flag = a > th
    runs = []
    i = 0
    while i < flag.size:
        j = i
        while j < flag.size and flag[j] == flag[i]:
            j += 1
        runs.append((i, j, bool(flag[i]), float(a[i:j].mean())))
        i = j
    print("  %s: 共 %d 段 (阈值 %.0f)" % (label, len(runs), th))
    for seg in runs[:n]:
        print("    [%4d,%4d) %s 均值=%.0f" % (seg[0], seg[1],
                                             "画面" if seg[2] else "黑边", seg[3]))
    if len(runs) > 2 * n:
        print("    ... 省略 %d 段 ..." % (len(runs) - 2 * n))
    for seg in runs[-n:]:
        print("    [%4d,%4d) %s 均值=%.0f" % (seg[0], seg[1],
                                             "画面" if seg[2] else "黑边", seg[3]))


def main():
    wins = PB.list_pal_windows()
    if not wins:
        print("没有标题含 Pal 的窗口 (游戏没开?)")
        return 2
    hwnd, title = None, ""
    for h, t in wins:
        if PB.TITLE_KEY.lower() in t.lower():
            hwnd, title = h, t
            break
    if hwnd is None:
        print("有 Pal 窗口但没有标题含 '%s' 的 -> 机器人不会选任何窗口" % PB.TITLE_KEY)
        for h, t in wins:
            print("   hwnd=%d  %r" % (h, t))
        return 2
    print("选中窗口 hwnd=%d" % hwnd)
    print("  标题 = %r" % title)
    try:
        import win32process
        import psutil
        _tid, pid = win32process.GetWindowThreadProcessId(hwnd)
        print("  进程 = %s (pid=%d)" % (psutil.Process(pid).name(), pid))
    except Exception as e:
        print("  进程 = (取不到: %s)" % e)

    l, t, r, b = win32gui.GetWindowRect(hwnd)
    cr = win32gui.GetClientRect(hwnd)
    pt = win32gui.ClientToScreen(hwnd, (0, 0))
    win = (r - l, b - t)
    coff = (pt[0] - l, pt[1] - t)
    csize = (cr[2], cr[3])
    print("\n窗口外框 %dx%d   客户区 %dx%d (偏移 %s)" % (win[0], win[1], csize[0], csize[1], coff))
    print("标定参考 REF_WIN=%s REF_CONTENT=%s" % (wingeom.REF_WIN, wingeom.REF_CONTENT))

    g = PB.Game(hwnd)
    a = g.shot()
    print("\n抓帧 shape = %s  (PrintWindow(2) 抓的是整个窗口外框)" % (a.shape,))
    if a.shape[0] != win[1] or a.shape[1] != win[0]:
        print("  ⚠️ 抓帧尺寸与窗口外框不一致 -> 说明 PrintWindow 抓的是别的区域")

    rowmax = a.max(axis=(1, 2))
    colmax = a.max(axis=(0, 2))
    print("\n【未裁剪】逐行/逐列亮度剖面 (含标题栏 -> 会被污染):")
    rr = _prof("rowmax", rowmax)
    cc = _prof("colmax", colmax)
    print("  逐行段:")
    _show_runs(rr, "行")
    print("  逐列段:")
    _show_runs(cc, "列")
    bad = wingeom.detect_content_rect(rowmax, colmax)
    print("  detect_content_rect -> %s" % (bad,))
    if bad:
        print("    宽高比 = %.4f (游戏 1.6000)  <- 若明显偏离就是标题栏在捣乱"
              % ((bad[2] - bad[0]) / float(bad[3] - bad[1])))

    # ---- 裁到客户区 (与 pal_bot.Game.geom / env_check 一致) ----
    cx = max(0, min(int(coff[0]), max(0, a.shape[1] - 1)))
    cy = max(0, min(int(coff[1]), max(0, a.shape[0] - 1)))
    sub = a[cy:cy + int(csize[1]), cx:cx + int(csize[0])]
    print("\n【已裁客户区】shape = %s (偏移 %s):" % (sub.shape, (cx, cy)))
    rowmax2 = sub.max(axis=(1, 2))
    colmax2 = sub.max(axis=(0, 2))
    _prof("rowmax", rowmax2)
    _prof("colmax", colmax2)
    print("  逐行段:")
    _show_runs(np.asarray(rowmax2, dtype=np.float64), "行")
    print("  逐列段:")
    _show_runs(np.asarray(colmax2, dtype=np.float64), "列")
    rect = wingeom.detect_content_rect(rowmax2, colmax2)
    print("  detect_content_rect -> %s" % (rect,))
    if rect:
        Rx = (rect[2] - rect[0]) / float(rect[3] - rect[1])
        print("    宽高比 = %.4f (游戏 320/200 = 1.6000)" % Rx)
        print("    -> 窗口坐标 %s"
              % (tuple(int(v + o) for v, o in zip(rect, (cx, cy))),))
    print("  bars_absent -> %s (True 表示画面铺满客户区 = 拉伸模式的特征)"
          % wingeom.bars_absent(rowmax2, colmax2))

    ka, ka_src = wingeom.read_keep_aspect_ex()
    print("\nKeepAspectRatio = %s  (来源 %s)" % (ka, ka_src))
    if ka:
        cw, ch = csize
        sc = min(cw / 320.0, ch / 200.0)
        cw2, ch2 = 320 * sc, 200 * sc
        ox = coff[0] + (cw - cw2) / 2.0
        oy = coff[1] + (ch - ch2) / 2.0
        print("  模型推算(等比, 客户区 %dx%d -> scale %.4f) 视口 = (%.1f,%.1f) %.1fx%.1f"
              % (cw, ch, sc, ox, oy, cw2, ch2))
        print("  -> 期望矩形 = (%d, %d, %d, %d)"
              % (round(ox), round(oy), round(ox + cw2), round(oy + ch2)))

    geo = wingeom.choose(win, coff, csize, rowmax=rowmax2, colmax=colmax2,
                         measure_off=(cx, cy))
    print("\nchoose() ->")
    print("  %s" % wingeom.describe(geo))
    print("  src=%s cfg=%s keep_aspect=%s scale=%s area=%s"
          % (geo.get("src"), geo.get("cfg"), geo.get("keep_aspect"),
             geo.get("scale"), geo.get("area")))
    print("  content=%s" % (geo.get("content"),))
    return 0


if __name__ == "__main__":
    sys.exit(main())
