# -*- coding: utf-8 -*-
"""
仙剑奇侠传98 (SDLPAL) 自动练级机器人 v3.1
==========================================
用法:
    python pal_bot.py                    # 无限练级（小范围巡逻）
    python pal_bot.py 20                 # 打 20 场后停止
    python pal_bot.py 99 left 60         # 往左导航最多60秒（遇敌自动用F打）

v3.1 变更:
    - 战斗中一路按 F = 最强法术; MP不足游戏会自动改用普攻（游戏内置行为，用户确认），
      无需任何检测回退逻辑
    - 支持导航模式: 指定单一方向 + 时限, 用于把队伍带回黑水镇

原理:
    - PostMessage 后台按键注入 + PrintWindow 后台画面识别, 游戏无需在前台
    - 战斗中: 按 F 放最强法术
    - 地图上: 巡逻走动触发随机遇敌（怪物"刷新" = 继续走动即可再遇敌）
    - 防抖: 连续多次确认非战斗画面才算脱战

注意:
    - 挂机前请先手动存档
    - Ctrl+C 停止；日志写入 bot_log.txt
"""
import sys
import os
import time
import datetime
import ctypes
import collections

# 关键: 必须声明 DPI 感知, 否则截图被系统缩放(如150%)导致小字特征(HP/MP青字)丢失, 战斗误判
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

import numpy as np
import psutil
import win32api
import win32gui
import win32ui
from PIL import Image
from shifttrack import match_shift, ncc_two, ncc_map

TITLE_KEY = "Pal Win95"
WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
KEYS = {
    "right": (0x27, 0x4D, True), "left": (0x25, 0x4B, True),
    "up": (0x26, 0x48, True), "down": (0x28, 0x50, True),
    "space": (0x20, 0x39, False), "esc": (0x1B, 0x01, False),
    "f": (0x46, 0x21, False),
    # SDLPAL 默认战斗热键 (input.c): A=自动战斗 D=防御 F=重复 Q=逃跑 W=使用物品 E=掷物品
    "a": (0x41, 0x1E, False), "d": (0x44, 0x20, False),
    "q": (0x51, 0x10, False), "w": (0x57, 0x11, False),
    "e": (0x45, 0x12, False),
    "enter": (0x0D, 0x1C, False),
}

# ---- 格子制移动参数 (实测标定: 窗口1687x1294, 缩放5.27x) ----
# 短按 0.09s = 精确走 1 格; 一格场景滚动 = (±83, ±42) 像素
STEP_TAP = 0.09
TILE_DX, TILE_DY = 83, 42
TILE_MAG = (TILE_DX * TILE_DX + TILE_DY * TILE_DY) ** 0.5  # ≈93
# 位移测量模板区: 画面左侧砖墙+地面 (避开中央队伍/黑边/UI)
# 约束: 滚动一格(±83,±42)后模板仍须完全落在画面内容区内(y>=135, 不含黑边)
PATCH = (120, 195, 460, 440)  # x0, y0, x1, y1
SEARCH_MARGIN = 260


def local_match(a0, a1):
    """测量 a0->a1 的场景滚动位移(像素), 返回 (dx, dy, score)"""
    px0, py0, px1, py1 = PATCH
    patch = a0[py0:py1, px0:px1]
    m = SEARCH_MARGIN
    y0 = max(0, py0 - m); x0 = max(0, px0 - m)
    y1 = min(a1.shape[0], py1 + m); x1 = min(a1.shape[1], px1 + m)
    crop = a1[y0:y1, x0:x1]
    x, y, sc = match_shift(crop, patch)
    return (x + x0 - px0, y + y0 - py0, sc)


def find_game_window():
    found = []

    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            if TITLE_KEY.lower() in win32gui.GetWindowText(hwnd).lower():
                found.append(hwnd)

    win32gui.EnumWindows(cb, None)
    return found


class Game:
    def __init__(self, hwnd):
        self.hwnd = hwnd

    def flush_keys(self):
        """冲洗所有方向键的keyup, 清除游戏内可能卡死的方向标志"""
        for name in ["up", "down", "left", "right", "space", "esc", "f"]:
            vk, sc, ext = KEYS[name]
            kup = 1 | (sc << 16) | ((1 << 24) if ext else 0) | (1 << 30) | (1 << 31)
            win32api.PostMessage(self.hwnd, WM_KEYUP, vk, kup)
        time.sleep(0.05)

    def key(self, name, dur=0.07):
        vk, sc, ext = KEYS[name]
        lp = 1 | (sc << 16) | ((1 << 24) if ext else 0)
        kup = lp | (1 << 30) | (1 << 31)
        # 防丢键三保险: 先补keyup(清卡键) -> keydown -> keyup -> 再补keyup
        win32api.PostMessage(self.hwnd, WM_KEYUP, vk, kup)
        time.sleep(0.03)
        win32api.PostMessage(self.hwnd, WM_KEYDOWN, vk, lp)
        time.sleep(dur)
        win32api.PostMessage(self.hwnd, WM_KEYUP, vk, kup)
        time.sleep(0.03)
        win32api.PostMessage(self.hwnd, WM_KEYUP, vk, kup)

    def hold(self, name, dur):
        vk, sc, ext = KEYS[name]
        lp = 1 | (sc << 16) | ((1 << 24) if ext else 0)
        win32api.PostMessage(self.hwnd, WM_KEYDOWN, vk, lp)
        time.sleep(dur)
        win32api.PostMessage(self.hwnd, WM_KEYUP, vk, lp | (1 << 30) | (1 << 31))

    def tap(self, name, dur=0.09):
        """极简点按(热路径专用): 先补 keyup 再立刻 keydown, 不做额外等待。
        实测每格省 0.06 秒(全流程 0.27s -> 0.21s), 且连续 10 格零丢键。"""
        vk, sc, ext = KEYS[name]
        lp = 1 | (sc << 16) | ((1 << 24) if ext else 0)
        kup = lp | (1 << 30) | (1 << 31)
        win32api.PostMessage(self.hwnd, WM_KEYUP, vk, kup)
        win32api.PostMessage(self.hwnd, WM_KEYDOWN, vk, lp)
        time.sleep(dur)
        win32api.PostMessage(self.hwnd, WM_KEYUP, vk, kup)
        win32api.PostMessage(self.hwnd, WM_KEYUP, vk, kup)

    def shot(self):
        l, t, r, b = win32gui.GetWindowRect(self.hwnd)
        w, h = r - l, b - t
        if w <= 0 or h <= 0:
            return np.zeros((100, 100, 3), dtype=np.int16)
        hwndDC = win32gui.GetWindowDC(self.hwnd)
        mfcDC = win32ui.CreateDCFromHandle(hwndDC)
        saveDC = mfcDC.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(mfcDC, w, h)
        saveDC.SelectObject(bmp)
        ctypes.windll.user32.PrintWindow(self.hwnd, saveDC.GetSafeHdc(), 2)
        info = bmp.GetInfo()
        data = bmp.GetBitmapBits(True)
        img = Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]), data, "raw", "BGRX", 0, 1)
        win32gui.DeleteObject(bmp.GetHandle())
        saveDC.DeleteDC()
        mfcDC.DeleteDC()
        win32gui.ReleaseDC(self.hwnd, hwndDC)
        return np.asarray(img, dtype=np.int16)

    # ---- 快速抓帧: 只抓窗口左上 FCAPW×FCAPH ----
    # 实测(本机 1687x1294 窗口, bench_cap.py):
    #   PrintWindow 的耗时几乎只跟"窗口"有关, 跟位图尺寸无关:
    #     900x700 -> 33.1ms, 700x700 -> 33.1ms, 590x650 -> 32.7ms, 500x610 -> 32.3ms
    #   所以缩位图省不到 PrintWindow 的钱; 但**灰度换算**是按像素走的, 900x700 要 21.6ms。
    #   因此把位图缩到刚好装下 BOX + 1 格位移(600x660), 灰度换算降到 ~13ms。
    #   已核对: 600x660 里 BOX(110,220,500,600) 的 4 个候选位置全部有效,
    #   判定行为与 900x700 完全一致(像素逐点一致, 最大差 0)。
    FCAPW, FCAPH = 600, 660

    def _fast_init(self):
        self._f_dc = win32gui.GetWindowDC(self.hwnd)
        self._f_mfc = win32ui.CreateDCFromHandle(self._f_dc)
        self._f_save = self._f_mfc.CreateCompatibleDC()
        self._f_bmp = win32ui.CreateBitmap()
        self._f_bmp.CreateCompatibleBitmap(self._f_mfc, self.FCAPW, self.FCAPH)
        self._f_save.SelectObject(self._f_bmp)

    def shot_fast(self):
        """左上 FCAPW×FCAPH 的灰度图 float32 (PrintWindow 33ms + 灰度 13ms ≈ 46ms)"""
        if getattr(self, "_f_bmp", None) is None:
            self._fast_init()
        ctypes.windll.user32.PrintWindow(self.hwnd, self._f_save.GetSafeHdc(), 2)
        raw = self._f_bmp.GetBitmapBits(True)
        a = np.frombuffer(raw, dtype=np.uint8).reshape(self.FCAPH, self.FCAPW, 4)
        f = a.astype(np.float32)                 # 通道序 BGRA
        return f[:, :, 0] * 0.114 + f[:, :, 1] * 0.587 + f[:, :, 2] * 0.299


def frame_diff(a0, a1):
    if a0.shape != a1.shape:
        return 1.0
    return float((np.abs(a0 - a1).max(axis=2) > 12).mean())


def is_battle(a):
    h, w = a.shape[:2]
    band = a[int(h * 0.70):, :, :]
    R, G, B = band[..., 0], band[..., 1], band[..., 2]
    box = (R >= 38) & (R <= 95) & (G >= 8) & (G <= 60) & (B >= 0) & (B <= 40) & (R > G + 15) & (G >= B)
    teal = (G >= 130) & (G > R + 55) & (B >= 100) & (B <= 190) & (G > B + 15)
    return float(box.mean()) > 0.05 and float(teal.mean()) > 0.0015


def menu_warm(a):
    h, w = a.shape[:2]
    reg = a[int(h * 0.15):int(h * 0.70), int(w * 0.02):int(w * 0.26), :]
    R, G, B = reg[..., 0], reg[..., 1], reg[..., 2]
    return float(((R > 120) & (G > 90) & (R > G + 15) & (G > B + 10)).mean())


def is_black(a):
    return float((a.sum(axis=2) < 45).mean()) > 0.95


def town_feat(a):
    """镇内特征: 画面中屋顶瓦片(灰棕横纹)占比。镇内~0.015, 野外~0.002"""
    R, G, B = a[..., 0], a[..., 1], a[..., 2]
    roof = (R >= 90) & (R <= 170) & (G >= 75) & (G <= 150) & (B >= 50) & (B <= 120) & (R > G) & (G > B)
    return float(roof.mean())

def olive_feat(a):
    """室外特征: 橄榄绿地面占比。室外 0.32~0.63, 室内(木地板) ~0.14"""
    R, G, B = a[..., 0], a[..., 1], a[..., 2]
    olive = (G >= R - 8) & (G > B + 12) & (G >= 20)
    return float(olive.mean())

def dungeon_feat(a):
    """迷宫特征: 灰蓝砖墙 + 暗棕地面占比 (鬼阴坛类内部场景)"""
    R, G, B = a[..., 0], a[..., 1], a[..., 2]
    wall = (np.abs(R - G) <= 30) & (B >= 40) & (B <= 130) & (R >= 40) & (R <= 140) & (B >= R)
    floor = (R >= 40) & (R <= 130) & (G >= 30) & (G <= 110) & (B <= 90) & (R >= G) & (G > B)
    return float(wall.mean()), float(floor.mean())

def zone_sig(a):
    """区域结构指纹: 8x8网格边缘密度 + 梯度方向直方图 + 6类颜色占比 (去均值余弦)"""
    gray = a.mean(axis=2)
    gx = np.zeros(gray.shape); gy = np.zeros(gray.shape)
    gx[:, :-1] = gray[:, 1:] - gray[:, :-1]
    gy[:-1, :] = gray[1:, :] - gray[:-1, :]
    mag = np.sqrt(gx * gx + gy * gy)
    H, W = mag.shape
    feat = []
    for j in range(8):
        for i in range(8):
            cell = mag[j * H // 8:(j + 1) * H // 8, i * W // 8:(i + 1) * W // 8]
            feat.append(float(cell.mean()))
    m = mag[:-1, :-1]
    gxc = gx[:-1, :-1]
    gyc = gy[:-1, :-1]
    an = np.arctan2(gyc, gxc)
    msk = m > 12
    if msk.sum() > 100:
        hist, _ = np.histogram(an[msk], bins=8, range=(-np.pi, np.pi))
        feat += [float(x) / (msk.sum() + 1) for x in hist]
    else:
        feat += [0.0] * 8
    R, G, B = a[..., 0], a[..., 1], a[..., 2]
    classes = [
        (R >= 180) & (G >= 60) & (G <= 170) & (B <= 90),              # 火把橙
        (np.abs(R - G) <= 30) & (B >= 40) & (B <= 130) & (B >= R - 20) & (R >= 40) & (R <= 140),  # 灰蓝墙
        (R >= 40) & (R <= 130) & (G >= 30) & (G <= 110) & (B <= 90) & (R >= G) & (G > B),         # 暗棕地
        a.sum(axis=2) < 60,                                            # 纯黑
        (G >= 90) & (G > R + 20) & (G > B + 30),                       # 绿地
        (R >= 150) & (G >= 150) & (B >= 150),                          # 亮白
    ]
    feat += [float(c.mean()) for c in classes]
    v = np.asarray(feat, dtype=np.float32)
    v = v - v.mean()  # 去均值, 突出结构差异
    n = np.linalg.norm(v)
    return v / (n + 1e-6)

def sig_sim(s1, s2):
    return float(np.dot(s1, s2))


# ---- 路线模式 (用户标注的鬼阴坛刷怪路线的蛇形近似) ----
# 按键->地图方向 (用户确认+实证): up=地图右上, down=地图左下, left=地图左上, right=地图右下
# 每段 = (按键, 格数); 格子制移动: 每格短按+验证, 撞墙精确感知即提前结束
ROUTE_LEGS = [
    ("up", 18),     # 地图右上: 深入上部
    ("left", 18),   # 地图左上: 横穿顶部
    ("down", 8),    # 地图左下: 换行
    ("right", 18),  # 地图右下: 横穿回来
    ("down", 8),    # 换行
    ("left", 18),   # 横穿
    ("down", 6),    # 换行 (浅进, 远离底部入口)
    ("right", 18),  # 横穿
    # ---- 折返, 蛇形回程 ----
    ("up", 8),
    ("right", 18),
    ("up", 8),
    ("left", 18),
    ("up", 14),
    ("right", 18),
]


class PalBot:
    # 连续这么多帧没有战斗UI才认为脱战（防敌人回合动画误判）
    EXIT_BATTLE_CONFIRM = 4

    def __init__(self, max_battles=None, patrol=None, max_seconds=None, stall_limit=600):
        hwnds = find_game_window()
        if not hwnds:
            print("[错误] 找不到游戏窗口")
            sys.exit(1)
        self.game = Game(hwnds[0])
        self.max_battles = max_battles
        # 巡逻模式: None=小范围四向循环; "left"/"right"/"up"/"down"=单向导航
        self.patrol = patrol
        self.max_seconds = max_seconds
        self.start_time = time.time()
        self.battles_won = 0
        self.walk_idx = 0
        self.pat = [patrol] if patrol else ["right", "right", "down", "down", "left", "left", "up", "up"]
        self.log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_log.txt")
        self.not_battle_count = 999  # 初始视为在地图
        self.was_battle = False
        self.last_log_state = ""
        self.map_stable_since = time.time()
        self.block_count = 0  # 连续被挡计数
        self.last_anchor_check = time.time()
        self.away_count = 0  # 连续"不在镇内"检测次数
        self.indoor_steps = 0  # 室内脱出步数计数
        self.indoor_count = 0  # 连续室内确认计数
        # 路线模式: patrol=="route" -> 按 ROUTE_LEGS 蛇形走位
        self.route_mode = (patrol == "route")
        self.route_idx = 0      # 当前路段索引
        self.route_cycle = 0    # 完整路线圈数
        self.leg_blocked = 0    # 当前路段连续被挡计数
        self.leg_started = time.time()
        self.leg_moved = 0        # 当前路段实际移动的步数
        self.leg_progress = []    # 最近路段的移动步数, 用于受困检测
        # "房间内打转"检测: 按圈统计移动量, 连续低移动圈 = 在同一房间兜圈子
        self.cycle_moves = 0      # 本圈累计移动步数
        self.cycle_stats = []     # 最近几圈的 (移动步数, 结束指纹)
        self.pocket_escapes = 0   # 房间脱出累计次数(战斗胜利清零), 连续太多次=环境异常
        # 卡死保护: 这么久没有任何战斗胜利 -> 判定异常, 保存现场并停止
        self.stall_limit = stall_limit
        self.last_win_time = time.time()
        self.escapes = 0  # 强脱困累计次数(胜利后清零), 太多说明环境异常
        # 场景自动识别: 迷宫(如鬼阴坛)模式=关闭室内停机/强脱困, 用区域指纹守护
        a0 = self.game.shot()
        wall, floor = dungeon_feat(a0)
        self.dungeon = olive_feat(a0) < 0.18 and wall > 0.03
        # 地图规划模式 (patrol=="map"): 按网格路线图执行
        self.map_mode = (patrol == "map")
        # 墙反馈蛇形覆盖模式 (patrol=="mow"): 免计步, 用真实墙体当路标
        self.mow_mode = (patrol == "mow")
        if self.map_mode:
            self.dungeon = True
            self._load_map_plan()
        if self.mow_mode:
            self.dungeon = True
            self.mow_dir = "left"
            self.mow_wall = 0   # 连续高墙占比计数(防误入入口窄道)
        # 精确格子 DFS 全覆盖 (patrol=="dfs"): 实测 0.09s短按=1格, 往返零漂移
        self.dfs_mode = (patrol == "dfs")
        if self.dfs_mode:
            self.dungeon = True
            self._dfs_reset()
            self._dfs_sweeps = 0
            self.sealed_portals = 0   # 已封堵的出口(会切换地图的格子)数
            self.portal_drift = 0     # 连续跨场景且无法回退的次数
            self.portal_probe = False  # 已废弃: 有了"门格禁入"就不需要穿门探怪了
            self.portal_passthrough = False  # 死胡同解禁后, 下一次换图按"穿门通过"处理
            self._last_a0 = None
            self._last_a1 = None
            self._last_fast = None
            self._last_fast_before = None
            self._last_full = None
            self._last_full_check = 0.0   # 上次全屏状态复核时间
            self._sweep_t0 = time.time()
            self.door_tiles = set()       # 会切换地图的格子(永久禁入)
            self.door_refusals = 0        # 因门禁而放弃的踏步次数(验证用)
            self._last_door_log = 0.0     # 门禁日志节流
            self._last_beat = time.time() # 心跳日志节流
        # 精确地图巡逻 (patrol=="gyt"): 地图解码工程产出的真实迷宫结构+精确门禁
        self.gyt_mode = (patrol == "gyt")
        if self.gyt_mode:
            self.dungeon = True
            self._gyt_init()
        if self.dungeon:
            self.anchor_sig = zone_sig(a0)
            self.anchor_shot = a0.copy()   # 起始房间参考帧(像素级判据, 替代无效指纹)
            self.dungeon_sim_thr = 0.80
            self.near_start = True
            self.save_shot("dungeon_anchor.png")
            self.log("[场景] 识别为迷宫模式 (wall=%.3f floor=%.3f), 起始房间已记录" % (wall, floor))
        else:
            self.anchor_sig = None
            self.anchor_shot = None
            self.log("[场景] 普通模式 (olive=%.3f wall=%.3f)" % (olive_feat(a0), wall))

    def save_shot(self, name):
        """保存现场截图(异常停机时用于诊断)"""
        try:
            arr = self.game.shot().astype("uint8")
            Image.fromarray(arr).save(os.path.join(os.path.dirname(os.path.abspath(__file__)), name))
            self.log("已保存现场截图: %s" % name)
        except Exception as e:
            self.log("现场截图失败: %s" % e)

    # ================= 墙反馈蛇形覆盖模式 (patrol=="mow") =================
    def _burst(self, g, d, secs):
        """按住方向走一小段, 返回 (moved/blocked/battle/outdoor, a1)"""
        a0 = g.shot()
        patch = a0[PATCH[1]:PATCH[3], PATCH[0]:PATCH[2]]
        g.flush_keys()
        g.key(d, 0.09)
        # 连续短按凑满时长(比单次长按可靠: 每次都有完整down/up对)
        t_end = time.time() + secs
        while time.time() < t_end:
            g.key(d, 0.09)
            time.sleep(0.05)
        time.sleep(0.5)
        a1 = g.shot()
        if is_battle(a1) or is_black(a1):
            return "battle", a1
        if olive_feat(a1) > 0.26:
            return "outdoor", a1
        # 纯世界滚动判定: 一次FFT算全NCC图, 检查0格与1~5格偏移假设。
        # 队伍/NPC精灵运动不影响patch(不含精灵), 火把在patch外。
        nm = ncc_map(a1, patch)
        ex, ey = self.EXPECTED[d]
        h, w = nm.shape
        s_zero = nm[PATCH[1], PATCH[0]] if (PATCH[1] < h and PATCH[0] < w) else 0.0
        if s_zero > 0.93:
            return "blocked", a1   # 世界纹丝不动 = 撞墙
        best = 0.0
        for k in range(1, 6):
            yy, xx = PATCH[1] + k * ey, PATCH[0] + k * ex
            if 0 <= yy < h and 0 <= xx < w:
                best = max(best, float(nm[yy, xx]))
        if best > 0.7:
            self.map_stable_since = time.time()
            return "moved", a1
        return "blocked", a1

    def mow_tick(self, g):
        """墙反馈蛇形覆盖: 沿行进方向走到撞墙, 向下换行, 反向再扫。
        免计步/免定位, 墙体就是路标; 兼容mask路线的蛇形覆盖意图。"""
        # 窄道护栏: 墙占比持续过高 = 疑似滑入入口走廊
        a = g.shot()
        wl, _fl = dungeon_feat(a)
        if wl > 0.28:
            self.mow_wall += 1
            if self.mow_wall >= 8:
                self.log("!!! 墙占比持续过高(%.2f) 疑似误入入口窄道 -> 停机" % wl)
                self.save_shot("narrow_%s.png" % datetime.datetime.now().strftime("%H%M%S"))
                self.log("!!! 机器人已停止 (本场累计胜利 %d)" % self.battles_won)
                raise SystemExit
        else:
            self.mow_wall = 0

        st, a1 = self._burst(g, self.mow_dir, 1.2)
        if st in ("battle", "outdoor"):
            return  # 交回主循环
        if st == "moved":
            self.mow_stuck = 0
            return
        self.mow_stuck = getattr(self, "mow_stuck", 0) + 1
        # 撞墙: 换行
        self.log("[mow] 行进%s撞墙 (卡%d)" % (self.mow_dir, self.mow_stuck))
        self.mow_dir = "right" if self.mow_dir == "left" else "left"
        for tries in range(6):
            st2, a2 = self._burst(g, "down", 1.2)
            if st2 in ("battle", "outdoor"):
                return
            if st2 == "moved":
                self.mow_stuck = 0
                return  # 已换行, 下轮继续新行
            self.mow_stuck += 1
            # 向下也被挡: 沿新方向回走找下行口
            st3, _a3 = self._burst(g, self.mow_dir, 1.2)
            if st3 in ("battle", "outdoor"):
                return
            if st3 == "moved":
                self.mow_stuck = 0
                continue  # 回走成功后再试一次down
            self.mow_stuck += 1
            # 两面都堵: 换回旧方向找口
            self.mow_dir = "right" if self.mow_dir == "left" else "left"
        # 持续卡死: 被困口袋 -> 突围
        if self.mow_stuck >= 10:
            self.log("[mow] 持续卡死(%d), 执行受困突围..." % self.mow_stuck)
            self.breakout(g)
            self.mow_stuck = 0
            self.mow_dir = "right" if self.mow_dir == "left" else "left"

    # ================= 地图规划模式 (patrol=="map") =================
    PLAN_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gyt_plan.npy")
    DIST_CAP = 36   # 墙距测量/匹配上限(格)

    def _load_map_plan(self):
        d = np.load(self.PLAN_PATH, allow_pickle=True).item()
        self.waypoints = [tuple(p) for p in d["waypoints"]]
        self.region = set(map(tuple, d["region"]))
        self.pos = None          # 键盘坐标系位置 (u,v), 定位后有效
        self.wp_idx = 0
        self.map_cycle = 0
        self.loc_tries = 0
        self.disagree = 0        # mask与实际不符计数
        # 预计算每格四向墙距
        dc = {}
        cap = self.DIST_CAP
        for (iu, iv) in self.region:
            n = 0
            while n < cap and (iu + 1 + n, iv) in self.region:
                n += 1
            d_up = n
            n = 0
            while n < cap and (iu - 1 - n, iv) in self.region:
                n += 1
            d_dn = n
            n = 0
            while n < cap and (iu, iv + 1 + n) in self.region:
                n += 1
            d_rt = n
            n = 0
            while n < cap and (iu, iv - 1 - n) in self.region:
                n += 1
            d_lf = n
            dc[(iu, iv)] = (d_up, d_rt, d_dn, d_lf)
        self.dist_map = dc
        self.log("[地图] 计划加载: %d 路标, %d 巡逻格" % (len(self.waypoints), len(self.region)))

    def _bfs(self, start, goal):
        if start == goal:
            return []
        prev = {start: None}
        q = collections.deque([start])
        while q:
            p = q.popleft()
            for da, db in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                np_ = (p[0] + da, p[1] + db)
                if np_ in self.region and np_ not in prev:
                    prev[np_] = p
                    if np_ == goal:
                        path = []
                        c = np_
                        while prev[c] is not None:
                            path.append((c[0] - prev[c][0], c[1] - prev[c][1]))
                            c = prev[c]
                        return path[::-1]
                    q.append(np_)
        return None

    def _measure_signature(self, g):
        """四向墙距签名: 每向走到墙, 再原路走回(格数应一致), 保证4次测量都在P0。
        返回 (up, right, down, left) 或 None"""
        back = {"up": "down", "down": "up", "right": "left", "left": "right"}
        sig = []
        for d in ["up", "right", "down", "left"]:
            n = self.march(g, d, self.DIST_CAP)
            if n is None:
                return None
            sig.append(n)
            m = self.march(g, back[d], n)
            if m is None:
                return None
            if m != n:
                self.log("! 签名回走不齐 %s (%d/%d), 该点测量不可信" % (d, m, n))
                return None
        return tuple(sig)

    def _localize(self, g):
        """四向墙距签名匹配, 确定键盘坐标系位置"""
        self.log("[定位] 四向墙距测量 (各向最多 %d 格)..." % self.DIST_CAP)
        sig = self._measure_signature(g)
        if sig is None:
            return None
        cands = []
        for t, ds in self.dist_map.items():
            score = sum(abs(a - b) for a, b in zip(sig, ds))
            cands.append((score, t))
        cands.sort(key=lambda x: x[0])
        best_score = cands[0][0]
        top = [t for s, t in cands if s <= best_score + 1]
        self.log("[定位] 签名=%s 最佳分=%d 候选%d个: %s" % (sig, best_score, len(top), top[:3]))
        if len(top) == 1:
            return top[0]
        if len(top) > 1:
            # 消歧: 向right走n5再走回, 候选需满足 d_right(P0)=n5... 已含在签名里;
            # 改为 up+n5 后测 right: d_right(t+(n5,0)) 应等于本次测量
            n5 = self.march(g, "up", 3)
            if n5 is None:
                return None
            m5 = self.march(g, "right", self.DIST_CAP)
            back2 = self.march(g, "left", m5 or 0) if m5 is not None else None
            back3 = self.march(g, "down", n5)
            if m5 is None or back2 is None or back3 is None or back2 != m5 or back3 != n5:
                return None
            ver = []
            for t in top:
                t2 = (t[0] + n5, t[1])
                if t2 in self.dist_map:
                    s = abs(self.dist_map[t2][1] - m5)
                    ver.append((s, t))
            ver.sort(key=lambda x: x[0])
            if ver:
                best2 = [t for s, t in ver if s <= ver[0][0] + 1]
                self.log("[定位] 二次消歧后候选%d个" % len(best2))
                if len(best2) == 1:
                    return best2[0]
                top = best2
        return top[0]

    def _navigate_to(self, g, target):
        """按mask最短路走向目标格, 逐格验证。
        返回 ok/blocked/battle/black/outdoor/unknown"""
        path = self._bfs(self.pos, target)
        if path is None:
            return "nopath"
        dirs = {(1, 0): "up", (-1, 0): "down", (0, 1): "right", (0, -1): "left"}
        for (du, dv) in path:
            st, n = self.step_tile(g, dirs[(du, dv)])
            if st == "moved":
                self.pos = (self.pos[0] + n * du, self.pos[1] + n * dv)
                self.disagree = 0
                self.map_stable_since = time.time()
            elif st == "blocked":
                return "blocked"
            else:
                return st
        return "ok"

    def map_tick(self, g):
        """地图规划模式主逻辑"""
        if self.pos is None:
            self.loc_tries += 1
            self.pos = self._localize(g)
            if self.pos is None:
                if self.loc_tries >= 3:
                    self.log("!!! 3次定位失败 -> 停机, 请把队伍走到开阔处再试")
                    self.save_shot("locfail_%s.png" % datetime.datetime.now().strftime("%H%M%S"))
                    self.log("!!! 机器人已停止 (本场累计胜利 %d)" % self.battles_won)
                    raise SystemExit
                return
            self.loc_tries = 0
            self.log("[定位] 队伍位置: (u=%d, v=%d)" % self.pos)
            if self.pos not in self.region:
                # 定位在巡逻区外(如小房间): 就近走向巡逻区
                near = min(self.region, key=lambda t: abs(t[0] - self.pos[0]) + abs(t[1] - self.pos[1]))
                self.log("[定位] 在巡逻区外, 最近区格 %s, 前往..." % (near,))
                self._goto_tmp = near
        target = getattr(self, "_goto_tmp", None) or self.waypoints[self.wp_idx]
        r = self._navigate_to(g, target)
        if r == "ok":
            if getattr(self, "_goto_tmp", None):
                self.log("[地图] 已进入巡逻区")
                self._goto_tmp = None
            else:
                self.wp_idx += 1
                if self.wp_idx >= len(self.waypoints):
                    self.wp_idx = 0
                    self.map_cycle += 1
                    self.log("[地图] === 覆盖第 %d 圈完成 ===" % self.map_cycle)
                    self.save_shot("map_cycle_%d.png" % self.map_cycle)
        elif r == "blocked":
            self.disagree += 1
            self.log("! 路线受阻 (pos=%s, 累计不符%d)" % (self.pos, self.disagree))
            if self.disagree >= 6:
                # 偏差过大: 可能定位在mask误差区/NPC挡路 -> 突围后重新定位
                self.stuck_rounds = getattr(self, "stuck_rounds", 0) + 1
                if self.stuck_rounds >= 2:
                    self.log("! 反复受阻, 先受困突围再重新定位...")
                    self.breakout(g)
                    self.stuck_rounds = 0
                self.pos = None
                self.disagree = 0
        elif r in ("nopath",):
            self.log("! 无路可达目标 %s, 跳到下一个路标" % (target,))
            self.wp_idx = (self.wp_idx + 1) % len(self.waypoints)
        # battle/black/outdoor/unknown -> 交回主循环

    def log(self, msg):
        line = "%s %s" % (datetime.datetime.now().strftime("%H:%M:%S"), msg)
        print(line, flush=True)
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

    def state_name(self, a):
        if is_battle(a):
            return "battle"
        if menu_warm(a) > 0.15:
            return "menu"
        if is_black(a):
            return "black"
        return "map"

    def _silence_watchdog(self):
        """日志静默看门狗: 日志超过 120 秒无更新而进程仍在 = 主循环卡死在
        某个无日志的循环里持续按键(实测教训: 曾静默失控把队伍走出场景)。
        -> 立即松开全部按键并终止进程。"""
        while True:
            time.sleep(15)
            try:
                if time.time() - os.path.getmtime(self.log_path) > 120:
                    try:
                        self.game.flush_keys()
                    except Exception:
                        pass
                    os._exit(3)
            except Exception:
                pass

    def run(self):
        import threading
        threading.Thread(target=self._silence_watchdog, daemon=True).start()
        self.log("=== 机器人 v3.1 启动 (F=最强法术), 模式: %s, 目标: %s ===" % (
            self.patrol or "巡逻", self.max_battles or "无限"))
        while True:
            if not win32gui.IsWindow(self.game.hwnd):
                self.log("!!! 游戏窗口已关闭, 机器人退出")
                return
            if self.max_battles and self.battles_won >= self.max_battles:
                self.log("=== 已完成 %d 场, 停止 ===" % self.battles_won)
                return
            if self.max_seconds and time.time() - self.start_time > self.max_seconds:
                self.log("=== 导航时间到 (%ds), 停止, 共 %d 场 ===" % (self.max_seconds, self.battles_won))
                return
            # 卡死保护: 长时间零战斗胜利 = 可能卡在死角 -> 先强脱困, 失败才停机
            # (迷宫模式禁用强脱困: 乱走可能穿门切换地图, 直接停机更安全)
            if self.stall_limit and time.time() - self.last_win_time > self.stall_limit:
                if self.escapes < 4 and not self.dungeon:
                    self.log("! %d 秒无战果, 尝试强脱困 (%d/4)..." % (self.stall_limit, self.escapes + 1))
                    g = self.game
                    escaped = False
                    for d in ["right", "down", "left", "up"]:
                        prev = g.shot()
                        g.hold(d, 2.0)
                        time.sleep(0.4)
                        if frame_diff(prev, g.shot()) > 0.03:
                            g.hold(d, 1.5)  # 顺势多走一段离开死角
                            time.sleep(0.4)
                            escaped = True
                            break
                    if escaped:
                        self.log("! 强脱困成功, 再给它 3 分钟找怪; 若仍无战果则继续换向")
                        self.escapes += 1
                        self.last_win_time = time.time() + 180 - self.stall_limit  # 3 分钟后再判定
                        continue
                    self.escapes += 1
                self.log("!!! 已 %d 秒没有任何战斗胜利且脱困失败 -> 自动停止"
                         % self.stall_limit)
                self.save_shot("stuck_%s.png" % datetime.datetime.now().strftime("%H%M%S"))
                self.log("!!! 机器人已停止, 请查看现场截图 (本场累计胜利 %d)" % self.battles_won)
                return
            self.tick()
            # DFS 快速通道每格自己就有"轮询到就返回"的同步, 不需要再补固定等待。
            # 旧代码在这里无条件 sleep(0.25) = 每格白送 250ms, 是效率最大的一笔税。
            time.sleep(0.02 if (self.dfs_mode or self.gyt_mode) else 0.25)

    def indoor_escape(self):
        """室内脱出: Esc解锁 -> 各方向committed burst, 穿门(画面切换diff>0.5)后顺势继续。
        返回 True=已到室外; False=失败"""
        g = self.game
        # Esc 清理可能卡住的菜单/对话框
        g.key("esc"); time.sleep(0.5)
        g.key("esc"); time.sleep(0.6)
        for cycle in range(5):
            for d in ["down", "left", "right", "up"]:
                progress = 0
                for i in range(6):
                    prev = g.shot()
                    g.hold(d, 1.0)
                    time.sleep(0.25)
                    a = g.shot()
                    m = frame_diff(prev, a)
                    ol = olive_feat(a)
                    if is_battle(a):
                        return True  # 遇敌交回主循环
                    if ol > 0.26:
                        return True  # 到室外了
                    if m > 0.5:
                        # 画面切换 = 穿门, 顺势继续同方向
                        progress = 99
                        continue
                    if m > 0.02:
                        progress += 1
                    else:
                        break  # 被挡, 换方向
                if progress >= 2:
                    break  # 此方向有进展, 下一轮继续优先
        return olive_feat(g.shot()) > 0.26

    # 每键对应的"相机预期滚动"位移 (格子制已标定: 一格=(±83,±42))
    # up=角色右上 => 相机左下(-83,+42); 其余对称
    EXPECTED = {"up": (-TILE_DX, TILE_DY), "down": (TILE_DX, -TILE_DY),
                "right": (-TILE_DX, -TILE_DY), "left": (TILE_DX, TILE_DY)}

    def step_tile(self, g, d):
        """格子制走一步: 短按 -> 轮询判定。
        关键: 按键后领队可能等队员跟上才走(火车式移动), 所以不能只测一次:
        持续观察, "预期滚动"得分冒头=走格; 静止超过2.2秒=撞墙; 超时=unknown。
        返回 (status, tiles); status ∈ moved/blocked/battle/black/outdoor/unknown"""
        a0 = g.shot()
        patch = a0[PATCH[1]:PATCH[3], PATCH[0]:PATCH[2]]
        g.flush_keys()
        g.key(d, STEP_TAP)
        ex, ey = self.EXPECTED[d]
        p_exp = (PATCH[0] + ex, PATCH[1] + ey)
        p_zero = (PATCH[0], PATCH[1])
        t0 = time.time()
        last = (0.0, 0.0)
        retried = False
        while True:
            time.sleep(0.2)
            a1 = g.shot()
            if is_battle(a1):
                return ("battle", 0)
            if is_black(a1):
                return ("black", 0)
            if olive_feat(a1) > 0.26:
                return ("outdoor", 0)
            # 地板纹理逐格重复, 自由找峰会混叠; 只比较两个假设:
            # 假设1=画面按本键预期滚动一格(走格); 假设2=原地不动(撞墙/等待中)
            s_exp, s_zero = ncc_two(a1, patch, p_exp, p_zero)
            last = (s_exp, s_zero)
            el = time.time() - t0
            if os.environ.get("BOT_DBG") and el > 1.0:
                print("DBG %-5s t=%.1f s_exp=%.3f s_zero=%.3f" % (d, el, s_exp, s_zero), flush=True)
            if s_exp > 0.5 and s_exp - s_zero > 0.05:
                self.map_stable_since = time.time()   # 刷新看门狗
                return ("moved", 1)   # 0.09s短按=1格(已标定)
            if el > 2.2 and s_zero > 0.9:
                if not retried:
                    # 防吞键复验: 冲洗+重按一次, 确认真的撞墙
                    retried = True
                    g.flush_keys()
                    g.key(d, STEP_TAP)
                    t0 = time.time()
                    continue
                return ("blocked", 0)   # 复验仍纹丝不动 = 撞墙
            if el > 5.0:
                return ("unknown", 0)

    def march(self, g, d, max_tiles):
        """同方向连续走最多 max_tiles 格, 返回实走格数。
        返回 None = 遇敌/黑屏/室外画面(需交回主循环)"""
        pushed = 0
        retries = 0
        while pushed < max_tiles:
            st, n = self.step_tile(g, d)
            if st in ("battle", "black", "outdoor"):
                return None
            if st == "unknown":
                retries += 1
                if retries <= 2:
                    continue    # 低纹理/过渡: 重试本步
                break
            if st != "moved":
                break
            pushed += n
        return pushed

    def breakout(self, g):
        """受困脱出(格子制): 各方向试走, 找最能动的方向连续推进。
        返回 True=已遇敌/触发状态变化(交回主循环); False=尝试完毕"""
        self.log("! 受困脱出: 各方向试探突围...")
        for attempt in range(3):
            results = {}
            for d in ["up", "right", "down", "left"]:
                n = self.march(g, d, 2)
                if n is None:
                    return True
                results[d] = n
            d = max(results, key=results.get)
            if results[d] == 0:
                g.key("esc")  # 四面被挡: 解锁可能的卡滞
                time.sleep(0.6)
                continue
            self.log("! 脱困推进方向: %s (已走 %d 格)" % (d, results[d]))
            n = self.march(g, d, 5)
            if n is None:
                return True
            if n >= 3:
                return True
        return False

    def room_escape(self, g):
        """房间打转脱出 v3 (格子制): 主判据 = 真实行军距离(格数)。
        单方向连续推进 >= 5 格 = 确认离开原房间(房内任意点直行5格必撞墙);
        指纹只作辅证。直行不通时用拐角走法(两方向交替)绕转角找门。"""
        self.log("! 房间脱出(格子制): 开始持续行军...")
        for attempt in range(4):
            results = {}
            for d in ["up", "right", "down", "left"]:
                n = self.march(g, d, 3)
                if n is None:
                    return True
                results[d] = n
            order = sorted(results, key=results.get, reverse=True)
            d = order[0]
            if results[d] == 0:
                g.key("esc")  # 四面被挡: 解锁可能卡滞
                time.sleep(0.6)
                continue
            # 1) 持续直行: 最多 22 格
            n = self.march(g, d, 22 - results[d])
            if n is None:
                return True
            total = results[d] + (n or 0)
            if total >= 5:
                self.log("! 直行 %s 共 %d 格 -> 判定已离开原房间" % (d, total))
                return True
            # 2) 拐角走法: 主方向与次方向交替 (绕转角找门)
            for d2 in order[1:3]:
                if results[d2] == 0:
                    continue
                t = 0
                for _ in range(6):
                    n1 = self.march(g, d, 2)
                    if n1 is None:
                        return True
                    n2 = self.march(g, d2, 2)
                    if n2 is None:
                        return True
                    t += n1 + n2
                    if t >= 5:
                        self.log("! 拐角走法 %s+%s 共推进 %d 格 -> 判定已离开原房间" % (d, d2, t))
                        return True
                    if n1 == 0 and n2 == 0:
                        break
                self.log("! 拐角走法 %s+%s 推进 %d 格后受阻" % (d, d2, t))
            self.log("! 方向组合 %s 最多推进 %d 格 (<5), 换向重试 (%d/4)" % (d, total, attempt + 1))
        self.log("! 房间脱出 4 轮行军均未离开原区域")
        return False

    def route_step(self, g, a):
        """路线模式(格子制): 执行当前路段 = 同方向连走至多cap格, 撞墙(连续2格没动)即转下一段。
        战斗随时打断 -> 交回主循环, 战后从当前路段重头走(撞墙收敛保证容错)。"""
        key, cap = ROUTE_LEGS[self.route_idx % len(ROUTE_LEGS)]
        tiles = 0
        interrupt = None
        for _ in range(cap):
            st, n = self.step_tile(g, key)
            if st == "moved":
                tiles += n
                self.leg_blocked = 0
                self.map_stable_since = time.time()
            elif st == "blocked":
                self.leg_blocked += 1
                if self.leg_blocked >= 2:
                    break
            else:  # battle/black/outdoor/unknown -> 交回主循环
                interrupt = st
                break
        if interrupt:
            return
        end_kind = "撞墙" if self.leg_blocked >= 2 else "走满"
        self.log("[路线] 路段%d (%s=地图%s) %s结束, 移动%d格"
                 % (self.route_idx + 1, key, {"up": "右上", "down": "左下",
                    "left": "左上", "right": "右下"}[key], end_kind, tiles))
        self.save_shot("route_leg.png")
        # 受困检测: 最近8个路段每个都走不满2格 = 被围进死角口袋
        self.leg_progress.append(tiles)
        self.leg_progress = self.leg_progress[-8:]
        if len(self.leg_progress) >= 8 and all(m < 2 for m in self.leg_progress):
            self.log("! 最近 %d 个路段几乎原地不动 -> 判定被围进死角口袋" % len(self.leg_progress))
            ok = self.breakout(g)
            self.leg_progress = []
            self.route_idx = 0  # 位置已变, 从路线头重走
            self.leg_blocked = 0
            self.leg_moved = 0
            self.leg_started = time.time()
            if not ok:
                self.log("! 脱困尝试完毕, 继续路线 (若反复停机请人工归位)")
            return
        self.cycle_moves += tiles
        self.route_idx += 1
        self.leg_blocked = 0
        self.leg_moved = 0
        self.leg_started = time.time()
        if self.route_idx % len(ROUTE_LEGS) == 0:
            self.route_cycle += 1
            end_sig = zone_sig(g.shot())
            self.cycle_stats.append({"moves": self.cycle_moves, "sig": end_sig})
            self.cycle_stats = self.cycle_stats[-3:]
            self.log("[路线] === 第 %d 圈完成, 本圈移动 %d 格, 保存全程截图 ==="
                     % (self.route_cycle, self.cycle_moves))
            self.save_shot("route_cycle_%d.png" % self.route_cycle)
            # 某圈移动量恢复正常 = 已回到正常游走, 脱困计数清零
            if self.cycle_moves >= 30:
                self.pocket_escapes = 0
            self.cycle_moves = 0
            # 房间打转判定: 连续2圈移动量都很低 = 在同一个房间里兜圈子
            if len(self.cycle_stats) >= 2 and all(s["moves"] < 30 for s in self.cycle_stats[-2:]):
                sim = sig_sim(self.cycle_stats[-2]["sig"], self.cycle_stats[-1]["sig"])
                self.log("!!! 判定原地转圈: 连续 %d 圈移动量过低(末圈指纹相似度=%.3f) -> 房间脱出"
                         % (len(self.cycle_stats[-2:]), sim))
                ok = self.room_escape(g)
                self.pocket_escapes += 1
                self.cycle_stats = []
                self.route_idx = 0
                self.leg_blocked = 0
                self.leg_moved = 0
                self.leg_started = time.time()
                if not ok:
                    self.log("! 房间脱出未成功, 继续路线观察")
                if self.pocket_escapes >= 3:
                    self.log("!!! 连续 %d 次房间脱出仍未恢复正常游走 -> 停机, 请人工查看位置" % self.pocket_escapes)
                    self.save_shot("pocket_%s.png" % datetime.datetime.now().strftime("%H%M%S"))
                    self.log("!!! 机器人已停止 (本场累计胜利 %d)" % self.battles_won)
                    raise SystemExit

    # ================= 精确格子 DFS 全覆盖 (patrol=="dfs") =================
    # 实测基础(probe.py / walk.py):
    #   短按 0.09s = 恰好 1 格; 位移假设分 0.97 vs 静止假设分 0.33~0.52 => 判定极可靠
    #   四方向射线往返完全一致 (1/1, 14/14, 5/5, 5/5) => 死推算零漂移
    # 因此可以维护"局部坐标地图", 做真正的 DFS 全覆盖: 不会迷路, 不会漏格。
    DIRS = {"up": (1, 0), "down": (-1, 0), "right": (0, 1), "left": (0, -1)}
    OPP = {"up": "down", "down": "up", "left": "right", "right": "left"}
    TAP = 0.09
    SETTLE = 0.5
    STEP_MARGIN = 160   # 局部搜索窗边距(格位移83/42, 160足够且比260快一倍)

    # ===== 快速通道(实测标定) =====
    # 抓帧: 只抓左上 900x700 (39ms, 像素与全屏一致) —— 见 Game.shot_fast
    # 模板: 390x360 不含角色精灵、不含黑边; 位移 1 格后仍在抓帧范围内
    BOX = (110, 220, 500, 600)
    # 步进节奏: 点按 0.09s 后位移在 0.22s 内已精确到位(+83/+42 px, 匹配分 0.97);
    #           "按住不放/重复 KEYDOWN"实测完全不产生连续行走, 所以只能点按。
    MOVE_SCORE = 0.78   # 1 格假设命中阈值
    STILL_SCORE = 0.88  # 静止假设命中阈值
    # 实测位移在 0.22s 内精确到位, 所以 0.28s 足以判"被挡"(旧值 0.34 白等 60ms/格)
    STILL_AFTER = 0.28

    def _prep_box(self, gf, box=None):
        """从灰度抓帧里取 BOX 区, 做成零均值单位范数模板"""
        b = box or self.BOX
        p = gf[b[1]:b[3], b[0]:b[2]].astype(np.float32)
        p -= p.mean()
        n = float(np.sqrt((p * p).sum()))
        return p / (n + 1e-6)

    def _ncc_box(self, gf, tmpl, d=None, kmax=1):
        """模板在 gf 中若干候选位置的 NCC 分(直接算, 不用 FFT, 约 1ms/候选)。
        d 给定时: 沿该方向的 0..kmax 格; d=None 时: 原地 + 四方向 1..kmax 格。"""
        b = self.BOX
        h, w = tmpl.shape
        if d is None:
            cands = [(0, 0)]
            for k in range(1, kmax + 1):
                for dd in ("up", "down", "left", "right"):
                    ex, ey = self.EXPECTED[dd]
                    cands.append((k * ex, k * ey))
        else:
            ex, ey = self.EXPECTED[d]
            cands = [(k * ex, k * ey) for k in range(kmax + 1)]
        out = []
        for ox, oy in cands:
            px, py = b[0] + ox, b[1] + oy
            if px < 0 or py < 0 or px + w > gf.shape[1] or py + h > gf.shape[0]:
                out.append(-2.0)
                continue
            r = gf[py:py + h, px:px + w]
            r = r - float(r.mean())
            out.append(float((r * tmpl).sum() / (float(np.sqrt((r * r).sum())) + 1e-6)))
        return out

    def _gray_same(self, gref, gnow, thr=0.80):
        """两张灰度小图是否同一场景(以 gref 的 BOX 为模板在 gnow 里找)"""
        if gref is None or gnow is None:
            return False
        return max(self._ncc_box(gnow, self._prep_box(gref), None, 1)) > thr

    def _dfs_reset(self):
        self.dfs_pos = (0, 0)       # 局部坐标 (up步数, right步数)
        self.dfs_visited = {(0, 0)}
        self.dfs_blocked = set()    # {(pos, dir)} 已确认走不通的边
        self.dfs_path = []          # 走过的路径 [(上一格, 走向)...] 用于回溯
        self.dfs_last = None
        self.dfs_steps = 0
        self.door_tiles = set()     # 会切换地图的格子(相对本局部坐标, 永久禁入)
        self._prev_sweep_cov = 0    # 上一轮覆盖格数(区分"起点在死角"和"真被困")

    def _k_walkable(self, a, b):
        """K按键格 -> map213 blocked 网格判定 (script.c:83 锚点像素 + scene.c 分段)。"""
        px = 16 * (a + b) + 16
        py = 8 * (b - a) + 8
        x = px // 32; y = py // 16
        xr = px % 32; yr = py % 16
        s = xr + yr * 2
        h = 0
        if s >= 16:
            if s >= 48:
                x += 1; y += 1
            elif 32 - xr + yr * 2 < 16:
                x += 1
            elif 32 - xr + yr * 2 < 48:
                h = 1
            else:
                y += 1
        if not (0 <= y < 128 and 0 <= x < 64):
            return False
        return not self._gyt_blocked_grid[y, x, h]

    def _gyt_init(self):
        """GYT v7: 定位融于巡逻 (边走边定位)。

        历史教训: v4"站任意位置"但门禁按固定锚点算 -> 门禁错位踩真门;
        v5 要求站精确落点 -> 用户做不到, 又踩门逃逸。
        各层(215=地图212, 216=地图213, ...)渲染相似但墙型/门位不同, 视觉无法分层。

        v7 方案: 启动时把 [候选地图(场景200~240所用) x 全部可走起点K] 全部当作候选,
        DFS 每走一步就把"该格可走/该边阻挡"喂给匹配器过滤候选(真候选永远兼容);
        踩到门则是一次超强的定位观测(候选必须在=>对应位置有门)。
        候选<=300 时启用"合并门禁"(所有候选门位的并集=真门超集, 绝不漏堵);
        候选=1 时锁定该层的精确地图/门禁, 此后与 v5 相同。"""
        import re
        base = os.path.dirname(os.path.abspath(__file__))
        self._gyt_all_grids = np.load(os.path.join(base, "map_walk.npy"))
        txt = open(os.path.join(base, "gyt_scene_report.txt"),
                   encoding="utf-8", errors="ignore").read()
        self._scene_map = {}
        for m in re.finditer(r"场景\s*(\d+)\s+地图(\d+)", txt):
            self._scene_map[int(m.group(1))] = int(m.group(2))
        self._scene_doors = {}
        for m in re.finditer(r"场景\s*(\d+)\s+对象\s*(\d+)\s+px\(\s*(\d+),\s*(\d+)\)\s+"
                             r"mode=\d+\s+state=\d+\s+->\s+场景\[(\d+)", txt):
            self._scene_doors.setdefault(int(m.group(1)), []).append(
                (int(m.group(3)), int(m.group(4)), int(m.group(5)), int(m.group(2))))
        self._map_doors = {}
        for sc, doors in self._scene_doors.items():
            mp = self._scene_map.get(sc)
            if mp is None:
                continue
            s = self._map_doors.setdefault(mp, set())
            for (px, py, dest, obj) in doors:
                s.add(((px - 2 * py) // 32, (px + 2 * py - 32) // 32))
        maps = sorted({mp for sc, mp in self._scene_map.items() if 200 <= sc <= 240})
        self._gyt_maps = maps
        self._gyt_kwalk = {}
        for mp in maps:
            grid = self._gyt_all_grids[mp]
            s = set()
            for x in range(64):
                for y in range(128):
                    row = grid[y, x]
                    for h in (0, 1):
                        if not row[h]:
                            s.add((x - y, x + y - 1 + h))
            self._gyt_kwalk[mp] = s
        self._gyt_cands = [(mp, a0, b0) for mp, ks in self._gyt_kwalk.items()
                           for (a0, b0) in ks]
        self._gyt_G = {}                 # 局部格 -> {dir: 0/1} 观测图
        self._gyt_lock = False           # 是否已锁定唯一 (地图,锚点)
        self._gyt_guard_on = False       # 合并门禁是否已启用
        self._gyt_last_log_n = 10 ** 9
        self._gyt_blocked_grid = self._gyt_all_grids[213]
        self.gyt_walk = None             # 未锁定: 无解码区界 (墙靠物理, 门靠并集)
        self.gyt_region = None
        self._dfs_reset()
        self.door_tiles = set()
        self.seal_positions = []
        self._dfs_sweeps = 0
        self.sealed_portals = 0
        self.door_refusals = 0
        self._last_door_log = 0.0
        self._last_beat = time.time()
        self._sweep_t0 = time.time()
        self._last_full_check = 0.0
        self._last_fast = None
        self._last_fast_before = None
        self._last_full = None
        self._prev_sweep_cov = 0
        self.portal_passthrough = False  # GYT 永不解禁穿门, 此标志恒 False
        self.portal_drift = 0
        self.portal_probe = False
        self._last_a0 = None
        self._last_a1 = None
        self._dfs_pending_cells = None
        self._last_moved_t = time.time()      # 上次成功移动时刻 (GYT 卡死自救用)
        self._last_deadcorner_log = 0.0       # 死角日志节流
        self.log("[GYT] v8 边走边定位: 候选 %d 个起点/跨 %d 张地图, 门数据 %d 场景; "
                 "候选<=300 自动启用合并门禁(±1保护圈), =1 锁定精确门禁; "
                 "观测矛盾自动弃用, 绝不停机"
                 % (len(self._gyt_cands), len(maps), len(self._scene_doors)))

    def _gyt_relocalize(self, reason):
        """v11: 放弃当前锁定帧, 恢复全量候选重新边走边定位 (当前位置为原点)。
        每步都经过镜头里程计实测, dfs_pos 始终可信, 因此重新收敛是安全的;
        收敛前门禁为空, 靠物理护栏(transition退回+室外熔断+虚空护栏)兜底。"""
        self._gyt_lock = False
        self._gyt_guard_on = False
        self._gyt_cands = [(mp, a0, b0) for mp, ks in self._gyt_kwalk.items()
                           for (a0, b0) in ks]
        self._gyt_G = {}
        self._gyt_last_log_n = 10 ** 9
        self.gyt_walk = None
        self.gyt_region = None
        self._gyt_travel_on = False
        self._gyt_path = None
        self._gyt_plan_fail = 0
        self._gyt_plan_sig = None
        self._gyt_plan_block = set()
        self._dfs_reset()
        self.door_tiles = set()
        self.seal_positions = []
        self._prev_sweep_cov = 0
        self._sweep_t0 = time.time()
        self.log("[定位] %s -> 重新自定位: 候选%d个恢复全量, 边走边重收敛, "
                 "门禁并集随后自动重建" % (reason, len(self._gyt_cands)))

    # ---- GYT v7 边走边定位 ----
    def _kw(self, grid, a, b):
        """K按键格 -> 指定地图 blocked 网格可走判定 (scene.c 分段规则)。"""
        px = 16 * (a + b) + 16
        py = 8 * (b - a) + 8
        x = px // 32; y = py // 16
        xr = px % 32; yr = py % 16
        s = xr + yr * 2
        h = 0
        if s >= 16:
            if s >= 48:
                x += 1; y += 1
            elif 32 - xr + yr * 2 < 16:
                x += 1
            elif 32 - xr + yr * 2 < 48:
                h = 1
            else:
                y += 1
        if not (0 <= y < 128 and 0 <= x < 64):
            return False
        return not grid[y, x, h]

    def _gyt_filter_walk(self, la, lb, d, bit):
        """用一条边观测过滤候选: 格(la,lb)可走 且 d 邻格可走性 == bit。"""
        da, db = self.DIRS[d]
        keep = []
        for (m, a0, b0) in self._gyt_cands:
            ks = self._gyt_kwalk[m]
            if (a0 + la, b0 + lb) not in ks:
                continue
            if ((a0 + la + da, b0 + lb + db) in ks) != (bit == 1):
                continue
            keep.append((m, a0, b0))
        self._gyt_cands = keep

    def _gyt_match_obs(self, cand):
        """候选是否与全部已存边观测一致。"""
        m, a0, b0 = cand
        ks = self._gyt_kwalk[m]
        for (la, lb), sig in self._gyt_G.items():
            if (a0 + la, b0 + lb) not in ks:
                return False
            for d, bit in sig.items():
                da, db = self.DIRS[d]
                if ((a0 + la + da, b0 + lb + db) in ks) != (bit == 1):
                    return False
        return True

    def _gyt_after_filter(self, tag):
        """过滤后的统一收尾: 矛盾停机 / 唯一锁定 / 里程碑日志+合并门禁。"""
        n = len(self._gyt_cands)
        if n == 0:
            self.log("!!! [定位] 观测与全部候选矛盾(%s) -> 解码数据与实际不符, 安全停机" % tag)
            try:
                self.game.flush_keys()
            except Exception:
                pass
            raise SystemExit
        if n == 1:
            m, a0, b0 = self._gyt_cands[0]
            self.log("[定位] 收敛唯一: 地图%d 锚点K(%d,%d) -> 锁定精确门禁" % (m, a0, b0))
            self._gyt_finish(m, (a0, b0))
            return
        for th in (50000, 10000, 2000, 300, 60, 12):
            if n <= th and self._gyt_last_log_n > th:
                self.log("[定位] %s: 候选剩 %d (跨%d张图)" %
                         (tag, n, len({mm for (mm, _, _) in self._gyt_cands})))
                if th <= 300 and not self._gyt_guard_on:
                    self._gyt_guard_on = True
                    self._gyt_update_guard()
                    self.log("[定位] 已启用合并门禁: %d 格 (真门超集)" % len(self.door_tiles))
                elif self._gyt_guard_on:
                    self._gyt_update_guard()
                    self.log("[定位] 合并门禁收窄: %d 格" % len(self.door_tiles))
                break
        self._gyt_last_log_n = n

    def _gyt_observe(self, cell, d, bit):
        """记录一条边观测并过滤候选 (cell=局部格)。
        v8: 观测与数据矛盾时弃用该观测而非停机 (数据可能有误: NPC占格/
        2格步进的中间格等; 停机只会浪费一次带路)。"""
        if self._gyt_lock:
            return
        sig = self._gyt_G.setdefault(cell, {})
        if sig.get(d) == bit:
            return
        prev = self._gyt_cands
        sig[d] = bit
        self._gyt_filter_walk(cell[0], cell[1], d, bit)
        if not self._gyt_cands:
            del sig[d]
            self._gyt_cands = prev
            if time.time() - getattr(self, "_gyt_last_contra_log", 0) > 30:
                self._gyt_last_contra_log = time.time()
                self.log("[定位] 边观测(%s,%s,%d)与全部候选矛盾 -> 弃用该观测, 继续"
                         % (cell, d, bit))
            return
        self._gyt_after_filter("边观测")

    def _gyt_observe_door(self, cell):
        """踩到门(已退回): 最强定位观测。
        v8: 触发格可能偏离报告格 ±1~2 (2格步进中途触发/落点偏移/对象锚点差异),
        且此前的边观测可能有误 -> 门观测时从全空间重建候选(模糊±2匹配门), 自愈;
        仍无解则弃用本次观测(门已本地封堵, 安全不受影响), 绝不停机。"""
        if self._gyt_lock:
            return
        la, lb = cell
        pool = set()
        for m, doors in self._map_doors.items():
            if m not in self._gyt_kwalk:
                continue
            ks = self._gyt_kwalk[m]
            for (da, db) in doors:
                for ua in range(-2, 3):
                    for ub in range(-2, 3):
                        p = (da - la - ua, db - lb - ub)
                        if p in ks:
                            pool.add((m, p[0], p[1]))
        keep = [c for c in pool if self._gyt_match_obs(c)]
        if not keep:
            self.log("[定位] 门观测(模糊±2)无候选支撑 -> 弃用本次门观测(门已封堵), 继续")
            return
        self._gyt_cands = keep
        self._gyt_after_filter("门观测")

    def _gyt_update_guard(self):
        """合并所有候选的门位为临时门禁 (真门的超集, 宁可少走绝不踩门)。
        v8: 每个门格带 ±1 保护圈, 吸收触发格与报告格的偏差。"""
        forbidden = set()
        for (m, a0, b0) in self._gyt_cands:
            for (da_, db_) in self._map_doors.get(m, ()):
                for ua in (-1, 0, 1):
                    for ub in (-1, 0, 1):
                        forbidden.add((da_ - a0 + ua, db_ - b0 + ub))
        self.door_tiles = forbidden

    def _gyt_finish(self, m, K0):
        """定位锁定: 用该地图洪泛精确巡逻区, 门禁=该地图上所有场景的门。"""
        ks = self._gyt_kwalk[m]
        doorK = set(self._map_doors.get(m, ()))
        doorH = set()                     # v8: 门格 ±1 保护圈
        for (da, db) in doorK:
            for ua in (-1, 0, 1):
                for ub in (-1, 0, 1):
                    doorH.add((da + ua, db + ub))
        if K0 in doorH:
            self.log("!!! [定位] 队伍离门格过近(±1内) -> 安全停机, 请挪几步再启动")
            try:
                self.game.flush_keys()
            except Exception:
                pass
            raise SystemExit
        seen = {K0}
        q = collections.deque([K0])
        R = getattr(self, "GYT_PATROL_RADIUS", 10)   # v9: 半径限制的安全岛
        while q:
            a, b = q.popleft()
            if max(abs(a - K0[0]), abs(b - K0[1])) >= R:
                continue          # 安全岛边界: 不再向外扩展
            for da, db in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                p = (a + da, b + db)
                if p in ks and p not in doorH and p not in seen:
                    seen.add(p)
                    q.append(p)
        a0, b0 = K0
        self._gyt_blocked_grid = self._gyt_all_grids[m]
        self._dfs_reset()
        self.gyt_walk = {(a - a0, b - b0) for (a, b) in seen}
        self.gyt_region = self.gyt_walk
        self.door_tiles = {(a - a0, b - b0) for (a, b) in doorH}
        self._gyt_lock = True
        self.seal_positions = []          # v8.1: 旧局部坐标已作废(锁定重建坐标系),
                                          # 不清空会与旧帧封堵记录假性重合 -> 误触发漂移熔断
        self._gyt_lock_map = m            # v10: 记住锁定的地图号, 深入后重洪泛用
        self._gyt_lock_anchor = (a0, b0)  # v10: 锁定锚点(锁帧), 当前K = 锚点 + dfs_pos
        if not getattr(self, "_gyt_travel_done", False):
            # v10 用户路线: 锁定后先走到右上深区(解码图BFS规划), 别在入口原地刷
            self._gyt_travel_on = True
            self._gyt_path = None
            self._gyt_plan_block = set()
            self._gyt_plan_fail = 0
            self._gyt_plan_sig = None
            self.log("[路线] 深入模式: 规划往右上的路径再开始刷怪 (用户路线)")
        self._sweep_t0 = time.time()
        scenes = sorted(sc for sc, mp in self._scene_map.items() if mp == m)
        self.log("[GYT] 锁定生效: 地图%d 场景%s, 锚点K(%d,%d), 巡逻区%d格, 门禁%d格"
                 % (m, scenes, a0, b0, len(self.gyt_walk), len(self.door_tiles)))

    # ---- v10 深入模式 (用户路线: 沿解码图BFS路径走到右上深区, 再刷怪) ----
    def _probe_step(self, g, d):
        """走一步并用镜头里程计核实。返回 (state, k): moved/blocked/door, k=实测格数。"""
        st = self.step1(g, d)
        if st == "battle":
            self._fight_battle(g)
            st = self.step1(g, d)
        if st == "transition":
            return ("door", 0)
        if st != "moved":
            return ("blocked", 0)
        k = self._shift_cells(d)
        if k in (1, 2):
            return ("moved", k)
        return ("blocked", 0)               # k==0 假moved / -1 矛盾: 保守按没走

    def _gyt_plan_travel(self):
        """规划深入路径: 在整图可走域(门±1圈+实测受阻格除外)内从当前位置洪泛,
        取'右上最深'(b-a 最大)的可达格为目标, BFS 最短路。全部在锁帧内完成。"""
        m = self._gyt_lock_map
        ks = self._gyt_kwalk[m]
        a0, b0 = self._gyt_lock_anchor
        cur = (a0 + self.dfs_pos[0], b0 + self.dfs_pos[1])
        doorG = {(a0 + p[0], b0 + p[1]) for p in self.door_tiles}
        blockedG = getattr(self, "_gyt_plan_block", set())
        if cur not in ks or cur in doorG or cur in blockedG:
            # 帧漂移: 以当前实际位置为新的锁帧原点重建 (就地重置局部坐标)
            self._dfs_reset()
            self._gyt_lock_anchor = cur
            a0, b0 = cur
            self.door_tiles = set()          # 旧帧门禁作废, 由洪泛新帧重建
            self.log("[路线] 位置帧校准: 锚点平移到 K%s" % (cur,))
        seen = {cur}
        q = collections.deque([cur])
        far, best = cur, cur[1] - cur[0]
        while q:
            p = q.popleft()
            sc = p[1] - p[0]
            if sc > best:
                best, far = sc, p
            for da, db in ((-1, 0), (1, 0), (0, 1), (0, -1)):
                t = (p[0] + da, p[1] + db)
                if t in ks and t not in doorG and t not in blockedG and t not in seen:
                    seen.add(t)
                    q.append(t)
        if len(seen) <= 2 or far == cur:
            self.log("[路线] 可达域太小(%d格) -> 就地刷怪" % len(seen))
            return False
        prev = {cur: None}
        q = collections.deque([cur])
        while q:
            p = q.popleft()
            if p == far:
                break
            for da, db in ((-1, 0), (1, 0), (0, 1), (0, -1)):
                t = (p[0] + da, p[1] + db)
                if t in seen and t not in prev:
                    prev[t] = p
                    q.append(t)
        path = []
        p = far
        while p is not None:
            path.append(p)
            p = prev[p]
        path.reverse()
        self._gyt_path = (path, 0)
        sig = (len(path), far)
        if sig != getattr(self, "_gyt_plan_sig", None):
            self._gyt_plan_sig = sig
            self.log("[路线] 深入路径规划: %d 步 -> K%s (右上最深, 可达域%d格)"
                     % (len(path) - 1, far, len(seen)))
        return True

    def _gyt_travel_step(self, g):
        """沿规划路径深入一步。每步数据可走+里程计核实; 异常自动重规划。"""
        if self._gyt_path is None:
            if not self._gyt_plan_travel():
                self._gyt_end_travel()
                return
        cells, idx = self._gyt_path
        if idx >= len(cells) - 1:
            self._gyt_end_travel()
            return
        a0, b0 = self._gyt_lock_anchor
        cur = (a0 + self.dfs_pos[0], b0 + self.dfs_pos[1])
        if cur != cells[idx]:
            self._gyt_path = None            # 帧错位(死角重置等) -> 重规划
            return
        nxt = cells[idx + 1]
        d = {(-1, 0): "up", (1, 0): "down", (0, 1): "right",
             (0, -1): "left"}.get((nxt[0] - cur[0], nxt[1] - cur[1]))
        if d is None:
            self._gyt_path = None
            return
        st, k = self._probe_step(g, d)
        if st == "door":
            rr = self._return_from_transition(g, d)
            if rr:
                self._handle_returned(d, rr)
            else:
                self.log("!!! [路线] 深入途中踩门且退不回 -> 安全停机 (累计胜 %d)"
                         % self.battles_won)
                try:
                    g.flush_keys()
                except Exception:
                    pass
                raise SystemExit
            self._gyt_path = None            # 门观测/封堵后重规划
            return
        if st == "moved" and k >= 1:
            kd = self.KDELTA[d]
            self.dfs_pos = (self.dfs_pos[0] + kd[0] * k,
                            self.dfs_pos[1] + kd[1] * k)
            self._gyt_path = (cells, min(idx + k, len(cells) - 1))
            self._gyt_plan_fail = 0
            return
        # 被挡: 记住此格实测不可通行, 重规划绕开; 连续受阻就就地刷怪
        self._gyt_plan_fail = getattr(self, "_gyt_plan_fail", 0) + 1
        if self._gyt_plan_fail >= 8:
            self.log("[路线] 深入连续受阻%d次 -> 就地开始刷怪" % self._gyt_plan_fail)
            self._gyt_end_travel()
            return
        self._gyt_plan_block = getattr(self, "_gyt_plan_block", set())
        self._gyt_plan_block.add(nxt)
        self._gyt_path = None

    def _gyt_end_travel(self):
        """深入结束: 在当前位置重建安全岛, 开始刷怪。"""
        self._gyt_travel_on = False
        self._gyt_travel_done = True
        self._gyt_plan_block = set()
        self._gyt_plan_fail = 0
        cur = (self._gyt_lock_anchor[0] + self.dfs_pos[0],
               self._gyt_lock_anchor[1] + self.dfs_pos[1])
        self.log("[路线] 深入完成: 到达锁帧K%s, 在此重建安全岛开始刷怪" % (cur,))
        self._gyt_finish(self._gyt_lock_map, cur)

    def _shift_scores(self, a1, patch, d):
        """在 PATCH 附近局部窗口内一次 FFT, 返回 (s_zero, s_one) 两个假设的 NCC 分。"""
        m = self.STEP_MARGIN
        x0 = max(0, PATCH[0] - m)
        y0 = max(0, PATCH[1] - m)
        x1 = min(a1.shape[1], PATCH[2] + m)
        y1 = min(a1.shape[0], PATCH[3] + m)
        sub = a1[y0:y1, x0:x1]
        nm = ncc_map(sub, patch)
        h, w = nm.shape
        bx, by = PATCH[0] - x0, PATCH[1] - y0
        ex, ey = self.EXPECTED[d]

        def at(px, py):
            if 0 <= py < h and 0 <= px < w:
                return float(nm[py, px])
            return 0.0
        return at(bx, by), at(bx + ex, by + ey)

    def step1(self, g, d, a0=None):
        """快速一格步进。返回 moved/blocked/battle/transition

        实测标定(本机):
          - 点按 0.09s -> 位移在 0.22s 内精确到位, 1格假设匹配分 0.97 (静止假设 0.33~0.52)
          - 全屏抓帧 85ms -> 只抓左上 900x700 降到 39ms, 且内容逐像素一致
          - 旧实现每格: 2 次全屏抓帧 + 固定 0.5s 等待 + FFT, 约 1.2~2.4 秒/格
            新实现每格: 2 次小抓帧 + 轮询到就返回, 约 0.25 秒/格
        判定(不看颜色阈值, 颜色实测无区分度):
          1 格假设命中            -> moved
          静止假设持续命中        -> blocked
          两个都对不上            -> 画面换了: 再抓全屏区分 战斗 / 黑屏 / 换图
        """
        gf = g.shot_fast()
        tmpl = self._prep_box(gf)
        g.tap(d, self.TAP)
        t0 = time.time()
        s0 = s1 = 0.0
        while True:
            gf2 = g.shot_fast()
            s0, s1 = self._ncc_box(gf2, tmpl, d)
            if s1 > self.MOVE_SCORE and s1 - s0 > 0.10:
                self._last_fast = gf2
                self.map_stable_since = time.time()
                self._measure_shift(gf, d, settle=True)
                return "moved"
            el = time.time() - t0
            if el > self.STILL_AFTER and s0 > self.STILL_SCORE:
                self._last_fast = gf2
                self._measure_shift(gf, d, settle=False)
                return "blocked"
            if el > 0.9:
                break
            time.sleep(0.02)
        self._last_shift = (0, 0, -1.0)      # 异常路径无测量
        return self._handle_anomaly(g, gf, tmpl, d)

    def _measure_shift(self, gf_before, d, settle):
        """实测镜头位移 (内容位移, 与 EXPECTED 同约定), 存 self._last_shift。
        这是位置校正的真相来源: 按键可能没走动/走两格, 镜头不会说谎。"""
        try:
            if settle:
                time.sleep(0.10)             # 等滚动到位
            gf3 = self.game.shot_fast()
            mdx, mdy, msc = local_match(gf_before, gf3)
            self._last_shift = (mdx, mdy, msc)
        except Exception:
            self._last_shift = (0, 0, -1.0)

    def _shift_cells(self, d):
        """把 _last_shift 实测位移分类成实际走过的格数: 0/1/2, -1=测量与按键矛盾。
        不可信的测量(分数低)返回 1 (信任按键)。"""
        mdx, mdy, msc = getattr(self, '_last_shift', (0, 0, -1.0))
        if msc < 0.75:
            return 1
        ex, ey = self.EXPECTED[d]
        if abs(mdx - ex) <= 42 and abs(mdy - ey) <= 24:
            return 1
        if abs(mdx - 2 * ex) <= 42 and abs(mdy - 2 * ey) <= 24:
            return 2
        if abs(mdx) <= 42 and abs(mdy) <= 24:
            return 0
        return -1

    def _handle_anomaly(self, g, gf_before, tmpl, d):
        """画面既不前进也没静止: 区分 战斗 / 黑屏 / 换图(踩到出口)。"""
        self._last_fast_before = gf_before
        for _ in range(10):
            a = g.shot()                       # 全屏(只在异常时抓, 85ms, 可接受)
            if is_battle(a):
                self._last_full = a
                return "battle"
            if is_black(a):
                time.sleep(0.35)
                continue
            gm = g.shot_fast()
            if max(self._ncc_box(gm, tmpl, None, 2)) > 0.80:   # 其实没换图
                self._last_fast = gm
                return "blocked"
            self._last_fast = gm
            self._last_full = a
            return "transition"                # 真的换图了(踩到出口)
        return "transition"


    # ---- 场景是否相同的像素级判据 ----
    # zone_sig 指纹实测无区分度(迷宫/室外/换图互相都是 0.8~0.95), 已废弃。
    # 局部模板 NCC 实测: 同场景同位置≈1.00, 差一格≈0.97, 换场景≈0.25。
    def _patch_ncc(self, a_ref, a):
        """以 a_ref 的 PATCH 为模板, 在 a 的 PATCH 邻域内搜索, 返回最高 NCC"""
        tgt = a_ref[PATCH[1]:PATCH[3], PATCH[0]:PATCH[2]]
        m = self.STEP_MARGIN
        x0 = max(0, PATCH[0] - m)
        y0 = max(0, PATCH[1] - m)
        x1 = min(a.shape[1], PATCH[2] + m)
        y1 = min(a.shape[0], PATCH[3] + m)
        return float(ncc_map(a[y0:y1, x0:x1], tgt).max())

    def _scene_same(self, a_ref, a, thr=0.80):
        return self._patch_ncc(a_ref, a) > thr

    def _seal_door(self, door, wide=False):
        """把指定格列为门禁(会切换地图, 永久禁入)。
        wide=True: 退回落点有不确定性(传送落点未必是门口) ->
        连同周边 2 格整片禁入, 宁可少覆盖也绝不穿门(用户硬性要求)。"""
        R = 2 if wide else 0
        for uu in range(-R, R + 1):
            for vv in range(-R, R + 1):
                self.door_tiles.add((door[0] + uu, door[1] + vv))
        for uu in range(-1, 2):               # 从任何邻格进入这道门都禁掉
            for vv in range(-1, 2):
                for k in self.DIRS:
                    ku, kv = self.DIRS[k]
                    self.dfs_blocked.add(((door[0] + uu - ku, door[1] + vv - kv), k))
        self.sealed_portals += 1
        self.portal_drift = 0
        # 漂移熔断(仅锁定后): 坐标系经门观测持续校准, 若"新出口"仍与已封出口
        # 几乎重合, 说明当前锁定帧已漂移/不可信。v11: 不再直接停机, 而是恢复
        # 全量候选重新自定位(里程计保证当前位置可信); 2 次仍复现才停机。
        if self._gyt_lock:
            for p in self.seal_positions:
                if abs(p[0] - door[0]) <= 3 and abs(p[1] - door[1]) <= 3:
                    if getattr(self, "_gyt_reloc_used", 0) < 2:
                        self._gyt_reloc_used = getattr(self, "_gyt_reloc_used", 0) + 1
                        self._gyt_relocalize("新出口%s 与已封出口%s 仅隔%d格, 锁定帧不可信"
                                             % (door, p,
                                                abs(p[0] - door[0]) + abs(p[1] - door[1])))
                        return
                    self.log("!!! [漂移] 新出口%s 与已封出口%s 重合且已重新定位%d次 -> "
                             "环境与解码数据不符, 立即停机(累计胜 %d)"
                             % (door, p, self._gyt_reloc_used, self.battles_won))
                    try:
                        self.game.flush_keys()
                    except Exception:
                        pass
                    raise SystemExit
        self.seal_positions.append(door)
        self.log("[DFS] 封堵出口#%d: 局部%s 会切换地图, 已退回并列为%s禁入 (已覆盖 %d 格)"
                 % (self.sealed_portals, door,
                    "周边2格整片" if wide else "永久", len(self.dfs_visited)))

    def _handle_returned(self, d, r):
        """退回成功后的收尾: 落点有偏移就先平移局部坐标系, 再封门。"""
        du, dv = self.DIRS[d]
        door = (self.dfs_pos[0] + du, self.dfs_pos[1] + dv)
        if isinstance(r, tuple):              # ("shift", aa, bb)
            aa, bb = r[1], r[2]
            mua = aa * self.DIRS["up"][0] + bb * self.DIRS["right"][0]
            mvb = aa * self.DIRS["up"][1] + bb * self.DIRS["right"][1]
            self.dfs_pos = (self.dfs_pos[0] + mua, self.dfs_pos[1] + mvb)
            if self.gyt_mode:
                # v4: 局部坐标系平移时, 门禁/护栏集合必须同步平移,
                # 否则绝对换算的集合与 dfs_pos 脱钩 -> 反复踩门 (10:47 事故根因)
                self.door_tiles = {(p[0] - mua, p[1] - mvb) for p in self.door_tiles}
                if self.gyt_walk is not None:
                    self.gyt_walk = {(p[0] - mua, p[1] - mvb) for p in self.gyt_walk}
                self.seal_positions = [(p[0] - mua, p[1] - mvb) for p in self.seal_positions]
            self.log("[DFS] 已回到原场景, 但传送落点偏移 (%d,%d) 格 -> 局部坐标系已平移"
                     % (aa, bb))
            self._seal_door(door, wide=True)
            if self.gyt_mode:
                # 踩门=最强定位观测: 门格局部坐标与封堵用的同一帧
                self._gyt_observe_door(door)
        else:
            self._seal_door(door, wide=(r == "shifted"))
            if self.gyt_mode:
                self._gyt_observe_door(door)

    # ---- 换图退回的"宽域"场景核对 ----
    # 实测教训1: 从门外重新进图, 传送落点可能离出发格好几格(落点≠门口),
    #            ±1 格候选窗根本照不到 -> 被误判"没回到"。
    # 实测教训2: 固定 BOX(110,220,500,600) 在空洞(纯黑)占比高的场景里,
    #            相机滚一格 NCC 就从 1.00 跌到 0.77, 而阈值是 0.80 ->
    #            队伍明明已经回到原格却被判"没回到" -> 白白停机。
    #            所以核验用的模板窗要**自适应挑标准差最高**(纹理最丰富)的那块。
    # 这里用全屏抓帧 + ±3 格暴力 NCC。只发生在换图这种低频路径, 不在乎 ~200ms。
    WIDE_RANGE = 3
    GYT_PATROL_RADIUS = 10   # v9: 锁定后巡逻区=起点周围半径10格的安全岛 (入口区门密集且
                             # 部分门不在解码数据里, 小岛刷怪最安全; 遇敌靠步数, 小岛更高效)
    GYT_TRAVEL_STEPS = 40    # v10 用户路线: 锁定后先往右上(深处)走最多40步再刷怪
    # 队伍屏幕位移向量(快帧坐标, 1格=内部16x8px×3.375缩放): up=右上, right=右下
    SCREEN_STEP = {"up": (54, -27), "down": (-54, 27),
                   "right": (54, 27), "left": (-54, -27)}

    def _void_guard(self, g, d):
        """用户提供的地标护栏: 鬼阴坛入口附近画面左下是大片纯黑虚空。
        实测分离度(快帧左下象限纯黑占比): 入口区 0.76, 迷宫/室外 0.12~0.22 ->
        阈值 0.45。触发 => 正接近入口 => 禁止朝左下屏幕方向(down/left键)走,
        从视觉上杜绝'走向入口门群/走出塔外' (解码数据查不到的脚本门靠它兜住)。"""
        if d not in ("down", "left"):
            return False
        a = g.shot_fast()
        q = a[330:660, 0:300]               # 快帧(600x660)左下象限
        frac = float((q < 25.0).mean())
        if frac > 0.45:
            if not getattr(self, "_void_active", False):
                self.log("[虚空] 左下黑占比%.0f%% > 45%% -> 接近入口(黑色虚空标志), %s 方向禁行"
                         % (frac * 100, d))
                self._void_active = True
            return True
        self._void_active = False
        return False
    VERIFY_THR = 0.70          # 宽域核验阈值(同场景最优偏移实测 0.9x, 换场景 <0.4)

    def _pick_box(self, gf, w=390, h=380, need=2):
        """在抓帧里挑标准差最高(信息量最大)的 need 个互不重叠的 w×h 窗。
        返回 [(x0, y0, x1, y1, std), ...] 按标准差降序。"""
        H, W = gf.shape
        cands = []
        for y0 in range(0, H - h + 1, 40):
            for x0 in range(0, W - w + 1, 40):
                p = gf[y0:y0 + h, x0:x0 + w]
                cands.append((float(p.std()), x0, y0))
        cands.sort(reverse=True)
        out = []
        for s, x0, y0 in cands:
            ok = True
            for _, ox0, oy0, ox1, oy1, _s in out:
                if x0 < ox1 and ox0 < x0 + w and y0 < oy1 and oy0 < y0 + h:
                    ok = False
                    break
            if ok:
                out.append((s, x0, y0, x0 + w, y0 + h, s))
            if len(out) >= need:
                break
        return out

    def _scene_check_wide(self, g, tmpl0, box=None):
        """全屏找模板, 返回 (最佳分, up格数a, right格数b)。找不到给 (负数, 0, 0)。
        相机位移换算: 走 a 格 up + b 格 right => 相机偏移
        ox = -83*(a+b), oy = 42*(a-b)  (与 EXPECTED 一致)"""
        a_full = g.shot()
        gray = (0.299 * a_full[..., 0] + 0.587 * a_full[..., 1]
                + 0.114 * a_full[..., 2]).astype(np.float32)
        R = self.WIDE_RANGE
        b = box or self.BOX
        h, w = tmpl0.shape
        best = (-2.0, 0, 0)
        for aa in range(-R, R + 1):
            for bb in range(-R, R + 1):
                ox = -TILE_DX * (aa + bb)
                oy = TILE_DY * (aa - bb)
                px, py = b[0] + ox, b[1] + oy
                if px < 0 or py < 0 or px + w > gray.shape[1] or py + h > gray.shape[0]:
                    continue
                r = gray[py:py + h, px:px + w]
                r = r - float(r.mean())
                sc = float((r * tmpl0).sum() / (float(np.sqrt((r * r).sum())) + 1e-6))
                if sc > best[0]:
                    best = (sc, aa, bb)
        return best

    def _return_verify(self, g, gref):
        """宽域核验"是否已回到换图前的场景"。返回 (ok, up格数a, right格数b, 分数)。
        gref = 换图前那帧的灰度抓帧(shot_fast 的 600x660)"""
        boxes = self._pick_box(gref)
        if not boxes:
            return (False, 0, 0, -2.0)
        s1, x0, y0, x1, y1, _ = boxes[0]
        p = gref[y0:y1, x0:x1]
        p = p - p.mean()
        tmpl1 = p / (float(np.sqrt((p * p).sum())) + 1e-6)
        bs, aa, bb = self._scene_check_wide(g, tmpl1, (x0, y0, x1, y1))
        if bs < self.VERIFY_THR:
            return (False, 0, 0, bs)
        if bs >= 0.85 or len(boxes) < 2:
            return (True, aa, bb, bs)          # 分数够高, 单区域即可定论
        # 分数含糊(0.70~0.85): 用第二块独立区域复核, 偏移必须一致
        s2, u0, v0, u1, v1, _ = boxes[1]
        q = gref[v0:v1, u0:u1]
        q = q - q.mean()
        tmpl2 = q / (float(np.sqrt((q * q).sum())) + 1e-6)
        bs2, aa2, bb2 = self._scene_check_wide(g, tmpl2, (u0, v0, u1, v1))
        if bs2 >= self.VERIFY_THR and (aa2, bb2) == (aa, bb):
            return (True, aa, bb, min(bs, bs2))
        return (False, 0, 0, min(bs, bs2))

    def _return_from_transition(self, g, d, gref_before=None):
        """踩到出口(换图)后设法回到原场景。
        返回 "exact"=精确回到原格 / "shifted"=回到原场景但位置可能差 1 格 / False=没回到。

        关键改进(实测教训): 旧写法按固定顺序连走 7 格(反x2 -> 正 -> 上下左右),
        在陌生场景里一旦前几步没回去, 队伍就被自己带着越走越远(最多 7 格),
        从此再也找不到回来的门 -> 最终"跨场景无法回退"停机, 人还得手动走回来。
        现在每个候选方向都**从同一个格子**出发试: 走出去没回到原场景就原路退回,
        保证下一个候选仍站在到达格上试, 不会漂移。

        判据实测: 局部模板(390x360) 同位置同场景给 0.97, 别的场景/别的位置给 ~0.05;
                  整幅相似度对场景毫无区分度(不同场景也都有 0.87~0.91), 所以只能用局部模板。
        注意: 途中遇到战斗一律返回 False(视为没回到) —— 此时无法证明队伍已经回到原场景,
        绝不能在没核实的情况下封门(否则可能把队伍留在门另一侧还把回路封死)。
        """
        if gref_before is None:
            gref_before = self._last_fast_before
        if gref_before is None:
            return False
        tmpl0 = self._prep_box(gref_before)

        def wait_ready(timeout):
            """等黑屏/淡入结束。返回 False = 超时或中途进战斗(视为失败)"""
            t0 = time.time()
            while time.time() - t0 < timeout:
                a = g.shot()
                if is_battle(a):
                    return False
                if not is_black(a):
                    return True
                time.sleep(0.30)
            return False

        def check():
            gf = g.shot_fast()
            sc = self._ncc_box(gf, tmpl0, None, 1)
            self._last_fast = gf
            if olive_feat(g.shot()) > 0.35:      # 画面已是室外: 绝不算"回到原格"
                return None
            if sc[0] > 0.85:            # 0 位移假设命中 = 精确回到原格
                return "exact"
            # 候选顺序 = 原地 + 四方向各1格: 把命中的相机偏移换算成 (up格, right格)
            cands = [(0, 0)] + [self.EXPECTED[dd] for dd in ("up", "down", "left", "right")]
            i = max(range(len(sc)), key=lambda k: sc[k])
            if sc[i] > 0.85:
                ox, oy = cands[i]
                return ("shift", int(round((-ox / TILE_DX + oy / TILE_DY) / 2)),
                        int(round((-ox / TILE_DX - oy / TILE_DY) / 2)))
            # ±1 照不到 -> 宽域核验(传送落点可能离门口好几格; 空洞多的场景要放宽阈值)
            ok, aa, bb, scv = self._return_verify(g, gref_before)
            if ok:
                if aa == 0 and bb == 0:
                    return "exact"
                return ("shift", aa, bb)
            return None

        for mv in (self.OPP[d], d, "up", "down", "left", "right"):
            if not wait_ready(4.0):
                return False
            pre = g.shot_fast()
            g.tap(mv, self.TAP)
            time.sleep(0.50)
            if not wait_ready(3.0):
                return False
            r = check()
            if r:
                return r
            # 没回到原场景: 若这一步真的走动了, 就原路退回, 让下个候选仍从到达格出发
            post = g.shot_fast()
            if self._ncc_box(post, self._prep_box(pre), None, 1)[0] < 0.95:
                if not wait_ready(3.0):
                    return False
                g.tap(self.OPP[mv], self.TAP)
                time.sleep(0.50)
                if not wait_ready(3.0):
                    return False
                r = check()             # 退回途中可能正好穿回原场景
                if r:
                    return r
        return False


    def _dump_dfs_map(self, tag=""):
        """把局部 DFS 地图落成 ASCII 文件, 便于人工诊断(不参与决策)。
        (u,v) = (上步数, 右步数); 显示时 x=v(向右), y=-u(向上), 与画面方位一致。"""
        try:
            pts = set(self.dfs_visited)
            if not pts:
                return
            us = [p[0] for p in pts]
            vs = [p[1] for p in pts]
            u0, u1 = min(us) - 2, max(us) + 2
            v0, v1 = min(vs) - 2, max(vs) + 2
            lines = []
            lines.append("# DFS 局部地图 %s  覆盖 %d 格  门禁 %d 格  坐标x=右,y=上 (起点在 * )"
                         % (tag, len(pts), len(self.door_tiles)))
            for u in range(u1, u0 - 1, -1):
                row = ""
                for v in range(v0, v1 + 1):
                    p = (u, v)
                    if p == (0, 0):
                        row += "*"
                    elif p in self.door_tiles:
                        row += "X"
                    elif p in pts:
                        row += "o"
                    else:
                        row += "."
                lines.append(row)
            with open(os.path.join(os.path.dirname(os.path.abspath(self.log_path)),
                                   "dfs_map.txt"), "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        except Exception:
            pass

    def _fight_battle(self, g, tag=""):
        """战斗处理: 一旦进入就驻守此循环, 持续盯画面直到连续确认脱战。
        期间只按 F(攻击)/空格(结算推进), 绝不按方向键 (用户要求)。"""
        self.log("[战斗] 交战, 按 F 最强法术%s" % tag)
        t0 = time.time()
        won = 0
        while time.time() - t0 < 180.0:
            a = g.shot()
            if is_battle(a):
                t0 = time.time()                     # 还在打: 重置超时
                if self._fight_until_done(g):
                    won += 1
                    self.battles_won += 1
                    self.last_win_time = time.time()
                    self.log(">>> 第 %d 场战斗胜利!" % self.battles_won)
                continue
            if is_black(a):
                time.sleep(0.4)                      # 淡入淡出/换场: 干等, 不碰任何键
                continue
            g.key("space")                           # 非战斗: 可能是结算画面, 按空格推进
            ok = 0
            for _ in range(3):                       # 连续 3 次(间隔0.5s)非战斗非黑屏才算脱战
                time.sleep(0.5)
                b = g.shot()
                if is_battle(b) or is_black(b):
                    break
                ok += 1
            if ok >= 3:
                break
        g.flush_keys()
        self._last_full_check = time.time()
        if won == 0:
            self.log("[战斗] 本次交战未确认胜场(可能误触发或超时), 继续巡逻")

    def dfs_tick(self, g, a0=None):
        """DFS 全覆盖一 tick: 优先探索未访问格, 全方向受阻则沿路径回溯。
        热路径只抓左上小区域(39ms); 全屏抓帧(85ms)只在异常或每 4 秒复核一次。"""
        if getattr(self, "_gyt_need_localize", False):
            self._gyt_localize(g)
            return
        if self.gyt_mode and getattr(self, "_gyt_travel_on", False):
            # 用户路线: 锁定后沿解码图规划的路径走到右上深区, 再开始刷怪
            self._gyt_travel_step(g)
            return
        interval = 1.5 if self.gyt_mode else 4.0   # GYT: 更勤看画面, 逃逸窗口从4s缩到1.5s
        if time.time() - self._last_full_check > interval:
            self._last_full_check = time.time()
            a = g.shot()
            st = self.state_name(a)
            if st == "battle":
                self._fight_battle(g)
                return
            if st == "menu":
                self.log("[菜单] 关闭系统菜单")
                g.key("esc")
                time.sleep(0.7)
                return
            if st == "black":
                time.sleep(0.6)
                return
            if self.gyt_mode and olive_feat(a) > 0.40:
                # 真草地实测 olive≈0.59, 迷宫<0.20; 0.27左右的黄土区不算逃逸
                self.log("!!! [GYT] 画面已是室外(olive=%.2f) -> 判定失控逃逸, 立即停机" % olive_feat(a))
                g.flush_keys()
                raise SystemExit
        # ---- 心跳: 每 20 秒报一次"仍在原区域"的证据, 便于长跑无人值守时核对 ----
        if time.time() - self._last_beat > 20.0:
            self._last_beat = time.time()
            dt = max(1e-6, time.time() - self._sweep_t0)
            self.log("[心跳] 仍在本区域: 本轮覆盖 %d 格, 禁入门格 %d, 已封堵出口 %d, "
                     "拒踏门格 %d 次, 累计 %d 场, %.2f 格/秒"
                     % (len(self.dfs_visited), len(self.door_tiles), self.sealed_portals,
                        self.door_refusals, self.battles_won, self.dfs_steps / dt))
        base = ["up", "right", "down", "left"]
        if self.dfs_last:
            i = base.index(self.dfs_last)
            order = [base[i], base[(i + 1) % 4], base[(i - 1) % 4], base[(i + 2) % 4]]
        else:
            order = base
        for d in order:
            if (self.dfs_pos, d) in self.dfs_blocked:
                continue
            du, dv = self.DIRS[d]
            nxt = (self.dfs_pos[0] + du, self.dfs_pos[1] + dv)
            if nxt in self.dfs_visited or nxt in self.door_tiles:
                continue            # 优先探索没去过的格子; 门格永久禁入
            st = self._dfs_do(g, d, nxt)
            if st == "stop":
                return
            if st == "moved":
                return
            # blocked: 记录这条边不通, 换下一个方向
        # ---- 四个方向都走不通: 回溯 ----
        while self.dfs_path:
            prev_pos, d_in = self.dfs_path[-1]
            d_back = self.OPP[d_in]
            st = self.step1(g, d_back)
            if st == "battle":
                self._fight_battle(g)
                return
            if st == "transition":
                r = self._return_from_transition(g, d_back)
                if r:
                    self._handle_returned(d_back, r)
                    self.dfs_blocked.add((self.dfs_pos, d_back))
                else:
                    self.save_shot("leak_%s.png" % datetime.datetime.now().strftime("%H%M%S"))
                    self.log("!!! 回溯时踩到出口且无法退回 -> 队伍已置身门外, 立即停机 "
                             "(累计胜利 %d)" % self.battles_won)
                    raise SystemExit
                return
            if st == "moved":
                # 回溯也要过里程计: 防多退/假退导致位置错位
                k = self._shift_cells(d_back)
                if k == 0:
                    if self.gyt_mode:
                        self._gyt_observe(self.dfs_pos, d_back, 0)
                    self.dfs_blocked.add((self.dfs_pos, d_back))
                    self.dfs_path.pop()
                    return
                if self.gyt_mode and k == -1:
                    k = 1
                if self.gyt_mode:
                    da, db = self.KDELTA[d_back]
                    for i in range(k):
                        self._gyt_observe((self.dfs_pos[0] + i * da, self.dfs_pos[1] + i * db),
                                          d_back, 1)
                pops = 2 if (k == 2 and len(self.dfs_path) >= 2) else 1
                prev_pos = self.dfs_pos
                for _ in range(pops):
                    prev_pos, d_in = self.dfs_path.pop()
                self.dfs_pos = prev_pos
                self.dfs_last = d_back
                self.dfs_steps += 1
                return
            if self.gyt_mode:
                self._gyt_observe(self.dfs_pos, d_back, 0)
            self.dfs_blocked.add((self.dfs_pos, d_back))
            self.dfs_path.pop()
        # ---- 回到起点且四面受阻: 本连通区域已全覆盖 ----
        self._dfs_sweeps += 1
        dt = max(1e-6, time.time() - self._sweep_t0)
        cov = len(self.dfs_visited)
        if cov <= 3:
            if self.gyt_mode:
                # 解码地图模式: 门禁/区界都是精确数据, 绝不解禁穿门
                # (起点停在死角是正常的, 重置本轮继续即可)
                # 卡死自救: 超过 60 秒没挪动过 -> 清除阻挡记录全面重试
                # (区界护栏与位置无关, 清除后队伍依然不可能离开巡逻区/踏入门格)
                # 连续多次死角重置 = 本岛已刷尽(总在1格里打转) ->
                # 按用户路线转入深入模式, 去右上找新刷怪区, 不再原地空转
                self._gyt_dc_n = getattr(self, "_gyt_dc_n", 0) + 1
                if (self._gyt_dc_n >= 4 and self._gyt_lock
                        and not getattr(self, "_gyt_travel_on", False)):
                    self._gyt_dc_n = 0
                    self.log("[路线] 连续4次死角重置 = 本岛已刷尽 -> "
                             "转入深入模式, 往右上找新刷怪区 (用户路线)")
                    self._gyt_travel_on = True
                    self._gyt_path = None
                    self._gyt_plan_block = set()
                    self._gyt_plan_fail = 0
                    self._gyt_plan_sig = None
                    self._gyt_travel_done = False
                    return
                if time.time() - self._last_moved_t > 60.0:
                    if (self.gyt_mode and not self._gyt_lock and self.door_tiles):
                        # 未锁定阶段的门禁是"所有候选门的并集"(保守超集, 可能上万格),
                        # 会把起点围成 1 格死岛。真门只是其子集, 且踩门有物理退回兜底,
                        # 所以卡死时解除超集是安全的 (锁定后的精确门禁绝不解除)。
                        self.log("[GYT] 60秒未移动且未锁定 -> 解除门禁并集%d格脱困 "
                                 "(踩门有物理退回+观测兜底, 锁定后门禁精确重建)"
                                 % len(self.door_tiles))
                        self.door_tiles.clear()
                    else:
                        self.log("[GYT] 60秒未移动 -> 清除 %d 条阻挡记录全面重试 (区界/门禁护栏不变)"
                                 % len(self.dfs_blocked))
                    self.dfs_blocked.clear()
                    self._last_moved_t = time.time()
                if time.time() - self._last_deadcorner_log > 5.0:   # 防刷屏
                    self._last_deadcorner_log = time.time()
                    self.log("[DFS] 起点在死角(覆盖 %d 格) -> 正常重置, 门禁保留 (GYT模式永不解禁)"
                             % cov)
                self.dfs_visited = {self.dfs_pos}
                self.dfs_path = []
                self.dfs_last = None
                self._sweep_t0 = time.time()
                self._prev_sweep_cov = cov
                return
            # 死胡同(典型: 门后的小耳房): 解禁所有门, 主动穿门去找出路。
            # 否则会永远困在 1~2 格里(历史上就是这么被卡住的)。
            # 注意(2026-09-22 实测): 上一轮刚做完大范围覆盖时, "本轮只有 1 格"
            # 只是**起点正好停在死角**, 不是被困 —— 此时解禁会把刚封堵好的门全放掉。
            # 只有连续两轮都是极小覆盖, 才是真的被困在门后耳房, 才值得解禁穿门。
            if self._prev_sweep_cov > 3:
                self.log("[DFS] 起点在死角(上一轮覆盖 %d 格) -> 正常重置, 门禁保留"
                         % self._prev_sweep_cov)
                self.dfs_visited = {self.dfs_pos}
                self.dfs_path = []
                self.dfs_last = None
                self._sweep_t0 = time.time()
                self._prev_sweep_cov = cov
                return
            self.log("[DFS] 连续两轮只覆盖 %d/%d 格 = 真被困在死胡同 -> 解除门禁, 主动穿门找出路"
                     % (self._prev_sweep_cov, cov))
            self.door_tiles.clear()
            self.dfs_blocked.clear()
            self.portal_passthrough = True
            self.dfs_visited = {self.dfs_pos}
            self.dfs_path = []
            self.dfs_last = None
            self._sweep_t0 = time.time()
            self._prev_sweep_cov = cov
            return
        msg = ("[DFS] 第 %d 轮全覆盖完成: 本轮覆盖 %d 格, 用时 %d 秒 (%.2f 格/秒, 累计 %d 场)"
               % (self._dfs_sweeps, cov, int(dt), cov / dt, self.battles_won))
        if cov > 3:
            self._gyt_dc_n = 0                # 正常刷完一轮, 死角重置计数清零
        if self.battles_won == 0:
            msg += " (注意: 至今零遇敌, 本区域可能不刷怪)"
        if self.door_tiles:
            msg += " [禁入区 %d 格]" % len(self.door_tiles)
        self.log(msg)
        if (self.gyt_mode and self.battles_won == 0
                and getattr(self, "_gyt_travel_done", False)):
            # v10: 刷完一圈零遇敌 = 这片不刷怪 -> 按用户路线继续往右上深入
            self._gyt_travel_done = False
            self._gyt_travel_on = True
            self._gyt_path = None
            self.log("[路线] 本区域零遇敌 -> 继续深入 (用户路线)")
        self.dfs_visited = {self.dfs_pos}
        self.dfs_path = []
        self.dfs_last = None
        self._prev_sweep_cov = cov
        self._sweep_t0 = time.time()

    # ---- 新场景"是否刷怪"探测 ----
    # 挂机最怕的不是走错路, 而是停在一个不刷怪的空区白转(实测: 同一机器人
    # 在原刷怪区 100 秒打 3 场, 换到另一个场景后 300 秒 122 格零遇敌)。
    # 所以穿门进入新场景后先走一小段数遇敌: 有怪 = 就地驻扎; 没怪 = 退回并封堵这道门。
    MONSTER_PROBE_TILES = 16

    def _fight_until_done(self, g, max_rounds=90):
        """战斗中连按 F 直到脱战。返回 True = 本次确实打了一场"""
        if not is_battle(g.shot()):
            return False
        for _ in range(max_rounds):
            g.key("f")
            time.sleep(1.1)
            if is_battle(g.shot()):
                continue
            ok = 0
            for _ in range(self.EXIT_BATTLE_CONFIRM):
                time.sleep(0.45)
                if is_battle(g.shot()):
                    break
                ok += 1
            if ok >= self.EXIT_BATTLE_CONFIRM:
                return True
        return False

    def _undo_path(self, g, path):
        """按记录的路径原路返回(逐格验证), 用于探怪后回到门前"""
        for d in reversed(path):
            back = self.OPP[d]
            for _ in range(3):
                st = self.step1(g, back)
                if st == "battle":
                    if self._fight_until_done(g):
                        self.battles_won += 1
                        self.last_win_time = time.time()
                        self.log(">>> 第 %d 场战斗胜利! (返回途中)" % self.battles_won)
                    continue
                if st == "moved":
                    break
                if st == "transition":
                    continue
                time.sleep(0.4)

    def _monster_probe(self, g):
        """在当前场景走若干格数遇敌。返回 (遇敌次数, 是否又换图, 走过的路径)"""
        hits = 0
        path = []
        last = None
        for _ in range(self.MONSTER_PROBE_TILES):
            order = ["up", "right", "down", "left"]
            if last:
                i = order.index(last)
                order = [order[i], order[(i + 1) % 4], order[(i - 1) % 4], order[(i + 2) % 4]]
            got = False
            for d in order:
                st = self.step1(g, d)
                if st == "battle":
                    if self._fight_until_done(g):
                        self.battles_won += 1
                        self.last_win_time = time.time()
                        self.log(">>> 第 %d 场战斗胜利! (探怪中)" % self.battles_won)
                        hits += 1
                    got = True
                    break
                if st == "transition":
                    return hits, True, path
                if st == "moved":
                    path.append(d)
                    last = d
                    got = True
                    time.sleep(0.1)
                    break
            if not got:
                break          # 四面皆堵, 本场景极小
        return hits, False, path

    KDELTA = {"up": (1, 0), "right": (0, 1), "down": (-1, 0), "left": (0, -1)}

    def _dfs_advance(self, d, cells):
        """按实测格数推进 DFS 坐标 (1 或 2 格), 沿途格都标记已访问。
        dfs_path 约定: (该步起点格, 走向), 供逐步回溯。"""
        da, db = self.KDELTA[d]
        for _ in range(cells):
            prev = self.dfs_pos
            self.dfs_pos = (prev[0] + da, prev[1] + db)
            self.dfs_visited.add(self.dfs_pos)
            self.dfs_path.append((prev, d))
        self.dfs_last = d
        self.dfs_steps += cells
        self._last_moved_t = time.time()

    def _dfs_do(self, g, d, nxt, a0=None):
        """执行一次探索步。返回 moved/blocked/stop"""
        if self.gyt_mode and self._void_guard(g, d):
            # 用户地标: 入口附近画面有大片黑色虚空, 朝虚空方向 = 朝入口门群走, 禁行
            self.dfs_blocked.add((self.dfs_pos, d))
            return "blocked"
        if self.gyt_mode and self.gyt_walk is not None and nxt not in self.gyt_walk:
            # 解码图中的墙/图外: 不踏 (v4: 区界已放宽为全图可走格, 仅墙挡路)
            self.dfs_blocked.add((self.dfs_pos, d))
            return "blocked"
        if nxt in self.door_tiles:
            # 已知会切换地图的格子: 永不踏入 (用户要求: 不能进入那两个门的区域)
            self.dfs_blocked.add((self.dfs_pos, d))
            self.door_refusals += 1
            if time.time() - self._last_door_log > 3.0:      # 防刷屏: 3 秒最多记一条
                self._last_door_log = time.time()
                self.log("[门禁] 拒绝踏入禁入格 %s (累计 %d 次, 队伍仍留在本区域)"
                         % (nxt, self.door_refusals))
            return "blocked"
        st = self.step1(g, d)
        if st == "blocked" and self.gyt_mode:
            # 瞬态误判防御: 停 0.7s 再试一次 (区界/门禁由上方护栏保证, 重试绝对安全)
            time.sleep(0.7)
            st = self.step1(g, d)
            # 镜头里程计: 若实测位移显示其实走动了, 推翻 blocked 判定
            k = self._shift_cells(d)
            if st == "blocked" and k in (1, 2):
                self.log("[里程计] blocked判定被实测位移(%d格)推翻 @%s %s"
                         % (k, self.dfs_pos, d))
                st = "moved"
                self._dfs_pending_cells = k
        if st == "battle":
            self._fight_battle(g)
            return "stop"
        if st == "transition":
            if self.portal_passthrough:
                # 死胡同解禁后主动穿门: 直接在新场景继续测绘
                self.portal_passthrough = False
                self.log("[DFS] 主动穿门通过 -> 在新场景重新测绘")
                self._dfs_reset()
                return "stop"
            # 踩到出口: 立即设法退回, 成功后把该格列入永久禁入区
            r = self._return_from_transition(g, d)
            if r:
                self._handle_returned(d, r)
                self.dfs_blocked.add((self.dfs_pos, d))
                return "blocked"
            self.save_shot("leak_%s.png" % datetime.datetime.now().strftime("%H%M%S"))
            self.log("!!! 踩到出口且无法退回 -> 队伍已置身门外, 立即停机 (累计胜利 %d)"
                     % self.battles_won)
            self.log("!!! 请把队伍手动走回练级区后重新启动; 启动后会重新测绘并封堵该出口")
            raise SystemExit
        if st == "moved":
            # 镜头里程计: 用实测位移校正实际走过的格数
            k = getattr(self, '_dfs_pending_cells', None) or self._shift_cells(d)
            self._dfs_pending_cells = None
            if k == 0:
                # 判定说动了但镜头没动 = 实际没走 (假 moved)
                self.dfs_blocked.add((self.dfs_pos, d))
                if self.gyt_mode:
                    self._gyt_observe(self.dfs_pos, d, 0)
                if time.time() - getattr(self, '_last_fake_move_log', 0) > 5.0:
                    self._last_fake_move_log = time.time()
                    self.log("[里程计] moved判定被实测位移(0格)推翻 @%s %s (按假blocked处理)"
                             % (self.dfs_pos, d))
                return "blocked"
            if k == -1:
                # 测量与按键矛盾 (周期纹理误锁等): 信任按键 = 1 格
                k = 1
            if k == 2 and self.gyt_mode:
                mid = (self.dfs_pos[0] + self.KDELTA[d][0],
                       self.dfs_pos[1] + self.KDELTA[d][1])
                if self.gyt_walk is not None and (mid not in self.gyt_walk or mid in self.door_tiles):
                    # 中间格在数据禁区: 但没触发换图说明它实际不是门 (数据保守误标)。
                    # 接受现实并记录, 拒绝认可会导致位置永久错位。
                    self.log("[里程计] 2格步进穿过数据禁格 %s (未触发换图, 按实测接受)" % (mid,))
            if self.gyt_mode:
                da, db = self.KDELTA[d]
                for i in range(k):
                    self._gyt_observe((self.dfs_pos[0] + i * da, self.dfs_pos[1] + i * db), d, 1)
            self._dfs_advance(d, k)
            if self.dfs_steps % 30 == 0:
                self.log("[DFS] 坐标%s 已覆盖 %d 格 (第%d轮, %.2f 格/秒)"
                         % (self.dfs_pos, len(self.dfs_visited), self._dfs_sweeps + 1,
                            self.dfs_steps / max(1e-6, time.time() - self._sweep_t0)))
                self._dump_dfs_map("进行中")
            return "moved"
        if self.gyt_mode:
            self._gyt_observe(self.dfs_pos, d, 0)
        self.dfs_blocked.add((self.dfs_pos, d))
        return "blocked"


    def tick(self):
        g = self.game
        # ---- DFS 快速通道: 热路径只抓左上小区域, 全屏抓帧只在异常/定期复核时用 ----
        if self.dfs_mode or self.gyt_mode:
            self.dfs_tick(g)
            return
        a = g.shot()
        st = self.state_name(a)

        # ---- 战斗中: 一路按 F（没蓝游戏自动普攻）----
        if st == "battle":
            if self.not_battle_count != 0:
                self.log("[战斗] 交战, 按 F 最强法术")
            self.not_battle_count = 0
            self.was_battle = True
            self.map_stable_since = time.time()
            g.key("f")
            time.sleep(1.1)
            return

        # ---- 非战斗帧 ----
        self.not_battle_count += 1

        if st == "menu":
            self.log("[菜单] 关闭系统菜单")
            g.key("esc")
            time.sleep(0.9)
            return

        if st == "black":
            time.sleep(0.8)
            return

        # 过场/结算窗口期：按空格推进但不走路
        if self.not_battle_count < self.EXIT_BATTLE_CONFIRM:
            g.key("space")
            time.sleep(1.0)
            return

        # 脱战确认
        if self.was_battle:
            self.was_battle = False
            self.battles_won += 1
            self.last_win_time = time.time()
            self.escapes = 0
            # 注意: 不清零 cycle_stats / pocket_escapes —— 房间内也会遇敌,
            # 打过仗不代表走出了房间, 打转判定只看每圈移动量
            self.log(">>> 第 %d 场战斗胜利!" % self.battles_won)

        # ---- 室内检测(仅普通模式): 屋内没有怪, 连续确认后停机留证 ----
        if not self.dungeon and olive_feat(a) < 0.18:
            self.indoor_count += 1
            if self.indoor_count < 3:   # 连续3次确认, 防止室外遮挡角落误报
                return
            self.log("!!! 确认进入室内 (olive=%.3f) -> 停机, 等待人工把队伍走出房屋"
                     % olive_feat(a))
            self.save_shot("indoor_%s.png" % datetime.datetime.now().strftime("%H%M%S"))
            self.log("!!! 机器人已停止 (本场累计胜利 %d)" % self.battles_won)
            raise SystemExit
        self.indoor_count = 0

        # ---- 迷宫模式: 出现室外画面 ----
        # 注意: olive 颜色在"迷宫内 / 多场景迷宫的另一层 / 野外"之间实测无区分度
        # (三者都在 0.28~0.45), 所以 DFS 模式不靠它停机 —— 出口由 step1 的双假设
        # NCC 判据识别并就地封堵, 这样机器人永远不会离开巡逻区。老模式保留此保护。
        if self.dungeon and not self.dfs_mode and olive_feat(a) > 0.26:
            self.log("!!! 迷宫模式检测到室外画面 (olive=%.3f) -> 疑似地图已切换, 停机"
                     % olive_feat(a))
            self.save_shot("map_switched_%s.png" % datetime.datetime.now().strftime("%H%M%S"))
            self.log("!!! 机器人已停止 (本场累计胜利 %d)" % self.battles_won)
            raise SystemExit

        # ---- 地图: 走路寻找怪物 ----
        if "map" != self.last_log_state:
            self.log("[地图] 走路寻找怪物 (已胜利 %d 场)" % self.battles_won)
            self.last_log_state = "map"

        # 路线模式: 按预设蛇形路线走位 (撞墙即转下一段, 战后从当前路段重来)
        if self.map_mode:
            self.map_tick(g)
        elif self.mow_mode:
            self.mow_tick(g)
        elif self.route_mode:
            self.route_step(g, a)
        else:
            # 巡逻模式: 走一步; 导航模式(单方向)被挡时穿插垂直方向找通路
            prev = a
            d = self.pat[self.walk_idx % len(self.pat)]
            if len(self.pat) == 1 and self.block_count > 0:
                perp = {"up": ("left", "right"), "down": ("left", "right"),
                        "left": ("up", "down"), "right": ("up", "down")}
                p = perp[d]
                d = p[self.block_count % 2]
            self.walk_idx += 1
            g.hold(d, 1.05)
            time.sleep(0.35)
            a2 = g.shot()
            # 真走路=全屏平移(diff大); 撞墙转身动画(diff很小)
            moved = frame_diff(prev, a2) > 0.025
            if not moved:
                # 被障碍挡住: 计数并让下一步试探垂直方向, 避免四向循环失衡漂移
                self.block_count += 1
                self.map_stable_since = min(self.map_stable_since, time.time() - 30)
            else:
                self.block_count = 0
                self.map_stable_since = time.time()

        # 看门狗: 在地图上 60 秒毫无动静 -> Esc+空格
        if time.time() - self.map_stable_since > 60:
            self.log("! 看门狗: 地图静止超时, Esc+空格 解救")
            g.key("esc")
            time.sleep(0.5)
            g.key("space")
            time.sleep(0.8)
            self.map_stable_since = time.time()

        # 认路/守护: 每45秒检查一次是否还在巡逻区
        if time.time() - self.last_anchor_check > 45:
            self.last_anchor_check = time.time()
            if self.dungeon:
                # 用像素级模板 NCC 判断是否还在起始房间附近
                # (zone_sig 指纹实测无区分度: 迷宫/室外/换图互相都是 0.8~0.95)
                if self.anchor_shot is not None:
                    sim = self._patch_ncc(self.anchor_shot, a)
                    now_near = sim > self.dungeon_sim_thr
                    if now_near != self.near_start:
                        self.near_start = now_near
                        self.log("! 与起始房间相似度=%.2f -> %s"
                                 % (sim, "仍在起始房间" if now_near else "已离开起始房间(正常巡逻)"))
            else:
                tf = town_feat(a)
                if tf < 0.006:
                    self.away_count += 1
                    self.log("! 疑似跑出镇区 (roof=%.4f) %d/2" % (tf, self.away_count))
                    if self.away_count >= 2:
                        self.log(">>> 回家: 往镇内方向走 12 秒")
                        for _ in range(10):
                            g.hold("up", 1.05)
                            time.sleep(0.3)
                            if is_battle(g.shot()):
                                break  # 路上遇敌, 交回主循环处理
                        self.away_count = 0
                        self.map_stable_since = time.time()
                        return
                else:
                    self.away_count = 0


LOCK_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".pal_bot.lock")
_locks = []   # 必须持有引用, 句柄被回收=释放锁


def _acquire_lock():
    """尝试取独占文件锁。成功返回句柄, 失败返回 None。
    用 Windows 独占句柄: 进程一旦结束(含被强杀)锁自动释放。
    关键: 被创建后挂起/未真正执行的副本永远不会拿到锁, 因此不会阻塞真实例。"""
    import win32file
    import win32con
    try:
        h = win32file.CreateFile(LOCK_PATH, win32con.GENERIC_WRITE, 0, None,
                                 win32con.OPEN_ALWAYS, win32con.FILE_ATTRIBUTE_NORMAL, None)
    except Exception:
        return None
    try:
        win32file.SetFilePointer(h, 0, win32con.FILE_BEGIN)
        win32file.WriteFile(h, ("%d\n" % os.getpid()).encode())
        win32file.FlushFileBuffers(h)
    except Exception:
        pass
    return h


def _elect_single_instance():
    """单实例自选举: 同一时刻只允许一个进程真正操作游戏。
    双实例会互相抢键盘导致走位混乱(历史教训)。
    落选者**不退出**, 而是待命轮询(退出会让宿主把整个任务判完成并回收进程组),
    并持有锁的实例结束后自动接管。返回 True = 本进程应当真正运行。"""
    if os.environ.get("PAL_BOT_ALLOW_MULTI"):
        return True
    for attempt in range(720):          # 最多待命 6 小时
        h = _acquire_lock()
        if h is not None:
            _locks.append(h)
            if attempt:
                print("[单实例] 已接管运行 (PID %d)" % os.getpid(), flush=True)
            return True
        if attempt == 0:
            try:
                with open(LOCK_PATH, "r", encoding="utf-8", errors="replace") as f:
                    holder = f.read().strip()
            except Exception:
                holder = "?"
            print("[单实例] 游戏已被另一个机器人实例 (PID %s) 占用, 本进程转入待命" % holder,
                  flush=True)
        time.sleep(30)
    print("[单实例] 待命超时, 退出", flush=True)
    return False


if __name__ == "__main__":
    if not _elect_single_instance():
        sys.exit(0)
    maxb = int(sys.argv[1]) if len(sys.argv) > 1 else None
    pat = sys.argv[2] if len(sys.argv) > 2 else None
    secs = int(sys.argv[3]) if len(sys.argv) > 3 else None
    stall = int(sys.argv[4]) if len(sys.argv) > 4 else 600
    bot = PalBot(max_battles=maxb, patrol=pat, max_seconds=secs, stall_limit=stall)
    # 按键安全网: 退出时冲洗方向键, 防止 keydown 悬空让队伍自己走 (历史上"跑丢"元凶之一)
    import atexit
    atexit.register(lambda: bot.game.flush_keys())
    try:
        bot.game.flush_keys()      # 启动时先清掉可能残留的卡键
        bot.run()
    except KeyboardInterrupt:
        print("\n[手动停止] 共胜利 %d 场" % bot.battles_won)
    finally:
        bot.game.flush_keys()
