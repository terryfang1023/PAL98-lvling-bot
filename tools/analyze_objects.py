# -*- coding: utf-8 -*-
"""analyze_objects.py — 结合对象表 + 脚本表, 判定"怪点"到底是什么。

思路:
  1. 读 lprgEventObject / rgScene, 得到每场景的对象
  2. 读 lprgScriptEntry, 找出所有含 opcode 0x0007(START BATTLE) 的脚本入口
  3. 对每个 touch 对象, 看它的 trigger/auto 脚本入口附近有没有 0x0007
  4. 输出: 疑似固定战斗点 / 疑似装饰动画 / 疑似剧情NPC
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
SCENE_NOW = 0x00C3C95A
MAX_SCENES = 300
OBJ_SIZE = 32
OBJ_FIELDS = ["sVanishTime", "x", "y", "sLayer", "wTriggerScript",
              "wAutoScript", "sState", "wTriggerMode", "wSpriteNum",
              "nSpriteFrames", "wDirection", "wCurrentFrameNum",
              "nScriptIdleFrame", "wSpritePtrOffset", "nSpriteFramesAuto",
              "wScriptIdleFrameCountAuto"]


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


OBJ_TAB = u32at(PTR_ADDR)
N_OBJ = u32at(N_ADDR)
# 脚本表: 由 probe_script2 扫描确定 @0x00C3AAB8
SCRIPT_TAB_ADDR = 0x00C3AAB8
SC_TAB = u32at(SCRIPT_TAB_ADDR)
N_SC = u32at(SCRIPT_TAB_ADDR + 4)
print("对象表 0x%08X x%d   脚本表 0x%08X x%d"
      % (OBJ_TAB, N_OBJ, SC_TAB, N_SC))

objs_raw = rd(OBJ_TAB, N_OBJ * OBJ_SIZE)
objs = [dict(zip(OBJ_FIELDS, struct.unpack_from("<16H", objs_raw, i * OBJ_SIZE)))
        for i in range(N_OBJ)]
sc_raw = rd(SC_TAB, N_SC * 8)
SC = [struct.unpack_from("<4H", sc_raw, i * 8) for i in range(N_SC)]


def scan_script(entry, maxlen=80, depth=0):
    """从 entry 顺序扫描, 返回 (含0x07?, 出现的关键op集合)"""
    ops = []
    e = entry
    for _ in range(maxlen):
        if e <= 0 or e >= N_SC:
            break
        op = SC[e][0]
        ops.append(op)
        if op == 0x0000:      # 结束
            break
        if op == 0x0007:
            return True, ops
        if op == 0x0004 and depth < 2:      # 无条件跳转
            e = SC[e][1]
            continue
        e += 1
    return False, ops


# 所有 0x0007 条目索引
battle_entries = [i for i, s in enumerate(SC) if s[0] == 0x0007]
print("脚本表含 START_BATTLE(0x0007) 的条目数 = %d" % len(battle_entries))

# 场景表
tab = rd(SCENE_TAB, MAX_SCENES * 8)
scenes = [struct.unpack_from("<4H", tab, i * 8) for i in range(MAX_SCENES)]

per_scene = {}
for s in range(1, MAX_SCENES + 1):
    lo = scenes[s - 1][3] + 1
    hi = scenes[s][3] if s < MAX_SCENES else N_OBJ
    if hi >= lo and lo - 1 < N_OBJ:
        per_scene[s] = objs[lo - 1:min(hi, N_OBJ)]


def klass(o):
    if o["wTriggerMode"] < 4:
        return "anim" if o["wAutoScript"] else "static"
    fs, _ = scan_script(o["wTriggerScript"]) if o["wTriggerScript"] else (False, [])
    if fs:
        return "BATTLE"
    if o["wSpriteNum"] == 0:
        return "hiddenTouch"
    return "solidTouch"


print("\n=== 各场景触摸对象分类统计 (前 30 个有 BATTLE 的场景) ===")
rows = []
for s, lst in per_scene.items():
    if s > 296:
        continue
    k = [klass(o) for o in lst]
    nb = k.count("BATTLE")
    nt = sum(1 for x in k if x.startswith("solid") or x == "hiddenTouch")
    if nb:
        rows.append((s, nb, nt, len(lst)))
rows.sort(key=lambda r: -r[1])
for s, nb, nt, tot in rows[:30]:
    print("  场景 %3d: BATTLE=%2d  touch实体=%2d  总对象=%3d" % (s, nb, nt, tot))

print("\n含 BATTLE 脚本的场景数 =", len(rows))


def kof(px, py):
    return ((px - 2 * py) // 32, (px + 2 * py - 32) // 32)


print("\n=== 场景 157 / 168 / 77 的对象明细 ===")
for s in (157, 168, 77):
    if s not in per_scene:
        continue
    print("--- 场景 %d (%d 个对象) ---" % (s, len(per_scene[s])))
    for i, o in enumerate(per_scene[s]):
        c = klass(o)
        if c in ("static",):
            continue
        a, b = kof(o["x"], o["y"])
        print("  #%2d %-11s K(%4d,%4d) spr=%3d mode=%d state=%d trig=%5d auto=%5d"
              % (i, c, a, b, o["wSpriteNum"], o["wTriggerMode"], o["sState"],
                 o["wTriggerScript"], o["wAutoScript"]))
