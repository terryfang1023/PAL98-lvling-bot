# -*- coding: utf-8 -*-
"""wingeom.py — 窗口几何自标定 (2026-09-30, v17.2)

问题
====
机器人"看图"的那部分逻辑里有一批**绝对像素常量**, 它们都是在
1687x1294 的窗口截图 (缩放 5.27x) 上标定出来的:

    追怪     屏幕动屑像素 -> K 坐标 (视口中心 + SCALE_X/SCALE_Y)
    视口裁剪 黑边 y 范围 (GAME_Y0/Y1)
    队伍框   视口中心 ±(100,110) 像素
    停机分类 亮度/diff 的绝对像素阈值 (2500 / 150000 / 8000 / 1500 / 300)

一旦窗口被手动缩放 (拖边框 / 改 sdlpal.cfg / 换显示器 / 换分辨率),
这些常量全部失效 —— 追怪会换算到错误的格子, diff 阈值会随像素总数漂移
(7% 像素的"战斗转场"在大窗口更容易触发, 小窗口永远触发不了)。

做法
====
把上述常量改写为**原生游戏坐标 (320x200)** 表达, 运行时按实测窗口几何换算:

    原生常量 = 标定窗口像素 / 标定缩放     (见下方 N_* 常量)
    运行时   = 视口原点 + 原生常量 * 实测缩放

关键不变量 (由 test_wingeom.py 逐项断言):
    **在标定窗口下, 换算结果必须与旧的硬编码值逐位相同**,
    否则等于重新调参 —— 那是另一种形式的 bug。

标定几何的依据 (实测/来源):
    - sdlpal.cfg: KeepAspectRatio=1 -> 等比缩放 + 居中留黑边
    - 旧常量 SCALE_X = 1687/320、SCALE_Y = (1189-139)/200、视口 y 139..1189
    - 旧常量 PATCH=(120,195,460,440)、BOX=(110,220,500,600)、TILE=(83,42)
"""

NATIVE = (320, 200)                       # 游戏原生分辨率

# ---- 标定基准 (全部来自旧硬编码常量, 不要随手改) ----
REF_WIN = (1687, 1294)                    # 标定时的窗口截图尺寸
REF_CONTENT = (0, 139, 1687, 1189)        # 视口矩形 (x0, y0, x1, y1), 窗口坐标
REF_SX = REF_WIN[0] / float(NATIVE[0])                      # 5.2719
REF_SY = (REF_CONTENT[3] - REF_CONTENT[1]) / float(NATIVE[1])   # 5.25
REF_AREA = REF_WIN[0] * REF_WIN[1]        # 2182738 像素

# ---- 原生坐标系常量 (标定像素 / 标定缩放) ----
# 全部相对视口左上角 (content_x0, content_y0)
N_TILE_X = 83 / REF_SX                    # 15.744  一格滚动 = ±83 窗口像素
N_TILE_Y = 42 / REF_SY                    # 8.0
N_PATCH = ((120 - REF_CONTENT[0]) / REF_SX, (195 - REF_CONTENT[1]) / REF_SY,
           (460 - REF_CONTENT[0]) / REF_SX, (440 - REF_CONTENT[1]) / REF_SY)
N_BOX = ((110 - REF_CONTENT[0]) / REF_SX, (220 - REF_CONTENT[1]) / REF_SY,
         (500 - REF_CONTENT[0]) / REF_SX, (600 - REF_CONTENT[1]) / REF_SY)
N_PARTY_DX = 100 / REF_SX                 # 队伍排除框半宽 (列)
N_PARTY_DY = 110 / REF_SY                 # 队伍排除框半高 (行)
N_FCAP = (600 / REF_SX, 660 / REF_SY)     # 快速抓帧尺寸
N_MASK_PAD = 4 / REF_SY                   # 黑边裁剪余量
N_BAND0 = (905 - REF_CONTENT[1]) / REF_SY  # is_battle 波段顶 (int(1294*0.70)=905)

# ---- 绝对像素阈值 -> 面积比例 ----
#   _diffpix 的返回值是"变化像素个数", 与截图面积成正比, 必须按比例比较
FRAC = {
    "anim_min": 300 / float(REF_AREA),        # mem2: 判定"画面在动"
    "menu_chg": 1500 / float(REF_AREA),       # mem3: esc 后画面变了 = 真菜单
    "wall": 2500 / float(REF_AREA),           # mem3/mem4: 画面完全静止 = 真墙
    "anim_big": 8000 / float(REF_AREA),       # mem4: 战斗攻击/施法动画
    "battle_trans": 150000 / float(REF_AREA),  # mem4: 全屏级变化 = 战斗转场
}


def _r(v):
    return int(round(v))


def _legit(win, content, scale):
    """几何是否可信: 窗口为正, 视口在窗口内, 缩放为正。"""
    w, h = win
    x0, y0, x1, y1 = content
    sx, sy = scale
    if w <= 0 or h <= 0 or sx <= 0 or sy <= 0:
        return False
    if x0 < 0 or y0 < 0 or x1 > w or y1 > h or x1 - x0 < NATIVE[0] // 4:
        return False
    if y1 - y0 < NATIVE[1] // 4:
        return False
    return True


def make(win, client_off, client_size, keep_aspect=True, src="auto"):
    """由窗口/客户区尺寸推出几何。失败退回 legacy()。

    win        (w, h)        窗口外框尺寸 (GetWindowRect, 含标题栏/边框)
    client_off (ox, oy)      客户区左上角相对窗口左上角的偏移 (ClientToScreen)
    client_size (cw, ch)     客户区尺寸 (GetClientRect)

    ★ 关键: 等比缩放时, "客户区比例 != 1.6" 是**常态**(上下留黑边), 所以不能
      拿 cw/320 与 ch/200 是否相等来判断是等比还是拉伸 —— 那正是旧实现的坑。
      等比 = 取 min() 的等比缩放, 居中; 拉伸 = 各轴独立缩放, 铺满客户区。
      到底是哪种由 sdlpal.cfg 的 KeepAspectRatio 决定 (见 keep_aspect 参数),
      再由截图黑边验证 (verify) 兜底。
    """
    try:
        w, h = int(win[0]), int(win[1])
        ox, oy = int(client_off[0]), int(client_off[1])
        cw, ch = int(client_size[0]), int(client_size[1])
    except Exception:
        return legacy()
    if w <= 0 or h <= 0 or cw <= 0 or ch <= 0:
        return legacy()
    if keep_aspect:
        s = min(cw / float(NATIVE[0]), ch / float(NATIVE[1]))
        cwp, chp = _r(NATIVE[0] * s), _r(NATIVE[1] * s)
        x0 = ox + (cw - cwp) // 2
        y0 = oy + (ch - chp) // 2
        content = (x0, y0, x0 + cwp, y0 + chp)
        scale = (s, s)
    else:
        # 拉伸: 客户区就是画面 (KeepAspectRatio=0)
        content = (ox, oy, ox + cw, oy + ch)
        scale = (cw / float(NATIVE[0]), ch / float(NATIVE[1]))
    if not _legit((w, h), content, scale):
        return legacy()
    return {"win": (w, h), "client": (ox, oy, cw, ch), "content": content,
            "scale": scale, "k": scale[0] / REF_SX, "src": src,
            "uniform": abs(scale[0] - scale[1]) <= 0.02 * max(scale)}


def detect_content_rect(rowmax, colmax, min_span=40):
    """从一帧的"逐行/逐列最大值"里量出真实画面矩形 (x0,y0,x1,y1)。

    ⚠️ 喂进来的剖面必须是**已裁掉标题栏/边框的客户区**:
       抓帧 (PrintWindow) 覆盖整个窗口外框, 标题栏一行很亮, 会让"逐列最大值"
       在**每一列**都变亮 -> 逐列完全量不出黑边 -> 误判成"画面铺满整宽"。
       裁到客户区再量就没这个问题 (见 pal_bot.Game.geom 与 tools/probe_geom.py)。

    判据不是"够黑", 而是"比画面暗一大截" (黑边可能是深灰, 不是纯黑):
        lo = 最小值 (黑边电平)   hi = 最大值 (画面电平)
        阈值 = lo + max(12, 25%*(hi-lo))
    行/列最大值高于阈值 = 画面内容。返回 None 表示量不出来 (整体同亮度/太小)。

    ★ lo 取 min 而不是 5% 分位: 黑边常常只占该轴的 2~4% (视口 1019/1065 行),
      5% 分位会落在**画面内容**上, 于是"lo≈内容电平" -> 阈值过高 -> 黑边判不出来,
      甚至两轴都判成"铺满"。min 只受"整行/整列全暗"影响, 对黑边是天然敏感的。
    """
    import numpy as np
    try:
        rr = np.asarray(rowmax, dtype=np.float64).ravel()
        cc = np.asarray(colmax, dtype=np.float64).ravel()
    except Exception:
        return None
    if rr.size < min_span or cc.size < min_span:
        return None

    def span(a):
        lo = float(a.min())
        hi = float(a.max())
        if hi - lo < 30:
            # 该轴整体同亮度 = 没有黑边 -> 铺满该轴
            return 0, int(a.size), True
        th = lo + max(12.0, 0.25 * (hi - lo))
        idx = np.nonzero(a > th)[0]
        if idx.size < 8:
            return 0, int(a.size), True
        return int(idx[0]), int(idx[-1]) + 1, False

    ys = span(rr)
    xs = span(cc)
    if ys[2] and xs[2]:
        return None            # 两轴都没有黑边 -> 分不清"铺满"还是"全黑"
    y0, y1 = ys[0], ys[1]
    x0, x1 = xs[0], xs[1]
    if (y1 - y0) < min_span or (x1 - x0) < min_span:
        return None
    return (x0, y0, x1, y1)


def bars_absent(rowmax, colmax):
    """两轴都"没有黑边" -> 画面铺满客户区 = **拉伸模式 (KeepAspectRatio=0)** 的特征。

    仅用于 cfg 读不到时判断"等比还是拉伸"。剖面同样必须是客户区裁剪过的,
    否则标题栏会把逐列剖面照成一片亮, 于是这个函数会误报"拉伸"。
    """
    import numpy as np
    for a in (rowmax, colmax):
        if a is None:
            return False
        try:
            arr = np.asarray(a, dtype=np.float64).ravel()
        except Exception:
            return False
        if arr.size < 16:
            return False
        if float(arr.max()) - float(arr.min()) >= 30:
            return False           # 该轴存在"整行/整列全暗" -> 有黑边, 不是拉伸
    return True


def from_rect(win, rect, src="pixel"):
    """按"实测画面矩形"构造几何; 比例不像 320:200 就判为量错, 返回 None。"""
    try:
        x0, y0, x1, y1 = [int(v) for v in rect]
    except Exception:
        return None
    sx = (x1 - x0) / float(NATIVE[0])
    sy = (y1 - y0) / float(NATIVE[1])
    if sx <= 0 or sy <= 0:
        return None
    if abs(sx - sy) > 0.06 * max(sx, sy):      # 不是 320:200 -> 量错了
        return None
    content = (x0, y0, x1, y1)
    if not _legit((int(win[0]), int(win[1])), content, (sx, sy)):
        return None
    return {"win": (int(win[0]), int(win[1])),
            "client": (x0, y0, x1 - x0, y1 - y0), "content": content,
            "scale": (sx, sy), "k": sx / REF_SX, "src": src,
            "uniform": abs(sx - sy) <= 0.02 * max(sx, sy)}


def legacy(note=""):
    """标定几何 (k=1): 检测失败时的兜底, 行为与旧版完全一致。"""
    return {"win": REF_WIN, "client": (0, 0, REF_WIN[0], REF_WIN[1]),
            "content": REF_CONTENT, "scale": (REF_SX, REF_SY), "k": 1.0,
            "src": "legacy", "uniform": True, "note": note}


def close_to_ref(geo, tol=0.01):
    """窗口与标定窗口基本一致 -> 直接用标定常量 (零回归通道)。

    旧常量是手工实测的, 与像素级理论值有 1~4px 的出入; 只要窗口没变,
    就应当继续用那套已经跑过 17 小时实测的常量, 而不是换成理论值。
    """
    if tuple(geo.get("win", ())) != REF_WIN:
        return False
    sx, sy = geo["scale"]
    return (abs(sx / REF_SX - 1.0) <= tol and abs(sy / REF_SY - 1.0) <= tol)


def is_legacy(geo):
    return abs(geo.get("k", 1.0) - 1.0) < 1e-9 and geo.get("src") == "legacy"


def _read_keep_aspect_impl(cfg_path=None):
    """(值, 来源路径)。找不到 -> (None, None)。"""
    import os
    if cfg_path:
        # 显式给了路径就只看它 (便于测试; 生产代码不传这个参数)
        cands = [cfg_path]
    else:
        here = os.path.dirname(os.path.abspath(__file__))
        cands = [os.path.join(d, "sdlpal.cfg")
                 for d in (here, os.path.dirname(here),
                           os.path.dirname(os.path.dirname(here)))]
    for p in cands:
        try:
            if not os.path.exists(p):
                continue
            with open(p, "r", encoding="utf-8", errors="ignore") as f:
                for ln in f:
                    if "=" in ln:
                        k, v = ln.split("=", 1)
                        if k.strip().lower() == "keepaspectratio":
                            return (v.strip() not in ("0", "false", "False")), p
        except Exception:
            continue
    return None, None


def read_keep_aspect(cfg_path=None):
    """从 sdlpal.cfg 读 KeepAspectRatio (找不到就按等比 True)。"""
    v, _ = _read_keep_aspect_impl(cfg_path)
    return True if v is None else bool(v)


def read_keep_aspect_ex(cfg_path=None):
    """同上, 但把"没读到"和"读到 True"区分开 -> (值或None, 来源路径)。"""
    return _read_keep_aspect_impl(cfg_path)


def _scale_agree(g1, g2, tol=0.06):
    s1, s2 = g1["scale"], g2["scale"]
    return (abs(s1[0] - s2[0]) <= tol * max(s1[0], s2[0])
            and abs(s1[1] - s2[1]) <= tol * max(s1[1], s2[1]))


def choose(win, client_off, client_size, rowmax=None, colmax=None,
           keep_aspect=None, cfg_path=None, rect=None, measure_off=(0, 0)):
    """完整判定, 优先级从高到低:

      ① 窗口尺寸 = 标定尺寸   -> 沿用标定常量 (零回归, 行为与旧版逐位相同)
      ② KeepAspectRatio 已知  -> 客户区模型推算 (精确解, 不依赖像素)
      ③ KeepAspectRatio 未知  -> 像素量测 (最直接的证据), 量不到再退回 ②(按等比)

    ★ 为什么 ② 优先于像素量测 (v17.2 血的教训):
      keep_aspect=1 时画面矩形是**纯几何确定的** —— 客户区里等比缩放 + 居中,
      没有歧义; 而像素量测要跟"场景本来就黑""标题栏很亮""黑边不是纯黑"搏斗,
      实测就在 1653x1121 窗口上量出了 (0,0,1653,1090) 这种把标题栏算进去的错矩形。
      所以有 cfg 背书时信模型, 像素量测只做交叉校验 (不符就在 note 里写清楚)。

    rowmax/colmax  客户区裁剪后的逐行/逐列最大值 (未裁剪会量错, 见 detect_content_rect)
    rect           直接给窗口坐标系的画面矩形 (优先于 rowmax/colmax)
    measure_off    把 rowmax/colmax 的坐标系换算到窗口坐标系的偏移 (= client_off)
    """
    ka, ka_src = read_keep_aspect_ex(cfg_path)
    if keep_aspect is not None:
        ka, ka_src = bool(keep_aspect), (ka_src or "调用方指定")

    # ① 标定尺寸 -> 零回归通道 (不截图, 不换算)
    if tuple(win) == tuple(REF_WIN):
        return legacy("窗口尺寸 = 标定尺寸 -> 沿用标定常量")

    # 像素量测 (可选)
    pix = None
    if rect is None and rowmax is not None and colmax is not None:
        r = detect_content_rect(rowmax, colmax)
        if r is not None:
            ox, oy = int(measure_off[0]), int(measure_off[1])
            rect = (r[0] + ox, r[1] + oy, r[2] + ox, r[3] + oy)
    if rect is not None:
        try:
            pix = from_rect(win, rect)
        except Exception:
            pix = None

    # ③ cfg 读不到 -> 靠"有没有黑边"判等比还是拉伸
    if ka is None:
        ka = not bars_absent(rowmax, colmax)
        src = "cfg 缺失 -> 按'有无黑边'判断为%s" % ("等比" if ka else "拉伸")
        if pix is not None:
            pix = dict(pix)
            pix["cfg"] = "cfg 缺失"
            pix["note"] = "cfg 缺失 -> 采用像素量测 (与'有无黑边'判断一致)"
            return pix
    else:
        src = "sdlpal.cfg KeepAspectRatio=%d" % int(ka)

    model = make(win, client_off, client_size, keep_aspect=ka)
    model["cfg"] = src
    if pix is not None:
        if _scale_agree(pix, model):
            model["note"] = "模型推算与像素量测一致"
        else:
            model["note"] = ("⚠️ 像素量测 %.3fx%.3f 与模型 %.3fx%.3f 相差 %.1f%% "
                             "-> 已按模型取值"
                             % (pix["scale"][0], pix["scale"][1],
                                model["scale"][0], model["scale"][1],
                                100 * abs(pix["scale"][0] / model["scale"][0] - 1.0)))
    return model


# ---------------- 换算 ----------------

def area(geo):
    w, h = geo["win"]
    return int(w) * int(h)


def nx(geo, v_native):
    """原生 x -> 窗口像素 x (绝对值: 含视口原点)"""
    return geo["content"][0] + _r(v_native * geo["scale"][0])


def ny(geo, v_native):
    """原生 y -> 窗口像素 y (绝对值: 含视口原点)"""
    return geo["content"][1] + _r(v_native * geo["scale"][1])


def dx_px(geo, v_native):
    """原生长度 -> 像素长度 (x 方向; 不含原点, 用于位移/尺寸)"""
    return _r(v_native * geo["scale"][0])


def dy_px(geo, v_native):
    """原生长度 -> 像素长度 (y 方向; 不含原点, 用于位移/尺寸)"""
    return _r(v_native * geo["scale"][1])


def rect_native(geo, box):
    return (nx(geo, box[0]), ny(geo, box[1]), nx(geo, box[2]), ny(geo, box[3]))


def patch(geo):
    return rect_native(geo, N_PATCH)


def box(geo):
    return rect_native(geo, N_BOX)


def fcap(geo):
    """快速抓帧尺寸 (长度, 不含原点): ★必须装得下 BOX + 一格位移。

    shot_fast 从**窗口左上角**抓 FCAPW×FCAPH, 而 BOX 是含视口原点的绝对坐标,
    窗口一缩放, 视口原点会往里缩 -> 余量被吃掉。这里把"装得下"写成构造条件,
    免得余量被悄悄吃光 (标定窗口下取值不变: 600x660)。
    """
    b = box(geo)
    w = max(64, dx_px(geo, N_FCAP[0]), b[2] + abs(dx_px(geo, N_TILE_X)) + 2)
    h = max(64, dy_px(geo, N_FCAP[1]), b[3] + abs(dy_px(geo, N_TILE_Y)) + 2)
    return int(w), int(h)


def tile_shift(geo, key):
    dx, dy = dx_px(geo, N_TILE_X), dy_px(geo, N_TILE_Y)
    return {"up": (-dx, dy), "down": (dx, -dy),
            "right": (-dx, -dy), "left": (dx, dy)}[key]


def content_cx(geo):
    x0, _, x1, _ = geo["content"]
    return x0 + (x1 - x0) // 2


def content_cy(geo):
    _, y0, _, y1 = geo["content"]
    return y0 + (y1 - y0) // 2


def band_top(geo):
    """is_battle 波段顶: 视口底部约 27% 起 (含视口下方黑边)。"""
    return ny(geo, N_BAND0)


def content_band(geo, pad=None):
    """(上界不含, 下界含) 用于挖掉视口外的黑边: mask[:y0] / mask[y1:] 置 False。"""
    p = N_MASK_PAD if pad is None else pad
    return ny(geo, -p), ny(geo, NATIVE[1] + p)


def party_box(geo):
    """队伍排除框: 视口中心 ±(dx, dy) 像素 (行, 列)。"""
    return _r(N_PARTY_DX * geo["scale"][0]), _r(N_PARTY_DY * geo["scale"][1])


def px_thr(frac, area_px):
    """面积比例 -> 像素个数阈值 (至少 1)。"""
    v = int(round(float(frac) * int(area_px)))
    return v if v > 0 else 1


def describe(geo):
    c = geo["content"]
    return ("窗口 %dx%d 视口 y%d..%d x%d..%d 缩放 %.3fx%.3fx "
            "(标定 %.3fx%.3fx, k=%.4f, 来源=%s%s%s)%s"
            % (geo["win"][0], geo["win"][1], c[1], c[3], c[0], c[2],
               geo["scale"][0], geo["scale"][1], REF_SX, REF_SY, geo["k"],
               geo["src"], ("/" + geo["cfg"]) if geo.get("cfg") else "",
               "" if geo["uniform"] else ", 拉伸(非等比)",
               (" | " + geo["note"]) if geo.get("note") else ""))
