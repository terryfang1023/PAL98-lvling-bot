# -*- coding: utf-8 -*-
"""probe_script.py — 验证脚本表 lprgScriptEntry 地址推导是否正确。

推导 (SDLPAL global.h tagGAMEDATA):
    +0x0000 lprgEventObject (4)
    +0x0004 nEventObject    (4)
    +0x0008 rgScene[300]    (300*8 = 0x960)
    +0x0968 rgObject[600]   (600*14 = 0x20D0)
    +0x2438 lprgScriptEntry (4)
    +0x243C nScriptEntry    (4)
已知 rgScene 绝对地址 0x00C38088 => GAMEDATA 基址 0x00C38080
=> lprgScriptEntry @ 0x00C3A4B8, nScriptEntry @ 0x00C3A4BC
"""
import ctypes
import ctypes.wintypes as wt
import struct

k32 = ctypes.windll.kernel32
u32 = ctypes.windll.user32
OPEN_OK = 0x0010 | 0x0400
OPTAB = 0x00C3A4B8
NTAB = 0x00C3A4BC


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


pids = find_pid()
print("pids", pids)
h = k32.OpenProcess(OPEN_OK, False, pids[0])
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


print("lprgScriptEntry = 0x%08X" % (u32at(OPTAB) or 0))
print("nScriptEntry    = %d" % (u32at(NTAB) or -1))

# 读若干脚本条目, 看 wOperation 是否合理
ptr = u32at(OPTAB)
n = u32at(NTAB)
if ptr and n and 1000 < n < 200000:
    raw = rd(ptr, 64 * 8)
    print("\n前若干脚本条目:")
    for i in range(16):
        op, a, b, c = struct.unpack_from("<4H", raw, i * 8)
        print("  [%4d] op=0x%04X args=%5d %5d %5d" % (i, op, a, b, c))
    # 统计 opcode 分布
    raw2 = rd(ptr, min(n, 40000) * 8)
    cnt = {}
    for i in range(len(raw2) // 8):
        op = struct.unpack_from("<H", raw2, i * 8)[0]
        cnt[op] = cnt.get(op, 0) + 1
    top = sorted(cnt.items(), key=lambda kv: -kv[1])[:14]
    print("\nopcode 频次 top:", ["0x%04X:%d" % t for t in top])
    print("含 0x0007(战斗) 条数 =", cnt.get(0x0007, 0))
else:
    print("!! 地址推导可疑")
