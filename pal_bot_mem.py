# -*- coding: utf-8 -*-
"""pal_bot_mem.py — v12 内存真值驱动引擎 (替代视觉定位)。

核心事实 (2026-09-23 实测标定):
  - sdlpal.exe 32位, rgScene 场景表 @0x00C38088 (SCENE=8B{map,scriptOnEnter,scriptOnTeleport,eventObjIdx})
  - GLOBALVARS: viewport@0x00C3C8FC, partyoffset@0x00C3C900, rgParty[5]@0x00C3C908(10B/条),
    rgTrail[5]@0x00C3C93A(6B/条, 视口内渲染坐标, 会振荡, 不是地图坐标!), wPartyDirection@0x958,
    wNumScene@0x00C3C95A, wNumPalette@0x95C
  - 队伍真实地图像素 P = viewport + partyoffset
  - 地图数据K坐标: K=(a,b) = ((Px-2Py)//32, (Px+2Py-32)//32)  (与门对象/解码图同一坐标系)
  - 按键真值位移: up=ΔP(+16,-8)→ΔK(+1,0), down=(-16,+8)→(-1,0), left=(-16,-8)→(0,-1), right=(+16,+8)→(0,+1)
    !!! 旧视觉bot把 up/down 的K方向弄反了, 这是历次锁错层/坐标漂移的总根源 !!!
  - 换图 = wNumScene 变化 (穿门), 门对象坐标可从 gyt_scene_report.txt 预取
"""
import ctypes, ctypes.wintypes as wt, heapq, struct, os, re, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pal_bot as PB
import wingeom

k32 = ctypes.windll.kernel32
OPEN_OK = 0x0010 | 0x0400
READ_AT = 0x00C3C8F0
READ_N = 120
SCENE_ADDR = 0x00C3C95A
TABLE_ADDR = 0x00C38088
TABLE_REGION = 0x00C30000
TABLE_SIZE = 0x10000
IVP = (0x00C3C8FC - READ_AT) // 2
IPO = (0x00C3C900 - READ_AT) // 2
# ★ 2026-09-30 修正: 队伍朝向 wPartyDirection @0x00C3C958 -> 索引 52 (不是 51!)
#   0x00C3C958 - 0x00C3C8F0 = 104 字节 = 52 个 uint16。
#   佐证: 同窗口 wNumScene @0x00C3C95A -> 索引 53, 实测恒等于当前场景号。
#   旧值 51 指向 0x00C3C956 (另一个字段), 导致 "转身" 永远检测不到
#   (历代日志 "转身0" 的根源), step() 的 TURN_EXT 追加预算从未生效 ->
#   每次改变方向按键被引擎吃成转身时, 0.46s 预算耗尽被误判成 "真墙"。
IPD = (0x00C3C958 - READ_AT) // 2
KEYDK = {"up": (1, 0), "down": (-1, 0), "left": (0, -1), "right": (0, 1)}
DIRKEY = {v: k for k, v in KEYDK.items()}
BASE = os.path.dirname(os.path.abspath(__file__))


def _cfg_float(key, default):
    """取一个浮点配置: 环境变量 PAL_<KEY> > bot_config.json > default。

    mem.py 是底层模块, 不 import botcfg (那是可选依赖), 所以这里容错到底 ——
    配置读不动绝不能让机器人起不来。风格与 pal_bot_mem5.cfg_int 一致。
    """
    try:
        ev = os.environ.get("PAL_" + str(key).upper())
        if ev is not None and str(ev).strip() != "":
            return float(str(ev).strip())
    except Exception:
        pass
    try:
        import botcfg as _BC
        return float(_BC.resolve(key)[0])
    except Exception:
        return float(default)


def find_pal_pid():
    u32 = ctypes.windll.user32
    res = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def cb(hwnd, lparam):
        pid = wt.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        n = u32.GetWindowTextLengthW(hwnd)
        if n:
            b = ctypes.create_unicode_buffer(n + 1)
            u32.GetWindowTextW(hwnd, b, n + 1)
            if "Pal" in b.value:
                res.append((pid.value, b.value))
        return True
    u32.EnumWindows(cb, 0)
    return res


class Mem:
    """内存读取器: 一次 120B 读取拿到 viewport/partyoffset/scene。"""

    def __init__(self):
        wins = find_pal_pid()
        if not wins:
            raise SystemExit("NO_PAL_WINDOW")
        self.pid = sorted({p for p, _ in wins})[0]
        self.h = k32.OpenProcess(OPEN_OK, False, self.pid)
        if not self.h:
            raise SystemExit("OPEN_FAIL")
        buf = (ctypes.c_char * TABLE_SIZE)()
        got = ctypes.c_size_t()
        if not k32.ReadProcessMemory(self.h, ctypes.c_void_p(TABLE_REGION),
                                     buf, TABLE_SIZE, ctypes.byref(got)):
            raise SystemExit("READ_TABLE_FAIL")
        self._tw = struct.unpack('<%dH' % (got.value // 2), buf.raw[:got.value])
        off = (TABLE_ADDR - TABLE_REGION) // 2
        self.scene_map = {}
        for sc in range(1, 297):
            i = off + (sc - 1) * 4
            if i + 3 < len(self._tw):
                self.scene_map[sc] = int(self._tw[i])
        self._buf = (ctypes.c_char * READ_N)()
        self._got = ctypes.c_size_t()

    def raw(self):
        if not k32.ReadProcessMemory(self.h, ctypes.c_void_p(READ_AT),
                                     self._buf, READ_N, ctypes.byref(self._got)):
            return None
        return struct.unpack_from('<%dH' % (READ_N // 2), self._buf.raw)

    def state(self):
        W = self.raw()
        if W is None:
            return None
        vp = W[IVP] | (W[IVP + 1] << 16)
        po = W[IPO] | (W[IPO + 1] << 16)
        px = (vp & 0xFFFF) + (po & 0xFFFF)
        py = (vp >> 16) + (po >> 16)
        return {"scene": W[(SCENE_ADDR - READ_AT) // 2],
                "pal": W[(SCENE_ADDR - READ_AT) // 2 + 1],
                "px": px, "py": py,
                "K": ((px - 2 * py) // 32, (px + 2 * py - 32) // 32)}

    # ------------------------------------------------------------------
    #  只读自检: 硬编码基址在当前游戏进程里是否仍然成立 (v17.2)
    #  动机: 这些地址是"某一次"对某个 exe 标定的。换 exe
    #  (sdlpal.exe <-> sdlpal.hud.exe) 或换版本后, ReadProcessMemory 依然
    #  会"成功", 但读到的是垃圾 —— 机器人会拿着假坐标瞎走几千步。
    #  这里用 4 组互相独立的证据交叉验证, 任何一组异常都要报警。
    # ------------------------------------------------------------------
    MAX_SCRIPT = 43503                 # lprgScriptEntry 条数 (实测)

    def selfcheck(self, report_map=None):
        """返回 (ok, [明细行...])。不按键、不写盘。"""
        out = []
        W = self.raw()
        if W is None:
            return False, ["读取 GLOBALVARS @0x%08X 失败 (进程句柄失效?)" % READ_AT]
        st = self.state()
        if st is None:
            return False, ["state() 解析失败"]
        ok = True
        sc = st["scene"]
        try:
            import psutil
            p = psutil.Process(self.pid)
            out.append("游戏进程 %s (pid %d) %s"
                       % (p.name(), self.pid, p.exe() or ""))
        except Exception:
            pass
        out.append("场景号=%d 队伍像素 P=(%d,%d) K=%s"
                   % (sc, st["px"], st["py"], st["K"]))
        if not (1 <= sc <= 300):
            ok = False
            out.append("★ 场景号 %d 越界 (合理 1..300) -> 基址不对" % sc)
        if not (0 <= st["px"] < 64 * 32 and 0 <= st["py"] < 128 * 16):
            ok = False
            out.append("★ 队伍像素 (%d,%d) 越界 (引擎 x<2048 y<2048) -> 基址不对"
                       % (st["px"], st["py"]))
        po = W[IPO] | (W[IPO + 1] << 16)
        if (po & 0xFFFF, po >> 16) != (160, 112):
            out.append("… partyoffset=(%d,%d), 实测本版恒为 (160,112)"
                       % (po & 0xFFFF, po >> 16))
        # rgScene 表: 296 条里 map/脚本号合理的比例
        off = (TABLE_ADDR - TABLE_REGION) // 2
        good = tot = 0
        for s in range(1, 297):
            i = off + (s - 1) * 4
            if i + 3 >= len(self._tw):
                break
            mv, s1, s2, _ev = (self._tw[i], self._tw[i + 1],
                               self._tw[i + 2], self._tw[i + 3])
            tot += 1
            if mv <= 255 and (s1 <= self.MAX_SCRIPT or s1 == 0xFFFF) \
                    and (s2 <= self.MAX_SCRIPT or s2 == 0xFFFF):
                good += 1
        ratio = (good / float(tot)) if tot else 0.0
        out.append("rgScene 表 296 条中 %d 条结构合理 (%.1f%%)" % (good, ratio * 100))
        if ratio < 0.90:
            ok = False
            out.append("★ 场景表结构合理率 %.1f%% < 90%% -> 基址不对" % (ratio * 100))
        # 与解码报告对照 (最强的证据: map 号是独立来源)
        if report_map and sc in report_map:
            got, exp = self.scene_map.get(sc), report_map[sc]
            out.append("场景%d 地图=%s (解码报告 %s)" % (sc, got, exp))
            if got != exp:
                ok = False
                out.append("★ 场景%d 地图号与解码报告不一致 -> 基址不对" % sc)
        return ok, out


class MemBot:
    # ★ 2026-09-30: 只读诊断工具 (pilot_probe / door_audit / dryrun5) 置 True。
    #   否则 log() 会懒加载打开 bot_log_mem.txt 追加, 把诊断输出写进**正在跑的
    #   机器人**的日志里; 而 run_report.py 用「[v13] 战斗判定」定位运行段,
    #   会被这些假头部骗出一条假的「运行段」, 归因跟着走偏。
    NO_LOGFILE = False
    RADIUS = 28          # 单场景探索半径(格)
    STEP_TIMEOUT = 0.55
    TAP = 0.09
    # ---- 像素阈值: 一律用"面积比例"表达, 运行时 px_thr() 换算成像素个数 ----
    #   (绝对值 = 标定窗口 1687x1294 下的实测值; 写字面量会被窗口缩放带偏)
    F_ANIM_MIN = wingeom.FRAC["anim_min"]            # 300    画面在动
    F_MENU_CHG = wingeom.FRAC["menu_chg"]            # 1500   esc 后画面变了
    F_WALL = wingeom.FRAC["wall"]                    # 2500   完全静止 = 真墙
    F_ANIM_BIG = wingeom.FRAC["anim_big"]            # 8000   攻击/施法动画
    F_BATTLE_TRANS = wingeom.FRAC["battle_trans"]    # 150000 全屏级 = 战斗转场
    # ---- 生路偏好 (2026-10-04): 寻路时给"走过很多次的格"加软代价 ----
    #   为什么: reach() 是无权 BFS, 代价恒为 1, 所以两条怪点之间**永远走同一条
    #   最短路** —— 同一条走廊被反复踩, 明明旁边有等长的路却一次也不走。
    #   现在代价 = 1 + REVISIT_LAMBDA × min(visit_cnt, REVISIT_CAP):
    #     全没走过 (visit_cnt=0) 时权重恒为 1 -> 与历史版本**逐位一致**。
    #     REVISIT_LAMBDA=0 可当逃生开关, 直接走老的纯 BFS 分支。
    #   调参: 加大 λ = 更愿意绕远走生路; 加大 CAP = 更不在意"到底走过几次"。
    REVISIT_LAMBDA = 0.15
    REVISIT_CAP = 4

    def __init__(self, max_battles=None, max_seconds=None, stall_limit=1200):
        self.mem = Mem()
        self.g = PB.Game(PB.find_game_window()[0])
        self.max_battles = max_battles
        self.max_seconds = max_seconds
        self.stall_limit = stall_limit
        self.battles_won = 0
        self.last_win = time.time()
        self.t0 = time.time()
        self.log_lines = 0
        # 解码数据
        self.grids = np.load(os.path.join(BASE, "map_walk.npy"))
        txt = open(os.path.join(BASE, "gyt_scene_report.txt"),
                   encoding="utf-8", errors="ignore").read()
        self.report_map = {}
        for m in re.finditer(r"场景\s*(\d+)\s+地图\s*(\d+)", txt):
            self.report_map[int(m.group(1))] = int(m.group(2))
        self.report_doors = {}
        for m in re.finditer(r"场景\s*(\d+)\s+对象\s*\d+\s+px\(\s*(\d+),\s*(\d+)\)"
                             r"\s+mode=\d+", txt):
            sc = int(m.group(1))
            px, py = int(m.group(2)), int(m.group(3))
            self.report_doors.setdefault(sc, set()).add(
                ((px - 2 * py) // 32, (px + 2 * py - 32) // 32))
        self._kwalk_cache = {}
        # 运行状态 (按场景持久: 踩过的门/被挡格/访问格/起点, 换图往返不丢)
        self.scene_know = {}
        self.OPP = {"up": "down", "down": "up", "left": "right", "right": "left"}
        self.scene = None
        self.map = None
        self.doors = set()           # 当前场景门格 (预取±2圈+学习±1圈)
        self.blocked = set()         # 实测被挡格 (数据说可走但走不动)
        self.visited = set()
        # ★ 2026-10-04 「走过几次」台账: 格 -> 踏上次数, **按场景**存
        #   (enter_scene 里绑到 scene_know[sc]["visit_cnt"])。
        #   用途: ① 巡逻层「访问次数最少的一档」 ② _weighted_path 的生路偏好
        #        ③ 怪点/宝箱选点的轮转 ④ 定期探索配额。
        #   ⚠ 别退回"全局只按 K 键" —— 不同场景共用 K 空间, 计数会串味。
        self.visit_cnt = {}
        # 生路偏好强度 (环境变量 PAL_REVISIT_LAMBDA / bot_config.json)
        #   λ=0 -> bfs_path 走老的纯 BFS 分支, 行为与历史版本逐位一致。
        self.REVISIT_LAMBDA = _cfg_float("revisit_lambda", self.REVISIT_LAMBDA)
        try:
            self.REVISIT_CAP = int(_cfg_float("revisit_cap", self.REVISIT_CAP))
        except Exception:
            pass
        self.startK = None
        self.steps = 0
        self.steps_since_battle = 0
        self.migrate = False
        self.MIGRATE_AFTER = 120
        self.migrate_cd = 0
        self.MIGRATE_COOLDOWN = 600.0     # 两次迁移尝试的最小间隔(防死循环刷屏)
        self.known_doors = self.load_doors()
        self._door_fail = {}         # 门K -> 连续"踩了不触发"次数 (换下一个门试)
        self._mig_why = ""           # 上次迁移未果的原因 (写日志用)
        self.last_beat = time.time()
        # ---- 困区逃逸 (2026-09-29 熔岩走廊实测) ----
        # 现象: 数据把链条/浮桥表面标为可走, 引擎却只在特定点让上下桥,
        # 一片「数据可走但实测被挡」的假通路把队伍困在 ~10 格小区域里,
        # 自愈清 2 条 -> 又撞 -> 又记, 无限循环 (单场景 3847 次实测被挡)。
        self._trace = []             # [(t, K)] 走路采样, 供困区判定
        self._trap_last = 0.0        # 上次困区检查时刻
        self._trap_cd_until = 0.0    # 困区逃逸冷却
        self._trap_goal = None       # (scene, 突围目标K, 截止时刻)
        self._ban_warned = False     # 升级机制失效告警只打一次
        # ---- 窗口几何 (v17.2): 像素级常量的唯一来源, 见 wingeom.py ----
        self._geo_cache = None
        self._geo_t = 0.0
        self._geo_note = ""

    def log(self, msg):
        t = time.strftime("%H:%M:%S")
        line = "%s %s\n" % (t, msg)
        try:
            print(line, end="", flush=True)
        except Exception:
            pass
        if self.NO_LOGFILE:          # ★ 只读诊断工具: 只到 stdout, 不落盘
            return
        try:
            if getattr(self, "_logfh", None) is None:
                self._logfh = open(os.path.join(BASE, "bot_log_mem.txt"), "a",
                                   encoding="utf-8", buffering=1)
            self._logfh.write(line)
        except Exception:
            pass

    # ---- 窗口几何 (v17.2) ----
    #  所有"看图"用的像素常量都从这里取, 不再硬编码。
    #  在标定窗口 (1687x1294) 下取值与旧硬编码**逐位相同** (见 test_wingeom.py)。
    def geo(self):
        now = time.time()
        if self._geo_cache is None:
            try:
                self._geo_cache = self.g.geom(refresh=True)
            except Exception:
                self._geo_cache = wingeom.legacy()
            self._geo_t = now
            self._geo_note = wingeom.describe(self._geo_cache)
        elif now - self._geo_t > 30.0:
            # 用户可能中途拖过边框: 尺寸没变则零开销, 变了就重新标定
            self._geo_t = now
            try:
                old = self._geo_cache
                new = self.g.geom_if_changed()
                if new is not old:
                    self.log("[几何] 窗口尺寸变化 -> 重新标定: %s"
                             % wingeom.describe(new))
                self._geo_cache = new
            except Exception:
                pass
        return self._geo_cache

    def px_thr(self, frac):
        """面积比例 -> 当前截图下的像素个数阈值 (绝对阈值随窗口缩放自动换算)。"""
        return wingeom.px_thr(frac, wingeom.area(self.geo()))

    def band0(self):
        """is_battle 波段顶 (像素行); 窗口缩放后仍需圈住视口底部。"""
        return wingeom.band_top(self.geo())

    # ---- 场景/地图 ----
    def kwalk(self, m):
        if m not in self._kwalk_cache:
            grid = self.grids[m]
            s = set()
            for x in range(64):
                for y in range(128):
                    if x < 5 or x > 57 or y < 7:
                        # ★ 引擎移动判定自带边界检查 (game.c fCheckRange):
                        #   x < partyoffset.x/32(5) 或 x >= 64-5-1 或 y < 112/16(7)
                        #   一律视为阻挡。这些格子数据说可走但引擎必挡,
                        #   不排除会让 BFS 规划出永远走不进去的边缘路。
                        continue
                    row = grid[y, x]
                    for hh in (0, 1):
                        if not row[hh]:
                            s.add((x - y, x + y - 1 + hh))
            self._kwalk_cache[m] = s
        return self._kwalk_cache[m]

    ESCALATE_AFTER = 3           # 同一格短窗口内被挡 N 次 -> 升级长期禁入
    ESCALATE_WINDOW = 600.0      # 升级计数窗口 (秒); 超窗重新计数, 防误封游走NPC路过格
    ESCALATE_TTL = 1800.0        # 长期禁入持续时间 (秒), 到期自动解除
    EXIT_BAN_TTL = 300.0         # 「仅剩出口」格被反复撞后的短禁入时长(秒)

    def _exit_count(self, cur):
        """当前位置还剩几个可走出口 (4 邻中在可走域且未被禁的格数)。"""
        try:
            ks = self.kwalk(self.map) if self.map is not None else set()
        except Exception:
            return 99
        n = 0
        for dd in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            t = (cur[0] + dd[0], cur[1] + dd[1])
            if t in ks and t not in self.blocked:
                n += 1
        return n

    def _ban_escalate(self, cell):
        """被挡升级: 同一格 10 分钟内被挡 3 次, 多为 NPC/怪等持久阻挡物,
        升级为长期禁入 (把 AgedSet 时间戳推到未来, 绕过 TTL 与孤岛自愈,
        30 分钟后自动解除 —— 临时阻挡物走后地图自动复原)。"""
        hits = getattr(self, "blocked_hits", None)
        if hits is None or not hasattr(self.blocked, "ts"):
            if not getattr(self, "_ban_warned", False):
                self._ban_warned = True
                self.log("[绕行] 警告: 升级机制不可用 (blocked_hits=%s, "
                         "blocked=%s) -> 撞墙知识只会 70s 过期, 不会升级"
                         % (type(hits).__name__, type(self.blocked).__name__))
            return
        # ★ 出口保护 v2 (2026-09-29 桥区实测教训): 原来对"仅剩出口"直接放行
        #   (绝不升级) —— 但锁妖塔窄桥上相邻格几乎全是"仅剩出口", 每格每
        #   BLOCK_TTL(70s) 就重撞一次 (单场景日志 384 次「仅剩的出口」),
        #   与 RESET 清绕行形成死循环 -> 吞吐 0.03 格/秒。
        #   现改为「短禁入」: 同格 3 次被挡 -> 只禁 EXIT_BAN_TTL(5 分钟, 非
        #   30 分钟长期禁入); 禁完若当前位置一个出口都不剩, 立即撤销 ——
        #   移动能力仍然优先, 但"此路不通"能被记住 5 分钟。
        try:
            cur = self.mem.state()["K"]
        except Exception:
            cur = None
        if cur is not None and \
                max(abs(cell[0] - cur[0]), abs(cell[1] - cur[1])) <= 1 and \
                self._exit_count(cur) <= 1:
            now2 = time.time()
            cnt, last = hits.get(cell, (0, 0.0))
            if now2 - last > self.ESCALATE_WINDOW:
                cnt = 0
            cnt += 1
            hits[cell] = (cnt, now2)
            if cnt >= self.ESCALATE_AFTER:
                if cell not in self.blocked:
                    self.blocked.add(cell)
                self.blocked.ts[cell] = now2 + self.EXIT_BAN_TTL
                self._pc_key = None
                if self._exit_count(cur) == 0:
                    # 真的会把自己锁死 -> 撤销 (discard 会连 ts 一起清,
                    # 下次被挡重新计数; 效果退化为旧行为, 不会更糟)
                    self.blocked.discard(cell)
                    self.log("[绕行] K%s 短禁入会让当前位置无路可走 -> 撤销"
                             % (cell,))
                else:
                    self.log("[绕行] K%s 仅剩出口但 %d 分钟内被挡 %d 次 "
                             "-> 短禁入 %.0f 分钟"
                             % (cell, self.ESCALATE_WINDOW // 60, cnt,
                                self.EXIT_BAN_TTL // 60))
            return
        now = time.time()
        cnt, last = hits.get(cell, (0, 0.0))
        if now - last > self.ESCALATE_WINDOW:
            cnt = 0
        cnt += 1
        hits[cell] = (cnt, now)
        if cnt >= self.ESCALATE_AFTER and self.blocked.ts.get(cell, 0) < now + 60:
            if cell not in self.blocked:
                self.blocked.add(cell)
            self.blocked.ts[cell] = now + self.ESCALATE_TTL
            self._pc_key = None
            self.log("[绕行] K%s %d 分钟内被挡 %d 次 -> 长期禁入 %.0f 分钟"
                     " (多为 NPC/怪挡路)"
                     % (cell, self.ESCALATE_WINDOW // 60, cnt,
                        self.ESCALATE_TTL // 60))

    def _is_perm(self, cell):
        """该格是否处于"长期禁入"状态 (时间戳被推到远期)。"""
        ts = getattr(self.blocked, "ts", None)
        return bool(ts) and ts.get(cell, 0) > time.time() + 60

    def _release_perm_near(self, cur, radius=5):
        """死锁自救: 解除当前位置附近的长期禁入格。
        窄路上两个出口都被长期禁入 -> 完全走不出去, 此时移动优先于避让。"""
        if not hasattr(self.blocked, "ts"):
            return 0
        hits = getattr(self, "blocked_hits", None)
        n = 0
        for c in list(self.blocked):
            if not self._is_perm(c):
                continue
            if max(abs(c[0] - cur[0]), abs(c[1] - cur[1])) <= radius:
                self.blocked.discard(c)
                if hits is not None:
                    hits.pop(c, None)      # 计数清零, 否则下次被挡立刻又升级
                n += 1
        if n:
            self._pc_key = None
        return n

    def _clear_blocked(self, keep_perm=False):
        """清空绕行记录 (含长期禁入)。返回清掉的条数。"""
        hits = getattr(self, "blocked_hits", None)
        n = 0
        for c in list(self.blocked):
            if keep_perm and self._is_perm(c):
                continue
            self.blocked.discard(c)
            if hits is not None:
                hits.pop(c, None)
            n += 1
        if n:
            self._pc_key = None
        return n

    def enter_scene(self, sc, why=""):
        self.scene = sc
        self.map = self.mem.scene_map.get(sc)
        self._trace = []             # 换场景后旧坐标没有可比性
        self._trap_goal = None
        st = self.scene_know.get(sc)
        cur = self.mem.state()["K"]
        if st is None:
            # 预取门: 报告对象坐标与真实触发格有 ~2 格系统偏差(实测), 外扩 ±2 圈
            doors = set()
            for (da, db) in self.report_doors.get(sc, set()):
                for ua in (-2, -1, 0, 1, 2):
                    for ub in (-2, -1, 0, 1, 2):
                        doors.add((da + ua, db + ub))
            st = {"doors": doors, "blocked": set(), "visited": set(),
                  "startK": cur, "warned": False, "blocked_hits": {}}
            self.scene_know[sc] = st
        self.doors = st["doors"]
        self.blocked = st["blocked"]
        self.visited = st["visited"]
        # ★ 2026-10-04: 「走过几次」按场景绑定。用 setdefault 而不是 st[...] ——
        #   handle_door/开门模式会用 setdefault(sc, {"doors": set()}) 造出
        #   只带 doors 的残缺 st, 老场景回来时不会走上面的创建分支。
        self.visit_cnt = st.setdefault("visit_cnt", {})
        self.startK = st["startK"]
        self.blocked_hits = st.setdefault("blocked_hits", {})
        ks = self.kwalk(self.map) if self.map is not None else set()
        ok = cur in ks
        self.log("[MEM] 场景%d 地图%s(报告%s) K%s 门禁%d格 可走域%d格 落点可走=%s %s"
                 % (sc, self.map, self.report_map.get(sc), cur, len(self.doors),
                    len(ks), ok, why))
        if not ok and not st["warned"]:
            st["warned"] = True
            self.log("[MEM] 警告: 落点不在解码可走域, 继续观察")

    # ---- 步进 ----
    def step(self, key):
        """按一个方向键, 用内存真值判定结果。返回 (moved/blocked/door, K1, scene1)。"""
        s0 = self.mem.state()
        K0, sc0 = s0["K"], s0["scene"]
        self.g.tap(key, self.TAP)
        t1 = time.time()
        while time.time() - t1 < self.STEP_TIMEOUT:
            s1 = self.mem.state()
            if s1 is None:
                time.sleep(0.05)
                continue
            if s1["scene"] != sc0:
                return ("door", K0, s1["K"], s1["scene"])
            if s1["K"] != K0:
                # 等待位移到位 (最多再 0.2s)
                t2 = time.time()
                last = s1
                while time.time() - t2 < 0.2:
                    s2 = self.mem.state()
                    if s2 and (s2["scene"] != sc0 or s2["K"] != last["K"]):
                        if s2["scene"] != sc0:
                            return ("door", K0, s2["K"], s2["scene"])
                        last = s2
                    time.sleep(0.012)
                return ("moved", K0, last["K"], last["scene"])
            time.sleep(0.012)
        return ("blocked", K0, K0, sc0)

    def fight(self):
        """战斗处理 (照搬验证过的 v11 逻辑): F 连击直到脱战, 3 次确认。"""
        self.log("[战斗] 交战, 按 %s 最强法术" % PB.attack_key())
        t0 = time.time()
        fought = False
        while time.time() - t0 < 180.0:
            if not PB.is_battle(self.g.shot()):
                ok = 0
                for _ in range(3):
                    time.sleep(0.45)
                    if PB.is_battle(self.g.shot()):
                        break
                    ok += 1
                if ok >= 3:
                    break
                continue
            fought = True
            t0 = time.time()                 # 还在打: 重置超时
            self.g.attack()
            time.sleep(1.1)
        self.g.flush_keys()
        if fought:
            self.battles_won += 1
            self.last_win = time.time()
            self.steps_since_battle = 0
            self.log(">>> 第 %d 场战斗胜利!" % self.battles_won)
        else:
            self.log("[战斗] 未确认实际交战, 继续巡逻")

    def maybe_fight(self):
        a = self.g.shot()
        if PB.is_battle(a):
            self.fight()
            return True
        if PB.is_black(a):
            self._black_n = getattr(self, "_black_n", 0) + 1
            if self._black_n % 10 == 1:
                self.log("[MEM] 黑屏中 (%d 次), 等待恢复" % self._black_n)
            if self._black_n >= 20:            # ~10s 仍黑: 按空格/ESC 恢复
                self.g.key("space")
                self.g.key("esc")
                self.log("[MEM] 黑屏过久, 已发 space/esc 尝试恢复")
                self._black_n = 0
            time.sleep(0.5)
            return True
        self._black_n = 0
        return False

    # ---- 路径 ----
    def bfs_path(self, src, dsts, extra_blocked=frozenset(), allow_doors=False,
                 relax_blocked=False):
        """解码可走域 BFS: 从 src 到 dsts 中任一格的最短路 (门格/实测挡格除外)。

        relax_blocked=True: 忽略「绕行」标记再算一次 (mem2 覆写版同款语义)。
        """
        ks = self.kwalk(self.map)
        blk = set() if relax_blocked else self.blocked
        bad = blk | set(extra_blocked) if allow_doors else self.doors | blk | set(extra_blocked)
        dsts = set(dsts) - bad
        if not dsts or src not in ks:
            return None
        prev = {src: None}
        q = [src]
        head = 0
        while head < len(q):
            p = q[head]
            head += 1
            if p in dsts:
                path = []
                while p is not None:
                    path.append(p)
                    p = prev[p]
                return path[::-1]
            for d in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                t = (p[0] + d[0], p[1] + d[1])
                if t in ks and t not in bad and t not in prev:
                    prev[t] = p
                    q.append(t)
        return None

    # ---- 加权最短路 (生路偏好, 2026-10-04) ----
    def _weighted_path(self, src, dst, bad, lam):
        """Dijkstra 单目标最短路, 代价 = 1 + lam×min(visit_cnt, REVISIT_CAP)。

        为什么要有它: reach()/BFS 是无权的, 两条怪点之间**永远走同一条最短路**,
        旁边等长的路一次也不走。给"走过很多次的格"加软代价之后, 只有等长或
        近似长度的生路才会被选中 —— 目标选择完全不变, 所以刷怪密度不受影响。

        visit_cnt 全空 (开局/影子对象) 时权重恒为 1 -> 与纯 BFS 同解。
        目标就是"要走过去的那一格", 所以一旦弹出 dst 就早退 —— 近距离目标
        比老的整图泛洪还便宜。
        """
        ks = self.kwalk(self.map)
        if src not in ks or dst in bad or dst not in ks:
            return None
        vc = getattr(self, "visit_cnt", None) or {}
        cap = self.REVISIT_CAP
        prev = {src: None}
        dist = {src: 0.0}
        heap = [(0.0, src)]
        while heap:
            d0, p = heapq.heappop(heap)
            if d0 > dist.get(p, 1e18):
                continue                     # 堆里的陈旧条目
            if p == dst:
                path = []
                while p is not None:
                    path.append(p)
                    p = prev[p]
                return path[::-1]
            for d in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                t = (p[0] + d[0], p[1] + d[1])
                if t not in ks or t in bad:
                    continue
                nd = d0 + 1.0 + lam * min(vc.get(t, 0), cap)
                if nd < dist.get(t, 1e18) - 1e-9:
                    dist[t] = nd
                    prev[t] = p
                    heapq.heappush(heap, (nd, t))
        return None

    def walk_path(self, path):
        """沿路径走, 每步内存核实; 偏离/被挡返回 False (调用方重规划)。"""
        for i in range(1, len(path)):
            # ★ 2026-09-26 修复: 长路径中途也要能响应停止
            #   (原来只在 run() 主循环/fight 循环检测, 跨场景迁移上千步
            #    期间放 mem_stop.flag 要等路径走完才生效, 数分钟不响应)
            if os.path.exists(os.path.join(BASE, "mem_stop.flag")):
                self.log("[MEM] walk_path 检测到 mem_stop.flag -> 中止路径")
                return False
            cur = self.mem.state()["K"]
            # 困区逃逸的采样: 每步(含被挡步)都记录位置, 6 分钟窗口
            self._trace.append((time.time(), cur))
            if len(self._trace) > 4000:
                del self._trace[:2000]
            if cur != path[i - 1]:
                return False                     # 位置漂移(战斗/意外): 重规划
            d = (path[i][0] - cur[0], path[i][1] - cur[1])
            key = DIRKEY.get(d)
            if key is None:
                return False
            st, K0, K1, sc1 = self.step(key)
            if st == "door":
                self.handle_door(K0, sc1, key)
                return False
            if st != "moved" or K1 != path[i]:
                if st == "blocked":
                    if self.maybe_fight():       # 可能是遇敌而非墙
                        return False
                    if time.time() - getattr(self, "_last_move_t", 0) < 90:
                        if path[i] == getattr(self, "_chase_goal", None):
                            # ★ 追怪目标格: 走不进去多半是怪本身挡路(对象碰撞),
                            #   地图数据是对的 -> 记绕行会误封目标, 反复升级成死锁
                            self.log("[追怪] 目标格 %s 走不进去 (怪挡路?) -> 不记绕行"
                                     % (path[i],))
                        else:
                            self.blocked.add(path[i])
                            self._ban_escalate(path[i])
                    self.log("[MEM] 实测被挡(数据说可走): %s -> 记入绕行" % (path[i],))
                return False
            self.visited.add(K1)
            # ★ 2026-10-04 「走过几次」在这里记账 —— 只有**真的踏上去**才 +1。
            #   以前这行只在 MemBot4.pick_target 里, 而主模式(要素巡回)压根不走
            #   那个分支 -> visit_cnt 在主模式下永远是空的, 所有"避重/探索"信号
            #   全是死的。getattr 兜底: 裸影子对象没有这个字段。
            _vc = getattr(self, "visit_cnt", None)
            if _vc is not None:
                _vc[K1] = _vc.get(K1, 0) + 1
            self.steps += 1
            self._last_move_t = time.time()
            self.steps_since_battle += 1
            if time.time() - self.last_beat > 20:
                self.last_beat = time.time()
                self.log("[心跳] 场景%d K%s 步数%d 胜%d场 用时%.0fs"
                         % (self.scene, K1, self.steps, self.battles_won,
                            time.time() - self.t0))
        return True

    def handle_door(self, K_door, new_scene, key):
        """意外踩门: 先按来向的反向键试退回一次; 退不回就接受新场景继续。
        门格±1圈永久记入该场景门禁 (跨换图持久, 防乒乓)。"""
        self.log("[MEM] 踩门! K%s 场景 %d -> %d (来向 %s)" % (K_door, self.scene,
                                                             new_scene, key))
        st = self.scene_know.setdefault(self.scene, {"doors": set()})
        for ua in (-1, 0, 1):
            for ub in (-1, 0, 1):
                st["doors"].add((K_door[0] + ua, K_door[1] + ub))
        self.remember_door(self.scene, K_door)
        old_scene = self.scene
        if self.migrate:
            self.log("[MEM] 迁移穿门 -> 接受新场景 %d" % new_scene)
            self.enter_scene(new_scene, "(迁移穿门)")
            return
        st2, K0, K1, sc1 = self.step(self.OPP[key])
        if st2 == "door":
            # 反向也是门: 把那格也记入门禁, 接受新场景
            stn = self.scene_know.setdefault(old_scene, {"doors": set()})
            for ua in (-1, 0, 1):
                for ub in (-1, 0, 1):
                    stn["doors"].add((K0[0] + ua, K0[1] + ub))
        if sc1 == old_scene:
            self.log("[MEM] 已退回原场景 %d, 门格 %s ±1 永久禁入" % (old_scene, K_door))
            self.enter_scene(old_scene, "(退回)")
            return
        self.log("[MEM] 退回失败, 接受新场景 %d 继续探索" % new_scene)
        self.enter_scene(new_scene, "(穿门)")

    # ---- 主循环 ----
    def pick_target(self, exclude=None):
        """目标: 半径内 (a+b) 最大且未访问的可走格 (深入+刷怪)。"""
        ks = self.kwalk(self.map)
        bad = self.doors | self.blocked
        cur = self.mem.state()["K"]
        cands = []
        for (a, b) in ks:
            if max(abs(a - self.startK[0]), abs(b - self.startK[1])) > self.RADIUS:
                continue
            if (a, b) in bad:
                continue
            cands.append((a, b))
        unvis = [c for c in cands if c not in self.visited]
        pool = unvis or cands
        if exclude is not None:
            pool = [c for c in pool if c != exclude] or \
                   [c for c in cands if c != exclude]
        if not pool:
            return None
        pool.sort(key=lambda c: (-(c[0] + c[1]), max(abs(c[0] - cur[0]),
                                                     abs(c[1] - cur[1]))))
        # 从"最深"的 30% 里挑最近的, 兼顾深入与少走回头路
        top = pool[:max(1, len(pool) * 3 // 10)]
        top.sort(key=lambda c: max(abs(c[0] - cur[0]), abs(c[1] - cur[1])))
        return top[0]

    # ---- 迁移 (跨场景找刷怪区) ----
    def load_doors(self):
        import json
        p = os.path.join(BASE, "mem_doors.json")
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            return {int(k): {tuple(x) for x in v} for k, v in d.items()}
        except Exception:
            return {}

    def save_doors(self):
        import json
        p = os.path.join(BASE, "mem_doors.json")
        try:
            with open(p, "w", encoding="utf-8") as f:
                json.dump({str(k): sorted(list(v)) for k, v in
                           self.known_doors.items()}, f)
        except Exception as e:
            self.log("[MEM] 门知识保存失败: %s" % e)

    def remember_door(self, sc, K):
        self.known_doors.setdefault(sc, set()).add(K)
        self.save_doors()

    # ★ 2026-09-30: 门距上限写死 80 会把"角落的门"静默排除。
    #   场景 157 实测: 用户指认的"右下角出口门" K(-96,132) 距起点 82 格,
    #   于是迁移永远只去 K(-21,63)/K(30,66) 两个门, 那两个门踩了不触发 ->
    #   一整轮 13 次换场景尝试 0 成功, 白烧约 20% 的日志活动。
    MIGRATE_DOOR_MAX = 200
    DOOR_FAIL_MAX = 2         # 同一门连续失败 N 次 -> 先换下一个门试

    def pick_migrate_door(self, cur):
        """迁移目标门: 预取门 + 历史实测门(跨运行持久), 取最近且有可走邻格的。

        连续失败 DOOR_FAIL_MAX 次的门先跳过; 全都被跳过时清空失败史重来
        (避免把用户真正想去的门永久拉黑)。
        """
        def _d(d):
            return max(abs(d[0] - cur[0]), abs(d[1] - cur[1]))
        ks = self.kwalk(self.map)
        cands = list(set(self.report_doors.get(self.scene, ())) |
                     set(self.known_doors.get(self.scene, ())))
        cands = [d for d in cands if _d(d) <= self.MIGRATE_DOOR_MAX]
        cands.sort(key=_d)
        fail = getattr(self, "_door_fail", None)
        if fail is None:
            fail = self._door_fail = {}
        ok = [d for d in cands if fail.get(d, 0) < self.DOOR_FAIL_MAX]
        if not ok and cands:
            fail.clear()
            ok = cands
        for d in ok:
            if any((d[0] + da, d[1] + db) in ks
                   for da in (-1, 0, 1) for db in (-1, 0, 1)):
                return d
        return None

    def migrate_probe(self, doorK):
        """走到门 K 的可走邻格, 再在 ±2 邻域内逐格试踩门。返回是否触发换图。

        ★ 2026-09-30: 失败原因必须写日志。原实现静默返回 False, 结果 10 次
          "门未触发" 在 1 秒内刷完 (根本没走路), 完全看不出卡在哪一步。
        """
        ks = self.kwalk(self.map)
        self._mig_nopath = False
        near = [(a, b) for (a, b) in ks
                if abs(a - doorK[0]) <= 2 and abs(b - doorK[1]) <= 2
                and (a, b) not in self.blocked]
        if not near:
            self._mig_why = "门外 ±2 无可走格 (全被绕行禁掉?)"
            self._mig_nopath = True
            return False
        cur0 = self.mem.state()["K"]
        near.sort(key=lambda c: max(abs(c[0] - cur0[0]), abs(c[1] - cur0[1])))
        n_nopath = 0
        n_tried = 0
        for cell in near:
            cur = self.mem.state()["K"]
            if cur != cell:
                path = self.bfs_path(cur, {cell}, allow_doors=True)
                if path is None:
                    n_nopath += 1
                    continue
                if not self.walk_path(path):
                    n_nopath += 1
                    continue
            n_tried += 1
            for d in ("up", "right", "down", "left"):
                st, K0, K1, sc1 = self.step(d)
                if st == "door":
                    self.remember_door(self.scene, K0)
                    self.enter_scene(sc1, "(迁移穿门)")
                    return True
                if st == "moved":
                    st2, _, _, sc2 = self.step(self.OPP[d])
                    if st2 == "door":
                        self.remember_door(self.scene, K0)
                        self.enter_scene(sc2, "(迁移穿门)")
                        return True
                    if sc2 != self.scene:
                        self.enter_scene(sc2, "(迁移穿门)")
                        return True
        self._mig_nopath = (n_tried == 0)   # 一次键都没按 -> 不算「试过」
        self._mig_why = ("到了门外 %d 格逐个踩 4 方向都没换图" % n_tried
                         if n_tried else "门外 %d 格全走不进去 (无路/被挡)" % n_nopath)
        return False

    def _migrate_failed(self, door, why):
        """迁移未果: 记该门失败次数 + 通知调度器退避 (覆写点)。"""
        fail = getattr(self, "_door_fail", None)
        if fail is None:
            fail = self._door_fail = {}
        fail[door] = fail.get(door, 0) + 1
        self.log("[MEM] 迁移: 门 K%s 记失败 %d 次 (%s)"
                 % (door, fail[door], why))

    def _on_migrate_end(self, success):
        """迁移分支收尾钩子 (覆写点)。success=False -> 子类可让调度器退避。"""
        pass


    def tick(self):
        cur = self.mem.state()["K"]
        if self.migrate:
            # 注意: 探测期间保持 migrate=True, 这样途中踩到门由 handle_door 走
            # "迁移穿门 -> 接受新场景" 分支, 否则迁移永远被退回逻辑取消。
            self.log("[MEM] 迁移: %d 步无战斗, 穿门找刷怪区" % self.steps_since_battle)
            sc0 = self.scene
            door = self.pick_migrate_door(cur)
            ok_mig = False
            if door is None:
                self.log("[MEM] 迁移: 本场景无已知门, 放弃本轮")
                self.migrate = False
            else:
                self.log("[MEM] 迁移: 目标门 K%s" % (door,))
                if self.migrate_probe(door):
                    self.log("[MEM] 迁移成功: 场景 %d -> %d" % (sc0, self.scene))
                    self.migrate = False
                    self._mtries = 0
                    ok_mig = True
                else:
                    why = getattr(self, "_mig_why", "未知")
                    if getattr(self, "_mig_nopath", False):
                        # ★ 2026-09-30: 一次键都没按 (门邻格全部 bfs 无路;
                        #   典型是可达域被误记的「被挡」压塌了) -> 不叫「试过一次」。
                        #   旧实现照样计数, 于是 10 次「重试」在 1 秒内刷完
                        #   (实测 12:03:15 / 12:06:10 / 12:09:07 各 x10, 一步都没走),
                        #   日志看着像在重试, 实际是空转。
                        self.log("[MEM] 迁移: 门 K%s 当前不可达 (原因: %s)"
                                 " -> 放弃本轮 (未按键, 不计入重试)" % (door, why))
                        self._migrate_failed(door, why)
                        self._mtries = 0
                        self.migrate = False
                    else:
                        self._mtries = getattr(self, "_mtries", 0) + 1
                        self.log("[MEM] 迁移: 门未触发(第%d次), 跨轮继续逼近；原因: %s"
                                 % (self._mtries, why))
                        self._migrate_failed(door, why)
                        if self._mtries < 10:
                            return              # 保持 migrate=True, 下一轮接着逼近
                        self.log("[MEM] 迁移: 尝试%d次未果, 进入冷却" % self._mtries)
                        self._mtries = 0
                        self.migrate = False
            if not self.migrate:
                # ★ 只有"没换成"才进冷却。成功换场景由新场景自己重新判 (原实现
                #   无条件设冷却, 但除了 NO_MONSTER 分支没人读它, 所以无影响;
                #   现在 _pilot_tick 会读, 必须区分成功/失败, 否则换到好场景反而
                #   被锁 10 分钟不让走)。
                if not ok_mig:
                    self.migrate_cd = time.time() + self.MIGRATE_COOLDOWN
                self.steps_since_battle = 0
                self._on_migrate_end(ok_mig)      # 覆写点: 失败则让调度器退避
            return
        tgt = self.pick_target()
        if tgt is None:
            self.log("[MEM] 半径内无可走格, 原地等待")
            time.sleep(1.0)
            return
        if tgt == cur:
            tgt = self.pick_target(exclude=cur)
            if tgt is None:
                self.log("[MEM] 半径内无处可去 -> 就地等待刷怪")
                time.sleep(1.0)
                return
        path = self.bfs_path(cur, {tgt})
        if path is None or len(path) < 2:
            if tgt != getattr(self, "_chase_goal", None):
                self.blocked.add(tgt)
            self._spin = getattr(self, "_spin", 0) + 1
            if self._spin % 80 == 0:
                # 绕行知识过密把局部地图糊死 -> 清掉周边污染, 换锚点重新测绘
                # ★ 2026-09-29 桥区教训: 原来无差别清除 —— 被反复实测挡住
                #   的真知识也被清掉 -> BFS 又说能走 -> 再撞 -> 再 RESET,
                #   单场景 174 次 RESET / 0.03 格/秒。现改为:
                #   ① 2 分钟内不重复 RESET  ② 长期/短期禁入格(升级过的)不清
                #   ③ 60 秒内被挡 ≥2 次的格(可能是菜单毒化, 但更可能是真挡)不清
                now_r = time.time()
                if now_r - getattr(self, "_last_reset_t", 0.0) < 120.0:
                    self._spin = 0
                else:
                    self._last_reset_t = now_r
                    cur = self.mem.state()["K"]
                    hits = getattr(self, "blocked_hits", {})
                    cleared = 0
                    kept = 0
                    for c in list(self.blocked):
                        if max(abs(c[0] - cur[0]), abs(c[1] - cur[1])) > 16:
                            continue
                        if self._is_perm(c):
                            kept += 1
                            continue
                        if hits.get(c, (0, 0.0))[0] >= 2:
                            kept += 1
                            continue
                        self.blocked.discard(c)
                        cleared += 1
                    self.startK = cur
                    self.visited = {c for c in self.visited
                                    if max(abs(c[0] - cur[0]),
                                           abs(c[1] - cur[1])) <= 6}
                    self._spin = 0
                    self.log("[MEM] RESET 局部绕行知识 -> 清 %d 格(保留真知识 "
                             "%d 格) 重新测绘 K%s" % (cleared, kept, cur))
            time.sleep(0.15)          # 防 100% CPU 空转 (曾被宿主判异常清掉)
            return
        if not self.walk_path(path):
            time.sleep(0.1)

    def menu_close(self):
        """esc 是打开/关闭系统菜单的开关: 连按两次以确保最终处于关闭态。"""
        try:
            self.g.key("esc")
            time.sleep(0.35)
            self.g.key("esc")
            time.sleep(0.35)
            self.g.key("space")
        except Exception:
            pass

    def startup_selfcheck(self):
        """启动自检: 若四方向全被挡(菜单/对话卡住), 关闭菜单并推进对话。"""
        try:
            for _ in range(2):
                k0 = self.mem.state()["K"]
                moved = False
                for k in ("up", "right", "down", "left"):
                    self.g.key(k)
                    time.sleep(0.25)
                    if self.mem.state()["K"] != k0:
                        moved = True
                        break
                if moved:
                    return
                self.log("[MEM] 启动自检: 四方向全被挡 -> 连按 esc 关菜单 + space 推进")
                self.menu_close()
                time.sleep(0.5)
        except Exception as e:
            self.log("[MEM] 启动自检失败: %s" % e)

    STALE_STOP_GRACE = 60.0     # 标志比启动时刻早这么多秒 -> 判为陈旧遗留

    def _clear_stale_stop(self):
        """启动时清掉上一轮遗留的 mem_stop.flag (返是否清了)。

        mem_stop.flag 不会被自动清理: 上一轮被强杀后残留的标志会让这次启动
        **立刻**走「检测到 mem_stop.flag, 收键退出」, 看着像「跑不起来」。
        判据用启动时刻 —— mtime 早于 _t_boot-GRACE 才算陈旧, 所以运行期间
        别人写的停止标志 (mtime 必然更晚) 不受影响, 正常停机语义不变。
        """
        p = os.path.join(BASE, "mem_stop.flag")
        try:
            if os.path.exists(p) and \
                    os.path.getmtime(p) < getattr(self, "_t_boot", 0) \
                    - self.STALE_STOP_GRACE:
                os.remove(p)
                self.log("[MEM] 清掉上一轮遗留的"
                         " mem_stop.flag (陈旧标志, 否则一启动就退出)")
                return True
        except OSError:
            pass
        return False

    def run(self):
        self._t_boot = time.time()
        # ★ 2026-09-30: mem_stop.flag 不会被自动清理。上一轮被强杀后残留的
        #   标志会让这次启动**立刻**退出 (看着像「跑不起来」)。判据用启动
        #   时刻: mtime 早于 _t_boot-60s 才算陈旧, 所以运行期间别人写的停止
        #   标志 (mtime 必然更晚) 不受影响, 正常停机语义不变。
        self._clear_stale_stop()
        # ---- 启动校验①: 硬编码内存基址是否对得上当前 exe ----
        ok, det = self.mem.selfcheck(self.report_map)
        for ln in det:
            self.log("[校验] %s" % ln)
        if not ok and os.environ.get("PAL_ALLOW_MEM_MISMATCH", "").lower() \
                not in ("1", "true", "on", "yes"):
            self.log("[校验] ★ 内存基址与当前游戏进程对不上 -> 不按键直接停机")
            self.log("[校验] 常见原因: 换了 exe / 游戏升级。换回标定的那个 exe, "
                     "或先跑 python tools/env_check.py 诊断")
            return
        self.geo()                        # 窗口几何自标定 (顺带补一次日志)
        self.log("[几何] %s" % (self._geo_note or "未取到"))
        self.log("[校验] 内存基址 OK: 场景表/坐标/报告三方一致")
        s = self.mem.state()
        self.enter_scene(s["scene"], "(启动)")
        self.g.flush_keys()
        self.startup_selfcheck()   # RESET_MENU_FIX
        while True:
            if self.max_battles and self.battles_won >= self.max_battles:
                self.log("[MEM] 达到目标胜场 %d, 停机" % self.battles_won)
                break
            if self.max_seconds and time.time() - self.t0 > self.max_seconds:
                self.log("[MEM] 达到时限, 停机")
                break
            if time.time() - self.last_win > self.stall_limit:
                self.log("[MEM] %d 秒无胜场, 保险停机" % self.stall_limit)
                break
            # 温和停止: 外部创建 mem_stop.flag 即收键退出 (避免强杀留下卡键)
            if os.path.exists(os.path.join(BASE, "mem_stop.flag")):
                self.log("[MEM] 检测到 mem_stop.flag, 收键退出")
                break
            # 场景号突变(非本进程造成)自愈
            s = self.mem.state()
            if s["scene"] != self.scene:
                self.enter_scene(s["scene"], "(场景号漂移自愈)")
            if self.maybe_fight():
                continue
            if (not self.migrate and time.time() > self.migrate_cd
                    and self.steps_since_battle >= self.MIGRATE_AFTER):
                self.migrate = True
            self.tick()
            now = time.time()
            if self.steps == getattr(self, "_last_steps", -1):
                if now - getattr(self, "_last_prog", now) > 30:
                    self._last_prog = now
                    cur = self.mem.state()["K"]
                    n = getattr(self, "_stuck_n", 0)
                    self._stuck_n = n + 1
                    self.log("[MEM] 30秒无位移 (K%s) 第%d次 -> 脱困" % (cur, n + 1))
                    # !! esc 在仙剑里是"打开系统菜单", 菜单开着时方向键全废 !!
                    # 只用 space 推进对话; 若菜单已开则连按两次 esc 关闭(净效果清零)。
                    if n % 3 == 0:
                        self.g.key("space")
                    elif n % 3 == 1:
                        self.menu_close()
                    else:
                        # 清除身边被"菜单期误判"毒化的绕行格 (只清不新增!)
                        cleared = 0
                        for c in list(self.blocked):
                            if max(abs(c[0] - cur[0]), abs(c[1] - cur[1])) <= 6:
                                self.blocked.discard(c)
                                cleared += 1
                        if cleared:
                            self.log("[MEM] 清除身边 %d 个可疑绕行格" % cleared)
                        if n >= 9:
                            self.log("[MEM] 长期受困 -> 强制迁移")
                            self.migrate = True
                    ks = self.kwalk(self.map)
                    near = [c for c in ks
                            if 0 < max(abs(c[0] - cur[0]), abs(c[1] - cur[1])) <= 6
                            and c not in self.doors and c not in self.blocked]
                    moved = False
                    if near:
                        t = near[int(time.time() * 7) % len(near)]
                        path = self.bfs_path(cur, {t})
                        if path:
                            moved = self.walk_path(path)
                    if not moved:
                        # 兜底: 四方向硬试 (即便数据说不能走也试, 门/毒化格都能突破)
                        self.log("[MEM] 无路可规划 -> 四方向硬试突围")
                        for k in ("up", "right", "down", "left"):
                            st, K0, K1, sc1 = self.step(k)
                            if st == "door":
                                break
                            if st == "moved":
                                break
            else:
                self._last_steps = self.steps
                self._last_prog = now
                self._stuck_n = 0
            if now - self.last_beat > 30:
                self.last_beat = now
                st = self.mem.state()
                self.log("[心跳] 场景%s K%s 步数%d 胜%d场 迁移=%s 用时%.0fs"
                         % (st["scene"], st["K"], self.steps, self.battles_won,
                            self.migrate, now - self.t0))
        self.g.flush_keys()


if __name__ == "__main__":
    _lock = PB._acquire_lock()
    if _lock is None:
        # 锁被旧实例占用: 杀掉它并接管 (自动替换, 避免双实例按键互踩)
        try:
            import psutil as _ps
            _me = os.getpid()
            _pp = _ps.Process(_me).parent().pid
            for _q in _ps.process_iter(["pid", "name"]):
                try:
                    if not _q.info["name"] or "python" not in _q.info["name"].lower():
                        continue
                    if _q.info["pid"] in (_me, _pp):
                        continue
                    _cl = _q.cmdline()
                    if len(_cl) >= 3 and _cl[1] == "-u" and _cl[2].endswith("pal_bot_mem.py"):
                        print("[接管] 终止旧实例 pid=%d" % _q.info["pid"], flush=True)
                        _q.kill()
                except Exception:
                    continue
            time.sleep(1.5)
        except Exception as _e:
            print("[接管] 扫描失败: %s" % _e, flush=True)
        _lock = PB._acquire_lock()
        if _lock is None:
            print("[单实例] 旧实例仍占用游戏, 本进程退出", flush=True)
            sys.exit(0)
    import win32file, win32con
    try:
        win32file.SetFilePointer(_lock, 0, win32con.FILE_BEGIN)
        win32file.WriteFile(_lock, ("%d pal_bot_mem\n" % os.getpid()).encode())
    except Exception:
        pass
    maxb = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1] != "0" else None
    secs = int(sys.argv[2]) if len(sys.argv) > 2 else None
    bot = MemBot(max_battles=maxb, max_seconds=secs)
    import atexit
    atexit.register(lambda: bot.g.flush_keys())
    _stop = os.path.join(BASE, "mem_stop.flag")
    if os.path.exists(_stop):
        os.remove(_stop)
    try:
        bot.run()
    except KeyboardInterrupt:
        print("[手动停止] 共胜利 %d 场" % bot.battles_won)
    except Exception:
        import traceback
        traceback.print_exc()
    finally:
        bot.g.flush_keys()
        for _ in range(3):
            time.sleep(0.15)
            bot.g.flush_keys()
        print("[退出] 已清空按键, 共胜利 %d 场" % bot.battles_won, flush=True)
