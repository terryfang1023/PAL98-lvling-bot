# -*- coding: utf-8 -*-
"""pal_bot_mem5.py — v16「要素巡回版」：直奔怪点刷怪 + 顺路开宝箱

════════════════════════════════════════════════════════════════════
为什么不再靠"均匀巡逻"找怪（2026-09-29 内存逆向结论，全部有实测证据）
════════════════════════════════════════════════════════════════════
SDLPAL 把地图上的怪/NPC/宝箱/传送阵都存成**事件对象**(EVENTOBJECT)，
从运行中的游戏内存可以一次性全部读出来（tools/build_scene_info.py）：

  怪   = sprite≠0 且**触发脚本第一句就是 0x0007 START_BATTLE**
         坐标固定；玩家进入其周围 1 格（含斜角）立即开打 (play.c:112)
  宝箱 = 触发脚本含 0x001F ADD_ITEM_TO_INVENTORY
         需走到相邻格、朝它按空格（search 触发）
  传送 = 0x0059 CHANGE_SCENE      石碑 = 0xFFFF TEXT

实测证据链:
  * 场景 157 的 21 个 touch 实体(spr=11/59/470/460/495)脚本链全部是 [0x0007]
  * 日志里 234 场战斗的战斗热点 K(-65,100)/( -11,39)/(14,50) 与
    对象表的 K(-65,99)/(-10,39)/(15,50) 一一对上（差 1 格 = 触发半径）
  * 战斗收尾脚本 = 0x0052 HIDE_FOR_A_WHILE（隐藏 ~800 帧 = 40~50 秒后重生）
    日志里同一怪点重复触发的最小间隔集中在 **96~107 秒** => 与重生周期吻合
  * 部分怪的自动脚本含 0x004C CHASE_PLAYER => 会主动追玩家（所以偶尔
    战斗发生地离出生点较远）

=> 最优策略不是"全图均匀巡逻"，而是**按最近邻顺序巡回已知怪点**：
   走到 => 触发 => 打完 => 换下一个（约 70 秒后原怪已重生，可再回访）。
   这比"走路数恰好撞上随机遇敌"快得多，而且完全不依赖运气。

本版相对 v15(pal_bot_mem4) 只做加法：
  1. pick_target 优先返回「当前场景未在冷却中的怪点」；
  2. 走到怪点/在此开打后，该怪点进入冷却（怪在重生，别原地干等）；
  3. 怪点都在冷却时，顺路去未开的宝箱：走到相邻格 -> 转身 -> 按空格；
  4. 以上全部不可用时，**原样回退 v15 的渐进巡逻**（保底不当机）。
要素清单由 tools/build_scene_info.py 从内存生成 scene_info.json，
启动时缺失会自动尝试生成一次（1~2 秒，不用真的跑遍地图）。

════════════════════════════════════════════════════════════════════
★ v17 新增「场景评分 + 自动换场景」(2026-09-29)
════════════════════════════════════════════════════════════════════
实测数据摆在那儿: 一场战斗只要 11 秒, 但相邻两场之间平均要等 104 秒 ——
瓶颈不在打, 在「找怪」。场景 157 的几何上限 1.0 场/分、实测 0.50;
而场景 229 上限 2.8 场/分。=> 换场景的收益(3~7 倍)远大于路线微调(4~16%)。

但「门通向哪个场景」事先不可知(踩下去才知道), 所以不做全局最优路由,
只做**效率驱动的逐段前进**:
    在 A 刷 -> 长时间刷不动 -> 走最近的门去 B -> B 好就留下, B 也差就继续走
判据与防抖全在 scene_pilot.py (纯逻辑, 可离线单测):
  · 过路场景(可达怪点<3): 无战斗 100s 就走
  · 其它场景: 无战斗 > 240s x 耐心系数 就走; 或停留>8分钟且<0.15 场/分 就走
  · 耐心系数由 tools/scene_score.py 的评分给出(好场景更耐心), 反复低效递减
  · MIN_STAY 150s / 换场景冷却 180s / 刚离开又回来耐心砍半 (防乒乓)
走门时自动临时打开"穿门模式"(否则踩门会被弹回来), 到达新场景后还原。
每次穿门都记下「场景x的门K -> 场景y」到 scene_links.json, 攒跨场景门路图。

评分表: python tools/scene_score.py   (GUI 里对应「场景评分」按钮)
关闭要素模式（回到纯 v15 行为）: 第 8 个参数写 巡逻 或 off
关闭自动换场景: 第 9 个参数写 不换 或 off   (GUI 里取消勾选)
临时调调度参数字: 环境变量 PAL_PILOT_IDLE / _MIN_STAY / _CD / _PASS_IDLE
"""
import ctypes
import ctypes.wintypes as _wt
import json
import os
import struct
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import pal_bot as PB                                          # noqa: E402
import bot_chestlog as chestlog                                # noqa: E402
import bot_featlog as featlog                                  # noqa: E402
import bot_winlog as winlog                                    # noqa: E402
import bot_explog as gainlog                                   # noqa: E402
import game_addr                                               # noqa: E402
from pal_bot_mem import MemBot                                # noqa: E402
from pal_bot_mem4 import MemBot4                              # noqa: E402
from scene_pilot import ScenePilot                            # noqa: E402


# ======================================================================
#  实时事件对象表访问 (只读)
# ======================================================================
# ★ 2026-09-29 实测: 部分怪(spr=495 / 460 等)会在地图上**自动游走**,
#   静态快照 scene_info.json 的坐标必然失效 —— 实测 K(-74,100) 的怪跑到了
#   K(-81,105)。bot 走到旧坐标当然「走到 1 格内未触发」, 跨 28 分钟反复出现。
#   => 怪点坐标必须每次**实时**从内存读。
#
# 地址来自 SDLPAL global.h tagGAMEDATA (与 tools/dump_objects.py 同源):
#   lprgEventObject @0x00C38080 / nEventObject @0x00C38084
#   rgScene[300]    @0x00C38088 (每场景 8 字节, 第 4 个 WORD = 该场景对象末序号)
#   wNumScene       @0x00C3C95A
OBJ_PTR_ADDR = 0x00C38080
OBJ_N_ADDR = 0x00C38084
OBJ_SCENE_TAB = 0x00C38088
OBJ_SCENE_NOW = 0x00C3C95A
OBJ_MAX_SCENES = 300
OBJ_SIZE = 32
PROC_OPEN_OK = 0x0010 | 0x0400
# 脚本表: lprgScriptEntry 指针槽 + nScriptEntry (build_scene_info.py 同源)
SCRIPT_PTR_ADDR = 0x00C3AAB8
# 关键脚本指令 (与 build_scene_info.py 一致)
OP_JUMP = 0x0003
OP_STOP_NEXT = 0x0001
OP_STOP_ENTRY = 0x0002
OP_CALL = 0x0004
OP_RANDJUMP = 0x0006
OP_GOTOIFNO = 0x000A
OP_CHANGESCENE = 0x0059
OP_ADD_ITEM = 0x001F              # 加物品 = 宝箱的"身份指令" (见 _chest_verdict)


def kof(px, py):
    """地图像素坐标 -> 格坐标 K。"""
    return ((px - 2 * py) // 32, (px + 2 * py - 32) // 32)


def chest_verdict(objs, c, has_op):
    """一个宝箱的内存体检 —— 纯函数, 便于回归测试 (不碰进程)。

    objs   : 本场景全部事件对象元组 (每对象 OBJ_SIZE//2 个 WORD)
    c      : 静态快照条目 {"K":[a,b], "spr":.., "mode":.., "trig":..}
    has_op : (entry, op) -> True/False/None (None = 脚本表读不到)

    ★ 判据来源 (SDLPAL 源码 _src/sdlpal + 2026-10-04 真机 1058 箱实测):
      宝箱被打开时, 它自己的脚本最后一条是 `0x0002 STOP_ENTRY -> 36509`
      (36509 = 三条指令的"箱子已空"提示脚本), play.c:153 把这个返回值
      **回写**到该对象的 wTriggerScript —— 所以"已开"的唯一内存痕迹就是
      「触发脚本再也走不到 0x001F 加物品」。实测 935/935 与
      「trig 被改写成同一个常量」完全一致, 两种说法等价。
      另外 play.c:467/:107 要求 sState > 0 才可能触发 -> sState<=0 的箱子
      (快照里 123 个隐形箱) 走过去也按不出东西, 一并算 dead。

    返回 ("dead" 走过去白费 / "live" 还能开 / "unknown" 拿不准), 原因文本。
    """
    K = (c["K"][0], c["K"][1])
    at_k = [o for o in objs if kof(o[1], o[2]) == K]
    if not at_k:
        return "unknown", "该格没有对象"
    cand = [o for o in at_k if o[8] == c.get("spr") and o[7] == c.get("mode")]
    if not cand:
        return "unknown", "身份对不上(精灵/触发方式变了)"
    dead, live = [], []
    for o in cand:
        st = o[6] - 65536 if o[6] >= 32768 else o[6]
        trig = o[4]
        if st <= 0:
            dead.append("状态%d不可触发" % st)
            continue
        if not trig:
            dead.append("无触发脚本")
            continue
        g = has_op(trig, OP_ADD_ITEM)
        if g is None:                    # 脚本表读不到 -> 退回"脚本有没有被改写"
            if trig != c.get("trig"):
                dead.append("脚本已改写")
            else:
                live.append("脚本未变")
            continue
        if g:
            live.append("还能开")
        else:
            dead.append("脚本已空")
    if not live:
        return "dead", "/".join(dead[:2]) or "?"
    if not dead:
        return "live", live[0]
    return "unknown", "同格判定不一致(%s)" % "/".join(dead + live)[:40]


def botcfg_env_bool(key="rope", default=True):
    """botcfg 不可用时的兜底: 只看环境变量 PAL_<KEY> (空/缺省 -> default)。"""
    d = "1" if default else "0"
    s = str(os.environ.get("PAL_" + str(key).upper(), d)).strip().lower()
    if not s:
        s = d
    return s not in ("0", "off", "false", "no", "关", "否")


def cfg_bool(key, default=True):
    """取一个布尔配置: 环境变量 > bot_config.json > default (botcfg 优先)。"""
    try:
        import botcfg as _BC
        return _BC.resolve_bool(key)[0]
    except Exception:
        return botcfg_env_bool(key, default)


def cfg_int(key, default=0, lo=None):
    """取一个整数配置: 环境变量 > bot_config.json > default; lo 为下限钳位。"""
    try:
        import botcfg as _BC
        v = _BC.resolve_int(key)[0]
    except Exception:
        try:
            v = int(float(str(os.environ.get(
                "PAL_" + str(key).upper(), default)).strip()))
        except Exception:
            v = default
    if lo is not None and v < lo:
        v = lo
    return v


# K 空间按键真值 (全项目一致: pal_bot_mem.KEYDK / pal_bot.py:1330 /
# tools/test_key.py / go_home.py / trial_door.py): up=+a, down=-a,
# left=-b, right=+b。反查表就是"要到 ΔK 该按哪个键"。
KEYDK_INV = {(1, 0): "up", (-1, 0): "down", (0, -1): "left", (0, 1): "right"}


def face_key(dka, dkb):
    """从站格看向 ΔK=(dka,dkb) 处的目标时, 该按哪个方向键。

    ★ 2026-10-02 修反 (血案): 原实现写成 {(1,0):"down",(0,1):"left"...},
      正好是把 up/down、left/right 对调 —— 绳子探测"走到绳子旁边 -> 朝绳子
      转身"这一步转的是**背对**绳子的方向, 转身后按空格面前什么都没有,
      所以永远"没反应"。旁证: 真机日志 "[宝箱] 目标 箱K(-27,124) 站位
      K(-28,124) 朝向 up" —— 箱在站格的 +a 侧(=K 空间的 up), 真值就是 up。
    """
    return KEYDK_INV[(dka, dkb)]


def chest_face_key(da, db):
    """_pick_chest 用: (da,db) 是「箱子 -> 站格」的偏移, 需按反方向。

    等价于 face_key(-da, -db) —— 单独写出来是为了让两处朝向口径一眼可辨,
    免得下次又有人"顺手"把其中一处改成另一处的表。
    """
    return KEYDK_INV[(-da, -db)]


def _game_pids():
    """标题含 'Pal' 的窗口所属进程 pid。
    注意: ctypes.wintypes 只有**类型**, 库要从 ctypes.windll 取。

    ★ 2026-10-12: 排除自家 GUI ("挂机机器人") —— 以前只靠大小写侥幸不撞车,
      详见 pal_bot_mem.find_pal_pid 的说明。
    """
    u32 = ctypes.windll.user32
    res = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, _wt.HWND, _wt.LPARAM)
    def cb(hwnd, lp):
        pid = _wt.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        n = u32.GetWindowTextLengthW(hwnd)
        if n:
            b = ctypes.create_unicode_buffer(n + 1)
            u32.GetWindowTextW(hwnd, b, n + 1)
            if "Pal" in b.value and "挂机机器人" not in b.value:
                res.append(pid.value)
        return True
    u32.EnumWindows(cb, 0)
    return sorted(set(res))


class _ObjMem:
    """只读的事件对象表访问器(独立于 pal_bot_mem.Mem, 不碰它的句柄)。

    ★ 2026-10-16 地址平移: 下边几个 OBJ_* / SCRIPT_PTR_ADDR 是**标定值**
      (标定那次游戏模块加载在 game_addr.CALIB_BASE); sdlpal.exe 开了 ASLR,
      而 Windows 的映像基址随机化**按"本次开机"决定一次** —— 换机器/重启就
      可能换基址。所以读这些常量前一律过 `self._sa()` 叠平移。

      ⚠ 但 `ptr = self.u32(OBJ_PTR_ADDR)` 拿回来的**指针是游戏运行时写的**,
        它已经是真实地址 -> 之后 `ptr + n*OBJ_SIZE` 之类**绝不能再叠平移**。
        所以这里没有把平移塞进 rd(), 而是在"读常量"的 5 个点上显式 _sa()。
    """

    def __init__(self):
        self.k32 = ctypes.windll.kernel32
        self.h = None
        self.got = ctypes.c_size_t()
        self._fail = 0
        self.slide = 0

    def open(self):
        if self.h:
            return True
        try:
            pids = _game_pids()
            if not pids:
                self.err = "找不到标题含 Pal 的窗口"
                return False
            self.slide = game_addr.slide(pids[0])
            self.h = self.k32.OpenProcess(PROC_OPEN_OK, False, pids[0])
            if not self.h:
                self.err = "OpenProcess 失败 pid=%d" % pids[0]
        except Exception as e:
            self.h = None
            self.err = "枚举窗口异常: %s" % e
        return bool(self.h)

    def _sa(self, addr):
        """静态标定地址 -> 本次运行的真实地址 (叠基址平移)。"""
        return addr + self.slide

    def rd(self, addr, size):
        buf = (ctypes.c_char * size)()
        if not self.k32.ReadProcessMemory(self.h, ctypes.c_void_p(addr), buf,
                                          size, ctypes.byref(self.got)):
            return None
        return buf.raw[:self.got.value]

    def u32(self, addr):
        d = self.rd(addr, 4)
        return struct.unpack("<I", d)[0] if d and len(d) == 4 else None

    def u16(self, addr):
        d = self.rd(addr, 2)
        return struct.unpack("<H", d)[0] if d and len(d) == 2 else None

    def scene_objects(self, s):
        """返回场景 s 的对象原始字段元组列表(每个 16 个 WORD)。"""
        ptr = self.u32(self._sa(OBJ_PTR_ADDR))
        n = self.u32(self._sa(OBJ_N_ADDR))
        if not ptr or not n or n > 6000:
            return []
        tab = self.rd(self._sa(OBJ_SCENE_TAB), OBJ_MAX_SCENES * 8)
        if not tab or len(tab) < OBJ_MAX_SCENES * 8:
            return []
        idxs = [struct.unpack_from("<4H", tab, i * 8)[3]
                for i in range(OBJ_MAX_SCENES)]
        lo = idxs[s - 1] + 1                       # 1-based
        hi = idxs[s] if s < OBJ_MAX_SCENES else n
        cnt = hi - lo + 1
        if cnt <= 0 or cnt > 3000:
            return []
        raw = self.rd(ptr + (lo - 1) * OBJ_SIZE, cnt * OBJ_SIZE)
        if not raw or len(raw) < cnt * OBJ_SIZE:
            return []
        return [struct.unpack_from("<16H", raw, i * OBJ_SIZE)
                for i in range(cnt)]

    # ---- 脚本表 (绳子识别用: 触发脚本能走到 0x0059 CHANGE_SCENE) ----
    def _script_tab(self):
        """(脚本表指针, 条目数); 读不到返回 (None, 0)。"""
        if getattr(self, "_stab", None) is None:
            ptr = self.u32(self._sa(SCRIPT_PTR_ADDR))
            n = self.u32(self._sa(SCRIPT_PTR_ADDR) + 4)
            self._stab = (ptr, n if n and 0 < n < 200000 else 0)
        return self._stab

    def script_hits(self, entry, want):
        """从 entry 出发遍历脚本控制流, 返回命中的 op 集合 (build_scene_info 同款)。"""
        return self._walk(entry, want)[0]

    def script_has_op(self, entry, op):
        """触发脚本还走不走得到 op。True/False; **脚本表读不到 -> None**。

        与 script_hits 的区别: 后者读不到时返回空集合, 会被误读成"没命中"。
        宝箱体检要靠这个 None 兜底 (读不到就别下结论)。
        """
        hit, ok = self._walk(entry, (op,))
        if not ok:
            return None
        return op in hit

    def _walk(self, entry, want):
        """(命中集合, 是否真的读到了脚本表)。控制流遍历, 与 build_scene_info 同款。"""
        ptr, n_sc = self._script_tab()
        if not ptr or not n_sc:
            return set(), False
        hit = set()
        stack = [entry]
        seen = set()
        cnt = 0
        while stack:
            e = stack.pop()
            while True:
                if e <= 0 or e >= n_sc or e in seen:
                    break
                seen.add(e)
                cnt += 1
                if cnt > 400:
                    return hit, True
                d = self.rd(ptr + e * 8, 8)
                if not d or len(d) < 8:
                    return hit, False
                op, a, _b, _c = struct.unpack("<4H", d)
                if op in want:
                    hit.add(op)
                if op == 0x0000:
                    break
                if op == OP_JUMP:
                    e = a
                    continue
                if op == OP_STOP_ENTRY:
                    break
                if op == OP_STOP_NEXT:
                    e += 1
                    continue
                if op == OP_CALL or op == OP_RANDJUMP or op == OP_GOTOIFNO:
                    stack.append(a)
                    e += 1
                    continue
                e += 1
        return hit, True

    # ---- 宝箱体检: 这个箱子还值不值得走过去 ----
    def chest_verdict(self, sc, c):
        """单个箱子体检 -> (verdict, 原因); verdict ∈ dead/live/unknown。"""
        try:
            objs = self.scene_objects(sc)
        except Exception:
            objs = []
        if not objs:
            return "unknown", "对象表读不到"
        return chest_verdict(objs, c, self.script_has_op)

    def chest_verdicts(self, sc, cs):
        """整场景批量体检 -> {K元组: (verdict, 原因)} (对象表只读一次)。"""
        try:
            objs = self.scene_objects(sc)
        except Exception:
            objs = []
        out = {}
        for c in cs:
            K = (c["K"][0], c["K"][1])
            out[K] = (("unknown", "对象表读不到") if not objs
                      else chest_verdict(objs, c, self.script_has_op))
        return out

    def scene_ropes(self, s):
        """场景 s 的"绳子": 站上去按空格才触发的换景点。

        ★ 真机标定 (2026-10-02 场景220): 绳子的贴图画在**地图背景**里,
          事件对象本身是隐形的 (sprite=0) —— 与普通门的区别不在 sprite,
          而在触发方式: wTriggerMode >= 4 是走近自动触发 (门),
          0 < mode < 4 是"搜索触发" = 必须站在格上按空格 (play.c PAL_Search:
          mode*6-4 决定搜索点范围, mode=1 只查脚下半格)。
        返回 [{"K":[a,b], "spr":0, "trig":.., "mode":..}]。读不到返回 []。
        """
        if not self.u32(self._sa(OBJ_PTR_ADDR)) \
                or not self.u32(self._sa(OBJ_N_ADDR)):
            return []
        out = []
        for f in self.scene_objects(s):
            spr, trig, auto, state, mode = f[8], f[4], f[5], f[6], f[7]
            if spr or state <= 0 or not (0 < mode < 4):
                continue
            hit = set()
            for e in (trig, auto):
                if e:
                    hit |= self.script_hits(e, (OP_CHANGESCENE,))
            if OP_CHANGESCENE in hit:
                a, b = kof(f[1], f[2])
                out.append({"K": [a, b], "spr": 0, "trig": trig,
                            "mode": mode})
        return out


def cheb(p, q):
    return max(abs(p[0] - q[0]), abs(p[1] - q[1]))


class MemBot5(MemBot4):
    # ---- 驻留练级 (2026-10-20) ----
    #   PIN_BAN_PAD: 出口禁区半径(格)。为什么不是 0 —— 门的触发对象与"踩到的
    #     那一格"经常差 1~2 格(十里坡实测: 对象在 [-63,179] 附近, 队伍在
    #     相对位置踩响), 只禁门格等于没禁。禁 3 个门 ≈ 75 格, 对 5891 格的
    #     十里坡只损失 1.3%, 换"绝不误出门"划算。
    #   PIN_EXIT_TTL: 出口清单缓存寿命。对象表一次扫描要几十次 ReadProcessMemory,
    #     不能每 tick 扫; 60 秒刷一次足够 (门是地图景物, 不会每秒变)。
    PIN_BAN_PAD = 2
    PIN_EXIT_TTL = 60.0
    PIN_BACK_MAX = 5             # 连续 N 次走不回去 -> 停止尝试并告警
    PIN_REACH_KEEP = 0.6         # 门禁后可达域 >= 无门禁的 60% 就算"够用"
    PIN_REACH_ABS = 80           # ...但绝对不低于 80 格 (再小就是把路锁死了)

    # ---- 触发圈几何 (2026-09-29 实测修正) ----
    # ★ 只有「正交 4 邻格」才算进了触发圈。原先的 3x3 邻域含 4 个斜角格,
    #   站到斜角格上引擎**不触发**战斗(实测: K(-74,104)/K(-74,100) 两个点
    #   跨 17 分钟反复报「走到 1 格内未触发」, 远超过 45 秒重生期, 排除
    #   「怪在恢复期」的解释) -> 选站格必须优先正交邻格。
    ORTHO = ((1, 0), (-1, 0), (0, 1), (0, -1))
    DIAG = ((1, 1), (1, -1), (-1, 1), (-1, -1))
    ORTHO_SET = set(ORTHO) | {(0, 0)}

    def _in_trigger(self, mk, cur):
        """队伍是否真的站进了怪点的触发圈(正交 1 格内, 含怪点本身)。"""
        return (cur[0] - mk[0], cur[1] - mk[1]) in self.ORTHO_SET

    # ---- 怪点巡回参数 ----
    MON_CD = 70.0           # 打过之后该怪点的冷却(怪隐藏~800帧≈45s, 留余量)
    MON_SETTLE_MIN = 1.5    # 到了目标点后至少站这么久才判「有没有开打」
    MON_GOAL_TTL = 18.0     # 在途目标的基准时限(实际 = 基准 + 1.0×距离, 见 _goal_ttl)
    MON_MOVE_STALL = 12.0   # 在途但步数一步没涨超过 N 秒 -> 视为卡死, 提前放弃
    MON_HIT_NEAR = 3        # 开打位置距怪点 ≤N 格 => 算这个怪点的战果
    MON_TRY_N = 4           # 走到 1 格内仍不触发 N 次 -> 本局放弃该点
    MON_MISS_N = 3          # 连续 N 次超时都没走到触发圈 -> 判长冷却
    MON_MISS_CD = 600.0     # miss 长冷却(10分钟)。★不永久放弃: miss 真因多半
                            # 是 BFS 假通路/怪点在另一连通块(锁妖塔分层),
                            # 绕行知识更新后可能就通了
    MON_LOG_EVERY = 15      # 每 N 次选点打一条日志
    SLOG_GAP = 20.0         # 同一怪点的日志最小间隔
    LIVE_TTL = 2.0          # 实时怪点坐标缓存时长(闹钟式重读内存, 很便宜)
    ELEM_NEAR_D = 18        # 就近优先半径: 有 ≤N 格的怪点就绝不长途跋涉
                            #   (锁妖塔窄桥区长途 BFS 极易"实测被挡", 吞吐掉到 0.2)
                            #   近处全没有时才退而选最近的(不硬排除)
    # ---- 未访问偏好 (2026-10-04) ----
    # ★ 纯"最近优先"会让队伍死盯最近那一两只怪, 旁边的怪点永远排不上队。
    #   改成分数制: score = 距离 + MON_FRESH_W × min(visit_cnt[站格], CAP)
    #     visit_cnt 全空(开局/影子对象) -> score 退化成距离, 与旧版逐位一致。
    #   上限 CAP 保证"偶尔去过"不会像"去过很多次"一样被重罚;
    #   ELEM_NEAR_D 那道闸门保留 -> 绝不会为了走生路而长途跋涉。
    MON_FRESH_W = 0.5       # 每"走过一次"折算多少格距离 (0 = 关掉轮转)
    MON_FRESH_CAP = 6       # 访问次数的封顶 (再多次也只按 6 次算)

    # ---- 定期探索配额 (2026-10-04) ----
    # ★ 「走遍没走过的区域」: 每打 N 场强制去一次本场景 visit_cnt=0 的远端格。
    #   这是三档「走生路」里唯一**主动牺牲刷怪密度**换覆盖率的一档 ——
    #   用来发现漏掉的宝箱/绳子/黑洞, 以及那些永远轮不到的角落。
    #   EXPLORE_EVERY_BATTLES=0 或开关关掉 = 完全不探索。
    EXPLORE_EVERY_BATTLES = 25
    EXPLORE_MIN_GAP = 240.0    # 两次探索之间最小间隔(秒), 双保险防连发
    EXPLORE_TTL = 60.0         # 探索目标在途时限(秒)
    EXPLORE_MIN_D = 12         # 目标至少这么远(格): 太近的"没走过"两步就踩完了

    # ---- 宝箱参数 ----
    # ★ 2026-10-02 v18.2 用户要求「碰到就记」: 原来要试满 4 次才记账, 于是
    #   场景里**早就被开过**的箱子(游戏里原本就是打开状态)每次重启都要走过去
    #   白按 4 次空格才被拉黑。现在默认**试 1 次没反应就记账**(ok=False)并本局
    #   拉黑, 下次不再去。一次到访内部本身含 2 次空格 + 「对话框 或 物品栏涨了」
    #   的 2.5s 判据, 判定足够稳; 想更保守可改 bot_config.json: {"chest_try_n": 2}
    CHEST_TRY_N = 1         # 同一个箱子试 N 次没反应 -> 记「已开着/空箱」并拉黑
    CHEST_CD = 240.0        # 试失败后的冷却
    CHEST_SETTLE = 0.55     # 转身后等待时间(转身动画)
    CHEST_DLG_WAIT = 0.80   # 按空格后等对话框弹出
    CHEST_ALIVE_MAX = 3     # ★内存说"还满"却连 N 次按不开 -> 先放一边(不记空箱)
    CHEST_ALIVE_LONG = 1800.0   # 放弃前的长冷却 (只冷却, 绝不永久拉黑)
    CHEST_BIAS = 0.60       # ★顺路开箱: 宝箱距离 < 最近怪点距离×此系数就去开
                            #   否则「怪点永远有候选」(21个点/70s冷却)会让宝箱
                            #   一次都轮不到(三轮实测 0 次尝试)

    # ---- 绳子攀爬参数 (2026-10-02, 试炼窟上下层) ----
    # 绳子 = sprite=0 且 0<wTriggerMode<4 (搜索触发) 且脚本能走到
    # 0x0059 CHANGE_SCENE 的事件对象 (贴图在地图背景里, 对象隐形)。
    # 触发方式: 站上绳格按空格(调查), 弹"是否攀爬"再按一次确认。
    ROPE_CD = 300.0         # 试失败后的冷却 (真按了空格没换层才算)
    ROPE_CD_WALKFAIL = 45.0 # 走位失败的短冷却 (没按过空格, 不算攀爬失败)
    ROPE_FAIL_MAX = 2       # 同一根绳连败 N 次 -> 拉长冷却
    ROPE_CD_LONG = 900.0    # 连败后的长冷却
    ROPE_DLG_WAIT = 0.80    # 按空格后等对话框/脚本响应
    ROPE_SC_WAIT = 5.0      # 确认后等场景切换的上限 (爬绳有淡入淡出)
    ROPE_MAX_D = 200        # 迁移时考虑的绳子距离上限 (与门一致)
    # ★ v18.2「绳子最高优先级, 遇到就爬」(用户要求):
    #   不再等"这个场景刷不动了"才想起绳子 —— 只要近处有能爬的绳子就去爬。
    ROPE_HUNT_D = 10        # "遇到"的距离: 绳子在 N 格内才主动去爬(不长途跋涉)
    ROPE_HUNT_CD = 120.0    # 爬成功后 N 秒内不再主动爬 (防在两层之间来回横跳)
    ROPE_HUNT_GAP = 5.0     # 两次主动攀爬**尝试**之间的最小间隔 (节流/防连点)
    ROPE_GOAL_TTL = 30.0    # 去绳子的在途时限: 走了这么久还没到 -> 放弃, 回落常规

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.elem_mode = True
        self.auto_scene = True    # ★ 自动换场景 (效率驱动, 见 scene_pilot.py)
        self.pilot = None
        self.scene_info = {}
        self.mon_cd = {}          # (scene,a,b) -> 该怪点可再次前往的时间戳
        self.mon_visit = {}       # (scene,a,b) -> 到了却没开打的次数
        self._mon_miss = {}       # (scene,a,b) -> 没走到触发圈的次数
        self.mon_dead = set()     # 本局放弃的怪点
        self.chest_cd = {}
        self.chest_try = {}
        self.chest_dead = set()
        # ---- 绳子 (最高优先级的换层手段) ----
        # 开关: 环境变量 PAL_ROPE > bot_config.json > 默认开 (GUI 有勾选框)
        try:
            import botcfg as _BC
            self.use_ropes = _BC.resolve_bool("rope")[0]
        except Exception:
            self.use_ropes = botcfg_env_bool()
        # ★ v18.2「遇到就爬」: 绳子优先级最高, 不再等换场景判定
        self.rope_hunt = cfg_bool("rope_hunt", True)
        self.ROPE_HUNT_D = cfg_int("rope_hunt_d", self.ROPE_HUNT_D, lo=1)
        self.ROPE_HUNT_CD = float(cfg_int("rope_hunt_cd", int(self.ROPE_HUNT_CD),
                                          lo=0))
        self.rope_cd = {}         # (scene,K) -> 可再试的时间戳
        self.rope_fail = {}       # (scene,K) -> 连败次数
        self._rope_cache = None   # (scene, 时刻, 清单) 实时读数缓存
        self._mig_is_rope = False # 本轮迁移目标是绳子(而非门)
        self._mig_is_hole = False # 本轮迁移目标是黑洞(v18.7)
        self._rope_goal = None    # (scene, 绳K, 设定时刻) ★主动攀爬的在途目标
        self._rope_hunt_t = 0.0   # 上次**发起**主动攀爬的时刻 (ROPE_HUNT_GAP 节流)
        self._rope_ok_t = 0.0     # 上次爬**成功**的时刻 (ROPE_HUNT_CD 防横跳)
        if not self.use_ropes:
            self.log("[配置] 绳子攀爬 = 关 (不认绳子, 只能穿门换层)")
        elif self.rope_hunt:
            self.log("[配置] 绳子攀爬 = 开 ★最高优先级: 见到 %d 格内的绳子就爬 "
                     "(爬完 %.0fs 内不再爬; 可在 GUI 关掉「遇到就爬」)"
                     % (self.ROPE_HUNT_D, self.ROPE_HUNT_CD))
        else:
            self.log("[配置] 绳子攀爬 = 开 (仅无门可走时用绳子换层)")
        # ---- 开箱台账 (chest_log.json, 跨重启记住已开的箱子) ----
        # 开关: 环境变量 PAL_CHEST_MEMO > bot_config.json > 默认开
        self.chest_memo = cfg_bool("chest_memo", True)
        # ★ v18.2: 试几次没反应就记账 (默认 1 = 碰到就记, 已开着的箱子不再白跑)
        self.CHEST_TRY_N = cfg_int("chest_try_n", self.CHEST_TRY_N, lo=1)
        self.chest_log = chestlog.ChestLog()
        # ---- ★ v18.9 空箱从内存判断 (用户要求: 是空箱就完全忽略) ----
        #   真机实测 (2026-10-04, 1058 个宝箱全表): 箱子被打开时它自己的脚本
        #   最后一条 `0x0002 STOP_ENTRY -> 36509` 会被引擎回写到该对象的
        #   wTriggerScript (play.c:153), 从此**再也走不到 0x001F 加物品**;
        #   快照里还有 123 个 sState=0 的隐形箱, play.c:467 要求 sState>0
        #   才可能触发 -> 走过去也按不出东西。这两种箱子直接不认, 省掉
        #   "走过去 + 站旁边 + 按空格 + 记账" 的一整趟冤枉路。
        #   开关: 环境变量 PAL_CHEST_MEM > bot_config.json > 默认开
        self.CHEST_MEM = cfg_bool("chest_mem", True)
        self._mem_dead = {}       # 场景 -> {内存判定"白跑"的箱子 K}
        self._mem_warned = False  # 体检不可用只提示一次
        self._chest_alive = {}    # (scene,K) -> 内存说"还满"却打不开的次数
        self._objmem = _ObjMem()  # 只读对象表访问器 (open() 惰性)
        self.chest_memo_start()   # ★ 开局就把已开的箱子塞进 chest_dead
        # ---- ★ v19.0 要素台账 (feat_log.json, 按「编号」记每一次战斗) ----
        #   编号从哪来: 游戏引擎**不给**要素任何编号/名字, 由 tools/build_scene_info.py
        #   从数据里派生 —— id = 事件对象的全局序号 (1..5077, 由 rgScene[] 区间唯一
        #   确定, 重进场景/读档不变), 编号 = `场景号-序号` (如 221-3813);
        #   名字: 怪点 = 0x0007 的队伍号 -> 敌人名, 拾取点 = 0x001F 的物品ID -> 物品名。
        #   台账同时记 K 与名字, 于是"哪些怪点打过/几次/哪些没打过"一眼可查
        #   (报告: tools/feat_report.py)。
        #   开关: 环境变量 PAL_FEAT_LOG > bot_config.json > 默认开
        self.FEAT_LOG = cfg_bool("feat_log", True)
        self.feat_log = featlog.FeatLog() if self.FEAT_LOG else None
        # ---- 总胜场 (win_total.json): 跨重启累计, 可用 win_reset.flag 清空 ----
        self.win_log = winlog.WinLog()
        self._win_total = self.win_log.total()
        # ---- ★ v19.3/v3 战斗收益 (exp_log.json): 每场经验/金钱 + 会话累计 ----
        #   快照口径见 bot_explog 模块头。机器人是唯一写者, GUI 只读。
        #   ★ 会话累计改为**逐场累加** (旧写法"当前-基线"遇升级/满级循环/读档
        #     会整段作废, 把累计反复清零 -> 时均明显偏小)。
        self.gain_log = gainlog.GainLog()
        self._gain_t0 = time.time()     # 会话起点 (每小时平均的分母)
        self._gain_exp_tot = 0          # 会话累计经验 (逐场累加, 单人口径)
        self._gain_cash_tot = 0         # 会话累计金钱 (逐场累加)
        self._gain_bad = 0              # 会话内跳过的脏数据场次
        self._gain_pre = None           # 上一场战前快照
        try:
            self._levelup_tab = self.mem.levelup_table() \
                if hasattr(self.mem, "levelup_table") else None
        except Exception:
            self._levelup_tab = None    # 游戏还没起来/读不到 -> 记账时惰性重读
        self.log("[配置] 要素台账 = %s | 累计总胜场 = %d 场%s"
                 % ("开 (按编号记每次战斗)" if self.FEAT_LOG else "关",
                    self._win_total,
                    " (清空请求待兑现)" if self.win_log.pending_reset() else ""))
        self._inv_reader = None   # bot_itemmon.ItemReader (惰性, 只读内存)
        self._inv_names = {}
        self._mon_goal = None     # (scene, 走位K, 怪点K, 设定时刻, 设定时步数)
        self._chest_goal = None   # (scene, 宝箱K, 站格K, 朝向键, 设定时刻, 设定时步数)
        self._last_fight_end = 0.0
        self._pick_n5 = 0
        # ---- 未访问偏好 + 定期探索配额 (2026-10-04) ----
        # 开关: 环境变量 PAL_EXPLORE > bot_config.json > 默认开 (GUI 有勾选框)
        self.explore = cfg_bool("explore", True)
        self.EXPLORE_EVERY_BATTLES = cfg_int(
            "explore_every", self.EXPLORE_EVERY_BATTLES, lo=0)
        self._explore_at = None    # 打满这么多场就触发 (None = 还没武装)
        self._explore_done = 0.0   # 上次**发起**探索的时刻
        self._explore_goal = None  # (scene, 目标K, 设定时刻, 设定时步数)
        if self.explore and self.EXPLORE_EVERY_BATTLES > 0:
            self.log("[配置] 定期探索 = 开: 每 %d 场强制去一次没走过的区域 "
                     "(距当前 ≥%d 格, 目标 %d 秒内没走到就放弃)"
                     % (self.EXPLORE_EVERY_BATTLES, self.EXPLORE_MIN_D,
                        int(self.EXPLORE_TTL)))
        else:
            self.log("[配置] 定期探索 = 关 (只在最近的目标之间刷, 不主动走生路)")
        if getattr(self, "REVISIT_LAMBDA", 0.0) > 0:
            self.log("[配置] 寻路避重 = 开 (λ=%.2f, 上限 %d 次): 同长度时优先走"
                     "没走过的格" % (self.REVISIT_LAMBDA, self.REVISIT_CAP))
        else:
            self.log("[配置] 寻路避重 = 关 (最短路径, 同一条走廊会反复走)")
        self._elem_t = 0.0
        self._slog_t = {}         # 怪点日志节流
        self._nearest_hint = None
        self._live_t = 0.0
        self._live_cache = None
        self._live_moved = {}     # (scene,静态K) -> 实时K, 只用于日志提示
        self._live_warned = False
        self.stat = {"mon_hit": 0, "mon_visit": 0, "chest_ok": 0,
                     "chest_try": 0, "chest_empty": 0, "fallback": 0,
                     "scene_chg": 0, "rope_ok": 0, "rope_try": 0,
                     "chest_dead_mem": 0, "chest_alive_miss": 0,
                     "feat_mark": 0, "far_battle": 0, "feat_pick": 0}
        self._scene_links = {}      # (旧场景, 门K) -> 新场景 (穿门学习, 持久化)
        self._link_file = os.path.join(BASE, "scene_links.json")
        # ---- ★ 驻留练级 (2026-10-20, 用户要求) ----
        #   原话: 「在某个地方练级的时候, 机器人不会主动离开, 并且会自动避开
        #   出入口, 以防误离开」。三层防护, 缺一层就会重演 10-20 事故
        #   (在十里坡练了一夜, 早上自己漂到场景 2/4 来回横跳 512 次):
        #     ① 主动层: 不迁移 / 不换场景 / 不爬绳 / step 里每帧复位 migrate
        #     ② 被动层: 出口禁区 pad=2 (scene_exits 实时扫脚本里的换景指令),
        #        且门禁分级**永不降到「无门禁」**
        #     ③ 兜底层: 真被脚本/没登记的门送出去 -> 按 scene_links 走回来
        #   开关: 环境变量 PAL_PIN_SCENE > bot_config.json > 0 (不驻留)
        #   ★ 取值语义: 0 = 不驻留; >0 = 钉住这个场景号; **-1 = 钉住「启动时
        #     所在的场景」** (GUI 勾了驻留但没填号时就是它 —— 用户把队伍带到
        #     练级点、点开始, 就钉在那儿了, 完全不用知道场景号是多少)。
        _pin_v = cfg_int("pin_scene", 0)
        self._pin_auto = _pin_v < 0
        self.pin_scene = max(0, _pin_v)
        self._pin_back = False      # 正在「走回驻留场景」的模式
        self._pin_back_n = 0        # 连续返回失败次数
        self._pin_open_tmp = False  # 回场时临时打开的穿门
        self._pin_warn_t = 0.0      # 回场告警节流
        self._pin_hold_t = 0.0      # 「拦下迁移」日志节流
        self._exit_cache = None     # (scene, 时刻, 出口清单) 实时扫描缓存
        if self.pin_scene:
            self.auto_scene = False          # 决策层换场景整个关掉
            self.log("[驻留] ★ 已钉住场景 %d: 不主动换场景 / 出入口 %d 格禁区 / "
                     "被送出去会自动走回来" % (self.pin_scene, self.PIN_BAN_PAD))
            self.log("[驻留]    (想解除: GUI 把「驻留练级」的勾去掉再启动, "
                     "或把 bot_config.json 里的 pin_scene 改回 0)")
        elif self._pin_auto:
            self.auto_scene = False          # 决策层换场景整个关掉
            self.log("[驻留] ★ 驻留 = 「启动时所在的场景」: 落地即钉住, "
                     "不再迁移/换场景/爬绳")
        self._load_scene_info()
        self._init_pilot()

    # ------------------------------------------------------------------
    #  要素清单
    # ------------------------------------------------------------------
    def _load_scene_info(self):
        path = os.path.join(BASE, "scene_info.json")
        if not os.path.exists(path) or os.path.getsize(path) < 100:
            # ★ v19.0: 生成器默认走**离线 SSS.MKF** (不需要游戏开着, 也不受
            #   "已开过的箱子被引擎改写脚本"影响 -> 清单永远是全量的)。
            self.log("[要素] scene_info.json 不存在 -> 从离线 SSS.MKF 生成")
            try:
                r = subprocess.run(
                    [sys.executable, os.path.join(BASE, "tools",
                                                  "build_scene_info.py")],
                    cwd=BASE, capture_output=True, timeout=120,
                    env=dict(os.environ, PYTHONIOENCODING="utf-8"))
                if r.returncode != 0:
                    self.log("[要素] 生成失败 -> 退回 v15 巡逻模式")
                    self.elem_mode = False
                    return
            except Exception as e:
                self.log("[要素] 生成异常(%s) -> 退回 v15 巡逻模式" % e)
                self.elem_mode = False
                return
        try:
            with open(path, encoding="utf-8") as f:
                self.scene_info = json.load(f)
        except Exception as e:
            self.log("[要素] 清单读取失败(%s) -> 退回 v15 巡逻模式" % e)
            self.elem_mode = False
            return
        nm = sum(len(v.get("monsters", [])) for v in self.scene_info.values())
        nc = sum(len(v.get("chests", [])) for v in self.scene_info.values())
        nid = sum(1 for v in self.scene_info.values()
                  for m in v.get("monsters", []) if m.get("id"))
        self.log("[要素] 清单载入: %d 个场景 / 怪点 %d (带编号 %d) / 拾取点 %d "
                 "(数据来自游戏原始 SSS.MKF, 不受开箱改写影响)"
                 % (len(self.scene_info), nm, nid, nc))
        if nm and nid < nm:
            self.log("[要素] ⚠ 有 %d 个怪点没有编号 -> 要素台账会退化成按坐标记"
                     "(删掉 scene_info.json 重启即可重新生成)" % (nm - nid))

    # ------------------------------------------------------------------
    #  场景调度 (自动换场景) —— 见 scene_pilot.py
    # ------------------------------------------------------------------
    def _init_pilot(self):
        """载入 tools/scene_score.py 生成的评分表, 装配调度器。"""
        scores = {}
        path = os.path.join(BASE, "scene_score.json")
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    scores = (json.load(f) or {}).get("scenes") or {}
            except Exception as e:
                self.log("[调度] 评分表读取失败(%s) -> 用默认耐心" % e)
        best = sorted(scores.items(), key=lambda kv: -kv[1].get("expect", 0))[:3]
        if scores:
            self.log("[调度] 场景评分载入 %d 个场景; 最优: %s"
                     % (len(scores),
                        "  ".join("场景%s(%.2f场/分)" % (k, v.get("expect", 0))
                                  for k, v in best)))
        else:
            self.log("[调度] 无 scene_score.json -> 自动换场景用默认耐心 "
                     "(可运行: python tools/scene_score.py 生成)")
        self.pilot = ScenePilot(scores, log=self.log)
        self.pilot.enabled = bool(self.auto_scene)
        try:
            with open(self._link_file, encoding="utf-8") as f:
                d = json.load(f)
            self._scene_links = {k: int(v) for k, v in (d or {}).items()}
        except Exception:
            self._scene_links = {}
        self._pilot_open_tmp = False      # 是否为换场景临时打开了穿门
        self._pilot_changing = False
        try:
            if self.scene is not None:
                self.log(self.pilot.on_scene(self.scene, time.time(),
                                             getattr(self, "battles_won", 0),
                                             "启动"))
        except Exception as e:
            self.log("[调度] 初始化异常(%s) -> 不做自动换场景" % e)
            self.auto_scene = False

    def enter_scene(self, sc, why=""):
        old = self.scene
        super().enter_scene(sc, why)
        if old is not None and sc != old and "退回" not in why:
            self._note_scene_link(old, sc, why)
        pilot = getattr(self, "pilot", None)
        if pilot is not None:
            self.log(pilot.on_scene(sc, time.time(), self.battles_won, why))
        # 换场景流程结束: 还原「临时打开」的穿门 (不改变用户自己的设置)
        self._pilot_finish_change()
        # ★ v18.9: 落地就体检一次宝箱 —— 已开空/不可触发的箱子本局不再认
        self._chest_mem_tick(sc)
        # ★ 驻留 (2026-10-20): 每次落地都核对是不是还在钉住的场景里
        self._pin_scene_tick(sc, why)

    # ==================================================================
    #  ★ 驻留练级 (2026-10-20)
    #  用户要求: 「在某个地方练级的时候, 机器人不会主动离开, 并且会自动避开
    #  出入口, 以防误离开」。十个字的意思都落到下面的三层防护上:
    #    ① 主动不离开 —— pick_target/tick/_pilot_tick/step 各自拦一道
    #    ② 避开出入口 —— scene_exits 实时扫「触发脚本走得到 0x0059」的对象,
    #       不等静态快照, 也不管它有没有贴图
    #    ③ 万一离开 —— 按 scene_links 门路图走回 pin_scene
    # ==================================================================
    def _pin_on(self):
        return bool(getattr(self, "pin_scene", 0))

    def home_active(self):
        """驻留时禁用 mem2 的「回家」—— 它和 _pin_back 是同一件事的两种实现,
        同时开会互相抢目标 (一个往 pin 走, 一个往"来的那个门"走)。"""
        if self._pin_on():
            if getattr(self, "home", None):
                self.home = None
            return False
        return super().home_active()

    def _trap_escape(self, cur, now):
        """困区突围。★ 父类挑的是「未禁门可达域里最远的格」—— 那个格十有八九
        就是场景另一头的出口 (最远的点往往是门口)。驻留时必须把目标拉回禁区内,
        宁可少突围一次, 也不能顺着突围把自己送出去。"""
        super()._trap_escape(cur, now)
        if not self._pin_on():
            return
        if self.migrate:
            self.migrate = False
            self.log("[困区] 驻留: 父类那条逃逸路要换场景 -> 否决, 继续找场内目标")
        tg = getattr(self, "_trap_goal", None)
        if tg is None:
            return
        goal = tg[1]
        ban, reach = self.plan_ctx(cur)
        if goal in reach and goal not in ban:
            return
        cand = [c for c in reach if c not in ban and cheb(c, cur) > 0]
        if not cand:
            self._trap_goal = None
            self.log("[困区] 驻留: 禁区之外没有更远的格 -> 放弃这次突围")
            return
        g2 = max(cand, key=lambda c: cheb(c, cur))
        self._trap_goal = (self.scene, g2, tg[2])
        self.log("[困区] 驻留: 原突围点 K%s 在禁区/不通 -> 改 K%s" % (goal, g2))

    def scene_exits(self, sc=None, fresh=False):
        """本场景**所有**会换场景的触发点 —— 实时读, 不看静态快照。

        判据只有一条: 触发脚本的控制流**走得到 OP_CHANGESCENE (0x0059)**。
        故意不看 sprite / wTriggerMode, 因为:
          · 十里坡的门是隐形对象 (spr=0), 贴图画在地图背景里;
          · 0<mode<4 = 按键搜索型 (绳子), mode>=4 = 走近触发型 (门/洞);
          · 还有**脚本动态创建**的出口 —— 静态 scene_info.json 里压根没有。
        2026-10-20 事故实证: 踩响的是 K(-79,89), 而 scene_info 里场景 7 只有
        [-63,179] / [-4,13] 两个洞, 静态表全漏 -> 只能实时扫。

        返回 [{"K":(a,b), "spr":, "mode":, "trig":, "how":"near"/"search"}];
        **读不到对象表/脚本表 -> None** (调用方必须区分"没有出口"与"读不到",
        后者不能当成"安全"来处理)。
        """
        sc = self.scene if sc is None else sc
        if sc is None:
            return None
        now = time.time()
        c = getattr(self, "_exit_cache", None)
        if (not fresh and c is not None and c[0] == sc
                and now - c[1] < self.PIN_EXIT_TTL):
            return c[2]
        try:
            objs = self.scene_objects(sc)
        except Exception:
            objs = None
        if not objs:
            self._exit_cache = (sc, now, None)
            return None
        ptr, n_sc = self._script_tab()
        if not ptr or not n_sc:
            self._exit_cache = (sc, now, None)
            return None
        out = []
        for f in objs:
            spr, trig, auto, state, mode = f[8], f[4], f[5], f[6], f[7]
            hit = set()
            for e in (trig, auto):
                if e:
                    hit |= self.script_hits(e, (OP_CHANGESCENE,))
            if OP_CHANGESCENE not in hit:
                continue
            a, b = kof(f[1], f[2])
            out.append({"K": (a, b), "spr": spr, "mode": mode, "trig": trig,
                        "how": "near" if mode >= 4 else "search"})
        self._exit_cache = (sc, now, out)
        return out

    def door_ban(self, pad):
        """门禁。驻留模式**额外**把实时扫出来的所有出口圈起来。

        父类只在「穿门模式」下才撤门禁; 驻留是相反诉求, 所以这里先拿父类的
        结果, 再把 scene_exits 叠上去 —— 两者同时开时驻留优先 (安全 > 效率)。
        """
        s = super().door_ban(pad)
        if not self._pin_on():
            return s
        for e in (self.scene_exits() or ()):
            a, b = e["K"]
            for ua in range(-pad, pad + 1):
                for ub in range(-pad, pad + 1):
                    s.add((a + ua, b + ub))
        return s

    def graded_reach(self, cur):
        """门禁分级。驻留模式下**绝不降到「无门禁」**。

        父类三档: pad=0 -> 仅实测门 -> 无门禁, 判据是"可达域 >= REACH_MIN
        (2000 格)"。小场景/室内几乎必然小于 2000, 于是门禁被撤, 队伍直奔门口
        —— 10-20 那一夜日志里 765 次 `无门禁` 就是这个口子。

        驻留模式改成 pad=2 -> pad=1 -> pad=0, 而且"够不够用"用**比例**判
        (相对无门禁可达域), 不是 2000 格绝对值:
          · 十里坡 5891 格 -> 门槛 3534, pad=2 后 5816 -> 直接停在 pad=2
          · 小房间 480 格 -> 门槛 288, pad=2 后 405 -> 还是 pad=2
        只有窄到"门锁咽喉"的地方才会降级 (那本来就该降), 但永远不撤门禁。
        """
        if not self._pin_on():
            return super().graded_reach(cur)
        blk = set(self.blocked) - {cur}
        ref = len(self.reach(cur, blk))              # 无门禁基准
        need = max(self.PIN_REACH_ABS, int(ref * self.PIN_REACH_KEEP))
        best = None
        for tag, pad in (("驻留pad=%d" % self.PIN_BAN_PAD, self.PIN_BAN_PAD),
                         ("驻留pad=1", 1),
                         ("驻留pad=0", 0)):
            b = (self.door_ban(pad) | blk) - {cur}
            rr = self.reach(cur, b)
            if best is None or len(rr) > len(best[2]):
                best = (b, tag, rr)
            if len(rr) >= need:
                break
        return best

    def _pin_scene_tick(self, sc, why=""):
        """落地核对: 还在钉住的场景里吗? 不在就进入「走回去」模式。"""
        # ★ -1 = 钉住「启动时所在的场景」: 第一次落地就地取材
        if getattr(self, "_pin_auto", False) and not self.pin_scene and sc:
            self.pin_scene = int(sc)
            self.log("[驻留] ★ 已自动钉住当前场景 %d (%s): 从这里开始不再离开"
                     " (下次想钉别处就把队伍带过去再启动)"
                     % (self.pin_scene, why or "启动"))
        if not self._pin_on():
            return
        self._exit_cache = None          # 换场景了, 出口清单必须重扫
        # ★ 驻留时「回家」(mem2 那套)一律作废: 它也是"走回某个场景",
        #   两套同时开会互相抢目标。统一由 _pin_back 管。
        if getattr(self, "home", None):
            self.home = None
        if sc == self.pin_scene:
            if getattr(self, "_pin_back", False):
                self.log("[驻留] ★ 已回到驻留场景 %d (%s)"
                         % (sc, why or "?"))
            self._pin_back = False
            self._pin_back_n = 0
            if getattr(self, "_pin_open_tmp", False):
                if hasattr(self, "_open_doors_user"):
                    self.open_doors = self._open_doors_user
                self._pin_open_tmp = False
            return
        self._pin_back = True
        self.log("[驻留] ★★ 意外离开驻留场景 %d -> 场景 %d (%s) -> 立刻走回去"
                 % (self.pin_scene, sc, why or "?"))

    def _pin_link_door(self, cur):
        """在本场景里找一个「出去能到 pin_scene」的出口格 —— 优先真踩过的。

        走法: scene_links 的键是 "旧场景:A,B"、值是 "新场景"。只挑键的场景
        部分 == 当前场景、值 == pin_scene 的那些, 那就是**一步能回去**的门。
        没有 -> None (调用方退化为"随便找个出口赌一把")。
        """
        links = getattr(self, "_scene_links", None) or {}
        hit = []
        for key, tgt in links.items():
            try:
                if int(tgt) != self.pin_scene:
                    continue
                sc_s, kk = str(key).split(":", 1)
                if int(sc_s) != self.scene:
                    continue
                a, b = [int(x) for x in kk.split(",")]
            except Exception:
                continue
            hit.append((a, b))
        if not hit:
            return None
        ks = self.kwalk(self.map) if self.map is not None else set()
        hit = [k for k in hit
               if any((k[0] + da, k[1] + db) in ks
                      for da in (-1, 0, 1) for db in (-1, 0, 1))]
        if not hit:
            return None
        hit.sort(key=lambda k: cheb(k, cur))
        return hit[0]

    def _pin_back_fail(self, why=""):
        """回不去时的收尾: 计数 + 告警, 连败到上限就放弃 (别把时间全耗在门上)。"""
        self._pin_back_n = getattr(self, "_pin_back_n", 0) + 1
        now = time.time()
        if self._pin_back_n >= self.PIN_BACK_MAX:
            self._pin_back = False
            if now - getattr(self, "_pin_warn_t", 0.0) > 60:
                self._pin_warn_t = now
                self.log("[驻留] !!! 连续 %d 次走不回场景 %d (%s) -> 停止尝试, "
                         "请手动把队伍带回, 否则只会在场景 %d 里原地刷"
                         % (self._pin_back_n, self.pin_scene, why or "无路",
                            self.scene))
            return
        if now - getattr(self, "_pin_warn_t", 0.0) > 30:
            self._pin_warn_t = now
            self.log("[驻留] 走不回场景 %d (%s) 第 %d 次 -> 换个出口再试"
                     % (self.pin_scene, why or "无路", self._pin_back_n))

    def _pin_return_target(self, cur=None):
        """「走回驻留场景」模式下, 返回该走的那一格 (出口旁的可站格)。

        ★ 这里**故意不禁门**: 目标是出门, 禁门等于原地罚站。
        """
        if cur is None:
            cur = self.mem.state()["K"]
        ks = self.kwalk(self.map) if self.map is not None else set()
        if not ks:
            return None
        want = self._pin_link_door(cur)
        if want is not None:
            # 优先: 有"真踩过能回去"的门 -> 直奔它 (站到门格上即可)
            if cheb(want, cur) == 0:
                return want              # 已经站上去了, 再走一步触发
            if want in ks:
                return want
        ex = self.scene_exits()
        cand = []
        if ex:
            for e in ex:
                k = e["K"]
                if k in ks:
                    cand.append(k)
                else:
                    for da, db in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        t = (k[0] + da, k[1] + db)
                        if t in ks:
                            cand.append(t)
                            break
        if not cand:
            # 对象表里没有出口 (或读不到) -> 退回静态门表
            for src in (getattr(self, "report_doors", {}) or {},
                        getattr(self, "known_doors", {}) or {}):
                for k in (src.get(self.scene, ()) or ()):
                    k = (int(k[0]), int(k[1]))
                    if k in ks:
                        cand.append(k)
        if not cand:
            self._pin_back_fail("本场景找不到任何出口")
            return None
        cand = [c for c in cand if cheb(c, cur) > 0] or cand
        cand.sort(key=lambda c: cheb(c, cur))
        # 回场要能穿门: 临时打开 (回到 pin 场景时 _pin_scene_tick 会还原)
        if not self.open_doors:
            self._open_doors_user = False
            self.open_doors = True
            self._pin_open_tmp = True
        return cand[0]

    # ------------------------------------------------------------------
    #  ★ v18.9 宝箱内存体检: "是空箱子就完全忽略" (用户 2026-10-04 要求)
    #  真机取证 (_src/sdlpal + 1058 箱全表实测):
    #    · 开箱脚本最后一条 = 0x0002 STOP_ENTRY -> 36509, play.c:153 把返回值
    #      回写到该对象的 wTriggerScript -> 从此走不到 0x001F 加物品;
    #      实测"脚本没了 ADD_ITEM"与"trig 被改写成同一个常量"935/935 等价。
    #    · sState<=0 的箱子 (快照 123 个隐形箱) play.c:467/:107 根本不触发。
    #  这两类走过去纯属白跑, 直接不认 —— 连"站旁边按一次空格"都省掉。
    # ------------------------------------------------------------------
    def chest_mem_scan(self, sc=None, chests=None):
        """读内存体检本场景宝箱。

        返回 {"dead": set(K), "why": {K:原因}, "n": 总数};
        **读不到对象表/脚本表 -> None** (调用方保持旧行为, 绝不瞎跳过)。
        """
        if not getattr(self, "CHEST_MEM", True):
            return None
        sc = getattr(self, "scene", None) if sc is None else sc
        try:
            cs = self.scene_chests(sc) if chests is None else chests
        except Exception:                   # 裸影子对象 (测试) 没有 scene_info
            return None
        if not cs:
            return {"dead": set(), "why": {}, "n": 0}
        om = getattr(self, "_objmem", None)
        if om is None or not om.open():
            return None
        try:
            verd = om.chest_verdicts(sc, cs)
        except Exception:
            return None
        if not verd:
            return None
        dead, why = set(), {}
        for K, (v, r) in verd.items():
            if v == "dead":
                dead.add(K)
                why[K] = r
        return {"dead": dead, "why": why, "n": len(cs)}

    def _chest_mem_tick(self, sc=None):
        """换场景时的一次体检: 结论存 self._mem_dead[场景] 供 _pick_chest 跳过。"""
        sc = getattr(self, "scene", None) if sc is None else sc
        self._mem_dead = {}                 # 只保留当前场景的结论 (防跨场景串味)
        if not getattr(self, "CHEST_MEM", True):
            return
        r = self.chest_mem_scan(sc)
        if r is None:
            if not getattr(self, "_mem_warned", False):
                self._mem_warned = True
                self.log("[宝箱] 内存体检不可用(%s) -> 照旧按台账+试开来判断"
                         % (getattr(getattr(self, "_objmem", None), "err", "?")
                            or "?"))
            return
        if not r["dead"]:
            return
        self._mem_dead = {sc: r["dead"]}
        self.stat["chest_dead_mem"] += len(r["dead"])
        ks = sorted(r["dead"])
        self.log("[宝箱] ★内存体检: 场景%d 有 %d/%d 个箱子已开空/不可触发 -> "
                 "直接跳过 (例: K%s %s)"
                 % (sc, len(ks), r["n"], ks[0], r["why"].get(ks[0], "")))

    def chest_mem_alive(self, K, sc=None):
        """内存里这个箱子还能开吗?

        True  = 还能开 (那么"按空格没反应"是我方站位/丢键问题, 不是空箱)
        False = 已开空 / 不可触发
        None  = 读不到 (调用方保持旧行为)
        """
        sc = getattr(self, "scene", None) if sc is None else sc
        try:
            k = (int(K[0]), int(K[1]))
            cs = self.scene_chests(sc)
        except Exception:                   # 裸影子对象 (测试) 一律"读不到"
            return None
        for c in cs:
            if (c["K"][0], c["K"][1]) != k:
                continue
            om = getattr(self, "_objmem", None)
            if om is None or not om.open():
                return None
            try:
                v, _r = om.chest_verdict(sc, c)
            except Exception:
                return None
            if v == "live":
                return True
            if v == "dead":
                return False
            return None
        return None

    def _pilot_finish_change(self):
        self._pilot_changing = False
        if getattr(self, "_pilot_open_tmp", False):
            self.open_doors = getattr(self, "_open_doors_user", False)
            self._pilot_open_tmp = False

    def handle_door(self, K_door, new_scene, key):
        """记下"从哪个门出去的", 供 enter_scene 学习场景连通关系。"""
        self._last_door_K = K_door
        return MemBot4.handle_door(self, K_door, new_scene, key)

    def _note_scene_link(self, old, new, why=""):
        """记录「从这个场景的这个门出去会到哪」—— 为将来的跨场景路由攒地图。"""
        try:
            K = getattr(self, "_last_door_K", None)
            if K is None:
                return
            key = "%d:%d,%d" % (old, K[0], K[1])
            if self._scene_links.get(key) != new:
                self._scene_links[key] = new
                with open(self._link_file, "w", encoding="utf-8") as f:
                    json.dump(self._scene_links, f, ensure_ascii=False)
                self.log("[调度] 记下门路: 场景%d 门K%s -> 场景%d (%s)"
                         % (old, K, new, why))
        except Exception:
            pass

    def _pilot_tick(self):
        """每个 tick 检查一次「这个场景还值不值得待」。"""
        if self._pin_on():
            return                       # 驻留: 不问"值不值得待", 答案永远是待
        if self.pilot is None or not self.auto_scene or not self.elem_mode:
            return
        if self.migrate:
            return                       # 已经在去门的路上
        # ★ v18.2: 正在去爬绳子的路上 -> 别同时再发起一次换场景 (绳子优先)
        if getattr(self, "_rope_goal", None) is not None:
            return
        # ★ 2026-09-30: 迁移没换成时 migrate_cd 会被推到 +600s, 这里必须尊重它。
        #   原来不检查 -> 失败后 pilot 的 CHANGE_CD(180s) 一到就重新武装,
        #   每 3 分钟白跑一趟门 (实测一轮 13 次尝试 0 成功, 白烧 ~20% 活动)。
        if time.time() <= getattr(self, "migrate_cd", 0.0):
            return
        need, why = self.pilot.decide(time.time(), self.battles_won)
        if not need:
            return
        try:
            cur = self.mem.state()["K"]
        except Exception:
            return
        door = self.pick_migrate_door(cur)
        if door is None:
            n = self.pilot.note_dud()
            self.log("[调度] 判定「该走」(%s) 但本场景无已知门 -> 只能留下 "
                     "(该场景低效标记 x%d)" % (why, n))
            self.pilot.note_fail(time.time())
            return
        # 走门 = 穿门, 临时打开穿门模式(否则踩门会被弹回来)
        if not self.open_doors:
            self._open_doors_user = False
            self.open_doors = True
            self._pilot_open_tmp = True
        else:
            self._open_doors_user = True
        self.pilot.note_change(time.time())
        self._pilot_changing = True
        self.migrate = True          # ★ 交给基类 tick 的迁移分支: 走去门 + 踩门
        self.stat["scene_chg"] += 1
        self.log("[调度] ★ 换场景 (第%d次): %s -> 走最近的门 K%s"
                 % (self.stat["scene_chg"], why, door))

    def _on_migrate_end(self, success):
        """迁移未成功 -> 让调度器进入静默期, 别再每 3 分钟重蹈一次。"""
        if success or getattr(self, "pilot", None) is None:
            return
        self.pilot.note_fail(time.time())
        if getattr(self, "_pilot_open_tmp", False):
            self.open_doors = self._open_doors_user       # 还原用户设定
            self._pilot_open_tmp = False

    # ------------------------------------------------------------------
    #  绳子作为迁移手段: 无门可走时 (试炼窟层间只靠绳子连通)
    # ------------------------------------------------------------------
    def _is_rope_k(self, k):
        """K 处是不是一根已知绳子 (试炼窟的绳子常被当成"门"学进 door 表)。"""
        try:
            k = (int(k[0]), int(k[1]))
        except Exception:
            return False
        for r in self.scene_ropes():
            if (r["K"][0], r["K"][1]) == k:
                return True
        return False

    def pick_migrate_door(self, cur):
        """选迁移目标。四种情况依次退让:

        ① 常规门表里有门 -> 用它; 但若那个 K 其实是**绳子**(搜索型换景对象),
           就标成绳子走空格攀爬 (踩上去不会触发, 以前会"到了门边一直踩")。
        ② 完全没有门 -> 用最近的**黑洞** (scene_info["portals"] 里隐形、
           踩上自动触发的换景点, 2026-10-04 用户指认) —— 走到洞格就是进去。
        ③ 没有黑洞 -> 直接用最近的绳子换层 (试炼窟层间只靠绳子连通)。
        ④ 都没有 -> None。
        """
        self._mig_is_rope = False
        self._mig_is_hole = False
        door = MemBot.pick_migrate_door(self, cur)
        if door is not None:
            if getattr(self, "use_ropes", False) and self._is_rope_k(door):
                self._mig_is_rope = True
                self.log("[绳子] 迁移目标 K%s 是搜索型换景(=绳子), "
                         "按空格攀爬而不是踩上去" % (door,))
            return door
        hole = self._pick_hole(cur)
        if hole is not None:
            self._mig_is_hole = True
            self.log("[黑洞] 本场景无已知门 -> 踩洞 K%s 换景 "
                     "(走到洞格自动触发)" % (hole,))
            return hole
        if not getattr(self, "use_ropes", False):
            return None
        rope = self._pick_rope(cur)
        if rope is None:
            return None
        self._mig_is_rope = True
        self.log("[绳子] 本场景无已知门/黑洞 -> 改用绳子 K%s 换层" % (rope,))
        return rope

    def migrate_probe(self, doorK):
        """迁移目标是绳子时走 rope_probe (按空格攀爬), 否则照旧踩门/踩洞。

        ★ 2026-10-02 新增兜底: 门走不通时再试绳子。真机日志显示, 光有"没有门
          才用绳"是不够的 —— 试炼窟的绳子会被学成"门"进 door 表, 于是
          pick_migrate_door 一直返回它, migrate_probe 到了旁边踩不响,
          每轮"放弃本轮(未按键)", 绳子分支永远走不到 (绳子0(试0))。
        ★ 黑洞 (v18.7): 踩上洞格自动换景, step() 判"场景变了"=door,
          基类 migrate_probe 原样兼容; 失败也走绳子兜底。
        """
        if getattr(self, "_mig_is_rope", False):
            self._mig_is_rope = False
            return self.rope_probe(doorK)
        ok = MemBot.migrate_probe(self, doorK)
        if ok or not getattr(self, "use_ropes", False):
            return ok
        try:
            rope = self._pick_rope(self.mem.state()["K"])
        except Exception:
            rope = None
        if rope is None:
            return False
        self.log("[绳子] 门 K%s 走不通 (%s) -> 改用绳子 K%s 换层"
                 % (doorK, getattr(self, "_mig_why", "?"), rope))
        return self.rope_probe(rope)

    def scene_monsters(self, sc=None):
        """当前场景的怪点清单。

        ★ 优先**实时**从内存读真实坐标(怪会游走, 静态快照必然过期);
        读不到时退回 scene_info.json 的静态坐标。
        """
        sc = self.scene if sc is None else sc
        if sc == self.scene and getattr(self, "elem_mode", False):
            return self._live_monsters()
        return (self.scene_info.get(str(sc)) or {}).get("monsters") or []

    def _live_monsters(self):
        now = time.time()
        if self._live_cache is not None and now - self._live_t < self.LIVE_TTL:
            return self._live_cache
        self._live_t = now
        self._live_cache = self._read_live_monsters()
        return self._live_cache

    def _read_live_monsters(self):
        """实时读当前场景的怪点。

        判定: 对象的 wTriggerScript ∈ 静态清单里怪点的触发脚本集合
              (用 trig 而不是坐标做身份, 因为坐标会变)
              且 wSpriteNum != 0 且 sState == 2 (2 = 正常在场; 被打掉是 -2)。
        """
        static = (self.scene_info.get(str(self.scene)) or {}).get("monsters") or []
        if not static:
            return []
        wl = set(m.get("trig", 0) for m in static)
        wl.discard(0)
        if not wl:
            return list(static)
        # ★ v19.0: 静态表里带了编号(id)与敌人名(ename), 实时条目要继承下来,
        #   否则"按编号记台账"在实时模式下会退化成按 K 记账。
        by_trig = {}
        for m in static:
            by_trig.setdefault(m.get("trig"), m)
        if not self._objmem.open():
            if not self._live_warned:
                self._live_warned = True
                self.log("[怪点] 实时坐标不可用(%s) -> 退回静态快照"
                         % getattr(self._objmem, "err", "?"))
            return list(static)
        try:
            objs = self._objmem.scene_objects(self.scene)
        except Exception:
            return list(static)
        if not objs:
            return list(static)
        out = []
        moved = 0
        for f in objs:
            spr, trig, state, mode = f[8], f[4], f[6], f[7]
            if not spr or trig not in wl or state != 2:
                continue
            a, b = kof(f[1], f[2])
            st = by_trig.get(trig) or {}
            out.append({"K": [a, b], "spr": spr, "mode": mode, "state": state,
                        "trig": trig, "auto": f[5], "chase": True,
                        "id": st.get("id"), "ename": st.get("ename", "")})
        if not out:
            # 全都在恢复期(或读歪了): 交回静态坐标, 让冷却机制照常工作
            return list(static)
        # 只做一次「坐标漂移」提示, 方便确认实时读取生效
        if not self._live_moved:
            for m in static:
                k = tuple(m["K"])
                for o in out:
                    if o["trig"] == m.get("trig") and o["spr"] == m.get("spr"):
                        if cheb(k, tuple(o["K"])) > 2:
                            self._live_moved[k] = tuple(o["K"])
                        break
            if self._live_moved:
                k0 = next(iter(self._live_moved))
                self.log("[怪点] ★实时坐标生效: 场景%d 有 %d 个怪点已游走 "
                         "(例: 静态%s -> 实时%s); 本场景实时怪点 %d 个"
                         % (self.scene, len(self._live_moved), k0,
                            self._live_moved[k0], len(out)))
            else:
                self.log("[怪点] ★实时坐标生效: 场景%d 实时怪点 %d 个 "
                         "(与静态快照一致)" % (self.scene, len(out)))
        return out

    def scene_chests(self, sc=None):
        sc = self.scene if sc is None else sc
        return (self.scene_info.get(str(sc)) or {}).get("chests") or []

    # ------------------------------------------------------------------
    #  黑洞传送 (试炼窟等地面的洞): 踩上去自动触发换景, 与门的区别是隐形
    #  (sprite=0, 洞画在地图背景里)。build_scene_info 已把它们归入
    #  scene_info["portals"] (mode>=4 且脚本可达 CHANGE_SCENE), 此前无人消费。
    # ------------------------------------------------------------------
    def scene_portals(self, sc=None):
        """当前场景"黑洞"清单: 隐形 + 踩上触发 + 脚本换景。

        只用静态快照 scene_info.json (洞是地图景物不会动), 读不到返回 []。
        """
        sc = self.scene if sc is None else sc
        si = getattr(self, "scene_info", None) or {}   # 裸影子对象要兜底
        out = []
        for p in (si.get(str(sc)) or {}).get("portals") or []:
            if p.get("spr") == 0 and p.get("mode", 0) >= 4 \
                    and p.get("state", 0) > 0:
                out.append(dict(p))
        return out

    def _pick_hole(self, cur):
        """最近一个没被拉黑、旁边可站的黑洞; 没有返回 None。"""
        ks = self.kwalk(self.map)
        best = None
        bd = 10 ** 9
        for p in self.scene_portals():
            k = (p["K"][0], p["K"][1])
            d = max(abs(k[0] - cur[0]), abs(k[1] - cur[1]))
            if d > self.MIGRATE_DOOR_MAX:
                continue
            if getattr(self, "_door_fail", {}).get(k, 0) >= self.DOOR_FAIL_MAX:
                continue
            if not any((k[0] + da, k[1] + db) in ks
                       for da in (-1, 0, 1) for db in (-1, 0, 1)):
                continue
            if d < bd:
                bd, best = d, k
        return best

    # ------------------------------------------------------------------
    #  绳子攀爬 (试炼窟等): 无门可走时用绳子换层
    # ------------------------------------------------------------------
    def scene_ropes(self, sc=None):
        """当前场景绳子清单。优先实时读内存(对象表+脚本表), 失败兜静态快照。

        绳子是静态景物, 缓存 120s 足够; 跨场景自动失效。
        """
        sc = self.scene if sc is None else sc
        now = time.time()
        if self._rope_cache is not None:
            c_sc, c_t, c_lst = self._rope_cache
            if c_sc == sc and now - c_t < 120.0:
                return c_lst
        lst = []
        if self._objmem.open():
            try:
                lst = self._objmem.scene_ropes(sc)
            except Exception:
                lst = []
        if not lst:
            lst = [dict(r) for r in
                   (self.scene_info.get(str(sc)) or {}).get("ropes") or []]
        self._rope_cache = (sc, now, lst)
        if lst:
            self.log("[绳子] 场景%d 发现 %d 根绳子: %s"
                     % (sc, len(lst),
                        ["K%s" % (tuple(r["K"]),) for r in lst][:6]))
        return lst

    def _pick_rope(self, cur, max_d=None):
        """选一根能爬的绳子 (最近优先)。返回绳格 K 或 None。

        过滤: 冷却中 / 超出距离上限 / 旁边(含绳格本身)没有可走格。
        max_d 缺省用 ROPE_MAX_D(迁移用 200); 「遇到就爬」传一个小的值。
        """
        ks = self.kwalk(self.map)
        now = time.time()
        lim = self.ROPE_MAX_D if max_d is None else max_d
        best = None
        bd = 10 ** 9
        for r in self.scene_ropes():
            k = (r["K"][0], r["K"][1])
            key = (self.scene, k[0], k[1])
            if now < self.rope_cd.get(key, 0):
                continue
            d = max(abs(k[0] - cur[0]), abs(k[1] - cur[1]))
            if d > lim:
                continue
            # 绳子旁(或绳格本身)得有可走格, 否则根本站不进去
            if not any((k[0] + da, k[1] + db) in ks
                       for da in (-1, 0, 1) for db in (-1, 0, 1)):
                continue
            if d < bd:
                bd, best = d, k
        return best

    def _rope_stands(self, ropeK):
        """绳子的可站格(按离当前位置由近到远)。

        口径必须与 rope_probe 的 cands 完全一致 —— 否则会出现
        「走到 A 格, rope_probe 却选了 B 格」的来回空跑。
        ★ v18.3: 绕行标记(blocked)不再一票否决, 只作降序软惩罚 ——
        真机教训: 通往绳子的走廊被怪挡几次后绳位全进 blocked,
        走位/探测永远失败 (rope_probe 同款口径)。
        """
        ks = self.kwalk(self.map)
        out = []
        if ropeK in ks:
            out.append(ropeK)
        for da, db in self.ORTHO:
            c = (ropeK[0] + da, ropeK[1] + db)
            if c in ks and c not in out:
                out.append(c)
        try:
            cur = self.mem.state()["K"]
            out.sort(key=lambda c: (c in self.blocked, cheb(c, cur)))
        except Exception:
            pass
        return out

    def _rope_goal_target(self, cur):
        """在途的「主动攀爬」目标 -> 该走的下一格(就是绳子的可站格)。

        返回 None 有三种情况, 都表示"这条目标线放弃":
          ① 压根没有在途目标
          ② 目标已失效 (换了场景 / 绳子进了冷却 / 走了 ROPE_GOAL_TTL 还没到)
          ③ 绳子旁现在没有可站格了
        失效时会**顺手把 _rope_goal 清掉**, 所以调用方拿到 None 就能安心落回常规。
        """
        rg = getattr(self, "_rope_goal", None)
        if rg is None:
            return None
        key = (self.scene, rg[1][0], rg[1][1])
        if rg[0] != self.scene:
            self._rope_goal = None
            self.log("[绳子] 已换场景 -> 放弃上次的攀爬目标 K%s" % (rg[1],))
            return None
        if time.time() < (getattr(self, "rope_cd", None) or {}).get(key, 0):
            self._rope_goal = None
            self.log("[绳子] K%s 进了冷却 -> 放弃攀爬目标" % (rg[1],))
            return None
        if time.time() - rg[2] > self.ROPE_GOAL_TTL:
            self._rope_goal = None
            self.log("[绳子] 去绳子 K%s 走了 %.0fs 没到 -> 放弃"
                     % (rg[1], time.time() - rg[2]))
            return None
        st = self._rope_stands(rg[1])
        if not st:
            self._rope_goal = None
            self.log("[绳子] K%s 旁无可站格 -> 放弃攀爬目标" % (rg[1],))
            return None
        return st[0]

    def _rope_hunt(self):
        """★ v18.2 绳子最高优先级: 见到近处能爬的绳子就去爬 (用户要求)。

        与「无门可走时用绳子」(pick_migrate_door 的 ②) 的区别:
          那个要等 pilot 判「这层刷不动了」; 这个**不等**, 每个 tick 都看一眼,
          遇到就直接把绳子设成走位目标, 优先级高于怪点/宝箱/换场景。

        四道闸门 (防在两层之间横跳 / 防打断正在做的事):
          ① use_ropes + rope_hunt 都开
          ② 不在迁移途中(migrate) / 不在困区突围(_trap_goal)
          ③ 距上次爬成功 > ROPE_HUNT_CD, 距上次尝试 > ROPE_HUNT_GAP
          ④ 有 ≤ROPE_HUNT_D 格、不在冷却里、旁侧可站的绳子
        返回 True = 本 tick 已经发起了攀爬 (调用方直接收工)。
        """
        if not getattr(self, "use_ropes", False) or \
                not getattr(self, "rope_hunt", False):
            return False
        if self.migrate or getattr(self, "_trap_goal", None) is not None:
            return False
        # 战斗中/战斗结算窗口期: base tick 这些帧只按攻击键, 别在这里添乱
        if getattr(self, "not_battle_count", 99) < 3:
            return False
        now = time.time()
        if now < getattr(self, "_rope_ok_t", 0.0) + self.ROPE_HUNT_CD:
            return False
        if now < getattr(self, "_rope_hunt_t", 0.0) + self.ROPE_HUNT_GAP:
            return False
        try:
            cur = self.mem.state()["K"]
        except Exception:
            return False
        rope = self._pick_rope(cur, max_d=self.ROPE_HUNT_D)
        if rope is None:
            return False
        self._rope_hunt_t = now
        stands = self._rope_stands(rope)
        if not stands:
            if getattr(self, "rope_cd", None) is not None:
                self.rope_cd[(self.scene, rope[0], rope[1])] = (now + self.ROPE_CD)
            self.log("[绳子] K%s 旁无可站格 -> 冷却 %.0fs" % (rope, self.ROPE_CD))
            return False
        if cheb(cur, stands[0]) == 0:
            # 已经站在可爬位置 -> 立刻爬, 不用先"走过去"
            self.log("[绳子] ★遇到绳子 K%s 且已站好 -> 直接按空格攀爬" % (rope,))
            self.rope_probe(rope)
            return True
        self._rope_goal = (self.scene, rope, now)
        self.log("[绳子] ★遇到绳子 K%s (距 %d 格, 优先级最高) -> 去爬 "
                 "(站位 K%s)" % (rope, cheb(cur, stands[0]), stands[0]))
        return True

    def rope_probe(self, ropeK):
        """走到绳子旁/绳上, 按空格触发攀爬。返回是否换场景成功。

        尝试序列 (逐级退化, 谁有效下次优先用 -- _rope_method 记住):
          ① 站绳格上按空格  ② 站邻格面向绳子按空格
        按空格后若弹对话框("是否攀爬")再按一次空格确认默认项"是"。
        成功判据 = 内存里的场景号变化 (爬绳必然换层)。

        ★ v18.3 真机教训 (2026-10-02 试炼窟): 旧版把「走路失败」谎报成
          「空格没反应」—— 通往绳子的走廊有怪游走, walk_path 被挡记绕行,
          绕行标记把到绳位的路压塌, 结果一次空格都没按就上 300/900s 冷却;
          冷却又让「遇到就爬」哑火 (队伍站在绳子边 1 格也只能走开)。
          现在: 没按过空格 = 走位失败, 只给短冷却不算攀爬失败;
          只有真按了空格没换层才计 rope_fail/长冷却, 并留现场截图。
        """
        key = (self.scene, ropeK[0], ropeK[1])
        self.stat["rope_try"] += 1
        sc0 = self.scene
        ks = self.kwalk(self.map)
        # 站位候选: 绳格本身(可走时) + 四个正交邻格。
        # ★ blocked 不再一票否决 (软惩罚排序) —— 否则绕行标记会毒死绳位。
        cands = []
        if ropeK in ks:
            cands.append(ropeK)
        for da, db in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            c = (ropeK[0] + da, ropeK[1] + db)
            if c in ks and c not in cands:
                cands.append(c)
        if not cands:
            self.log("[绳子] K%s 旁无可走格 (地图数据全不可走) -> 短冷却"
                     % (ropeK,))
            self.rope_cd[key] = time.time() + self.ROPE_CD_WALKFAIL
            return False
        n_press = 0            # 真按下去的空格数
        n_walkfail = 0         # 走位失败次数
        mw_last = -1.0         # 最后一次按空格后的"菜单warm"读数 (诊断用)
        tries = list(cands)
        while tries:
            cur = self.mem.state()["K"]
            # 每轮按当前距离重排; 被绕行标记压过的格子排后面 (软惩罚)
            tries.sort(key=lambda c: (c in self.blocked,
                                      max(abs(c[0] - cur[0]),
                                          abs(c[1] - cur[1]))))
            stand = tries.pop(0)
            if cur != stand:
                path = self.bfs_path(cur, {stand}, allow_doors=True)
                if path is None:
                    # 绕行标记可能把路压塌 -> 放宽一次再算 (不信标记,
                    # 信真地图 + walk_path 逐步核实)
                    path = self.bfs_path(cur, {stand}, allow_doors=True,
                                         relax_blocked=True)
                if path is None:
                    n_walkfail += 1
                    continue
                if not self.walk_path(path):
                    n_walkfail += 1
                    try:        # 走位途中触发换景 (touch 型绳/暗门)?
                        if self.mem.state()["scene"] != sc0:
                            self.stat["rope_ok"] += 1
                            self._rope_ok_t = time.time()
                            self.log("[绳子] K%s 走位途中触发换层 (touch型), "
                                     "累计%d次" % (ropeK, self.stat["rope_ok"]))
                            return True
                    except Exception:
                        pass
                    continue
            # 走位途中就踩触发换景了 (touch 型绳/暗门) -> 目的已达
            try:
                if self.mem.state()["scene"] != sc0:
                    self.stat["rope_ok"] += 1
                    self._rope_ok_t = time.time()
                    self.log("[绳子] K%s 走近即触发换层 (touch型), 累计%d次"
                             % (ropeK, self.stat["rope_ok"]))
                    return True
            except Exception:
                pass
            for attempt in range(2):
                self.g.flush_keys()
                time.sleep(0.1)
                cur = self.mem.state()["K"]
                n_press += 1
                if cur == ropeK:
                    self.log("[绳子] 站绳格 K%s 上按空格 (第%d次)"
                             % (cur, attempt + 1))
                    pass                    # 已站绳上: 直接按空格
                else:
                    # 面向绳子 (被挡=只转身; 若走上去则下次循环变"站绳上按")
                    dka, dkb = ropeK[0] - cur[0], ropeK[1] - cur[1]
                    if (dka, dkb) in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        self.g.key(face_key(dka, dkb), 0.07)
                        time.sleep(0.3)
                    self.log("[绳子] 站 K%s 面向绳子按空格 (第%d次)"
                             % (cur, attempt + 1))
                self.g.key("space")
                time.sleep(self.ROPE_DLG_WAIT)
                # 弹了"是否攀爬"对话框 -> 再按一次空格确认默认项"是"
                try:
                    mw_last = PB.menu_warm(self.g.shot())
                except Exception:
                    mw_last = -1.0
                if mw_last > 0.18:
                    self.log("[绳子] 弹了对话框 (warm=%.2f) -> 空格确认" % mw_last)
                    self.g.key("space")
                # 等场景切换 (淡入淡出最多几秒)
                t0 = time.time()
                while time.time() - t0 < self.ROPE_SC_WAIT:
                    time.sleep(0.25)
                    try:
                        sc1 = self.mem.state()["scene"]
                    except Exception:
                        continue
                    if sc1 != sc0:
                        self._last_door_K = ropeK
                        self.enter_scene(sc1, "(绳子)")
                        # 落点 ±1 临时禁入: 防落地立刻又踩上这层的绳子爬回去
                        K = self.mem.state()["K"]
                        for ua in (-1, 0, 1):
                            for ub in (-1, 0, 1):
                                self.blocked.add((K[0] + ua, K[1] + ub))
                        self.stat["rope_ok"] += 1
                        # ★ v18.2: 记下成功时刻 -> 「遇到就爬」在 ROPE_HUNT_CD
                        #   内不再主动爬 (否则会在同一对上下层之间来回横跳)
                        self._rope_ok_t = time.time()
                        self.log("[绳子] ★攀爬成功: 场景%d -> %d "
                                 "(绳K%s, 站%s, 累计%d次, %.0fs 内不再主动爬)"
                                 % (sc0, sc1, ropeK, stand,
                                    self.stat["rope_ok"], self.ROPE_HUNT_CD))
                        self.g.flush_keys()
                        return True
                # 这一按没触发 -> 留现场截图 (对话框有没有弹出来一眼可见)
                self._rope_diag_save(ropeK, attempt + 1, mw_last)
                # 补个 esc 关掉可能弹出的菜单/文本, 再试
                self.g.key("esc")
                time.sleep(0.3)
                self.g.flush_keys()
        self.g.flush_keys()
        if n_press == 0:
            # ★ 一次空格都没按上 = 纯走位失败, 不算攀爬失败:
            #   短冷却 + 不计 rope_fail (旧版在这里谎报并上 300/900s 冷却,
            #   把「遇到就爬」也一起锁死)。
            self.rope_cd[key] = time.time() + self.ROPE_CD_WALKFAIL
            self.log("[绳子] K%s 没走到可攀爬位置 (走位失败 %d 次, 没按过"
                     "空格, 多半是绕行标记/怪挡路) -> %.0fs 后再试"
                     % (ropeK, n_walkfail, self.ROPE_CD_WALKFAIL))
            return False
        n = self.rope_fail.get(key, 0) + 1
        self.rope_fail[key] = n
        cd = self.ROPE_CD_LONG if n >= self.ROPE_FAIL_MAX else self.ROPE_CD
        self.rope_cd[key] = time.time() + cd
        self.log("[绳子] K%s 按了 %d 次空格没换层 (菜单warm=%.2f, 现场截图"
                 "在 _diag_rope/) (第%d次%s) -> 冷却 %.0fs"
                 % (ropeK, n_press, mw_last, n,
                    " -> 拉长冷却" if n >= self.ROPE_FAIL_MAX else "", cd))
        return False

    def _rope_diag_save(self, ropeK, attempt, mw):
        """攀爬失败的现场截图 (诊断用, 最多留 6 张, 出错绝不影响主流程)。"""
        try:
            import numpy as _np
            d = os.path.join(BASE, "_diag_rope")
            os.makedirs(d, exist_ok=True)
            fs = sorted(f for f in os.listdir(d) if f.lower().endswith(".png"))
            while len(fs) >= 6:
                os.remove(os.path.join(d, fs.pop(0)))
            arr = self.g.shot()
            if arr is None or getattr(arr, "ndim", 0) != 3:
                return
            from PIL import Image as _Img
            fn = "s%d_K%d_%d_try%d_warm%02d_t%d.png" % (
                self.scene, ropeK[0], ropeK[1], attempt,
                int(max(mw, 0) * 100), int(time.time() * 1000) % 10 ** 8)
            _Img.fromarray(_np.clip(arr, 0, 255).astype("uint8"),
                           "RGB").save(os.path.join(d, fn))
        except Exception:
            pass

    # ------------------------------------------------------------------
    #  选目标: 怪点优先 -> 宝箱 -> 回退 v15 巡逻
    # ------------------------------------------------------------------
    def _goal_ttl(self, mk, cur):
        """在途目标的时限: 距离越远给越久。★ 2026-09-29 收紧:
        原式 30+1.5d 让 21 格目标要干等 62 秒才判失败(桥区实测 81s/次),
        配合「无位移提前放弃」(MON_MOVE_STALL), 真卡死 12 秒就会脱身。"""
        return self.MON_GOAL_TTL + 1.0 * cheb(mk, cur)

    def pick_target(self, exclude=None):
        # ★ 驻留 (2026-10-20): 被人送错场景时, 一切目标让位给"走回去"
        if self._pin_on() and getattr(self, "_pin_back", False):
            t = self._pin_return_target()
            if t is not None:
                return t
        # ★ 驻留: 父类分支会按 NO_MONSTER / 步数把 migrate 打开, 这里先复位
        #   (tick 里还有一道, 双保险; 关掉 migrate 才会走下面的正常选点)
        if self._pin_on() and self.migrate:
            self.migrate = False
        if not self.elem_mode or self.migrate or self.home_active():
            return MemBot4.pick_target(self, exclude)
        cur = self.mem.state()["K"]
        # ★ 困区突围目标优先于一切 (怪点/宝箱在突围期间都会重新评估)
        tg = self._trap_goal
        if tg is not None:
            if tg[0] != self.scene or time.time() > tg[2]:
                self._trap_goal = None
            elif cheb(cur, tg[1]) == 0:
                self._trap_goal = None
                self.log("[困区] 已到达突围点 K%s -> 恢复正常刷怪" % (tg[1],))
            else:
                return tg[1]
        self._settle_goal(cur)                 # 先结算上一轮目标
        # ★ v18.2 绳子最高优先级: 在途的「主动攀爬」目标压过怪点/宝箱。
        #   到期没走到或绳子进了冷却 -> 放弃, 落回常规选点(绝不让队伍卡死)。
        st = self._rope_goal_target(cur)
        if st is not None:
            return st
        # ★ 目标还在途就直接沿用。两道防线:
        #   ①pick_target 不再每 tick 重设目标(否则驻留计时永远刷新)
        #   ②_settle_goal 只在「超时」时才判 miss(首跑实测: 用 MON_SETTLE_MIN
        #     判 miss 会让远距离目标刚设 1.5 秒就被弃, 8 个怪点全废)
        g = self._mon_goal
        if g is not None and g[0] == self.scene:
            if time.time() - g[3] < self._goal_ttl(g[2], cur):
                return g[1]
            self._mon_goal = None
        gc = self._chest_goal
        if gc is not None and gc[0] == self.scene:
            stuck = (self.steps == gc[5]
                     and time.time() - gc[4] > self.MON_MOVE_STALL)
            if time.time() - gc[4] < self._goal_ttl(gc[2], cur) and not stuck:
                return gc[2]
            self._chest_goal = None
        if not (self.scene_monsters() or self.scene_chests()):
            return MemBot4.pick_target(self, exclude)
        ban, reach = self.plan_ctx(cur)
        cand = [c for c in reach if c not in ban and c != cur and c != exclude]
        if not cand:
            return MemBot4.pick_target(self, exclude)
        # ★ 2026-10-04 定期探索配额: 到点了就压过怪点/宝箱去一趟生区。
        #   放在这儿而不是最前面 —— 绳子/困区/在途目标的优先级都还在它之上,
        #   所以只在"正准备选新目标"的这一刻才可能被探索截胡。
        ex = self._explore_pick(cur, cand)
        if ex is not None:
            return ex
        r = self._pick_monster(cur, cand)
        if r is not None:
            t, mk = r
            # ★ 顺路开箱: 明显比怪点近的宝箱先开掉, 否则怪点永远有候选,
            #   宝箱一次都轮不到
            tc = self._pick_chest(cur, cand,
                                  max_d=cheb(t, cur) * self.CHEST_BIAS)
            if tc is not None:
                return tc
            self._mon_goal = (self.scene, t, mk, time.time(), self.steps)
            return t
        t = self._pick_chest(cur, cand)
        if t is not None:
            return t
        self.stat["fallback"] += 1
        return MemBot4.pick_target(self, exclude)

    # ------------------------------------------------------------------
    #  定期探索配额: 每 N 场强制去一次"没走过的区域"
    # ------------------------------------------------------------------
    def _explore_arm(self):
        """武装下一次探索 (懒初始化: 开局不立刻触发, 先攒够 visit_cnt)。"""
        self._explore_at = self.battles_won + self.EXPLORE_EVERY_BATTLES

    def _explore_due(self):
        """现在该不该发起一次探索。"""
        if not getattr(self, "explore", False):
            return False
        if getattr(self, "EXPLORE_EVERY_BATTLES", 0) <= 0:
            return False
        at = getattr(self, "_explore_at", None)
        if at is None:
            self._explore_arm()
            return False                       # 武装当轮不触发
        if time.time() - getattr(self, "_explore_done", 0.0) \
                < self.EXPLORE_MIN_GAP:
            return False
        return self.battles_won >= at

    def _explore_pick(self, cur, cand):
        """在途探索目标优先/到期放弃; 到点则挑一个最生的远端格。无则 None。

        ★ 只挑 **一次都没走过** 的格 (visit_cnt==0)。全都走过 -> 直接把配额
        顺延到下一个周期 —— 那种情况属于「地表已测绘完」, 交给常规巡逻的
        "访问次数最少的一档"就够, 不必再牺牲刷怪密度。
        """
        eg = getattr(self, "_explore_goal", None)
        if eg is not None:
            sc, t, ts = eg[0], eg[1], eg[2]
            steps0 = eg[3]
            age = time.time() - ts
            if sc != self.scene:
                self._explore_goal = None      # 换场景了, 旧目标没意义
            elif age > self.EXPLORE_TTL:
                self._explore_goal = None
                self.log("[探索] 目标 K%s 走了 %.0fs 还没到 -> 放弃 (回落常规刷怪)"
                         % (t, age))
            elif self.steps == steps0 and age > self.MON_MOVE_STALL:
                self._explore_goal = None
                self.log("[探索] 目标 K%s 在途卡死 (步数零增长 %.0fs) -> 放弃"
                         % (t, age))
            elif cheb(cur, t) == 0:
                self._explore_goal = None
                vc = getattr(self, "visit_cnt", None) or {}
                self.log("[探索] ★ 抵达生区 K%s (本格 visit_cnt=%d, 本场景已走 "
                         "%d 格) -> 恢复常规刷怪"
                         % (t, vc.get(t, 0), len(vc)))
            else:
                return t                        # 还在路上, 沿用
        if not self._explore_due():
            return None
        vc = getattr(self, "visit_cnt", None) or {}
        pool = [c for c in cand if cheb(c, cur) >= self.EXPLORE_MIN_D]
        tier = [c for c in pool if vc.get(c, 0) == 0]
        if not tier:
            self._explore_arm()                 # 本场景已走遍 -> 顺延下一周期
            return None
        t = max(tier, key=lambda c: cheb(c, cur))     # 远端优先: 才叫"走出去"
        self._explore_goal = (self.scene, t, time.time(), self.steps)
        self._explore_done = time.time()
        self._explore_arm()
        self.log("[探索] ★ 第 %d 场战斗后触发: 强制去生区 K%s (距 %d 格, "
                 "全场景还剩 %d 个没走过的格)"
                 % (self.battles_won, t, cheb(t, cur), len(tier)))
        return t

    def _pick_monster(self, cur, cand):
        """返回 (要走的格, 该格对应的怪点) —— 全是冷却/已弃则 None。

        ★ 刻意排除"队伍已经站在其 1 格触发圈内"的怪点: 那种情况该触发早就
        触发了, 再把它当目标只会让队伍在相邻两格之间来回摆(首跑实测)。
        这类点交给 _settle_goal 去冷却/弃用。"""
        lst = self.scene_monsters()
        if not lst:
            return None
        now = time.time()
        rset = set(cand)
        cands = []                  # (距离, 站格, 怪点)
        n_cd = 0
        n_in = 0
        for m in lst:
            k = (m["K"][0], m["K"][1])
            key = (self.scene, k[0], k[1])
            if key in self.mon_dead:
                continue
            if now < self.mon_cd.get(key, 0):
                n_cd += 1
                continue
            if self._in_trigger(k, cur):
                n_in += 1          # 已经在触发圈里 -> 不是"要去"的目标
                continue
            # ★ 优先站到怪点的「正交」1 格邻格(才是真触发范围), 斜角格只是
            #   备选 —— 斜角站位引擎不触发, 会白白浪费一次往返
            alts_o = [c for c in ((k[0] + da, k[1] + db) for da, db in self.ORTHO)
                      if c in rset]
            alts_d = [c for c in ((k[0] + da, k[1] + db) for da, db in self.DIAG)
                      if c in rset]
            if alts_o:
                kk = min(alts_o, key=lambda c: cheb(c, cur))
            elif alts_d:
                kk = min(alts_d, key=lambda c: cheb(c, cur))
            elif k in rset:
                kk = k
            else:
                continue
            cands.append((cheb(kk, cur), kk, k))
        self._pick_n5 += 1
        if not cands:
            return None
        # ★ 就近优先: 有 ≤ELEM_NEAR_D 格的就绝不去远的
        near = [c for c in cands if c[0] <= self.ELEM_NEAR_D]
        pool = near if near else cands
        # ★ 2026-10-04 未访问偏好: 同档里给"去过很多次"的怪点加软代价,
        #   让怪点选择在几只之间轮转 (见 MON_FRESH_W 注释)。
        vc = getattr(self, "visit_cnt", None) or {}
        w = getattr(self, "MON_FRESH_W", 0.0)
        if w > 0.0:
            cap = getattr(self, "MON_FRESH_CAP", 6)
            bd, kk, k = min(pool, key=lambda c: (
                c[0] + w * min(vc.get(c[1], 0), cap), c[0]))
        else:
            bd, kk, k = min(pool, key=lambda c: c[0])
        best = (kk, k)
        if self._pick_n5 % self.MON_LOG_EVERY == 0:
            self.log("[怪点] 目标 K%s -> 怪 K%s (距 %d 格; 本场景 %d 个怪点, "
                     "冷却 %d, 圈内 %d, 已弃 %d)"
                     % (best[0], best[1], bd, len(lst), n_cd, n_in,
                        sum(1 for x in self.mon_dead if x[0] == self.scene)))
        return best

    def _pick_chest(self, cur, cand, max_d=None):
        """未开的宝箱 -> 挑一个可站的相邻格; 同时记住朝向。

        max_d 不为 None 时只考虑距离不超过它的箱子(用于「顺路开箱」)。
        """
        lst = self.scene_chests()
        if not lst:
            return None
        now = time.time()
        rset = set(cand)
        best = None
        bd = 10 ** 9
        bvs = 10 ** 9
        info = None
        # ★ 2026-10-04: 距离相同时优先没走过的站位 —— 纯 tie-break, 不动距离
        #   权衡 (宝箱不值得为了"生路"多走)。visit_cnt 空 -> 行为与旧版一致。
        vc = getattr(self, "visit_cnt", None) or {}
        cap = getattr(self, "MON_FRESH_CAP", 6)
        # ★ v18.9: 内存已判定"白跑"的箱子 (已开空/不可触发) 一并跳过
        memdead = (getattr(self, "_mem_dead", None) or {}).get(self.scene) or ()
        for c in lst:
            k = (c["K"][0], c["K"][1])
            key = (self.scene, k[0], k[1])
            if key in self.chest_dead or k in memdead \
                    or now < self.chest_cd.get(key, 0):
                continue
            # 站格 = 箱子 + (da,db); 朝向 = 该偏移的反方向 (见 chest_face_key)
            for da, db in ((1, 0), (-1, 0), (0, -1), (0, 1)):
                s = (k[0] + da, k[1] + db)
                if s not in rset:
                    continue
                kd = chest_face_key(da, db)
                d = cheb(s, cur)
                if max_d is not None and d > max_d:
                    continue
                vs = min(vc.get(s, 0), cap)
                if (d, vs) < (bd, bvs):
                    bd, bvs, best, info = d, vs, s, (k, kd)
        if best is not None:
            self._chest_goal = (self.scene, info[0], best, info[1],
                                time.time(), self.steps)
            self.log("[宝箱] 目标 箱K%s 站位 K%s 朝向 %s (距 %d 格)"
                     % (info[0], best, info[1], bd))
        return best

    # ------------------------------------------------------------------
    #  结算: 上一轮目标是怪点/宝箱, 到了之后该记什么
    # ------------------------------------------------------------------
    def _settle_goal(self, cur):
        """结算上一轮怪点目标。

        ★ 实测教训(2026-09-29 首跑): 原先用 cheb<=1 判「到达」, 队伍站在目标
        身侧 1 格时每 tick 都判一次 -> 174 次空转刷屏。
        ★★ 第二次实测教训: 修完之后改成「过了 MON_SETTLE_MIN(1.5s) 还没走到
        就记 miss」, 结果远距离目标刚设 1.5 秒就被判失败, 8 个怪点 3 秒内全部
        「连续 3 次没走到 -> 放弃」, 一场没赢。
        现在的判据分三段, 互不干扰:
          ① 在途(还没进触发圈) + 未超时 + 步数有进展 -> 什么都不做, 继续走
          ② 在途 + (超时 或 卡死: MON_MOVE_STALL 秒步数零增长) -> 记 miss, 重选
          ③ 已到触发圈 + 站够 MON_SETTLE_MIN  -> 判「开打 / 走到没打」
        ★★ 第三次实测教训(2026-09-29 桥区): 21 格外的怪点 TTL 高达 62 秒,
        队伍在窄桥上被挡得一步不动也要干等满 TTL 才换目标 —— 用户观感就是
        「每次都停顿好长时间」。加步数快照: 目标设定时记下 self.steps,
        在途期间 steps 零增长超过 MON_MOVE_STALL 秒 -> 立即按 miss 处理。
        """
        g = self._mon_goal
        if not g:
            return
        sc, t, mk, ts, steps0 = g
        if sc != self.scene:
            self._mon_goal = None
            return
        age = time.time() - ts
        key = (sc, mk[0], mk[1])
        if not self._in_trigger(mk, cur):
            # 站在斜角格上(cheb=1 但不触发) -> 目标选得不好, 短超时就重选,
            # 别按完整 TTL 干等
            diag_stuck = cheb(mk, cur) <= 1
            # ★ 卡死判定: 目标设定后一步都没走动(步数零增长)且超过 N 秒
            stuck = (self.steps == steps0 and age > self.MON_MOVE_STALL)
            ttl = 8.0 if diag_stuck else self._goal_ttl(mk, cur)
            if age < ttl and not stuck:
                return                   # ① 还在路上, 别打扰
            n = self._mon_miss.get(key, 0) + 1      # ② 超时/卡死, 没走到
            self._mon_miss[key] = n
            self._mon_goal = None        # 重选(BFS 会用最新绕行知识重算路径)
            if n >= self.MON_MISS_N:
                self.mon_cd[key] = time.time() + self.MON_MISS_CD
                self._mon_miss[key] = 0  # 重置, 冷却结束后再给一轮机会
                self._slog(key, "[怪点] K%s 连续 %d 次%s(%.0fs/次) -> "
                           "冷却 %d 分钟 (%s)"
                           % (mk, n, "原地卡死" if stuck else "超时没走到",
                              age, self.MON_MISS_CD // 60,
                              "步数零增长/窄桥假通路" if stuck
                              else "BFS 假通路/可能在别的层"))
            elif diag_stuck:
                self._slog(key, "[怪点] K%s 只走到斜角格(不触发) -> 重新选站位"
                           % (mk,))
            elif stuck:
                self._slog(key, "[怪点] K%s 在途卡死 %.0fs 步数零增长 -> 重选"
                           % (mk, age))
            return
        if age < self.MON_SETTLE_MIN:
            return                       # ③ 刚踏进触发圈, 等一会儿看是否开打
        self._mon_goal = None
        if time.time() - self._last_fight_end < 8.0:
            self.mon_visit[key] = 0
            self._mon_miss[key] = 0
            self.mon_cd[key] = time.time() + self.MON_CD
            self.stat["mon_hit"] += 1
            self._slog(key, "[怪点] K%s 已触发战斗 -> 冷却 %.0fs"
                       % (mk, self.MON_CD))
        else:
            # ★ 走进 1 格内仍不触发: 实测多半是这只怪刚被打掉 ——
            #   内存里 sState 从 2 变成 -2, 而 play.c:96 规定 sState<0 的对象
            #   **离开视口(约10格)后自动恢复成 +2** => 走远再回来就是新的一只。
            #   所以这里给冷却而不是永久放弃; 连试 4 次都不行才判该点失效。
            n = self.mon_visit.get(key, 0) + 1
            self.mon_visit[key] = n
            self.mon_cd[key] = time.time() + self.MON_CD
            self.stat["mon_visit"] += 1
            if n >= self.MON_TRY_N:
                self.mon_dead.add(key)
            self._slog(key, "[怪点] K%s 走到 1 格内未触发 (怪在恢复期/不触发) "
                       "第%d次 -> 冷却 %.0fs%s"
                       % (mk, n, self.MON_CD,
                          " -> 本局放弃" if n >= self.MON_TRY_N else ""))

    def _slog(self, key, msg):
        """怪点日志节流: 同一个点最多每 20 秒一条。"""
        now = time.time()
        if now - self._slog_t.get(key, 0) < 20:
            return
        self._slog_t[key] = now
        self.log(msg)

    # ------------------------------------------------------------------
    #  开宝箱: 走到相邻格 -> 朝箱子按方向键(被挡=只转身) -> 按空格搜索
    # ------------------------------------------------------------------
    def step(self, key):
        """每走一步同步迁移阈值。★ 驻留时永远不涨 (父类按"本场景打过仗没有"
        在 150 / 99999 之间来回切, 一旦某个场景刚进来还没开打就会 150 步就迁)。"""
        r = super().step(key)
        if self._pin_on():
            self.MIGRATE_AFTER = 99999
        return r

    def tick(self):
        if self._pin_on():
            # ★★ 驻留的主动层最后一道闸: 不管哪条路径 (NO_MONSTER 强迁 / 30 秒
            #    受困强迁 / 调度器) 把 migrate 打开了, 这一帧就复位。宁可原地
            #    多等一会儿, 也不能"自己走出去"(用户要求的就是这个)。
            self._rope_goal = None           # 绳子 = 换层, 驻留时一并不许
            if self.migrate and not getattr(self, "_pin_back", False):
                self.migrate = False
                now = time.time()
                if now - getattr(self, "_pin_hold_t", 0.0) > 60:
                    self._pin_hold_t = now
                    self.log("[驻留] 拦下一次自动迁移/换场景 (钉在场景 %d 不离开)"
                             % self.pin_scene)
        else:
            # ★ v18.2 绳子最高优先级放最前面: 遇到就爬 (不等换场景判定)
            self._rope_hunt()
        self._pilot_tick()          # 再判「这个场景还值不值得待」
        if self.elem_mode:
            self._trap_check()      # 困区逃逸 (熔岩走廊假通路)
        super().tick()
        self._after_arrive()

    def _after_arrive(self):
        if self.migrate:
            return                   # 去门换场景的路上, 别顺手开箱
        # ★ 战斗中/战斗结算窗口期绝不动手: base tick 这些帧直接 return(不走路),
        #   但本钩子在它后面照样会跑 —— 不加这道闸, 站在箱边/绳边遇敌就会
        #   "一边打架一边按空格"(开箱与爬绳都要按空格, 会把攻击键吞掉)。
        if getattr(self, "not_battle_count", 99) < 3:
            return
        # ★ v18.2 绳子最优先: 站到可爬位置 -> 按空格攀爬 (阻塞数秒, 但已到位)
        cur = self.mem.state()["K"]
        rg = getattr(self, "_rope_goal", None)
        if rg is not None:
            ropeK = rg[1]
            st = self._rope_goal_target(cur)          # 失效会自动清目标
            if st is None:
                pass                                  # 目标已废 -> 继续常规逻辑
            elif cheb(cur, st) != 0:
                return                                # 还在去绳子的路上, 别顺手开箱
            else:
                self._rope_goal = None
                self.log("[绳子] ★已站到 K%s 旁 -> 按空格攀爬" % (st,))
                if self.rope_probe(ropeK):
                    # 换了层: 告诉调度器"刚到新场景", 别立刻又判该走
                    try:
                        if self.pilot is not None:
                            self.pilot.note_change(time.time())
                    except Exception:
                        pass
                return
        g = self._chest_goal
        if not g or g[0] != self.scene:
            return
        ck, stand, kd = g[1], g[2], g[3]
        if cheb(cur, stand) != 0:
            return
        self._chest_goal = None
        self.open_chest(ck, kd, stand)

    # ------------------------------------------------------------------
    #  开箱台账: 物品栏差分 (记录"开出了什么") + chest_log.json 记账
    # ------------------------------------------------------------------
    def chest_memo_start(self):
        """开局把台账里已开的箱子塞进 chest_dead (关掉开关则什么都不做)。

        返回载入条数。这一步是"跨重启不再白跑"的唯一入口 ——
        chest_dead 是内存集合, 重启就空了, 所以必须由台账补回来。
        """
        if not getattr(self, "chest_memo", False):
            self.log("[配置] 开箱台账 = 关 "
                     "(不记录; 重启后会重新尝试已开过的箱子)")
            return 0
        n = 0
        try:
            ks = self.chest_log.keys()
            self.chest_dead |= ks
            n = len(ks)
            s = self.chest_log.stat()
        except Exception as e:                  # 台账坏了也绝不能带崩开局
            self.log("[宝箱] 台账载入失败 (当空台账继续): %s" % e)
            return 0
        self.log("[配置] 开箱台账 = 开 (%s; 已记 %d 个箱子/累计 %d 次/"
                 "%d 个场景 -> 这些箱子本局直接跳过)"
                 % (self.chest_log.path, s["n_chest"], s["n_marks"],
                    s["n_scene"]))
        self._chest_memo_verify()
        if getattr(self, "CHEST_MEM", True):
            self.log("[配置] 空箱判断 = 开 (读游戏内存: 已开空/不可触发的箱子"
                     "直接忽略, 不走过去)")
        else:
            self.log("[配置] 空箱判断 = 关 (只能靠「试开一次没反应」来记空箱)")
        return n

    def _chest_memo_verify(self):
        """用内存核对台账里的「空箱」记忆 —— 其实还满的划掉, 让它重新去开。

        ★ 2026-10-04 真机发现: 台账里 36 条 ok=False 中, 11 条的箱子在内存里
          **脚本原封不动、物品还在** —— 当初是走位/丢键失败被误判成"空箱",
          于是这些箱子永远拿不到了。内存才是真值, 所以开局反驳一次。
          读不到内存 -> 什么都不做 (宁可保守, 也不能把对的记录删了)。
        """
        if not getattr(self, "CHEST_MEM", True):
            return 0
        try:
            rows = self.chest_log.rows()
        except Exception:
            return 0
        if not rows:
            return 0
        om = getattr(self, "_objmem", None)
        if om is None or not om.open():
            return 0
        fixed = []
        for r in rows:
            if r["ok"]:
                continue                    # 「已开」记录与内存一致 (53/53 实测)
            sc, K = r["scene"], (r["a"], r["b"])
            try:
                c = None
                for x in self.scene_chests(sc):
                    if (x["K"][0], x["K"][1]) == K:
                        c = x
                        break
                if c is None:               # 明细里没这个箱子 -> 无法核对, 保留
                    continue
                v, _why = om.chest_verdict(sc, c)
            except Exception:
                continue
            if v != "live":                 # 已空/不可触发/拿不准 -> 原记录没错
                continue
            self.chest_dead.discard((sc, K[0], K[1]))
            try:
                self.chest_log.forget(sc, K)
            except Exception:
                pass
            fixed.append("场景%d K%s" % (sc, K))
        if fixed:
            self.log("[宝箱] ★台账核对: %d 条「空箱」记忆有误 (内存里箱子还是满的, "
                     "当初应是走位失败被误记) -> 已划掉, 会重新去开: %s"
                     % (len(fixed), " ".join(fixed[:6])))
        return len(fixed)

    def _inv_snapshot(self):
        """物品栏快照 {物品ID: 数量}; 读不到 -> None。只读内存, 绝不抛。"""
        try:
            if getattr(self, "_inv_reader", None) is None:
                import bot_itemmon as _IM
                self._inv_reader = _IM.ItemReader()
                self._inv_names = _IM.load_names()
            rows = self._inv_reader.entries()
            if rows is None:
                return None
            return {w: n for (_i, w, n, _u) in rows}
        except Exception:
            return None

    def _chest_gain(self, before, after):
        """开箱前后物品栏差分 -> (物品名, 增加量); 分不清 -> (None, 0)。"""
        if not before or not after:
            return None, 0
        gains = sorted(((w, after[w] - before.get(w, 0))
                        for w in after if after[w] > before.get(w, 0)),
                       key=lambda t: -t[1])
        if not gains:
            return None, 0
        w, dn = gains[0]
        names = getattr(self, "_inv_names", {}) or {}
        name = names.get(w) or ("物品 0x%03X" % w)
        if len(gains) > 1:                      # 一次给多样 (少见) 也记下来
            name += " 等%d种" % len(gains)
        return name, dn

    def chest_ledger_state(self):
        """台账一句话摘要 (GUI/日志用)。"""
        try:
            return self.chest_log.summary()
        except Exception:
            return "台账不可用"

    def _chest_search_wait(self, inv0, tmax=None, step=0.35):
        """按完空格后, 在 ~2.5 秒窗口里判定"这一下到底成没成"。

        两个判据取或:
          ① 画面出现"暖色框" (对话框/物品框) —— 原来的做法;
          ② **物品栏真的涨了** —— 兜住"对话框闪得快/截图慢半拍"的情况:
             以前只按 0.8s 看一次画面, 没看到就当失败, 于是把它记进本局黑名单
             (试满 4 次 -> 放弃), 箱子的东西其实已经到手了。
        返回 (是否成功, 判据说明)。
        """
        tmax = self.CHEST_DLG_WAIT * 3 if tmax is None else tmax
        t0 = time.time()
        while time.time() - t0 < tmax:
            time.sleep(step)
            try:
                if PB.menu_warm(self.g.shot()) > 0.18:
                    return True, "对话框"
            except Exception:
                pass
            if inv0 is not None:
                cur = self._inv_snapshot()
                if cur and any(cur.get(w, 0) > inv0.get(w, 0) for w in cur):
                    return True, "物品到手"
        return False, ""

    def open_chest(self, ck, kd, stand):
        key = (self.scene, ck[0], ck[1])
        self.stat["chest_try"] += 1
        inv0 = self._inv_snapshot() if self.chest_memo else None
        self.g.flush_keys()
        time.sleep(0.1)
        # ① 转到面向箱子: 箱子是 blocker, 按方向键只会转身不会走
        self.g.key(kd, 0.07)
        time.sleep(self.CHEST_SETTLE)
        self.g.flush_keys()
        cur = self.mem.state()["K"]
        if cur != tuple(stand):
            # 意外走掉了(箱子不挡路?) -> 走回站位再试一次, 失败就放弃
            self.log("[宝箱] 转身时意外移动 K%s -> %s, 回退" % (stand, cur))
            back = {"up": "down", "down": "up",
                    "left": "right", "right": "left"}[kd]
            self.g.key(back, 0.07)
            time.sleep(self.CHEST_SETTLE)
            self.g.flush_keys()
            cur = self.mem.state()["K"]
            if cur != tuple(stand):
                self.log("[宝箱] 回不到位 -> 放弃该箱 (记冷却)")
                self.chest_cd[key] = time.time() + self.CHEST_CD
                self.chest_dead.add(key)
                return
        # ② 按空格搜索（仙剑用空格调查面前的东西）
        got, how = False, ""
        for _ in range(2):                      # 丢键/慢半拍时再来一次
            self.g.key("space")
            got, how = self._chest_search_wait(inv0)
            if got:
                break
        if got:
            self.stat["chest_ok"] += 1
            self.log("[宝箱] K%s 打开成功! (累计 %d 个, 判据=%s)"
                     % (ck, self.stat["chest_ok"], how or "?"))
            # 关掉"获得XX"对话框 (最多 5 次空格)
            for _ in range(5):
                time.sleep(0.35)
                if PB.menu_warm(self.g.shot()) < 0.12:
                    break
                self.g.key("space")
            # ★ 开过的箱子本局不再去 (以前漏了这步 -> 会白试 4 次才进 dead)
            self.chest_dead.add(key)
            if self.chest_memo:
                item, dn = self._chest_gain(inv0, self._inv_snapshot())
                oid = self._chest_oid(ck)
                try:
                    self.chest_log.mark(self.scene, ck, item=item, dn=dn,
                                        oid=oid)
                    self.log("[宝箱] ★已记账: 场景%d K%s%s -> %s"
                             % (self.scene, ck,
                                " 开出 %s×%d" % (item, dn) if item else "",
                                self.chest_log.summary()))
                except Exception as e:          # 台账坏了也绝不影响挂机
                    self.log("[宝箱] 记账失败 (不影响挂机): %s" % e)
                # ★ v19.0 用户要求「每次开箱/采集都要有记录, 最好带编号」:
                #   除了 chest_log(按格子记物品/数量), 再往**按编号**的要素台账
                #   记一条 kind="pick" -> 覆盖率报告就能按编号统一对账。
                try:
                    fl = getattr(self, "feat_log", None)   # 裸影子对象兜底
                    if fl is not None and oid:
                        fl.mark(self.scene, oid, kind="pick",
                                K=[ck[0], ck[1]], name=item or "")
                        self.stat["feat_pick"] = \
                            self.stat.get("feat_pick", 0) + 1
                except Exception as e:
                    self.log("[宝箱] 要素台账记账失败 (不影响挂机): %s" % e)
        else:
            n = self.chest_try.get(key, 0) + 1
            self.chest_try[key] = n
            self.chest_cd[key] = time.time() + self.CHEST_CD
            give_up = n >= self.CHEST_TRY_N
            # ★ v18.9: 定论之前先问内存 —— 如果内存说这箱子**还能开**, 那
            #   "按空格没反应"就是我方站位/丢键的问题, 不是空箱。以前直接记
            #   「空」并永久跳过 -> 把还能开的箱子白丢掉 (真机台账里已有 11 条
            #   这种错记: 内存显示它们的脚本原封不动、物品还在)。
            if give_up:
                alive = self.chest_mem_alive(ck)
                if alive is True:
                    self.stat["chest_alive_miss"] += 1
                    na = self._chest_alive.get(key, 0) + 1
                    self._chest_alive[key] = na
                    if na < self.CHEST_ALIVE_MAX:
                        self.chest_cd[key] = time.time() + self.CHEST_CD * 3
                        self.log("[宝箱] K%s 按空格没反应, 但内存里它**还能开** -> "
                                 "判为站位/丢键失败, 不记空箱 (第%d次, %.0f秒后再试)"
                                 % (ck, n, self.CHEST_CD * 3))
                    else:
                        self.chest_cd[key] = time.time() + self.CHEST_ALIVE_LONG
                        self.log("[宝箱] K%s 内存说还满, 却已 %d 次按不开 -> 先放一边"
                                 "(不记空箱, %.0f秒后再试)"
                                 % (ck, na, self.CHEST_ALIVE_LONG))
                    give_up = False
            if give_up:
                self.chest_dead.add(key)
                self.stat["chest_empty"] += 1
                # ★ v18.2 用户要求「碰到就记」: 默认 CHEST_TRY_N=1, 所以第一次
                #   按空格没反应就定论 —— 场景里**本来就已打开**的箱子也是这个
                #   表现(按空格无对话框、物品栏也不涨), 一并入账, 下次不再去。
                self.log("[宝箱] K%s 按空格没反应 -> 判为「已开着/空箱」(第%d次)"
                         % (ck, n))
            else:
                self.log("[宝箱] K%s 按空格没反应 (第%d/%d次)"
                         % (ck, n, self.CHEST_TRY_N))
            if give_up and self.chest_memo:
                # ★ v18.9: 内存读得到时, 上面的 chest_mem_alive 已经拦掉了
                #   "还满却按不开"的情况; 走到这里说明内存确认它确实是空的
                #   (或内存读不到 -> 只能靠"试过"这个事实兜住, 否则每次重启
                #   都要把全地图开过的箱子再走一遍)。
                try:
                    self.chest_log.mark(self.scene, ck, ok=False, tries=n,
                                        oid=self._chest_oid(ck))
                    self.log("[宝箱] ★已记账(空/已开): 场景%d K%s 试%d次无反应 -> "
                             "以后不再去 (%s)"
                             % (self.scene, ck, n, self.chest_log.summary()))
                except Exception as e:
                    self.log("[宝箱] 记账失败 (不影响挂机): %s" % e)
        self.g.flush_keys()

    # ------------------------------------------------------------------
    #  战斗挂钩: 记战果 + 让怪点进入冷却
    # ------------------------------------------------------------------
    def fight(self, why=""):
        # ★ v19.3: 战前快照 (经验总和/金钱) —— 放在**进战斗之前**拍, 开箱/
        #   剧情的收益就不会混进"单场收益" (会话累计仍包含它们)。读失败就
        #   None, 战后 _gain_record 会跳过本场 (宁可漏一场不记假数)。
        self._gain_pre = self.mem.read_gain() \
            if hasattr(self.mem, "read_gain") else None
        st = self.mem.state()
        sc, K = st["scene"], st["K"]
        won0 = getattr(self, "battles_won", 0)
        ok = MemBot4.fight(self, why)
        if not ok:
            return ok
        self._last_fight_end = time.time()
        if self.pilot is not None:
            self.pilot.on_battle(self._last_fight_end, self.battles_won)
        near = [m for m in self.scene_monsters(sc)
                if cheb((m["K"][0], m["K"][1]), K) <= self.MON_HIT_NEAR]
        for m in near:
            key = (sc, m["K"][0], m["K"][1])
            self.mon_cd[key] = time.time() + self.MON_CD
            self.mon_visit[key] = 0
        self.stat["mon_hit"] += 1
        if near:
            self._slog((sc, near[0]["K"][0], near[0]["K"][1]),
                       "[怪点] K%s 战斗命中 -> %d 个怪点冷却 %.0fs"
                       % (K, len(near), self.MON_CD))
        else:
            self._slog((sc, "far"), "[怪点] K%s 战斗命中但附近 %d 格内无记录怪点 "
                       "(怪追过来打的?)" % (K, self.MON_HIT_NEAR))
        # ★ v19.0: 只有 battles_won 真的涨了才算"真胜场"(误抓不会涨) ->
        #   ①按编号记要素台账 ②累加跨重启的总胜场 并打一行 [胜场] 给 GUI 解析
        try:
            if getattr(self, "battles_won", 0) > won0:
                self._note_win(sc, K, near)
        except Exception as e:            # 台账坏了绝不影响挂机
            self.log("[胜场] 记账异常(%s) -> 只跳过记账" % e)
        # ★ v19.3: 战斗收益记账 (每场经验/金钱)。同样绝不影响挂机。
        try:
            self._gain_record()
        except Exception as e:
            self.log("[收益] 记账异常(%s) -> 跳过" % e)
        return ok

    def _note_win(self, sc, K, near):
        """一次真胜场: 记要素台账(按编号) + 累计总胜场 + 打 [胜场] 日志。"""
        total = None
        wl = getattr(self, "win_log", None)
        if wl is not None:
            total = wl.add(1)
            self._win_total = total
        fl = getattr(self, "feat_log", None)
        marked = 0
        if fl is not None:
            for m in near:
                oid = m.get("id")
                if not oid:
                    continue
                if fl.mark(sc, oid, kind="mon", K=m.get("K"),
                           name=m.get("ename", "")) is not None:
                    marked += 1
            if marked:
                self.stat["feat_mark"] = self.stat.get("feat_mark", 0) + marked
            else:
                self.stat["far_battle"] = self.stat.get("far_battle", 0) + 1
        codes = " ".join(featlog.code(sc, m["id"]) for m in near
                         if m.get("id"))
        self.log("[胜场] 本次 %d / 累计 %s | %s"
                 % (self.battles_won,
                    ("%d" % total) if total is not None else "?",
                    ("记 %s (%s)" % (marked, codes)) if marked else
                    ("附近无记录怪点(怪追来打的)" if not near else
                     "怪点无编号(清单太旧?)")))

    def _gain_record(self):
        """★ v19.3/v3: 一场打完 -> 算本场收益, 累加进会话累计, 写 exp_log.json。

        单场 = 战后快照 - 战前快照 (fight 开头拍的 _gain_pre); 经验取各槽
               增量的最大值 (= 每个在队角色实际拿到的那一份);
        ★ 会话累计 = **逐场累加**已入账的单场收益 (self._gain_exp_tot += d)。
          旧写法"当前快照 - 会话基线"在升级/满级循环/读档时基线整段作废,
          累计被反复清零 -> 时均明显偏小 (2026-10-14 真机: 会话累计只记到
          1,975, 而最近 200 场合计就 114,715; 根因是 wExp 是"等级内经验",
          升级会掉阈值 -> 被误判成读档, 见 bot_explog 模块头)。
        脏数据 (负值/超限, 见 gainlog.snap_delta) -> **只跳过本场**, 累计不重置,
          记 bad 计数。失败只跳过, 绝不影响挂机。
        """
        cur = self.mem.read_gain() if hasattr(self.mem, "read_gain") else None
        pre, self._gain_pre = getattr(self, "_gain_pre", None), cur
        if pre is None or cur is None:
            return
        tab = getattr(self, "_levelup_tab", None)
        if not tab and hasattr(self.mem, "levelup_table"):
            try:
                tab = self.mem.levelup_table()
            except Exception:
                tab = None
            self._levelup_tab = tab      # 读到就缓存 (换 exe 才需重读)
        d = gainlog.snap_delta(pre, cur, table=tab)
        if d is None:
            # 读档回退 / 内存读花 -> 只跳过本场, **累计基线不动**
            n = getattr(self, "_gain_bad", 0) + 1
            self._gain_bad = n
            self.log("[收益] 本场快照异常 -> 跳过 (累计不重置, 已跳过 %d 场)" % n)
            return
        d_exp, d_cash = d
        self._gain_exp_tot = getattr(self, "_gain_exp_tot", 0) + max(0, d_exp)
        self._gain_cash_tot = getattr(self, "_gain_cash_tot", 0) + max(0, d_cash)
        self.gain_log.record(
            wins=getattr(self, "battles_won", 0),
            elapsed=time.time() - self._gain_t0,
            t_start=time.strftime("%Y-%m-%d %H:%M:%S",
                                  time.localtime(self._gain_t0)),
            d_exp=self._gain_exp_tot,
            d_cash=self._gain_cash_tot,
            bad=getattr(self, "_gain_bad", 0),
            last={"n": getattr(self, "battles_won", 0),
                  "t": time.strftime("%H:%M:%S"),
                  "exp": d_exp, "cash": d_cash})

    def _chest_oid(self, ck):
        """箱子格的要素对象序号 (清单里查不到 -> 0, 报告会退回按 K 匹配)。"""
        try:
            for c in self.scene_chests(self.scene):
                k = c.get("K") or []
                if len(k) == 2 and int(k[0]) == int(ck[0]) \
                        and int(k[1]) == int(ck[1]):
                    return int(c.get("id") or 0)
        except Exception:
            pass
        return 0

    # ------------------------------------------------------------------
    #  效率日志: 附带要素统计
    # ------------------------------------------------------------------
    def _rate(self, dt, n_retry, stalled):
        super()._rate(dt, n_retry, stalled)
        now = time.time()
        if now - self._elem_t > 120:
            self._elem_t = now
            s = self.stat
            _ho, _hf = self.heal_stat()
            self.log("[要素] 怪点命中 %d / 走到没打 %d / 开箱 %d(试%d,空%d,试%d次计) / "
                     "回退巡逻 %d / 存盘成功%d失败%d / 绳子%d(试%d)%s"
                     % (s["mon_hit"], s["mon_visit"],
                        s["chest_ok"], s["chest_try"], s["chest_empty"],
                        self.CHEST_TRY_N,
                        s["fallback"], _ho, _hf,
                        s["rope_ok"], s["rope_try"],
                        " 遇到就爬" if (self.use_ropes and self.rope_hunt)
                        else (" 仅无门时" if self.use_ropes else " 已关")))
            if self.chest_memo:
                # 台账摘要 (前缀用 [宝箱] 以便 tools/run_report.py 归到同一栏)
                self.log("[宝箱] 台账: %s" % self.chest_log.summary())
            if self.feat_log is not None:
                # ★ v19.0 要素台账: 按编号记战斗/开箱, "哪些怪点打过/几次"看这里
                self.log("[要素台账] 本次胜 %d / 累计 %d | %s | 本次新记拾取 %d | "
                         "无记录怪点战斗 %d 次"
                         % (self.battles_won, self._win_total,
                            self.feat_log.summary(),
                            self.stat.get("feat_pick", 0),
                            self.stat.get("far_battle", 0)))
            if self.pilot is not None and self.auto_scene:
                p = self.pilot
                v = p.info()
                self.log("[调度] 场景%d 停留%.1f分 本场胜%d 已%.0fs无战斗 "
                         "耐心x%.2f 评分%s 换场景%d次%s"
                         % (self.scene, (now - p.t_arrive) / 60.0,
                            self.battles_won - p.b0,
                            now - p.t_last_battle, p.patience(),
                            ("%.2f场/分%s" % (v.get("expect", 0),
                                            "/过路" if v.get("pass") else ""))
                            if v else "无记录",
                            s["scene_chg"],
                            " 门路%d条" % len(self._scene_links)
                            if self._scene_links else ""))


if __name__ == "__main__":
    _lock = PB._acquire_lock()
    if _lock is None:
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
                    if len(_cl) >= 3 and _cl[1] == "-u" and \
                            _cl[2].replace("\\", "/").endswith(
                                ("pal_bot_mem.py", "pal_bot_mem2.py",
                                 "pal_bot_mem3.py", "pal_bot_mem4.py",
                                 "pal_bot_mem5.py")):
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
    maxb = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1] != "0" else None
    secs = int(sys.argv[2]) if len(sys.argv) > 2 else None
    if len(sys.argv) > 3 and sys.argv[3].strip():
        MemBot4.PATROL_R0 = int(sys.argv[3])
    if len(sys.argv) > 4 and sys.argv[4].strip():
        MemBot4.PATROL_RMAX = int(sys.argv[4])
    stall = 1200
    if len(sys.argv) > 7 and sys.argv[7].strip():
        try:
            stall = int(sys.argv[7])
        except ValueError:
            pass
    if stall <= 0:
        stall = 10 ** 9
    # 战斗攻击键: 环境变量 PAL_ATTACK_KEY > bot_config.json > 默认 R
    #   (GUI 的「攻击键」下拉就写这个配置; 换地图后按键变了不用改代码)
    _ak, _ak_src = PB.init_attack_key()
    bot = MemBot5(max_battles=maxb, max_seconds=secs, stall_limit=stall)
    bot.log("[配置] 战斗攻击键 = %s (%s) — 不对可在 GUI「攻击键」改" % (_ak, _ak_src))
    try:
        bot.log("[配置] 战后自动存盘 = %s" % bot.heal_summary())
    except Exception as _e:
        bot.log("[配置] 存盘配置读取失败 (本次不存盘): %s" % _e)
    if stall >= 10 ** 9:
        bot.log("[配置] 无胜场保险已关闭 (stall=0)")
    try:
        import subprocess as _sp
        _wd = os.path.join(BASE, "bot_watchdog.py")
        _DETACHED = 0x00000008 | 0x00000200
        _sp.Popen([sys.executable, _wd, str(os.getpid()), str(bot.g.hwnd)],
                  creationflags=_DETACHED, cwd=BASE,
                  stdin=_sp.DEVNULL, stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
        bot.log("[看门狗] 已启动 (守护 %d)" % os.getpid())
    except Exception as _e:
        bot.log("[看门狗] 启动失败: %s" % _e)
    if len(sys.argv) > 5 and sys.argv[5]:
        _BIAS = {"上": (1, 0), "下": (-1, 0), "左": (0, -1), "右": (0, 1),
                 "右上": (1, 1), "右下": (-1, 1), "左上": (1, -1),
                 "左下": (-1, -1),
                 "u": (1, 0), "d": (-1, 0), "l": (0, -1), "r": (0, 1),
                 "ru": (1, 1), "rd": (-1, 1), "lu": (1, -1), "ld": (-1, -1)}
        _name = sys.argv[5].strip()
        if _name in _BIAS:
            bot._bias = _BIAS[_name]
            bot._bias_name = _name
            bot.log("[巡逻] 方向偏置开启: %s" % _name)
    if len(sys.argv) > 6 and sys.argv[6].strip() in ("穿门", "开门", "open"):
        bot.open_doors = True
        bot.log("[配置] 开门模式: 踩到传送阵就直接前往下一层")
    # 第 8 参数: 巡逻/off = 关闭要素巡回, 回到 v15 行为
    if len(sys.argv) > 8 and sys.argv[8].strip() in ("巡逻", "off", "关"):
        bot.elem_mode = False
        bot.log("[要素] 已关闭要素巡回 -> 使用 v15 渐进巡逻")
    # 第 9 参数: 不换/off = 关闭自动换场景 (默认开启: 效率驱动地穿门找更好的场景)
    if len(sys.argv) > 9 and sys.argv[9].strip().lower() in ("不换", "off", "关",
                                                             "0", "no"):
        bot.auto_scene = False
        bot.pilot.enabled = False
        bot.log("[调度] 自动换场景已关闭 -> 只在当前场景刷")
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
        print("[退出] 已清空按键, 共胜利 %d 场, 误抓 %d 次, 要素统计 %s"
              % (bot.battles_won, bot._false_battle, bot.stat), flush=True)
