# -*- coding: utf-8 -*-
"""build_scene_info.py — 导出「全图场景要素清单」(编号 + 名字)。

★ v19.0 (2026-10-08): 数据源改为**离线 SSS.MKF**(默认), 保留 --live 走游戏内存。

为什么默认改成离线 —— 一句话: **活内存会忘记已开过的箱子。**
  * 宝箱/采集物的脚本最后一条是 `0x0002 STOP_ENTRY -> 36509` (三条指令的
    "箱子已空"提示); play.c:153 `p->wTriggerScript = PAL_RunTriggerScript(...)`
    把这个返回值**回写**给对象 —— 于是箱子一开, 从活内存里就再也看不出
    "它曾经是宝箱"。实测同一份场景 221: 9/30 生成时 22 个拾取点, 跑三天后
    只剩 9 个 (全表 1152 -> 497)。
  * 离线 SSS.MKF 是**游戏原始数据**, 永远是 `36939` 这种未改写值。
    实测: 离线清单与「9/30 脚本被改写前」的快照在场景 220/221/223/224/253
    上**逐点完全一致**(集合差为空), 并且 100% 覆盖 chest_log.json 记录过的
    92 个真机开箱坐标 —— 离线 92/92, 活内存 42/92。

数据出处 (SDLPAL `global.c`):
  * SSS.MKF#0 事件对象表 5077 × 32B = 162464B -> lprgEventObject
    (`global.c:402 LOAD_DATA(..., 0, fpSSS)` **直接加载, 无字节序转换**)
  * SSS.MKF#1 场景表 295 × 8B -> rgScene (`global.c:404`)
  * SSS.MKF#4 脚本表 43503 × 8B = 348024B -> lprgScriptEntry (`global.c:351`)
    实测与活内存逐字节一致。
  * DATA.MKF#2 敌人队伍表 380 × 10B (5 个 WORD = 敌人**对象 ID**)
  * WORD.DAT 词表 10B/条 GBK, **词索引 = 对象 ID** (bot_itemmon.load_names)

场景 s 的对象区间 = `[rgScene[s-1].wEventObjectIndex, rgScene[s].wEventObjectIndex)`
(0-based, 与引擎 `scene.c:237` 完全同源)。
⚠ 本构建 SSS.MKF#1 只有 295 条 (场景 1..295), **场景 296..300 是空表项**:
必须用单调性校验挡掉, 否则"最后一个场景取到对象表尾"会把末尾 452 个对象
全算成场景 300 的 (旧版就因此多统计了 984 个怪点)。

★ 要素编号 (引擎**不给**任何编号/名字, 这里派生):
  · **id** = 对象的全局 1-based 序号 (lprgEventObject 下标+1, 1..5077)。
    由 rgScene[] 区间唯一确定, 重进场景/读档都不变 -> 当稳定主键。
    展示形式 `场景号-序号` (如 `221-3799`), 见 bot_featlog.code()。
  · **K** = 格坐标 (可读性最好, 与地图对得上, 但同格可能叠多个对象;
    怪会游走, **K 只有拾取点/传送点/绳子是长期稳定的**)。
  · 怪点: `team` = 0x0007 的 operand[0] (队伍号, 380 支);
    `ename` = 队伍成员逐名拼出 ("史莱姆×2 / 蛹")。
  · 拾取点: `item` = 0x001F 的 operand[0] (物品对象 ID);
    `iname` = WORD.DAT[item]。宝箱与地上采集物在游戏数据里**完全同类**
    (都是带 ADD_ITEM 的对象), 本清单不区分, 用物品名区分。
  · 引擎**没有**场景名/地图名表, 只有 map 号 (= MAP.MKF 块索引)。

产物 scene_info.json:
  { "221": { "map": 85,
             "monsters": [{"id":3799,"K":[a,b],"spr":11,"trig":41839,"auto":41094,
                           "chase":false,"state":2,"team":0,"ename":"史莱姆×2"}],
             "chests":   [{"id":3813,"K":[...],"spr":..,"trig":..,"mode":..,
                           "item":152,"iname":"傀儡虫"}],
             "portals":  [...], "signs": [...], "ropes": [...] } }

用法:
    python tools/build_scene_info.py [输出路径] [--game 游戏目录] [--live]
    默认走离线 SSS.MKF (不需要游戏开着); --live 才去读运行中的游戏内存。
名字表找不到时不影响分类, 只把 team/item 记下来、名字留空。
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import game_addr                          # noqa: E402  只在 --live 路径用得到

# ★ 下面 5 个是**标定值**。默认的数据源是**离线 SSS.MKF**(不读进程), 只有
#   `--live` 才用它们读运行中的游戏; 而 sdlpal.exe 开了 ASLR 且映像基址
#   "每次开机随机一次" -> 换机器/重启后标定值就错了, 所以统一叠平移。
PTR_ADDR = 0x00C38080      # gpGlobals->g.lprgEventObject
N_ADDR = 0x00C38084        # gpGlobals->g.nEventObject
SCENE_TAB = 0x00C38088     # rgScene[300]
SCENE_NOW = 0x00C3C95A     # wNumScene
SCRIPT_TAB_ADDR = 0x00C3AAB8   # gpGlobals->g.lprgScriptEntry
game_addr.shift_module(globals(), "PTR_ADDR", "N_ADDR", "SCENE_TAB",
                       "SCENE_NOW", "SCRIPT_TAB_ADDR")
MAX_SCENES = 300
OBJ_SIZE = 32
OBJ_FIELDS = ["sVanishTime", "x", "y", "sLayer", "wTriggerScript",
              "wAutoScript", "sState", "wTriggerMode", "wSpriteNum",
              "nSpriteFrames", "wDirection", "wCurrentFrameNum",
              "nScriptIdleFrame", "wSpritePtrOffset", "nSpriteFramesAuto",
              "wScriptIdleFrameCountAuto"]

OP_BATTLE = 0x0007
OP_GIVEITEM = 0x001F
OP_CHANGESCENE = 0x0059
OP_TEXT = 0xFFFF
OP_CHASE = 0x004C
OP_JUMP = 0x0003
OP_STOP_NEXT = 0x0001
OP_STOP_ENTRY = 0x0002
OP_CALL = 0x0004
OP_RANDJUMP = 0x0006
OP_GOTOIFNO = 0x000A
WANT_OPS = {OP_BATTLE, OP_GIVEITEM, OP_CHANGESCENE, OP_TEXT, OP_CHASE}

TEAM_REC = 10               # ENEMYTEAM: 5 个 WORD / 支
EMPTY_TEAM = (0, 0xFFFF)    # 空槽


# ======================================================================
#  游戏数据文件 —— 纯离线, 只读
# ======================================================================
def find_game_dir(explicit=None):
    """找游戏目录 (含 DATA.MKF/WORD.DAT/SSS.MKF)。找不到返回 None。"""
    cands = []
    if explicit:
        cands.append(explicit)
    ev = os.environ.get("PAL_GAME_DIR")
    if ev:
        cands.append(ev)
    d = os.path.dirname(os.path.abspath(__file__))       # tools/
    for _ in range(5):
        cands.append(d)
        nd = os.path.dirname(d)
        if nd == d:
            break
        d = nd
    for c in cands:
        try:
            if os.path.exists(os.path.join(c, "SSS.MKF")) or \
               os.path.exists(os.path.join(c, "DATA.MKF")) or \
               os.path.exists(os.path.join(c, "WORD.DAT")):
                return c
        except Exception:
            continue
    return None


def parse_enemy_teams(raw):
    """ENEMYTEAM 原始字节 -> {队伍号: [敌人对象ID...]} (空槽丢掉)。

    纯函数, 便于回归测试: DATA.MKF#2 每 10 字节一支队伍 (5 个 WORD),
    WORD 存的是**敌人对象 ID**(= WORD.DAT 词条索引), 不是敌人表下标。
    """
    out = {}
    if not raw:
        return out
    for t in range(len(raw) // TEAM_REC):
        ids = struct.unpack_from("<5H", raw, t * TEAM_REC)
        lst = [i for i in ids if i not in EMPTY_TEAM]
        if lst:
            out[t] = lst
    return out


def team_label(ids, names, maxn=6):
    """队伍成员 -> 可读名字串, 同名合并计数: "史莱姆×2 / 蛹"。

    查不到名字的用 "敌0x###" 兜底 (宁可难看也别丢信息)。
    """
    cnt = {}
    order = []
    for i in (ids or []):
        if i in EMPTY_TEAM:
            continue
        nm = (names or {}).get(i) or ("敌0x%03X" % int(i))
        if nm not in cnt:
            cnt[nm] = 0
            order.append(nm)
        cnt[nm] += 1
    parts = []
    for nm in order[:maxn]:
        parts.append("%s×%d" % (nm, cnt[nm]) if cnt[nm] > 1 else nm)
    if len(order) > maxn:
        parts.append("…")
    return " / ".join(parts)


def _mkf_read(game_dir, block, what):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from mkf import MKF
    p = os.path.join(game_dir, "SSS.MKF")
    if not os.path.exists(p):
        raise IOError("找不到 %s" % p)
    return MKF(p).read(block)


def load_offline(game_dir):
    """读离线 SSS.MKF -> (objs, scenes, SC, n_obj, n_sc)。失败抛异常。

    scenes 补齐到 MAX_SCENES 条 (文件只有 295 条, 后面是空表项)。
    """
    raw0 = _mkf_read(game_dir, 0, "对象表")
    raw1 = _mkf_read(game_dir, 1, "场景表")
    raw4 = _mkf_read(game_dir, 4, "脚本表")
    n_obj = len(raw0) // OBJ_SIZE
    objs = [dict(zip(OBJ_FIELDS, struct.unpack_from("<16H", raw0, i * OBJ_SIZE)))
            for i in range(n_obj)]
    n_file_sc = len(raw1) // 8
    scenes = [struct.unpack_from("<4H", raw1, i * 8) for i in range(n_file_sc)]
    while len(scenes) < MAX_SCENES:
        scenes.append((0, 0, 0, 0))
    n_sc = len(raw4) // 8
    SC = [struct.unpack_from("<4H", raw4, i * 8) for i in range(n_sc)]
    return objs, scenes, SC, n_obj, n_sc


def load_teams(game_dir):
    """读 DATA.MKF#2 -> {队伍号: [对象ID]}。失败返回 {}。"""
    if not game_dir:
        return {}
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from mkf import MKF
        p = os.path.join(game_dir, "DATA.MKF")
        if not os.path.exists(p):
            return {}
        return parse_enemy_teams(MKF(p).read(2))
    except Exception as e:
        print("  (队伍表读取失败: %s -> 敌人名留空)" % e, flush=True)
        return {}


def load_words(game_dir):
    """读 WORD.DAT -> {词条ID: 名称} (物品名/敌人名同一张表)。

    复用 bot_itemmon.load_names (词表解析的唯一定义处, 避免两套实现漂移)。
    """
    if not game_dir:
        return {}
    p = os.path.join(game_dir, "WORD.DAT")
    if not os.path.exists(p):
        return {}
    try:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if base not in sys.path:
            sys.path.insert(0, base)
        import bot_itemmon
        return bot_itemmon.load_names(p)
    except Exception as e:
        print("  (词表读取失败: %s -> 名字留空)" % e, flush=True)
        return {}


# ======================================================================
#  活内存数据源 (--live; 只在需要"看当前真实状态"时用)
# ======================================================================
def find_pid():
    u32 = ctypes.windll.user32
    res = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def cb(hwnd, lp):
        pid = wt.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        n = u32.GetWindowTextLengthW(hwnd)
        if n:
            b = ctypes.create_unicode_buffer(n + 1)
            u32.GetWindowTextW(hwnd, b, n + 1)
            if "Pal" in b.value:
                res.append(pid.value)
        return True
    u32.EnumWindows(cb, 0)
    return sorted(set(res))


def open_game():
    k32 = ctypes.windll.kernel32
    pids = find_pid()
    if not pids:
        raise SystemExit("找不到游戏窗口(标题含 'Pal')，请先启动游戏")
    h = k32.OpenProcess(0x0010 | 0x0400, False, pids[0])
    if not h:
        raise SystemExit("OpenProcess 失败")
    return h


class Reader:
    def __init__(self, h):
        self.h = h
        self.got = ctypes.c_size_t()

    def rd(self, addr, size):
        k32 = ctypes.windll.kernel32
        buf = (ctypes.c_char * size)()
        if not k32.ReadProcessMemory(self.h, ctypes.c_void_p(addr), buf, size,
                                     ctypes.byref(self.got)):
            return None
        return buf.raw[:self.got.value]

    def u32(self, a):
        d = self.rd(a, 4)
        return struct.unpack("<I", d)[0] if d and len(d) == 4 else None

    def u16(self, a):
        d = self.rd(a, 2)
        return struct.unpack("<H", d)[0] if d and len(d) == 2 else None


def load_live():
    """读运行中的游戏内存 -> (objs, scenes, SC, n_obj, n_sc, scene_now)。"""
    r = Reader(open_game())
    obj_ptr = r.u32(PTR_ADDR)
    n_obj = r.u32(N_ADDR)
    sc_tab_ptr = r.u32(SCRIPT_TAB_ADDR)
    n_sc = r.u32(SCRIPT_TAB_ADDR + 4)
    scene_now = r.u16(SCENE_NOW)
    print("对象表 0x%08X x%d | 脚本表 0x%08X x%d | 当前场景 %s"
          % (obj_ptr, n_obj, sc_tab_ptr, n_sc, scene_now), flush=True)
    if not obj_ptr or not n_obj or n_obj > 5500:
        raise SystemExit("对象表指针异常")
    if not sc_tab_ptr or not n_sc or n_sc < 1000:
        raise SystemExit("脚本表指针异常(先在游戏里进过场景再运行)")
    raw_obj = r.rd(obj_ptr, n_obj * OBJ_SIZE)
    objs = [dict(zip(OBJ_FIELDS, struct.unpack_from("<16H", raw_obj,
                                                    i * OBJ_SIZE)))
            for i in range(n_obj)]
    raw_sc = r.rd(sc_tab_ptr, n_sc * 8)
    SC = [struct.unpack_from("<4H", raw_sc, i * 8) for i in range(n_sc)]
    raw_tab = r.rd(SCENE_TAB, MAX_SCENES * 8)
    scenes = [struct.unpack_from("<4H", raw_tab, i * 8)
              for i in range(MAX_SCENES)]
    return objs, scenes, SC, n_obj, n_sc, scene_now


# ======================================================================
#  分类 (纯函数 —— 回归测试的锚点)
# ======================================================================
def kof(px, py):
    """地图像素坐标 -> 格坐标 K (与 pal_bot_mem5.kof 同式)。"""
    return ((px - 2 * py) // 32, (px + 2 * py - 32) // 32)


def walk_ops(SC, n_sc, entry, want, maxnodes=400):
    """从 entry 出发做控制流图遍历, 判断会执行 want 中的哪些指令。

    返回 {命中的op: (a,b,c)} —— **只留第一次遇到的操作数** (取队伍号/物品ID 用);
    调用方用 `op in hits` 判命中。纯函数(SC 是脚本表切片), 便于回归测试。
    """
    hits = {}
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
            if cnt > maxnodes:
                break
            op, a, b, c = SC[e]
            if op in want:
                hits.setdefault(op, (a, b, c))
            if op == 0x0000:                      # stop
                break
            if op == OP_JUMP:                     # 无条件跳转
                e = a
                continue
            if op == OP_STOP_ENTRY:               # 停止并换入口
                break
            if op == OP_STOP_NEXT:                # 停止并用下一条做入口
                e += 1
                continue
            if op == OP_CALL:                     # 调用子脚本
                stack.append(a)
                e += 1
                continue
            if op == OP_RANDJUMP:                 # 按概率跳转
                stack.append(a)
                e += 1
                continue
            if op == OP_GOTOIFNO:
                stack.append(a)
                e += 1
                continue
            e += 1
    return hits


def scene_ranges(scenes, n_obj, max_scenes=MAX_SCENES):
    """场景号 -> (lo, hi) 的 **1-based 闭区间**; 空/退化表项跳过。

    区间语义 = 引擎 scene.c:237 的 `[rgScene[s-1].idx, rgScene[s].idx)`。
    ★ 单调性校验: 场景 296..300 在本构建里是空表项(idx=0), 必须挡掉,
      否则最后一个场景会把对象表尾全部吃掉 (旧版多算 984 个怪点)。
    """
    out = {}
    prev = 0
    for s in range(1, max_scenes + 1):
        cur = scenes[s][3] if s < len(scenes) else n_obj
        if cur <= prev:
            continue
        lo, hi = prev + 1, min(cur, n_obj)
        prev = cur
        if hi < lo or lo - 1 >= n_obj:
            continue
        out[s] = (lo, hi)
    return out


def classify(objs, scenes, SC, n_sc, n_obj, teams=None, names=None,
             keep_empty=False):
    """对象表 + 场景表 + 脚本表 -> {场景号: {map, monsters, chests, ...}}。

    纯函数, 不碰进程/文件 —— 这是 build_scene_info 唯一需要回归测试的部分。
    keep_empty=True 时连"只有传送点/石碑"的场景也留下。
    """
    teams = teams or {}
    names = names or {}
    per_scene = {}
    for s, (lo, hi) in sorted(scene_ranges(scenes, n_obj).items()):
        info = {"map": scenes[s - 1][0], "monsters": [], "chests": [],
                "portals": [], "signs": [], "ropes": [],
                "n_objects": hi - lo + 1}
        for i, o in enumerate(objs[lo - 1:hi]):
            if o["sState"] == 0 and o["wSpriteNum"] == 0 and not (
                    o["wTriggerScript"] or o["wAutoScript"]):
                continue
            hits = {}
            for fld in ("wTriggerScript", "wAutoScript"):
                if o[fld]:
                    for op, ops in walk_ops(SC, n_sc, o[fld], WANT_OPS).items():
                        hits.setdefault(op, ops)
            base = {"id": lo + i,               # ★ 全局对象序号 = 稳定编号
                    "K": list(kof(o["x"], o["y"])),
                    "spr": o["wSpriteNum"], "mode": o["wTriggerMode"],
                    "state": o["sState"], "trig": o["wTriggerScript"],
                    "auto": o["wAutoScript"]}
            if OP_BATTLE in hits and o["wSpriteNum"] != 0:
                m = dict(base)
                m["chase"] = OP_CHASE in hits or o["wAutoScript"] != 0
                team = hits[OP_BATTLE][0]
                m["team"] = team
                m["ename"] = team_label(teams.get(team), names)
                info["monsters"].append(m)
            elif OP_GIVEITEM in hits:
                c = dict(base)
                it = hits[OP_GIVEITEM][0]
                c["item"] = it                  # ★ 拾取的物品 ID
                c["iname"] = names.get(it, "")
                info["chests"].append(c)
            elif OP_CHANGESCENE in hits and 0 < o["wTriggerMode"] < 4:
                # 搜索触发型换景 = 绳子/调查式传送 (试炼窟上下层)
                info["ropes"].append(dict(base))
            elif OP_CHANGESCENE in hits:
                info["portals"].append(dict(base))
            elif OP_TEXT in hits:
                info["signs"].append(dict(base))
        if keep_empty or info["monsters"] or info["chests"] or info["ropes"]:
            per_scene[s] = info
    return per_scene


def stats(per_scene):
    """清单统计 (日志/报告共用)。"""
    return {
        "scenes": len(per_scene),
        "monsters": sum(len(v["monsters"]) for v in per_scene.values()),
        "chests": sum(len(v["chests"]) for v in per_scene.values()),
        "ropes": sum(len(v["ropes"]) for v in per_scene.values()),
        "portals": sum(len(v["portals"]) for v in per_scene.values()),
        "signs": sum(len(v["signs"]) for v in per_scene.values()),
        "named_mon": sum(1 for v in per_scene.values() for m in v["monsters"]
                         if m.get("ename")),
        "named_chest": sum(1 for v in per_scene.values() for c in v["chests"]
                           if c.get("iname")),
    }


def main():
    args = [a for a in sys.argv[1:]]
    game_dir = None
    use_live = False
    keep_empty = False
    for flag in ("--live", "--keep-empty"):
        if flag in args:
            args.remove(flag)
            use_live = use_live or flag == "--live"
            keep_empty = keep_empty or flag == "--keep-empty"
    if "--game" in args:
        i = args.index("--game")
        game_dir = args[i + 1] if i + 1 < len(args) else None
        del args[i:i + 2]
    out_path = args[0] if args else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "scene_info.json")

    game_dir = game_dir or find_game_dir()
    teams = load_teams(game_dir)
    names = load_words(game_dir)
    print("游戏目录 %s | 敌人队伍 %d 支 | 词条 %d 条"
          % (game_dir or "<未找到>", len(teams), len(names)), flush=True)

    scene_now = None
    if use_live:
        objs, scenes, SC, n_obj, n_sc, scene_now = load_live()
        src = "游戏内存(--live)"
    else:
        if not game_dir:
            raise SystemExit("找不到游戏目录(需要 SSS.MKF) -> 用 --game 指定 "
                             "或设置环境变量 PAL_GAME_DIR")
        objs, scenes, SC, n_obj, n_sc = load_offline(game_dir)
        src = "离线 SSS.MKF"
    print("数据源 %s | 对象 %d | 场景表 %d 条 | 脚本 %d"
          % (src, n_obj, len(scenes), n_sc), flush=True)

    per_scene = classify(objs, scenes, SC, n_sc, n_obj, teams, names,
                         keep_empty=keep_empty)
    st = stats(per_scene)
    print("含要素场景 %d 个 | 怪点 %d (有名 %d) | 拾取点 %d (有名 %d) | "
          "绳 %d | 传送 %d | 石碑 %d"
          % (st["scenes"], st["monsters"], st["named_mon"],
             st["chests"], st["named_chest"], st["ropes"],
             st["portals"], st["signs"]), flush=True)
    sc_now = scene_now
    if sc_now is None and not use_live:
        # 顺手读一下游戏当前场景号 (只读; 游戏没开就跳过)
        try:
            sc_now = Reader(open_game()).u16(SCENE_NOW)
        except Exception:
            sc_now = None
    if sc_now in per_scene:
        v = per_scene[sc_now]
        print("\n场景 %d (地图%d): 怪点 %d, 拾取点 %d, 绳子 %d, 传送 %d, 石碑 %d"
              % (sc_now, v["map"], len(v["monsters"]), len(v["chests"]),
                 len(v["ropes"]), len(v["portals"]), len(v["signs"])),
              flush=True)
        for m in v["monsters"]:
            print("   怪 %d-%04d K%-12s spr=%-4d 队伍=%-4s %s"
                  % (sc_now, m["id"], str(tuple(m["K"])), m["spr"],
                     m.get("team"), m.get("ename") or "?"))
        for c in v["chests"]:
            print("   拾 %d-%04d K%-12s spr=%-4d 物品=%-5s %s"
                  % (sc_now, c["id"], str(tuple(c["K"])), c["spr"],
                     c.get("item"), c.get("iname") or "?"))

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(per_scene, f, ensure_ascii=False, separators=(",", ":"))
    print("\n已写出 %s (%.1f KB)"
          % (out_path, os.path.getsize(out_path) / 1024.0), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
