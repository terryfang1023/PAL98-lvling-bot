# -*- coding: utf-8 -*-
"""env_check.py — 运行环境体检 (只读, 零副作用)

用途: 在替换 exe / 重启游戏 / 缩放窗口之后, 用 5 秒确认机器人还能不能跑。
      **不按任何键、不写任何文件**, 只读窗口信息与游戏内存。

检查三件事:
  ① 窗口: 有几个候选? 机器人会选哪个? 标题是否匹配 "Pal Win95"?
          进程是 sdlpal.exe 还是 sdlpal.hud.exe?
  ② 几何: 实际窗口/视口/缩放 vs 标定值 (1687x1294, 5.27x)
          -> 判断"窗口缩放"对追怪/停机阈值有没有影响
  ③ 基址: 硬编码内存地址 (rgScene/GLOBALVARS) 在该进程里是否仍然成立
          -> 换 exe 后最容易失效的就是这一项

用法:
    python tools/env_check.py
"""
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import ctypes                                              # noqa: E402
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

import win32gui                                            # noqa: E402
import win32process                                        # noqa: E402
import psutil                                              # noqa: E402
import pal_bot as PB                                       # noqa: E402
import pal_bot_mem as PM                                   # noqa: E402
import wingeom as WG                                       # noqa: E402

OK = "✅"
WARN = "⚠️ "
BAD = "❌"


def hr(t):
    print("\n" + "=" * 66)
    print(t)
    print("=" * 66)


def proc_of(hwnd):
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        p = psutil.Process(pid)
        return pid, p.name(), (p.exe() or "")
    except Exception:
        return 0, "?", ""


def main():
    hr("① 窗口检测")
    all_pal = PB.list_pal_windows()
    if not all_pal:
        print("%s 没有找到标题含 Pal 的窗口 —— 游戏没开?" % BAD)
        return 2
    picked = PB.find_game_window()
    for h, t in all_pal:
        pid, pname, pexe = proc_of(h)
        hit = "★机器人会选它" if (picked and h == picked[0]) else ""
        key = ("标题匹配 %s" % PB.TITLE_KEY) if PB.TITLE_KEY.lower() in t.lower() \
            else "标题不匹配"
        print("  hwnd=%-10s 进程=%-16s %s  [%s] %s"
              % (h, pname, key, os.path.basename(pexe) or "-", hit))
        print("      标题 = %r" % t)
        print("      路径 = %s" % (pexe or "-"))
    if not picked:
        print("%s 有 Pal 窗口, 但没有一个标题包含 %r -> 机器人会报"
              "'未找到游戏窗口' 并拒绝启动" % (BAD, PB.TITLE_KEY))
        print("   (这是安全失败: 不会瞎走。换回标题带 'Pal Win95' 的那个 exe 即可)")
        return 2
    # 只有"标题真的匹配"的窗口才会被机器人看到 -> 歧义警告只数这些
    matched = [h for h, t in all_pal if PB.TITLE_KEY.lower() in t.lower()]
    if len(matched) > 1:
        print("%s 有 %d 个窗口标题都含 %r -> 机器人固定取第一个 (可能选错)"
              " -> 建议只开一个游戏" % (WARN, len(matched), PB.TITLE_KEY))
    elif len(all_pal) > len(matched):
        print("  (另有 %d 个标题含 'Pal' 但不含 %r 的窗口, 机器人不会选)"
              % (len(all_pal) - len(matched), PB.TITLE_KEY))
    hwnd = picked[0]

    hr("② 窗口几何 (决定追怪换算与停机阈值)")
    l, t, r, b = win32gui.GetWindowRect(hwnd)
    cr = win32gui.GetClientRect(hwnd)
    pt = win32gui.ClientToScreen(hwnd, (0, 0))
    win = (r - l, b - t)
    coff = (pt[0] - l, pt[1] - t)
    csize = (cr[2], cr[3])
    ka, ka_src = WG.read_keep_aspect_ex()
    print("  窗口外框 %dx%d   客户区 %dx%d (偏移 %s)"
          % (win[0], win[1], csize[0], csize[1], coff))
    print("  KeepAspectRatio = %s  (来源: %s)"
          % ("未知" if ka is None else int(ka),
             os.path.basename(ka_src) if ka_src else "没找到 sdlpal.cfg"))
    rowmax = colmax = None
    try:
        g = PB.Game(hwnd)
        a = g.shot()
        # ★ 与 pal_bot.Game.geom 一致: 先裁到客户区再量, 否则标题栏把剖面照成一片亮
        cx = max(0, min(int(coff[0]), max(0, a.shape[1] - 1)))
        cy = max(0, min(int(coff[1]), max(0, a.shape[0] - 1)))
        sub = a[cy:cy + int(csize[1]), cx:cx + int(csize[0])]
        if sub.shape[0] >= 40 and sub.shape[1] >= 40:
            rowmax = sub.max(axis=(1, 2))
            colmax = sub.max(axis=(0, 2))
            rec = WG.detect_content_rect(rowmax, colmax)
            if rec:
                print("  客户区内量到画面矩形 = %s -> 窗口坐标 %s"
                      % (tuple(int(v) for v in rec),
                         tuple(int(v + o) for v, o in zip(rec, coff))))
            else:
                print("  客户区内量不到黑边 (画面铺满客户区 或 整体同亮度)")
        else:
            print("  %s 客户区太小 (%s) -> 跳过像素量测" % (WARN, sub.shape))
    except Exception as e:
        print("  %s 截图失败 (%s) -> 只能用尺寸推算" % (WARN, e))
    geo = WG.choose(win, coff, csize, rowmax=rowmax, colmax=colmax,
                    measure_off=coff)
    print("  实测几何: %s" % WG.describe(geo))
    if WG.close_to_ref(geo):
        print("  %s 与标定窗口一致 -> 全部沿用标定常量, 行为与旧版完全相同"
              % OK)
    else:
        print("  %s 窗口尺寸与标定不同 -> 已自动换算像素级常量:" % WARN)
        print("     标定 -> 实测")
        for nm, old, new in (
                ("TILE_DX/DY", (83, 42),
                 (WG.dx_px(geo, WG.N_TILE_X), WG.dy_px(geo, WG.N_TILE_Y))),
                ("PATCH", (120, 195, 460, 440), WG.patch(geo)),
                ("BOX", (110, 220, 500, 600), WG.box(geo)),
                ("视口中心", (843, 664), (WG.content_cx(geo),
                                          WG.content_cy(geo))),
                ("is_battle 波段顶", 905, WG.band_top(geo)),
                ("停机阈值 diff", (2500, 150000),
                 (WG.px_thr(WG.FRAC["wall"], WG.area(geo)),
                  WG.px_thr(WG.FRAC["battle_trans"], WG.area(geo))))):
            print("     %-14s %-22s -> %s" % (nm, str(old), str(new)))
        print("   (阈值按面积比例换算, 缩放后'全屏 7% 像素'的语义不变)")
        k = geo.get("k", 1.0)
        if k < 0.55 or k > 1.9:
            print("  %s 缩放 k=%.3f 已严重偏离标定 (1.0) —— 画面细节特征会明显变弱,"
                  " 建议把窗口调回接近 %dx%d" % (WARN, k, WG.REF_WIN[0], WG.REF_WIN[1]))

    hr("③ 内存基址自检 (换 exe / 换机器 / 重启过 —— 最容易炸的就是这里)")
    # ★ 2026-10-16: 先报"地址平移量"。sdlpal.exe 开了 ASLR, 而 Windows 的
    #   映像基址随机化**按'本次开机'决定一次** -> 换机器/重启就可能换基址,
    #   于是标定过的绝对地址全错 (轻则读到垃圾, 重则 READ_TABLE_FAIL)。
    try:
        import game_addr as GA                          # noqa: E402
        for ln in GA.diag_lines():
            print("   " + ln)
    except Exception as e:
        print("  %s game_addr 诊断取失败: %s" % (WARN, e))
    # 解码报告 (独立来源): 场景 -> 地图号
    report_map = {}
    try:
        txt = open(os.path.join(BASE, "gyt_scene_report.txt"),
                   encoding="utf-8", errors="ignore").read()
        for m in re.finditer(r"场景\s*(\d+)\s+地图\s*(\d+)", txt):
            report_map[int(m.group(1))] = int(m.group(2))
    except Exception as e:
        print("  %s 读不到 gyt_scene_report.txt (%s) -> 少一条交叉验证"
              % (WARN, e))
    try:
        mem = PM.Mem()
    except SystemExit as e:
        print("  %s 打开游戏进程失败: %s" % (BAD, e))
        return 2
    ok, det = mem.selfcheck(report_map)
    for ln in det:
        print("   " + ln)
    print("  基准: 场景 1..300 / 队伍像素 x<2048 y<2048 / 场景表合理率>=90%"
          " / 地图号与解码报告一致")

    hr("结论")
    if ok and WG.close_to_ref(geo):
        print("%s 这个 exe + 这个窗口 = 标定环境, 直接跑, 无附加说明。" % OK)
    elif ok:
        print("%s 基址 OK (可以跑); 窗口尺寸与标定不同 —— 几何按 [%s] 自动换算,\n"
              "   追怪换算 / 停机阈值 / 画面裁剪框都会跟着走。画面判定依赖的小字\n"
              "   特征在大幅缩放时会变弱 —— 建议窗口宽保持在 1200~2000 之间。"
              % (WARN, geo.get("cfg") or geo.get("src")))
        if geo.get("note") and "相差" in geo["note"]:
            print("   %s 注意: %s" % (WARN, geo["note"]))
    else:
        print("%s 内存基址对不上当前进程 -> 机器人启动时会拒绝按键并停机。\n"
              "   要么换回标定过的那个 exe (标题含 'Pal Win95' 的那个),"
              " 要么先做一轮重新标定。" % BAD)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
