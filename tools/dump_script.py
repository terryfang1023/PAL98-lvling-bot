# -*- coding: utf-8 -*-
"""dump_script.py — 打印指定脚本入口的字节码序列 (用于看懂遇敌/战斗脚本结构)。

用法: python tools/dump_script.py 36517 36569 41100 41827
"""
import ctypes
import ctypes.wintypes as wt
import struct
import sys

k32 = ctypes.windll.kernel32
u32 = ctypes.windll.user32
OPEN_OK = 0x0010 | 0x0400
SCRIPT_TAB_ADDR = 0x00C3AAB8

OPNAME = {
    0x0000: "END", 0x0001: "SET", 0x0002: "SET2", 0x0003: "ADD",
    0x0004: "JUMP", 0x0005: "RAW", 0x0006: "RAND_JUMP",
    0x0007: "START_BATTLE", 0x0008: "SET_NEXT", 0x0009: "WAIT_FRAMES",
    0x000A: "GOTO_IF_NO", 0x000B: "WALK_N", 0x000C: "WALK_E",
    0x000D: "WALK_S", 0x000E: "WALK_W", 0x000F: "SET_DIR/GESTURE",
    0x0010: "WALK_TO", 0x0011: "WALK_TO_SLOW", 0x0012: "SET_POS_REL",
    0x0013: "SET_POS", 0x0014: "SET_GESTURE", 0x0015: "SET_STATE",
    0x0016: "SET_OBJ_LAYER", 0x0017: "SET_OBJ_FRAME", 0x0020: "REMOVE_ITEM",
    0x0036: "WALK_PARTY", 0x0037: "RNG_ANIM", 0x0038: "TELEPORT_PARTY",
    0x0039: "DRAIN_HP", 0xFFFF: "TEXT",
}


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


h = k32.OpenProcess(OPEN_OK, False, find_pid()[0])
got = ctypes.c_size_t()


def rd(addr, size):
    buf = (ctypes.c_char * size)()
    if not k32.ReadProcessMemory(h, ctypes.c_void_p(addr), buf, size,
                                 ctypes.byref(got)):
        return None
    return buf.raw[:got.value]


def u32at(a):
    d = rd(a, 4)
    return struct.unpack("<I", d)[0] if d and len(d) == 4 else None


SC_TAB = u32at(SCRIPT_TAB_ADDR)
N_SC = u32at(SCRIPT_TAB_ADDR + 4)
raw = rd(SC_TAB, N_SC * 8)
SC = [struct.unpack_from("<4H", raw, i * 8) for i in range(N_SC)]
print("脚本表 0x%08X x%d\n" % (SC_TAB, N_SC))


def show(entry, limit=40, indent="  "):
    print("脚本 %d:" % entry)
    walked = set()
    e = entry
    for _ in range(limit):
        if e <= 0 or e >= N_SC:
            print(indent + "-> 结束于入口 %d" % e)
            break
        if e in walked:
            print(indent + "-> (回环)")
            break
        walked.add(e)
        op, a, b, c = SC[e]
        nm = OPNAME.get(op, "")
        print("%s[%5d] 0x%04X %-14s %5d %5d %5d" % (indent, e, op, nm, a, b, c))
        if op == 0x0000:
            print(indent + "-> END")
            break
        e += 1
    print()


for arg in sys.argv[1:]:
    show(int(arg))
