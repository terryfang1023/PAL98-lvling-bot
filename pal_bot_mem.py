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
import ctypes, ctypes.wintypes as wt, struct, os, re, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pal_bot as PB

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
KEYDK = {"up": (1, 0), "down": (-1, 0), "left": (0, -1), "right": (0, 1)}
DIRKEY = {v: k for k, v in KEYDK.items()}
BASE = os.path.dirname(os.path.abspath(__file__))


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


class MemBot:
    RADIUS = 28          # 单场景探索半径(格)
    STEP_TIMEOUT = 0.55
    TAP = 0.09

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
        self.startK = None
        self.steps = 0
        self.steps_since_battle = 0
        self.migrate = False
        self.MIGRATE_AFTER = 120
        self.migrate_cd = 0
        self.MIGRATE_COOLDOWN = 600.0     # 两次迁移尝试的最小间隔(防死循环刷屏)
        self.known_doors = self.load_doors()
        self.last_beat = time.time()

    def log(self, msg):
        t = time.strftime("%H:%M:%S")
        line = "%s %s\n" % (t, msg)
        try:
            print(line, end="", flush=True)
        except Exception:
            pass
        try:
            if getattr(self, "_logfh", None) is None:
                self._logfh = open(os.path.join(BASE, "bot_log_mem.txt"), "a",
                                   encoding="utf-8", buffering=1)
            self._logfh.write(line)
        except Exception:
            pass

    # ---- 场景/地图 ----
    def kwalk(self, m):
        if m not in self._kwalk_cache:
            grid = self.grids[m]
            s = set()
            for x in range(64):
                for y in range(128):
                    row = grid[y, x]
                    for hh in (0, 1):
                        if not row[hh]:
                            s.add((x - y, x + y - 1 + hh))
            self._kwalk_cache[m] = s
        return self._kwalk_cache[m]

    def enter_scene(self, sc, why=""):
        self.scene = sc
        self.map = self.mem.scene_map.get(sc)
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
                  "startK": cur, "warned": False}
            self.scene_know[sc] = st
        self.doors = st["doors"]
        self.blocked = st["blocked"]
        self.visited = st["visited"]
        self.startK = st["startK"]
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
        self.log("[战斗] 交战, 按 F 最强法术")
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
            self.g.key("f")
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
    def bfs_path(self, src, dsts, extra_blocked=frozenset(), allow_doors=False):
        """解码可走域 BFS: 从 src 到 dsts 中任一格的最短路 (门格/实测挡格除外)。"""
        ks = self.kwalk(self.map)
        bad = self.blocked | set(extra_blocked) if allow_doors else self.doors | self.blocked | set(extra_blocked)
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
                        self.blocked.add(path[i])
                    self.log("[MEM] 实测被挡(数据说可走): %s -> 记入绕行" % (path[i],))
                return False
            self.visited.add(K1)
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

    def pick_migrate_door(self, cur):
        """迁移目标门: 预取门 + 历史实测门(跨运行持久), 取最近且有可走邻格的。"""
        ks = self.kwalk(self.map)
        cands = list(self.report_doors.get(self.scene, set())) + \
            list(self.known_doors.get(self.scene, set()))
        cands = [d for d in cands
                 if max(abs(d[0] - cur[0]), abs(d[1] - cur[1])) <= 80]
        cands.sort(key=lambda d: max(abs(d[0] - cur[0]), abs(d[1] - cur[1])))
        for d in cands:
            if any((d[0] + da, d[1] + db) in ks
                   for da in (-1, 0, 1) for db in (-1, 0, 1)):
                return d
        return None

    def migrate_probe(self, doorK):
        """走到门 K 的可走邻格, 再在 ±2 邻域内逐格试踩门。返回是否触发换图。"""
        ks = self.kwalk(self.map)
        near = [(a, b) for (a, b) in ks
                if abs(a - doorK[0]) <= 2 and abs(b - doorK[1]) <= 2
                and (a, b) not in self.blocked]
        cur0 = self.mem.state()["K"]
        near.sort(key=lambda c: max(abs(c[0] - cur0[0]), abs(c[1] - cur0[1])))
        for cell in near:
            cur = self.mem.state()["K"]
            if cur != cell:
                path = self.bfs_path(cur, {cell}, allow_doors=True)
                if path is None or not self.walk_path(path):
                    continue
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
        return False

    def tick(self):
        cur = self.mem.state()["K"]
        if self.migrate:
            # 注意: 探测期间保持 migrate=True, 这样途中踩到门由 handle_door 走
            # "迁移穿门 -> 接受新场景" 分支, 否则迁移永远被退回逻辑取消。
            self.log("[MEM] 迁移: %d 步无战斗, 穿门找刷怪区" % self.steps_since_battle)
            sc0 = self.scene
            door = self.pick_migrate_door(cur)
            if door is None:
                self.log("[MEM] 迁移: 本场景无已知门, 放弃本轮")
                self.migrate = False
            else:
                self.log("[MEM] 迁移: 目标门 K%s" % (door,))
                if self.migrate_probe(door):
                    self.log("[MEM] 迁移成功: 场景 %d -> %d" % (sc0, self.scene))
                    self.migrate = False
                    self._mtries = 0
                else:
                    self._mtries = getattr(self, "_mtries", 0) + 1
                    self.log("[MEM] 迁移: 门未触发(第%d次), 跨轮继续逼近" % self._mtries)
                    if self._mtries < 10:
                        return                  # 保持 migrate=True, 下一轮接着逼近
                    self.log("[MEM] 迁移: 尝试%d次未果, 进入冷却" % self._mtries)
                    self._mtries = 0
                    self.migrate = False
            if not self.migrate:
                self.migrate_cd = time.time() + self.MIGRATE_COOLDOWN
                self.steps_since_battle = 0
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
            self.blocked.add(tgt)
            self._spin = getattr(self, "_spin", 0) + 1
            if self._spin % 80 == 0:
                # 绕行知识过密把局部地图糊死 -> 清掉周边污染, 换锚点重新测绘
                cur = self.mem.state()["K"]
                cleared = 0
                for c in list(self.blocked):
                    if max(abs(c[0] - cur[0]), abs(c[1] - cur[1])) <= 16:
                        self.blocked.discard(c)
                        cleared += 1
                self.startK = cur
                self.visited = {c for c in self.visited
                                if max(abs(c[0] - cur[0]), abs(c[1] - cur[1])) <= 6}
                self._spin = 0
                self.log("[MEM] RESET 局部绕行知识过密 -> 清除周边 %d 格并重新测绘 K%s"
                         % (cleared, cur))
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

    def run(self):
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
