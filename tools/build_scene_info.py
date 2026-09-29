# -*- coding: utf-8 -*-
"""build_scene_info.py — 从运行中的游戏内存导出「全图场景要素清单」。

依据 (SDLPAL 源码 + 2026-09-29 实测):
  * EVENTOBJECT: sVanishTime,x,y,sLayer,wTriggerScript,wAutoScript,sState,
                 wTriggerMode,wSpriteNum,...   (32 字节)
  * 关键脚本指令:
      0x0007 START_BATTLE           -> 怪（走上去/触发即打）
      0x001F ADD_ITEM_TO_INVENTORY  -> 宝箱（给物品）
      0x0059 CHANGE_SCENE           -> 传送阵/出入口
      0xFFFF TEXT                   -> 可读文本（石碑/告示牌）
      0x004C CHASE_PLAYER           -> 怪物会主动追玩家
      0x0052 HIDE_FOR_A_WHILE       -> 被打败后隐藏 ~800 帧后重生
  * 触发几何 (play.c:112):
      |dpx| + 2|dpy| < (mode-4)*32 + 16   <=>  32*切比雪夫格距 < 阈值
      mode=5 -> 玩家进入怪周围 1 格(含斜角)即触发; mode=4 -> 必须同格

产物 scene_info.json:
  { "157": { "map": 72,
             "monsters": [{"K":[a,b],"spr":11,"trig":41839,"auto":41094,
                           "chase":false,"state":2}],
             "chests":   [{"K":[...],"spr":..,"trig":..,"mode":..}],
             "portals":  [...], "signs": [...] } }

用法: python tools/build_scene_info.py [输出路径]
必须先让游戏跑到目标存档(内存里才是完整对象表)。
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import struct
import sys

k32 = ctypes.windll.kernel32
u32 = ctypes.windll.user32
OPEN_OK = 0x0010 | 0x0400

PTR_ADDR = 0x00C38080      # gpGlobals->g.lprgEventObject
N_ADDR = 0x00C38084        # gpGlobals->g.nEventObject
SCENE_TAB = 0x00C38088     # rgScene[300]
SCENE_NOW = 0x00C3C95A     # wNumScene
SCRIPT_TAB_ADDR = 0x00C3AAB8   # gpGlobals->g.lprgScriptEntry (扫描定位)
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


def find_pid():
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
    pids = find_pid()
    if not pids:
        raise SystemExit("找不到游戏窗口(标题含 'Pal')，请先启动游戏")
    h = k32.OpenProcess(OPEN_OK, False, pids[0])
    if not h:
        raise SystemExit("OpenProcess 失败")
    return h


class Reader:
    def __init__(self, h):
        self.h = h
        self.got = ctypes.c_size_t()

    def rd(self, addr, size):
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


def kof(px, py):
    return ((px - 2 * py) // 32, (px + 2 * py - 32) // 32)


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "scene_info.json")
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

    def walk(entry, want, maxnodes=400):
        """从 entry 出发做控制流图遍历, 判断是否会执行 want 中的任一指令。
        返回 (命中的op集合, 是否遇到 chase)。"""
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
                if cnt > maxnodes:
                    break
                op, a, b, c = SC[e]
                if op in want:
                    hit.add(op)
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
        return hit

    WANT = {OP_BATTLE, OP_GIVEITEM, OP_CHANGESCENE, OP_TEXT, OP_CHASE}
    per_scene = {}
    for s in range(1, MAX_SCENES + 1):
        lo = scenes[s - 1][3] + 1
        hi = scenes[s][3] if s < MAX_SCENES else n_obj
        if hi < lo or lo - 1 >= n_obj:
            continue
        lst = objs[lo - 1:min(hi, n_obj)]
        info = {"map": scenes[s - 1][0], "monsters": [], "chests": [],
                "portals": [], "signs": [], "n_objects": len(lst)}
        for i, o in enumerate(lst):
            if o["sState"] == 0 and o["wSpriteNum"] == 0 and not (
                    o["wTriggerScript"] or o["wAutoScript"]):
                continue
            hit = set()
            for fld in ("wTriggerScript", "wAutoScript"):
                if o[fld]:
                    hit |= walk(o[fld], WANT)
            k = list(kof(o["x"], o["y"]))
            base = {"K": k, "spr": o["wSpriteNum"], "mode": o["wTriggerMode"],
                    "state": o["sState"], "trig": o["wTriggerScript"],
                    "auto": o["wAutoScript"]}
            if OP_BATTLE in hit and o["wSpriteNum"] != 0:
                m = dict(base)
                m["chase"] = OP_CHASE in hit or o["wAutoScript"] != 0
                info["monsters"].append(m)
            elif OP_GIVEITEM in hit:
                info["chests"].append(dict(base))
            elif OP_CHANGESCENE in hit and o["wSpriteNum"] == 0:
                info["portals"].append(dict(base))
            elif OP_TEXT in hit:
                info["signs"].append(dict(base))
        if info["monsters"] or info["chests"]:
            per_scene[s] = info

    # 补上没有任何要素但有对象的场景(便于巡检)
    tot_m = sum(len(v["monsters"]) for v in per_scene.values())
    tot_c = sum(len(v["chests"]) for v in per_scene.values())
    print("含要素场景 %d 个 | 怪点合计 %d | 宝箱合计 %d"
          % (len(per_scene), tot_m, tot_c), flush=True)
    if scene_now in per_scene:
        v = per_scene[scene_now]
        print("\n当前场景 %d (地图%d): 怪点 %d, 宝箱 %d, 传送 %d, 石碑 %d"
              % (scene_now, v["map"], len(v["monsters"]), len(v["chests"]),
                 len(v["portals"]), len(v["signs"])), flush=True)
        for m in v["monsters"]:
            print("   怪 K%-12s spr=%-4d trig=%-6d 追玩家=%s"
                  % (str(tuple(m["K"])), m["spr"], m["trig"], m["chase"]))
        for c in v["chests"]:
            print("   箱 K%-12s spr=%-4d mode=%d" % (str(tuple(c["K"])), c["spr"],
                                                  c["mode"]))

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(per_scene, f, ensure_ascii=False, separators=(",", ":"))
    print("\n已写出 %s (%.1f KB)" % (out_path, os.path.getsize(out_path) / 1024.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
