# -*- coding: utf-8 -*-
"""dump_objects.py — 从运行中的 sdlpal.exe 内存直接导出「事件对象表」。

原理（来自 SDLPAL 源码 global.h tagGAMEDATA）:
    LPEVENTOBJECT lprgEventObject;   // +0
    int           nEventObject;      // +4
    SCENE         rgScene[300];      // +8   (已知绝对地址 0x00C38088)

  => lprgEventObject 指针变量在 0x00C38080, nEventObject 在 0x00C38084。

EVENTOBJECT 结构 (32 字节, 16 x WORD):
    sVanishTime, x, y, sLayer, wTriggerScript, wAutoScript, sState,
    wTriggerMode, wSpriteNum, nSpriteFrames, wDirection, wCurrentFrameNum,
    nScriptIdleFrame, wSpritePtrOffset, nSpriteFramesAuto, wScriptIdleFrameCountAuto

x/y 是**地图像素坐标**, 与队伍真实像素 P = viewport + partyoffset 同坐标系,
换算格坐标 K=(a,b) = ((x-2y)//32, (x+2y-32)//32)。

用途: 一次性列出所有场景的"怪点/对象"候选, 不需要机器人反复跑图。
"""
import ctypes
import ctypes.wintypes as wt
import json
import struct
import sys

k32 = ctypes.windll.kernel32
u32 = ctypes.windll.user32
OPEN_OK = 0x0010 | 0x0400

PTR_ADDR = 0x00C38080       # &gpGlobals->g.lprgEventObject
N_ADDR = 0x00C38084         # &gpGlobals->g.nEventObject
SCENE_TAB = 0x00C38088      # rgScene[]
SCENE_NOW = 0x00C3C95A      # wNumScene
MAX_SCENES = 300
OBJ_SIZE = 32
OBJ_FIELDS = ["sVanishTime", "x", "y", "sLayer", "wTriggerScript",
              "wAutoScript", "sState", "wTriggerMode", "wSpriteNum",
              "nSpriteFrames", "wDirection", "wCurrentFrameNum",
              "nScriptIdleFrame", "wSpritePtrOffset", "nSpriteFramesAuto",
              "wScriptIdleFrameCountAuto"]
TRIGGER = {0: "none", 1: "searchNear", 2: "searchNormal", 3: "searchFar",
           4: "touchNear", 5: "touchNormal", 6: "touchFar",
           7: "touchFarther", 8: "touchFarthest"}


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


class Mem:
    def __init__(self):
        pids = find_pid()
        if not pids:
            raise SystemExit("找不到游戏窗口 (标题含 'Pal')")
        self.pid = pids[0]
        self.h = k32.OpenProcess(OPEN_OK, False, self.pid)
        if not self.h:
            raise SystemExit("OpenProcess 失败")
        self.got = ctypes.c_size_t()

    def rd(self, addr, size):
        buf = (ctypes.c_char * size)()
        if not k32.ReadProcessMemory(self.h, ctypes.c_void_p(addr),
                                     buf, size, ctypes.byref(self.got)):
            return None
        return buf.raw[:self.got.value]

    def u32(self, addr):
        d = self.rd(addr, 4)
        return struct.unpack("<I", d)[0] if d and len(d) == 4 else None

    def u16(self, addr):
        d = self.rd(addr, 2)
        return struct.unpack("<H", d)[0] if d and len(d) == 2 else None

    def i16(self, addr):
        d = self.rd(addr, 2)
        return struct.unpack("<h", d)[0] if d and len(d) == 2 else None


def kof(px, py):
    return ((px - 2 * py) // 32, (px + 2 * py - 32) // 32)


def main():
    m = Mem()
    ptr = m.u32(PTR_ADDR)
    n = m.u32(N_ADDR)
    scene = m.u16(SCENE_NOW)
    print("pid=%d  lprgEventObject=0x%08X  nEventObject=%d  当前场景=%s"
          % (m.pid, ptr or 0, n or -1, scene))
    if not ptr or not n or n > 5500 or n < 10:
        print("!! 指针/数量不合理, 停止")
        return 1

    # 场景 -> 事件对象索引范围
    tab = m.rd(SCENE_TAB, MAX_SCENES * 8)
    scenes = []
    for i in range(MAX_SCENES):
        wmap, _s1, _s2, idx = struct.unpack_from("<4H", tab, i * 8)
        scenes.append((wmap, idx))

    # 整块读事件对象
    raw = m.rd(ptr, n * OBJ_SIZE)
    if not raw or len(raw) < n * OBJ_SIZE:
        print("!! 读事件对象块失败 (got=%s)" % (len(raw) if raw else 0))
        return 1
    objs = [dict(zip(OBJ_FIELDS, struct.unpack_from("<16H", raw, i * OBJ_SIZE)))
            for i in range(n)]

    # 按场景切分: 场景 s 的对象 = [idx[s-1]+1 .. idx[s]] (1-based 对象号 -> -1 下标)
    per_scene = {}
    for s in range(1, MAX_SCENES + 1):
        lo = scenes[s - 1][1] + 1          # 1-based
        hi = scenes[s][1] if s < MAX_SCENES else n
        if hi >= lo:
            per_scene[s] = objs[lo - 1:hi]

    tot = sum(len(v) for v in per_scene.values())
    print("总共 %d 个对象, 分布在 %d 个场景" % (tot, len(per_scene)))

    # 概览: 有触发脚本/自动脚本/精灵 的对象
    stats = {"with_sprite": 0, "with_trigger": 0, "with_auto": 0}
    for o in objs:
        if o["wSpriteNum"]:
            stats["with_sprite"] += 1
        if o["wTriggerScript"]:
            stats["with_trigger"] += 1
        if o["wAutoScript"]:
            stats["with_auto"] += 1
    print("统计:", stats)

    if scene and scene in per_scene:
        print("\n=== 当前场景 %d 的 %d 个对象 ===" % (scene, len(per_scene[scene])))
        for i, o in enumerate(per_scene[scene]):
            a, b = kof(o["x"], o["y"])
            print("  #%2d px(%5d,%5d) K(%4d,%4d) spr=%3d mode=%-13s "
                  "trig=%5d auto=%5d state=%d vanish=%d"
                  % (i, o["x"], o["y"], a, b, o["wSpriteNum"],
                     TRIGGER.get(o["wTriggerMode"], "?"),
                     o["wTriggerScript"], o["wAutoScript"],
                     o["sState"], o["sVanishTime"]))

    # 落盘全量
    out = {str(s): [{"px": o["x"], "py": o["y"], "K": list(kof(o["x"], o["y"])),
                     "sprite": o["wSpriteNum"], "mode": o["wTriggerMode"],
                     "trig": o["wTriggerScript"], "auto": o["wAutoScript"],
                     "state": o["sState"], "vanish": o["sVanishTime"]}
                    for o in v] for s, v in per_scene.items()}
    with open("objects_dump.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("\n已写出 objects_dump.json (%d 场景)" % len(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
