# -*- coding: utf-8 -*-
"""_audit_rope.py — 一次性只读审计: 当前场景的绳子对象 + 队伍位置/朝向。

零副作用: 只 ReadProcessMemory, 不按键、不写任何运行标志/日志。
用途: 真机排查「到绳子旁按空格却没反应」—— 看绳子的触发方式/朝向要求,
以及队伍站在哪、面朝哪, 与 rope_probe 的假设对不对得上。
"""
import ctypes
import ctypes.wintypes as wt
import json
import struct
import sys

k32 = ctypes.windll.kernel32
u32 = ctypes.windll.user32
OPEN_OK = 0x0010 | 0x0400

PTR_ADDR = 0x00C38080
N_ADDR = 0x00C38084
SCENE_TAB = 0x00C38088
MAX_SCENES = 300
OBJ_SIZE = 32
READ_AT = 0x00C3C8F0
READ_N = 120
IVP = (0x00C3C8FC - READ_AT) // 2
IPO = (0x00C3C900 - READ_AT) // 2
IPD = (0x00C3C958 - READ_AT) // 2
SCENE_OFF = (0x00C3C95A - READ_AT) // 2
# ★ 上边是标定值 -> 运行时地址 (ASLR 每次开机随机基址; 见 game_addr 模块头)。
#   索引 (IVP/IPO/IPD/SCENE_OFF) 是"相对 READ_AT 的偏移", 与基址无关, 所以在
#   平移 READ_AT 之前算好就不会受影响。
import os as _os                                        # noqa: E402
sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import game_addr as _GA                                 # noqa: E402
_GA.shift_module(globals(), "PTR_ADDR", "N_ADDR", "SCENE_TAB", "READ_AT")

FIELDS = ["sVanishTime", "x", "y", "sLayer", "wTriggerScript", "wAutoScript",
          "sState", "wTriggerMode", "wSpriteNum", "nSpriteFrames",
          "wDirection", "wCurrentFrameNum", "nScriptIdleFrame",
          "wSpritePtrOffset", "nSpriteFramesAuto", "wScriptIdleFrameCountAuto"]
MODE = {0: "none", 1: "searchNear", 2: "searchNormal", 3: "searchFar",
        4: "touchNear", 5: "touchNormal", 6: "touchFar"}
DIRS = {0: "south(下)", 1: "west(左)", 2: "north(上)", 3: "east(右)"}


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


def kof(px, py):
    return ((px - 2 * py) // 32, (px + 2 * py - 32) // 32)


def main():
    pids = find_pid()
    if not pids:
        print("NO_PAL_WINDOW")
        return
    h = k32.OpenProcess(OPEN_OK, False, pids[0])
    if not h:
        print("OPEN_FAIL")
        return
    got = ctypes.c_size_t()

    def rd(addr, size):
        buf = (ctypes.c_char * size)()
        if not k32.ReadProcessMemory(h, ctypes.c_void_p(addr), buf, size,
                                     ctypes.byref(got)):
            return None
        return buf.raw[:got.value]

    def u32v(addr):
        d = rd(addr, 4)
        return struct.unpack("<I", d)[0] if d and len(d) == 4 else None

    def u16v(addr):
        d = rd(addr, 2)
        return struct.unpack("<H", d)[0] if d and len(d) == 2 else None

    Wraw = rd(READ_AT, READ_N)
    if not Wraw:
        print("READ_STATE_FAIL")
        return
    W = struct.unpack("<%dH" % (len(Wraw) // 2), Wraw)
    sc = W[SCENE_OFF]
    vp = W[IVP] | (W[IVP + 1] << 16)
    po = W[IPO] | (W[IPO + 1] << 16)
    px = (vp & 0xFFFF) + (po & 0xFFFF)
    py = (vp >> 16) + (po >> 16)
    dird = W[IPD]
    print("scene=%d  party px,py=(%d,%d)  K=%s  direction=%s (%d)"
          % (sc, px, py, kof(px, py), DIRS.get(dird, "?"), dird))

    # 场景对象
    ptr = u32v(PTR_ADDR)
    n = u32v(N_ADDR)
    tab = rd(SCENE_TAB, MAX_SCENES * 8)
    if not ptr or not n or not tab:
        print("OBJ_TABLE_FAIL")
        return
    idxs = [struct.unpack_from("<4H", tab, i * 8)[3] for i in range(MAX_SCENES)]
    lo, hi = idxs[sc - 1] + 1, (idxs[sc] if sc < MAX_SCENES else n)
    cnt = hi - lo + 1
    print("objects: idx[%d..%d] cnt=%d" % (lo, hi, cnt))
    raw = rd(ptr + (lo - 1) * OBJ_SIZE, cnt * OBJ_SIZE)
    if not raw:
        print("OBJ_READ_FAIL")
        return
    # 已知绳子(来自 scene_info.json) 对照
    try:
        si = json.load(open("scene_info.json", encoding="utf-8"))
        ropes = si.get(str(sc), {}).get("ropes", [])
    except Exception:
        ropes = []
    ropeK = {(r["K"][0], r["K"][1]): r for r in ropes}
    print("scene_info.json ropes for scene %d: %s" % (sc, [list(k) for k in ropeK]))
    print("-" * 100)
    for i in range(cnt):
        f = struct.unpack_from("<16H", raw, i * OBJ_SIZE)
        if f[1] == 0 and f[2] == 0:
            continue
        K = kof(f[1], f[2])
        tag = ""
        if K in ropeK:
            tag = "  <== 绳子表里"
        interesting = (0 < f[7] < 4) or tag or (f[6] == 2 and f[8] == 0
                                                and f[4] not in (0, 0xFFFF))
        if not interesting:
            continue
        print("#%-4d K=%-12s x,y=(%d,%d) state=%-3d trigMode=%d(%s) "
              "trigScr=%-5d autoScr=%-5d spr=%-4d dir=%d%s"
              % (lo + i - 1, str(K), f[1], f[2], f[6], f[7],
                 MODE.get(f[7], "?"), f[4], f[5], f[8], f[10], tag))


if __name__ == "__main__":
    main()
