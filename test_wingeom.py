# -*- coding: utf-8 -*-
"""test_wingeom.py — 窗口几何换算回归测试 (v17.2)

核心铁律: **在标定窗口 (1687x1294) 下, 换算结果必须与旧硬编码常量逐位相同**。
任何一条不过, 就说明"改了行为"而不是"修了鲁棒性"。

不依赖游戏/窗口, 纯离线。
"""
import sys
import os
import tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wingeom as WG                                    # noqa: E402

# 一个保证不存在的 cfg 路径: 用来模拟"读不到 sdlpal.cfg"
NOSUCH = os.path.join(tempfile.gettempdir(), "_wingeom_no_such_cfg_9527.cfg")

OK, BAD = [], []


def check(name, cond, extra=""):
    (OK if cond else BAD).append(name)
    print("%s  %s%s" % ("PASS" if cond else "FAIL", name,
                        ("  [%s]" % extra) if extra else ""))


print("=" * 60)
print("第 1 节: 标定几何 -> 与旧硬编码逐位相同")
print("=" * 60)
L = WG.legacy()
check("legacy 的 k = 1", L["k"] == 1.0, str(L["k"]))
check("PATCH = (120,195,460,440)", WG.patch(L) == (120, 195, 460, 440),
      str(WG.patch(L)))
check("BOX = (110,220,500,600)", WG.box(L) == (110, 220, 500, 600),
      str(WG.box(L)))
check("FCAP = (600,660)", WG.fcap(L) == (600, 660), str(WG.fcap(L)))
check("TILE_DX = 83", WG.dx_px(L, WG.N_TILE_X) == 83, str(WG.dx_px(L, WG.N_TILE_X)))
check("TILE_DY = 42", WG.dy_px(L, WG.N_TILE_Y) == 42, str(WG.dy_px(L, WG.N_TILE_Y)))
check("party_box = (100,110)", WG.party_box(L) == (100, 110), str(WG.party_box(L)))
check("content_cx = 1687//2 = 843", WG.content_cx(L) == 1687 // 2,
      str(WG.content_cx(L)))
check("content_cy = (139+1189)//2 = 664", WG.content_cy(L) == (139 + 1189) // 2,
      str(WG.content_cy(L)))
check("band_top = int(1294*0.70) = 905", WG.band_top(L) == int(1294 * 0.70),
      "%d vs %d" % (WG.band_top(L), int(1294 * 0.70)))
check("content_band = (135,1193)", WG.content_band(L) == (135, 1193),
      str(WG.content_band(L)))
for k in ("up", "down", "left", "right"):
    old = {"up": (-83, 42), "down": (83, -42),
           "right": (-83, -42), "left": (83, 42)}[k]
    check("tile_shift(%s) = %s" % (k, old), WG.tile_shift(L, k) == old,
          str(WG.tile_shift(L, k)))
check("scale = (5.272, 5.25) 与旧常量一致",
      abs(L["scale"][0] - 1687 / 320.0) < 1e-9
      and abs(L["scale"][1] - 1050 / 200.0) < 1e-9, str(L["scale"]))

print()
print("=" * 60)
print("第 2 节: 面积比例阈值 -> 标定窗口下还原为原绝对值")
print("=" * 60)
AREA = WG.area(L)
check("area = 1687*1294", AREA == 1687 * 1294, str(AREA))
for key, old in (("anim_min", 300), ("menu_chg", 1500), ("wall", 2500),
                 ("anim_big", 8000), ("battle_trans", 150000)):
    v = WG.px_thr(WG.FRAC[key], AREA)
    check("px_thr(%s) = %d" % (key, old), v == old, str(v))
check("px_thr 至少为 1", WG.px_thr(1e-12, 100) == 1,
      str(WG.px_thr(1e-12, 100)))
# 窗口放大 4 倍 -> 阈值也应放大 4 倍 (而不是原地不动)
check("面积 x4 -> 阈值 x4", WG.px_thr(WG.FRAC["wall"], AREA * 4) == 10000,
      str(WG.px_thr(WG.FRAC["wall"], AREA * 4)))

print()
print("=" * 60)
print("第 3 节: 真实窗口几何 (含标题栏/黑边)")
print("=" * 60)
# 标定窗口: 外框 1687x1294, 标题栏 39, 客户区 1687x1255
g = WG.make((1687, 1294), (0, 39), (1687, 1255))
check("标定窗口能被识别为'与标定一致'", WG.close_to_ref(g), WG.describe(g))
check("标定窗口走零回归通道 (choose -> legacy)",
      WG.choose((1687, 1294), (0, 39), (1687, 1255))["src"] == "legacy")
check("标定窗口视口 y0 = 139",
      WG.choose((1687, 1294), (0, 39), (1687, 1255))["content"][1] == 139,
      str(WG.choose((1687, 1294), (0, 39), (1687, 1255))["content"]))

# 客户区正好 1.6 (无黑边): 1600x1000 -> 缩放 5.0
gA = WG.make((1600, 1039), (0, 39), (1600, 1000))
check("1600x1000 客户区 -> 缩放 5.0", abs(gA["scale"][0] - 5.0) < 1e-6,
      str(gA["scale"]))
check("1600x1000 视口铺满客户区", gA["content"] == (0, 39, 1600, 1039),
      str(gA["content"]))
check("1600x1000 k ≈ 0.948", abs(gA["k"] - 5.0 / WG.REF_SX) < 1e-6,
      "%.4f" % gA["k"])
check("1600x1000 PATCH ≈ (114,92,437,326)",
      all(abs(a - b) <= 2 for a, b in
          zip(WG.patch(gA), (114, 92, 437, 326))), str(WG.patch(gA)))

# 同一客户区放大 2 倍: 3200x2000 -> 缩放 10.0 (各量应约 2 倍)
gB = WG.make((3200, 2039), (0, 39), (3200, 2000))
check("3200x2000 缩放 10.0", abs(gB["scale"][0] - 10.0) < 1e-6,
      str(gB["scale"]))
def rel(g, box):
    """把窗口绝对坐标换成"相对视口原点"的坐标 (标题栏偏移不参与缩放)。"""
    c = g["content"]
    return (box[0] - c[0], box[1] - c[1], box[2] - c[0], box[3] - c[1])


check("PATCH 相对视口线性放大 (2 倍)",
      all(abs(b - 2 * a) <= 2 for a, b in
          zip(rel(gA, WG.patch(gA)), rel(gB, WG.patch(gB)))),
      "%s -> %s" % (rel(gA, WG.patch(gA)), rel(gB, WG.patch(gB))))
check("TILE 随窗口线性放大 (2 倍)",
      abs(WG.dx_px(gB, WG.N_TILE_X) - 2 * WG.dx_px(gA, WG.N_TILE_X)) <= 2
      and abs(WG.dy_px(gB, WG.N_TILE_Y) - 2 * WG.dy_px(gA, WG.N_TILE_Y)) <= 2,
      "%d/%d -> %d/%d" % (WG.dx_px(gA, WG.N_TILE_X), WG.dy_px(gA, WG.N_TILE_Y),
                          WG.dx_px(gB, WG.N_TILE_X), WG.dy_px(gB, WG.N_TILE_Y)))
check("band_top 始终落在视口内 (两种尺寸)",
      gA["content"][1] < WG.band_top(gA) < gA["content"][3]
      and gB["content"][1] < WG.band_top(gB) < gB["content"][3],
      "%d / %d" % (WG.band_top(gA), WG.band_top(gB)))
check("FCAP 装得下 BOX + 一格位移",
      WG.fcap(gB)[0] >= WG.box(gB)[2] + WG.dx_px(gB, WG.N_TILE_X)
      and WG.fcap(gB)[1] >= WG.box(gB)[3] + WG.dy_px(gB, WG.N_TILE_Y),
      "%s vs BOX%s" % (WG.fcap(gB), WG.box(gB)))

# 竖高窗口 (1000x800 客户区, 比例 1.25 < 1.6 -> 宽度受限, 上下留黑边)
gC = WG.make((1000, 839), (0, 39), (1000, 800))
check("1000x800 视口宽 = 客户区宽", gC["content"][2] - gC["content"][0] == 1000,
      str(gC["content"]))
check("1000x800 视口上下居中 (y0=126, y1=751)",
      gC["content"][1] == 126 and gC["content"][3] == 751,
      str(gC["content"]))
check("1000x800 等比 (sx == sy)", abs(gC["scale"][0] - gC["scale"][1]) < 1e-9,
      str(gC["scale"]))
check("1000x800 阈值随面积缩小",
      WG.px_thr(WG.FRAC["wall"], WG.area(gC)) < 2500,
      str(WG.px_thr(WG.FRAC["wall"], WG.area(gC))))

# 拉伸模式 (KeepAspectRatio=0): 视口 = 客户区
g4 = WG.make((1600, 900), (0, 39), (1600, 861), keep_aspect=False)
check("拉伸模式视口 = 客户区",
      g4["content"] == (0, 39, 1600, 900), str(g4["content"]))
check("拉伸模式非等比标记", g4["uniform"] is False, str(g4["uniform"]))
check("拉伸模式: 客户区偏宽 -> y 缩放 < x 缩放",
      g4["scale"][1] < g4["scale"][0], str(g4["scale"]))

print()
print("=" * 60)
print("第 4 节: 从像素量视口矩形 (黑边不必是纯黑)")
print("=" * 60)
import numpy as np                                      # noqa: E402


def frame_of(g, bg=0, bright=200):
    """造一帧"视口内亮、视口外=bg"的逐行/逐列最大值。"""
    W, H = g["win"]
    x0, y0, x1, y1 = g["content"]
    row = np.full(H, bg, dtype=np.int32)
    col = np.full(W, bg, dtype=np.int32)
    row[y0:y1] = bright
    col[x0:x1] = bright
    return row, col


r, c = frame_of(gC)
check("量出 1000x800 窗口的视口 (0,126,1000,751)",
      WG.detect_content_rect(r, c) == (0, 126, 1000, 751),
      str(WG.detect_content_rect(r, c)))
r, c = frame_of(gC, bg=32)          # ★ 深灰黑边 (SDL 清屏色不是纯黑)
check("黑边是深灰(32) 也能量出", WG.detect_content_rect(r, c) == (0, 126, 1000, 751),
      str(WG.detect_content_rect(r, c)))
r, c = np.full(1000, 200), np.full(1600, 200)   # ★ 客户区帧: 全亮 = 无黑边
check("无黑边(客户区全亮) -> 量不出来 (退回模型推算)",
      WG.detect_content_rect(r, c) is None, str(WG.detect_content_rect(r, c)))
check("无黑边 -> choose 用模型几何 (铺满客户区)",
      WG.choose((1600, 1039), (0, 39), (1600, 1000), rowmax=r,
                colmax=c)["content"] == (0, 39, 1600, 1039),
      str(WG.choose((1600, 1039), (0, 39), (1600, 1000), rowmax=r,
                    colmax=c)["content"]))
r, c = frame_of(gC)
check("只有上下黑边(左右铺满) 也能量出",
      WG.detect_content_rect(r, c) == (0, 126, 1000, 751),
      str(WG.detect_content_rect(r, c)))
check("整体同亮度 -> 量不出来 (返回 None)",
      WG.detect_content_rect(np.full(800, 100), np.full(1000, 100)) is None)
check("全黑 -> 量不出来", WG.detect_content_rect(np.zeros(800),
                                            np.zeros(1000)) is None)
check("太小的数组 -> None", WG.detect_content_rect(np.zeros(10),
                                              np.zeros(10)) is None)
gp = WG.from_rect((1000, 839), (0, 126, 1000, 751))
check("from_rect 缩放 = 3.125", abs(gp["scale"][0] - 3.125) < 1e-9,
      str(gp["scale"]))
check("比例不对(拉长) -> from_rect 拒绝",
      WG.from_rect((1000, 839), (0, 100, 1000, 300)) is None)
_g = WG.choose((1000, 839), (0, 39), (1000, 800), rowmax=r, colmax=c,
               keep_aspect=True)
check("choose: cfg 说等比 -> 模型与像素量测一致 (同一结果)",
      _g["content"] == (0, 126, 1000, 751) and "一致" in (_g.get("note") or ""),
      "%s | %s" % (_g["content"], _g.get("note")))
check("choose: cfg 读不到 -> 像素量测胜出 (src=pixel)",
      WG.choose((1000, 839), (0, 39), (1000, 800), rowmax=r, colmax=c,
                cfg_path=NOSUCH)["src"] == "pixel",
      WG.choose((1000, 839), (0, 39), (1000, 800), rowmax=r, colmax=c,
                cfg_path=NOSUCH)["src"])
r2, c2 = frame_of(gC)
check("实测矩形 = 标定几何时走零回归通道",
      WG.choose((1687, 1294), (0, 39), (1687, 1255), rowmax=r2,
                colmax=c2)["src"] in ("legacy", "pixel"))

print()
print("=" * 60)
print("第 5 节: 异常输入不崩、退回标定几何")
print("=" * 60)
for bad in ((0, 0), (-5, 100), (100, -1)):
    r = WG.make(bad, (0, 0), (10, 10))
    check("窗口 %s -> legacy" % (bad,), r["src"] == "legacy", r["src"])
r = WG.make((1687, 1294), (0, 39), (0, 0))
check("客户区为 0 -> legacy", r["src"] == "legacy", r["src"])
check("describe 不抛异常", isinstance(WG.describe(WG.legacy()), str))

print()
print("=" * 60)
print("第 6 节: 就地更新模块常量 (v3 独立模式用的全局名) 且能原样还原")
print("=" * 60)
import pal_bot as PB                                    # noqa: E402

g = PB.Game.__new__(PB.Game)                            # 不建窗口
before = (PB.PATCH, PB.TILE_DX, PB.TILE_DY, PB.TILE_MAG,
          PB.Game.FCAPW, PB.Game.FCAPH, PB.PalBot.BOX,
          tuple(sorted(PB.PalBot.EXPECTED.items())))
g._apply_geo(WG.legacy())                               # 标定几何 = 不改动
after = (PB.PATCH, PB.TILE_DX, PB.TILE_DY, PB.TILE_MAG,
         PB.Game.FCAPW, PB.Game.FCAPH, PB.PalBot.BOX,
         tuple(sorted(PB.PalBot.EXPECTED.items())))
check("标定几何 -> 常量保持不变", before == after,
      "%s -> %s" % (before[0:3], after[0:3]))
check("标定几何下 PATCH 仍是 (120,195,460,440)",
      PB.PATCH == (120, 195, 460, 440), str(PB.PATCH))

g._apply_geo(WG.make((1600, 1039), (0, 39), (1600, 1000)))
check("缩放窗口 -> TILE_DX/DY 被换算", (PB.TILE_DX, PB.TILE_DY) == (79, 40),
      "%s/%s" % (PB.TILE_DX, PB.TILE_DY))
check("缩放窗口 -> PATCH 被换算", PB.PATCH == (114, 92, 436, 326),
      str(PB.PATCH))
check("缩放窗口 -> PalBot.BOX 被换算", PB.PalBot.BOX == (104, 116, 474, 478),
      str(PB.PalBot.BOX))
check("缩放窗口 -> EXPECTED 跟着 TILE 变",
      PB.PalBot.EXPECTED["up"] == (-PB.TILE_DX, PB.TILE_DY),
      str(PB.PalBot.EXPECTED["up"]))
check("缩放窗口 -> 抓帧尺寸被重建",
      (PB.Game.FCAPW, PB.Game.FCAPH) == WG.fcap(WG.make(
          (1600, 1039), (0, 39), (1600, 1000))),
      "%s/%s" % (PB.Game.FCAPW, PB.Game.FCAPH))

g._apply_geo(WG.legacy())                               # ★ 还原
after2 = (PB.PATCH, PB.TILE_DX, PB.TILE_DY, PB.TILE_MAG,
          PB.Game.FCAPW, PB.Game.FCAPH, PB.PalBot.BOX,
          tuple(sorted(PB.PalBot.EXPECTED.items())))
check("回到标定几何 -> 全部原样还原", after2 == before,
      "%s vs %s" % (after2[0:3], before[0:3]))

# ======================================================================
#  第 7 节起: v17.2 真机实录修复 —— 标题栏污染 + cfg 优先
#  (1653x1121 窗口上, 未裁客户区的量测得到 (0,0,1653,1090) 这种错矩形)
# ======================================================================
import numpy as np                                      # noqa: E402

print()
print("=" * 60)
print("第 7 节: 标题栏污染 (为什么必须先裁客户区)")
print("=" * 60)


def _prof(n, lo, hi, dark=0.0, bright=200.0):
    a = np.full(n, dark, dtype=np.float64)
    a[lo:hi] = bright
    return a


def _asp(rect):
    if not rect:
        return float("nan")
    return (rect[2] - rect[0]) / float(rect[3] - rect[1])


# 真机参数: 外框 1653x1121, 客户区 1631x1065 @偏移(11,45), KeepAspectRatio=1
# -> 等比 5.0969x, 宽受限, 上下各约 23 行黑边
rows_clean = _prof(1065, 23, 1042)          # 客户区裁剪后的逐行剖面
cols_clean = np.full(1631, 200.0)           # 宽受限 -> 左右无黑边
rec_clean = WG.detect_content_rect(rows_clean, cols_clean)
check("已裁客户区 -> 量出 (0,23,1631,1042)", rec_clean == (0, 23, 1631, 1042),
      str(rec_clean))
check("已裁客户区 -> 宽高比贴近 1.6 (误差<1%)", abs(_asp(rec_clean) - 1.6) < 0.016,
      "%.4f" % _asp(rec_clean))

# ★ 黑边只占 2.2% (<5%): 若用 5% 分位定位"黑边电平", 会落到内容上 -> 量不出来
check("黑边占比 4.3% (<5%) 仍能量出 (lo 取 min 而不是 5% 分位)",
      WG.detect_content_rect(_prof(1065, 23, 1042), np.full(1631, 200.0))
      == (0, 23, 1631, 1042),
      str(WG.detect_content_rect(rows_clean, cols_clean)))

# 未裁客户区: 前 45 行是标题栏(亮 243) -> 逐行说"内容从 y=0 开始";
#            逐列被抬到 243 -> 左右黑边量不出来
rows_dirty = np.full(1121, 0.0)
rows_dirty[:45] = 243.0                     # 标题栏
rows_dirty[65:1110] = 200.0                 # 画面 (上下各留黑边)
cols_dirty = np.full(1653, 243.0)           # ★ 标题栏把每一列照成同一亮度
rec_dirty = WG.detect_content_rect(rows_dirty, cols_dirty)
check("未裁客户区 -> y0 = 0 (把 45 行标题栏算成画面)",
      bool(rec_dirty) and rec_dirty[1] == 0, str(rec_dirty))
check("未裁客户区 -> 宽高比明显偏离 1.6", abs(_asp(rec_dirty) - 1.6) > 0.05,
      "%.4f" % _asp(rec_dirty))
check("未裁客户区 -> 逐列黑边检测被污染 (hi-lo=0)",
      float(cols_dirty.max() - cols_dirty.min()) == 0.0, "243 vs 243")

print()
print("=" * 60)
print("第 8 节: bars_absent (cfg 读不到时判等比/拉伸)")
print("=" * 60)
check("两轴都有黑边 -> False",
      WG.bars_absent(_prof(1065, 23, 1042), _prof(1631, 30, 1600)) is False)
check("两轴都无黑边 -> True (拉伸特征)",
      WG.bars_absent(np.full(1000, 200.0), np.full(1600, 200.0)) is True)
check("只有一轴有黑边 -> False",
      WG.bars_absent(_prof(1065, 23, 1042), np.full(1631, 200.0)) is False)
check("rows=None -> False (数据不足不下结论)",
      WG.bars_absent(None, np.full(100, 1.0)) is False)
check("剖面太短 -> False",
      WG.bars_absent(np.full(8, 1.0), np.full(8, 1.0)) is False)

print()
print("=" * 60)
print("第 9 节: choose 优先级 (标定 > cfg 模型 > 像素量测)")
print("=" * 60)
try:
    os.remove(NOSUCH)
except Exception:
    pass
WIN = (1653, 1121)
OFF = (11, 45)
CLI = (1631, 1065)

g = WG.choose(WG.REF_WIN, (0, 39), (1680, 1255), keep_aspect=True)
check("① 窗口 = 标定尺寸 -> 零回归 legacy", WG.is_legacy(g) and g["k"] == 1.0,
      WG.describe(g))

g = WG.choose(WIN, OFF, CLI, rowmax=rows_dirty, colmax=cols_dirty,
              keep_aspect=True)
check("② cfg=等比 -> 忽略被污染的像素量测, 用客户区模型",
      g["content"] == (11, 68, 1642, 1087), str(g["content"]))
check("② cfg=等比 -> x/y 缩放相等 (uniform)", g["uniform"] is True,
      str(g["scale"]))
check("② cfg=等比 -> k = 5.0969/5.2719 = 0.9668", abs(g["k"] - 0.9668) < 0.001,
      "%.4f" % g["k"])
check("② 换算出来的 PATCH 落在视口内",
      WG.patch(g)[0] > g["content"][0] and WG.patch(g)[2] < g["content"][2],
      str(WG.patch(g)))
check("② cfg 来源写进 geo",
      "KeepAspectRatio=1" in (g.get("cfg") or ""), str(g.get("cfg")))

g = WG.choose(WIN, OFF, CLI, keep_aspect=False)
check("③ cfg=拉伸 -> 视口 = 整个客户区", g["content"] == (11, 45, 1642, 1110),
      str(g["content"]))
check("③ cfg=拉伸 -> 各轴独立缩放",
      abs(g["scale"][0] - 1631 / 320.0) < 1e-9
      and abs(g["scale"][1] - 1065 / 200.0) < 1e-9, str(g["scale"]))

g = WG.choose(WIN, OFF, CLI, rowmax=rows_clean, colmax=cols_clean,
              measure_off=OFF, cfg_path=NOSUCH)
check("④ cfg 缺失 + 有像素量测 -> 采用量测", g.get("cfg") == "cfg 缺失",
      str(g.get("cfg")))
check("④ 量测矩形被 measure_off 换算到窗口坐标",
      g["content"] == (11, 68, 1642, 1087), str(g["content"]))

g = WG.choose(WIN, OFF, CLI, cfg_path=NOSUCH)
check("⑤ cfg 缺失 + 无像素 -> 按等比兜底", g["content"] == (11, 68, 1642, 1087),
      str(g["content"]))
g = WG.choose(WIN, OFF, CLI, rowmax=np.full(1065, 200.0),
              colmax=np.full(1631, 200.0), cfg_path=NOSUCH)
check("⑤ cfg 缺失 + 无黑边 -> 判为拉伸",
      g["content"] == (11, 45, 1642, 1110), str(g["content"]))

g = WG.choose(WIN, OFF, CLI, rect=(11, 68, 1642, 1087), cfg_path=NOSUCH)
check("⑥ rect= 直传优先于 rowmax/colmax",
      g["content"] == (11, 68, 1642, 1087) and g.get("cfg") == "cfg 缺失",
      str(g["content"]))

# 量测与模型不符时: 有 cfg 就信模型, 但要把差值写进 note
# (给一个宽高比仍=1.6、但缩放只有 4.5 的矩形 -> 与模型 5.0969 差 11.7%)
g = WG.choose(WIN, OFF, CLI, rect=(11, 68, 1451, 968), keep_aspect=True)
check("⑦ 量测 vs 模型相差过大 -> 按模型取值并在 note 里说明",
      g["content"] == (11, 68, 1642, 1087) and "相差" in (g.get("note") or ""),
      "%s | %s" % (g["content"], str(g.get("note"))[:70]))

print()
print("=" * 60)
print("第 10 节: read_keep_aspect (区分'读到'与'没读到')")
print("=" * 60)
v, src = WG.read_keep_aspect_ex(NOSUCH)
check("显式给不存在的 cfg -> (None, None)", v is None and src is None,
      str((v, src)))
check("read_keep_aspect 遇缺失仍返回 True (兼容旧行为)",
      WG.read_keep_aspect(NOSUCH) is True)

_tmp = os.path.join(tempfile.gettempdir(), "_wingeom_cfg_9527.cfg")
with open(_tmp, "w", encoding="utf-8") as f:
    f.write("# test\nKeepAspectRatio=0\nWindowWidth=640\n")
v, src = WG.read_keep_aspect_ex(_tmp)
check("读到 KeepAspectRatio=0 -> (False, 路径)", v is False and src == _tmp,
      str((v, src)))
with open(_tmp, "w", encoding="utf-8") as f:
    f.write("KeepAspectRatio=1\n")
check("读到 KeepAspectRatio=1 -> (True, 路径)", WG.read_keep_aspect_ex(_tmp)[0] is True)
with open(_tmp, "w", encoding="utf-8") as f:
    f.write("FullScreen=0\n")
v, _ = WG.read_keep_aspect_ex(_tmp)
check("文件存在但没有该键 -> (None, None)", v is None, str(v))
try:
    os.remove(_tmp)
except Exception:
    pass

# 完整描述串里应能看出几何是"模型"还是"像素"来的
g = WG.choose(WIN, OFF, CLI, keep_aspect=True)
check("describe 含来源标记", "来源=" in WG.describe(g), WG.describe(g)[:40])

print()
print("=" * 60)
print("PASS %d  FAIL %d" % (len(OK), len(BAD)))
if BAD:
    for b in BAD:
        print("  FAILED:", b)
print("=" * 60)
sys.exit(1 if BAD else 0)
