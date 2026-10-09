# -*- coding: utf-8 -*-
"""item_scan.py — 物品栏只读扫描器 (零副作用: 只 ReadProcessMemory, 不写任何东西)。

用途:
  1. 验证 rgInventory 假设地址 (2026-10-02, 按 sdlpal GLOBALVARS 布局从真机锚点
     viewport@0x00C3C8FC 推算 rgInventory @0x00C3CC3C, 偏移 0x340):
        python tools/item_scan.py dump
     应看到一串 (物品ID, 数量, 使用中) 三元组, ID/数量都合理即布局正确。
  2. 定位某物品的槽位 (真机样本反查, 勿推算):
        python tools/item_scan.py watch 90
     间隔 90 秒拍两次快照, 打印数量变化的条目 —— 正在刷的物品掉落时 nAmount+1,
     据此锁定 物品ID 和槽位。
  3. 按物品 ID 反查:  python tools/item_scan.py dump --only 0x???

声明: 本工具不写进程内存、不写任何运行目录文件; 截图/输出只到 stdout。
      机器人运行中可安全使用 (OpenProcess 仅 PROCESS_VM_READ)。
"""
import ctypes
import ctypes.wintypes as wt
import struct
import sys
import time

# --- 与 pal_bot_mem.py 相同的锚点体系 (勿改动; 全部为真机实测标定) ---
VIEWPORT_ADDR = 0x00C3C8FC
INV_DELTA = 0x340                       # rgInventory - viewport (源码布局算出, dump 验证)
INV_ADDR = VIEWPORT_ADDR + INV_DELTA    # 0x00C3CC3C
MAX_INVENTORY = 256
ENTRY = struct.Struct("<HHH")           # wItem, nAmount, nAmountInUse

OPEN_OK = 0x0010                        # PROCESS_VM_READ (只读!)
k32 = ctypes.windll.kernel32
u32 = ctypes.windll.user32


def find_pid():
    """按窗口标题找游戏进程 (排除自家 GUI: 标题不含 'Pal' 的都不要)。"""
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


def read_inventory(h):
    """读整个物品栏, 返回 [(idx, wItem, nAmount, nAmountInUse)] (只列非空)。"""
    size = ENTRY.size * MAX_INVENTORY
    buf = (ctypes.c_char * size)()
    got = ctypes.c_size_t()
    if not k32.ReadProcessMemory(h, ctypes.c_void_p(INV_ADDR),
                                 buf, size, ctypes.byref(got)):
        err = k32.GetLastError()
        raise SystemExit("READ_FAIL @0x%08X (GetLastError=%d)" % (INV_ADDR, err))
    raw = buf.raw[:got.value]
    out = []
    for i in range(got.value // ENTRY.size):
        w, n, u = ENTRY.unpack_from(raw, i * ENTRY.size)
        if w or n:
            out.append((i, w, n, u))
    return out


def snapshot(h):
    """完整 256 条原始字节 (含空槽), 供 watch 差分。"""
    size = ENTRY.size * MAX_INVENTORY
    buf = (ctypes.c_char * size)()
    got = ctypes.c_size_t()
    if not k32.ReadProcessMemory(h, ctypes.c_void_p(INV_ADDR),
                                 buf, size, ctypes.byref(got)):
        raise SystemExit("READ_FAIL (GetLastError=%d)" % k32.GetLastError())
    return buf.raw[:got.value]


def fmt_entries(rows, only=None):
    lines = []
    for (i, w, n, u) in rows:
        if only is not None and w != only:
            continue
        lines.append("  [%3d] 物品ID=0x%03X  数量=%d  使用中=%d"
                     % (i, w, n, u))
    return lines


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "dump"
    pids = find_pid()
    if not pids:
        print("未找到游戏窗口 (标题含 Pal) —— 游戏没开?")
        return 1
    pid = pids[0]
    h = k32.OpenProcess(OPEN_OK, False, pid)
    if not h:
        print("OpenProcess 失败 (pid=%d, err=%d)" % (pid, k32.GetLastError()))
        return 1
    try:
        print("游戏 pid=%d, rgInventory @0x%08X (假设: viewport+0x%X)"
              % (pid, INV_ADDR, INV_DELTA))
        if cmd == "dump":
            only = None
            if "--only" in argv:
                only = int(argv[argv.index("--only") + 1], 0)
            rows = read_inventory(h)
            lines = fmt_entries(rows, only)
            print("非空条目 %d 个:" % len(rows))
            print("\n".join(lines) if lines else "  (空)")
        elif cmd == "watch":
            wait = float(argv[2]) if len(argv) > 2 else 90.0
            print("快照 A ...")
            a = snapshot(h)
            print("等待 %.0f 秒 (去游戏里让数量变化: 用掉一个或打一个) ..." % wait)
            time.sleep(wait)
            print("快照 B ...")
            b = snapshot(h)
            diff = []
            for i in range(len(a) // ENTRY.size):
                wa, na, ua = ENTRY.unpack_from(a, i * ENTRY.size)
                wb, nb, ub = ENTRY.unpack_from(b, i * ENTRY.size)
                if (wa, na, ua) != (wb, nb, ub):
                    diff.append("  [%3d] 物品ID=0x%03X  %d -> %d (使用中 %d -> %d)"
                                % (i, wb, na, nb, ua, ub))
            if diff:
                print("变化的条目:")
                print("\n".join(diff))
            else:
                print("(无变化 —— 间隔内数量没变, 拉长间隔再试)")
        else:
            print("用法: python tools/item_scan.py [dump|watch 秒数] [--only ID]")
            return 2
        return 0
    finally:
        k32.CloseHandle(h)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
