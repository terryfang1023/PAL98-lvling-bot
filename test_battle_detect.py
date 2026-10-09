# -*- coding: utf-8 -*-
"""test_battle_detect.py — 战斗判定 v2 / 菜单判定上限 回归测试 (2026-09-30)。

背景 (场景185 熔岩图事故):
  旧 is_battle 在底部 30% 波段里数"棕框/青字"两类颜色 —— 熔岩地图红色地面让
  棕框掩码命中 19% 像素, 而真面板底色 (148,32,24) 反而一个都不匹配;
  旧菜单判定的"暗红面板"(R90-190) 把红色地面全吃进去 (red=0.45 >> 0.04),
  ui_menu 恒真 -> 每步按 esc 开合死循环, 真战斗被判"进场即菜单 -> 误抓"。

v2 方案:
  is_battle  = 绿 MP 数字锚 + 同列上方白 HP 数字 + 周围棕框 (数字高度自标定,
               场景无关、缩放无关);
  ui_menu    = red 必须在 (0.04, 0.30) 区间 (真子页面 0.08~0.10, 红场景 0.45+);
  _esc_if_menu = "已关闭"以特征消失为准 + "esc 反而开出菜单"自愈 + 120s 冷却。

真实截图夹具 (绝对路径, 缺文件自动跳过 —— 游戏衍生数据不入 git):
  夹具来源: 用户截图 / 只读连拍 cap_*.png / 7 月标定截图 (母目录)。
"""
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import pal_bot as PB                                # noqa: E402
from pal_bot import battle_ui_feat, is_battle       # noqa: E402
from pal_bot_mem2 import MemBot2                    # noqa: E402

_N = [0]


def _ok(name, cond):
    _N[0] += 1
    print("%s %s" % ("PASS" if cond else "FAIL", name))
    return cond


# ---------------- 合成帧构造 (原生 320x200 布局, 按窗口几何绘制) ----------------
# 面板原生位置: 从真机截图标定 (两块, x 88..162 / 168..242, HP 行 y172..182, MP 行 y188..197)
PANEL_X0 = (88, 168)
PANEL_X1 = (162, 242)
HP_Y = (172, 182)
MP_Y = (188, 197)
C_WHITE = (212, 200, 168)     # HP 数字暖白 (真机标定)
C_GREEN = (80, 184, 148)      # MP 数字绿 (真机标定)
C_BROWN = (62, 28, 13)        # 面板深棕 (7 月截图)
C_BRICK = (148, 32, 24)       # 面板砖红底 (场景185 实测)
C_LAVA = (135, 63, 51)        # 熔岩地面
C_MENU_RED = (150, 40, 30)    # 菜单红面板


def _geo(win):
    """等比缩放 + 居中 的内容矩形 (与 wingeom.make 同规则)。"""
    w, h = win
    s = min(w / 320.0, h / 200.0)
    cw, ch = int(320 * s), int(200 * s)
    x0, y0 = (w - cw) // 2, (h - ch) // 2
    return x0, y0, s


def _draw_ui(d, x0, y0, s, members=2, brown=C_BROWN,
             hp=True, mp=True, panel_on=True):
    for i in range(members):
        px0, px1 = PANEL_X0[i], PANEL_X1[i]
        if panel_on:
            d.rectangle([x0 + px0 * s, y0 + 168 * s, x0 + px1 * s, y0 + 199 * s],
                        fill=brown)
        # HP 白字 / MP 绿字: 一排小方块模拟数字笔画 (块宽 3px 原生, 保证缩放后成块)
        for (yy, col) in ((HP_Y, hp), (MP_Y, mp)):
            if not col:
                continue
            c = C_WHITE if (yy is HP_Y) else C_GREEN
            for k in range(7):
                bx = px0 + 30 + k * 6
                d.rectangle([x0 + bx * s, y0 + yy[0] * s,
                             x0 + (bx + 3) * s, y0 + yy[1] * s], fill=c)


def synth_battle(win, members=2, ground=C_LAVA, brown=C_BROWN,
                 hp=True, mp=True, panel_on=True, noise=10, seed=7):
    rng = np.random.RandomState(seed)
    w, h = win
    img = Image.new("RGB", (w, h), (0, 0, 0))
    d = ImageDraw.Draw(img)
    x0, y0, s = _geo(win)
    # 地面噪点 (熔岩红)
    g = np.array(ground, dtype=np.int16)
    base = np.clip(g[None, None, :] + rng.randint(-noise, noise + 1, (h, w, 1)), 0, 255)
    img = Image.fromarray(base.astype("uint8"))
    d = ImageDraw.Draw(img)
    _draw_ui(d, x0, y0, s, members=members, brown=brown, hp=hp, mp=mp,
             panel_on=panel_on)
    return np.asarray(img, dtype=np.int16)


def synth_field(win, ground=C_LAVA, noise=14, seed=3):
    return synth_battle(win, members=0, ground=ground, noise=noise, seed=seed)


def synth_menu_root(win, seed=5):
    """菜单根: 左侧棕黄卷轴 (tan 区), 无数字。"""
    w, h = win
    img = Image.fromarray(synth_field(win, ground=(60, 60, 70), seed=seed).astype("uint8"))
    d = ImageDraw.Draw(img)
    x0, y0, s = _geo(win)
    d.rectangle([x0 + 6 * s, y0 + 52 * s, x0 + 61 * s, y0 + 120 * s],
                fill=(180, 140, 90))          # tan 卷轴
    return np.asarray(img, dtype=np.int16)


def synth_spell_page(win, seed=11):
    """法术子页: 中央暗红面板 (约占中心带 10%) + 白字行, 无绿数字。"""
    w, h = win
    img = Image.fromarray(synth_field(win, ground=(50, 70, 50), seed=seed).astype("uint8"))
    d = ImageDraw.Draw(img)
    x0, y0, s = _geo(win)
    px0, px1 = int(w * 0.25), int(w * 0.75)
    py0, py1 = int(h * 0.38), int(h * 0.62)
    d.rectangle([px0, py0, px1, py1], fill=C_MENU_RED)
    for r in range(4):
        yy = py0 + (py1 - py0) * (r + 1) // 5
        for k in range(10):
            d.rectangle([px0 + 20 + k * 30, yy, px0 + 40 + k * 30, yy + int(8 * s)],
                        fill=C_WHITE)
    return np.asarray(img, dtype=np.int16)


# ---------------- 测试主体 ----------------
def main():
    n_fail = 0

    # ---- ① 合成战斗帧: 多窗口尺寸 / 多队员数 / 两种面板底色 ----
    for win in ((1653, 1121), (1687, 1294), (900, 700), (800, 600)):
        for members in (1, 2):     # 面板原生位置只标定了两块 (88..242), 3人布局未知
            a = synth_battle(win, members=members)
            n_fail += not _ok("战斗判定 True %sx%s %d人" % (win[0], win[1], members),
                              is_battle(a))
    # 砖红面板底 (场景185 实测面板色) 也要能判
    a = synth_battle((1653, 1121), brown=C_BRICK)
    n_fail += not _ok("战斗判定 True 砖红面板底", is_battle(a))

    # ---- ② 竖长窗口: 旧 int(h*0.70) 波段会落在面板之下 (v2 默认 0.45h 修复) ----
    a = synth_battle((800, 1500))
    n_fail += not _ok("战斗判定 True 竖长窗口 800x1500", is_battle(a))

    # ---- ③ 合成负例 ----
    n_fail += not _ok("熔岩地面无UI -> False", not is_battle(synth_field((1653, 1121))))
    n_fail += not _ok("只有HP白字无绿锚 -> False",
                      not is_battle(synth_battle((1653, 1121), mp=False)))
    n_fail += not _ok("只有MP绿字无HP白字 -> False",
                      not is_battle(synth_battle((1653, 1121), hp=False)))
    n_fail += not _ok("数字悬浮无面板(灰洞地面) -> False",
                      not is_battle(synth_battle((1653, 1121), panel_on=False,
                                                 ground=(60, 60, 70), noise=6)))
    n_fail += not _ok("菜单根卷轴 -> False", not is_battle(synth_menu_root((1653, 1121))))
    n_fail += not _ok("法术子页(红面板+白字) -> False",
                      not is_battle(synth_spell_page((1653, 1121))))

    # ---- ④ 菜单判定上限 (MENU_RED_CAP): 红场景不再是菜单 ----
    red_lava, tan_lava = MemBot2._menu_feat(synth_field((1653, 1121)))
    n_fail += not _ok("熔岩地面 red=%.2f > CAP -> 非菜单" % red_lava,
                      red_lava > MemBot2.MENU_RED_CAP
                      and not MemBot2._menu_like(red_lava, tan_lava))
    red_pg, tan_pg = MemBot2._menu_feat(synth_spell_page((1653, 1121)))
    n_fail += not _ok("法术子页 red=%.2f 在区间 -> 菜单" % red_pg,
                      MemBot2.MENU_RED_TH < red_pg < MemBot2.MENU_RED_CAP
                      and MemBot2._menu_like(red_pg, tan_pg))
    red_mr, tan_mr = MemBot2._menu_feat(synth_menu_root((1653, 1121)))
    n_fail += not _ok("菜单根 tan=%.2f -> 菜单" % tan_mr,
                      MemBot2._menu_like(red_mr, tan_mr))
    n_fail += not _ok("MENU_RED_CAP = 0.30", MemBot2.MENU_RED_CAP == 0.30)

    # ---- ⑤ 真实截图夹具 (缺文件跳过) ----
    D = r"D:\Fang\Game\PAL_98\_bot"
    FIX = [
        (r"C:\Users\HP\.workbuddy\clipboard-images\clipboard-2026-09-30T07-57-58-129Z-af8eb709.png",
         True, "用户熔岩战斗截图"),
        (r"C:\Users\HP\WorkBuddy\2026-08-04-10-46-44\cap\cap_0.png",
         True, "连拍熔岩战斗帧"),
        (os.path.join(D, "g_02_battle.png"), True, "7月战斗帧"),
        (os.path.join(D, "bt_00.png"), True, "7月战斗帧bt_00"),
        (os.path.join(D, "battle_seq_00.png"), False, "7月战斗转场(无UI)"),
        (os.path.join(D, "d2_base.png"), False, "7月地图帧"),
        (os.path.join(D, "f_00_start.png"), False, "7月菜单根"),
        (os.path.join(D, "stuck_184524.png"), False, "7月卡住帧"),
    ]
    for p, want, name in FIX:
        if not os.path.exists(p):
            _ok("夹具跳过(缺文件) %s" % name, True)
            continue
        a = np.asarray(Image.open(p).convert("RGB"), dtype=np.int16)
        n_fail += not _ok("夹具 %s -> %s" % (name, want), is_battle(a) == want)

    # ---- ⑥ 特征自标定性: 数字高度随缩放变化仍判定一致 ----
    f1 = battle_ui_feat(synth_battle((1653, 1121)))
    f2 = battle_ui_feat(synth_battle((900, 700)))
    n_fail += not _ok("数字高度自标定 (大窗 dh=%d, 小窗 dh=%d 都命中)"
                      % (f1["spans"][0]["dh"], f2["spans"][0]["dh"]),
                      f1["ok"] and f2["ok"])

    print("=" * 60)
    print("PASS %d  FAIL %d" % (_N[0] - n_fail, n_fail))
    print("=" * 60)
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
