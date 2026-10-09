# -*- coding: utf-8 -*-
"""battle_probe.py — 战斗/菜单判定只读探针 (2026-09-30, is_battle v2 配套)。

★ 零副作用声明 (纪律见 live-process-safe-diagnostics 技能):
  * 只 PrintWindow 截屏, 不按任何键, 不碰 mem_stop.flag / .pal_bot.lock;
  * 不实例化 MemBot (不会写 bot_log_mem.txt);
  * 唯一可写文件是 --save 的标注图, 默认写系统临时目录, 绝不写游戏目录。

用法:
  python tools/battle_probe.py          # 对每个 Pal 窗口截帧并打印判定细节
  python tools/battle_probe.py --save   # 另存标注截图 (系统临时目录)

什么时候用: 换了地图怀疑「战斗识别不出来」时 —— 把疑似战斗的画面停在
屏幕上跑一次, 对比 is_battle v2 的每个特征 (绿 MP 数字锚 / 白 HP 数字 /
棕框) 与菜单判定 red/tan, 一眼看出哪个环节没过。

背景 (2026-09-30 场景185 熔岩图事故):
  旧 is_battle 的"棕框"掩码被熔岩红色地面污染 (19% 像素命中);
  旧菜单判定的"暗红面板"掩码更是直接把红色地面当菜单 (red=0.45 >> 0.04),
  机器人每步按 esc、真战斗被判"进场即菜单 -> 误抓", 一场都打不了。
  v2: is_battle 改为绿 MP 数字锚定 (场景无关); 菜单判定加上限
  MENU_RED_CAP=0.30 (真子页面 0.08~0.10, 红色场景 0.45+)。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np                                    # noqa: E402
from pal_bot import list_pal_windows, Game, battle_ui_feat  # noqa: E402
from pal_bot_mem2 import MemBot2                      # noqa: E402


def _annotate(a, feat, menu_feat, path):
    """标注图: 波段顶蓝线 + 命中 span 黄框 + 判定文字 (写到临时目录)。"""
    try:
        from PIL import Image, ImageDraw
    except Exception:
        return None
    img = Image.fromarray(a.astype("uint8")).convert("RGB")
    d = ImageDraw.Draw(img)
    y0 = feat["band_top"]
    d.line([(0, y0), (a.shape[1] - 1, y0)], fill=(60, 120, 255), width=3)
    for s in feat["spans"]:
        x, y, dh = s["x"], s["y"], s["dh"]
        half = max(24, dh * 3)
        color = (255, 220, 0) if s["ok"] else (255, 60, 60)
        d.rectangle([x - half, y - int(2.8 * dh), x + half, y + int(1.2 * dh)],
                    outline=color, width=3)
    d.rectangle([2, 2, a.shape[1] - 2, 46], fill=(0, 0, 0))
    d.text((8, 6), "battle=%s menu=%s red=%.3f tan=%.3f green=%d" %
           (feat["ok"], menu_feat[2], menu_feat[0], menu_feat[1], feat["green"]),
           fill=(255, 255, 255))
    img.save(path)
    return path


def probe(save=False):
    wins = list_pal_windows()
    if not wins:
        print("未找到标题含 Pal 的游戏窗口 —— 游戏没开? (只读探针, 未做任何操作)")
        return 1
    n_ok = 0
    for i, (hwnd, title) in enumerate(wins):
        g = Game(hwnd)
        a = g.shot()
        f = battle_ui_feat(a)
        red, tan = MemBot2._menu_feat(a)
        menu = MemBot2._menu_like(red, tan)
        print("窗口 #%d hwnd=%s 「%s」 帧 %dx%d" % (i, hwnd, title, a.shape[1], a.shape[0]))
        print("  [战斗判定 v2] ok=%s  绿MP数字=%d  波段顶=%d"
              % (f["ok"], f["green"], f["band_top"]))
        for s in f["spans"]:
            print("    数字块 x=%d y=%d 数字高=%d 绿=%d 白=%d 棕框=%.2f -> %s"
                  % (s["x"], s["y"], s["dh"], s["green"], s["white"],
                     s["brown"], "命中 ✓" if s["ok"] else "未命中"))
        cap = MemBot2.MENU_RED_CAP
        if red >= cap:
            why = "red %.3f ≥ 上限 %.2f -> 是红色场景不是菜单" % (red, cap)
        elif red > MemBot2.MENU_RED_TH:
            why = "red %.3f 在 (%.2f, %.2f) 菜单区间" % (red, MemBot2.MENU_RED_TH, cap)
        elif tan > MemBot2.MENU_TAN_TH:
            why = "tan %.3f > %.2f -> 菜单根卷轴" % (tan, MemBot2.MENU_TAN_TH)
        else:
            why = "red %.3f / tan %.3f 都在地图水平" % (red, tan)
        print("  [菜单判定] red=%.3f tan=%.3f -> menu=%s (%s)" % (red, tan, menu, why))
        if f["ok"] and not menu:
            print("  结论: ✓ 战斗中 (is_battle=True 且非菜单)")
            n_ok += 1
        elif f["ok"] and menu:
            print("  结论: 战斗 UI + 菜单特征并存 (战斗中开子页面? 会被按 esc)")
        elif menu:
            print("  结论: 菜单画面 (会被 esc 关闭)")
        else:
            print("  结论: 普通地图画面 (不战斗不按键)")
        if save:
            out = os.path.join(tempfile.gettempdir(), "battle_probe_%d.png" % i)
            p = _annotate(a, f, (red, tan, menu), out)
            if p:
                print("  标注图: %s" % p)
        print()
    print("判定为战斗的窗口数: %d / %d" % (n_ok, len(wins)))
    return 0


if __name__ == "__main__":
    probe(save="--save" in sys.argv)
