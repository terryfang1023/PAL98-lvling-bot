# -*- coding: utf-8 -*-
"""probe_script2.py — 扫描定位 lprgScriptEntry (不依赖结构偏移推导)。

已知 GAMEDATA 基址 = 0x00C38080, 后面是 rgScene[300] (0x960) 与 rgObject[600]。
在 rgScene 之后、viewport(0x00C3C8FC) 之前的区间里, 找"指针 + 计数"组合:
    值 v 指向新堆分配区 (0x01000000~0x7FFFFFFF)
    v+4 处的 int 像条目数 (5000~500000)
    v 处第一个 word 是合理 opcode
"""
import ctypes
import ctypes.wintypes as wt
import struct

k32 = ctypes.windll.kernel32
u32 = ctypes.windll.user32
OPEN_OK = 0x0010 | 0x0400
GAMEDATA = 0x00C38080
SCAN_FROM = GAMEDATA + 0x968       # rgScene 之后
SCAN_TO = 0x00C3C8FC               # viewport

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
h = k32.OpenProcess(OPEN_OK, False, sorted(set(res))[0])
got = ctypes.c_size_t()


def rd(addr, size):
    buf = (ctypes.c_char * size)()
    if not k32.ReadProcessMemory(h, ctypes.c_void_p(addr), buf, size,
                                 ctypes.byref(got)):
        return None
    return buf.raw[:got.value]


size = SCAN_TO - SCAN_FROM
raw = rd(SCAN_FROM, size)
print("扫描 0x%08X..0x%08X (%d 字节) got=%s" % (SCAN_FROM, SCAN_TO, size, len(raw)))
cands = []
for off in range(0, len(raw) - 8, 4):
    v, n = struct.unpack_from("<II", raw, off)
    if not (0x01000000 < v < 0x7F000000):
        continue
    if not (5000 < n < 500000):
        continue
    probe = rd(v, 16)
    if not probe or len(probe) < 16:
        continue
    ops = struct.unpack_from("<4H", probe)
    # 前 4 个条目(op 每 8 字节 1 个) 的 op 应是小数值
    ops = [struct.unpack_from("<H", probe, i * 8)[0] for i in (0, 1)]
    ok = all(o <= 0x200 for o in ops)
    cands.append((SCAN_FROM + off, v, n, ops, ok))

for a, v, n, ops, ok in cands:
    print("  候选 @0x%08X: ptr=0x%08X n=%-7d first_ops=%s %s"
          % (a, v, n, ["0x%04X" % o for o in ops], "OK" if ok else "?"))

# 对所有 ptr 候选统计 opcode 分布, 找最像脚本表的
print("\n=== 对每个候选检查 opcode 分布 ===")
for a, v, n, ops, ok in cands:
    raw2 = rd(v, min(n, 30000) * 8)
    if not raw2:
        continue
    cnt = {}
    for i in range(len(raw2) // 8):
        op = struct.unpack_from("<H", raw2, i * 8)[0]
        cnt[op] = cnt.get(op, 0) + 1
    known = sum(c for o, c in cnt.items() if o <= 0x00FF or o == 0xFFFF)
    print("  @0x%08X n=%d  已知op占比=%d%%  0x0007x%d  0xFFFFx%d  top3=%s"
          % (a, n, 100 * known // max(1, sum(cnt.values())), cnt.get(0x0007, 0),
             cnt.get(0xFFFF, 0),
             ["0x%04X:%d" % t for t in sorted(cnt.items(), key=lambda kv: -kv[1])[:3]]))
