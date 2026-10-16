# -*- coding: utf-8 -*-
"""chest_probe.py — 只读探针: 把「scene_info 快照 / 开箱台账 / 游戏当前内存」
三方摆在一起, 回答一个问题 —— 这个箱子到底还开不开得出来?

用途: 机器人现在的「空箱跳过」是 **读内存** 判的 (pal_bot_mem5.chest_verdict):
   · 开过的箱子: 脚本最后一条 `0x0002 STOP_ENTRY -> 已空提示脚本` 会被引擎
     回写进对象的 wTriggerScript (play.c:153), 从此走不到 0x001F 加物品;
   · 隐形箱 sState<=0: play.c:467/:107 根本不触发。
本工具就是这套判据的"体检报告", 顺手查台账里有没有记错的「空箱」。

判据来源 (SDLPAL 源码 D:/Fang/Game/PAL_98/_src/sdlpal):
  script.c:1716  0x0049 SET_OBJECT_STATE => pCurrent->sState = operand[1]
  script.c:1798  0x0052 HIDE_FOR_A_WHILE => sState *= -1 (负数=暂时隐身, 会自己好)
  play.c:107    触摸触发要求 sState > 0
  play.c:467    搜索触发(站格上按空格)要求 sState > 0 且 0 < wTriggerMode < 4
  play.c:153    p->wTriggerScript = PAL_RunTriggerScript(...) ← 0x0002 回写

本工具**只读**: OpenProcess 只要 PROCESS_VM_READ|PROCESS_QUERY_INFORMATION,
不写游戏内存、不按键、不碰 mem_stop.flag 等任何运行标志。

用法:
  python tools/chest_probe.py             # 台账对账 + 异常明细 (推荐)
  python tools/chest_probe.py --scene 221 # 看某场景快照里所有箱子
  python tools/chest_probe.py --all       # 1058 个箱子全列 (很长)
"""
import argparse
import ctypes
import ctypes.wintypes as wt
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import game_addr                          # noqa: E402  地址换算 (GUI 也会跑本工具)

k32 = ctypes.windll.kernel32
u32 = ctypes.windll.user32
OPEN_OK = 0x0010 | 0x0400                 # QUERY_INFORMATION | VM_READ (只要读)

# ★ 下面 5 个是**标定值**; sdlpal.exe 开了 ASLR 且映像基址"每次开机随机一次",
#   换机器 / 重启后基址会变 -> 直接拿标定值读进程会读到垃圾。shift_module
#   把它们换成本次运行的真实地址 (详情见 game_addr 模块头)。
PTR_ADDR = 0x00C38080                     # lprgEventObject
N_ADDR = 0x00C38084                       # nEventObject
SCENE_TAB = 0x00C38088                    # rgScene[300]
SCENE_NOW = 0x00C3C95A                    # wNumScene
SCRIPT_TAB = 0x00C3AAB8                   # lprgScriptEntry 指针槽
game_addr.shift_module(globals(), "PTR_ADDR", "N_ADDR", "SCENE_TAB",
                       "SCENE_NOW", "SCRIPT_TAB")
MAX_SCENES = 300
OBJ_SIZE = 32
OBJ_FIELDS = ["sVanishTime", "x", "y", "sLayer", "wTriggerScript",
              "wAutoScript", "sState", "wTriggerMode", "wSpriteNum",
              "nSpriteFrames", "wDirection", "wCurrentFrameNum",
              "nScriptIdleFrame", "wSpritePtrOffset", "nSpriteFramesAuto",
              "wScriptIdleFrameCountAuto"]

OP_JUMP, OP_STOP_NEXT, OP_STOP_ENTRY = 0x0003, 0x0001, 0x0002
OP_CALL, OP_RANDJUMP, OP_GOTOIFNO, OP_ADD_ITEM = 0x0004, 0x0006, 0x000A, 0x001F

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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
    """内存像素坐标 -> 机器人 K 空间 (build_scene_info 同款)。"""
    return ((px - 2 * py) // 32, (px + 2 * py - 32) // 32)


def signed(v):
    return v - 65536 if v >= 32768 else v


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def script_adds_item(rd, ptr, n_sc, entry, maxnodes=400):
    """触发脚本还走不走得到 0x001F 加物品。True/False/None(读不到)。"""
    if not entry:
        return False
    hit = False
    stack, seen, cnt = [entry], set(), 0
    while stack:
        e = stack.pop()
        while True:
            if e <= 0 or e >= n_sc or e in seen:
                break
            seen.add(e)
            cnt += 1
            if cnt > maxnodes:
                return hit
            d = rd(ptr + e * 8, 8)
            if not d or len(d) < 8:
                return None
            op, a, _b, _c = struct.unpack("<4H", d)
            if op == OP_ADD_ITEM:
                return True
            if op == 0x0000 or op == OP_STOP_ENTRY:
                break
            if op == OP_JUMP:
                e = a
                continue
            if op == OP_STOP_NEXT:
                e += 1
                continue
            if op in (OP_CALL, OP_RANDJUMP, OP_GOTOIFNO):
                stack.append(a)
                e += 1
                continue
            e += 1
    return hit


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", type=int, default=None)
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()

    pids = find_pid()
    if not pids:
        raise SystemExit("找不到游戏窗口(标题含 'Pal')，请先启动游戏")
    h = k32.OpenProcess(OPEN_OK, False, pids[0])
    if not h:
        raise SystemExit("OpenProcess 失败")
    r = Reader(h)

    obj_ptr = r.u32(PTR_ADDR)
    n_obj = r.u32(N_ADDR)
    cur_scene = r.u16(SCENE_NOW)
    if not obj_ptr or not n_obj or n_obj > 6000:
        raise SystemExit("对象表指针异常: 0x%X x%s" % (obj_ptr or 0, n_obj))
    raw = r.rd(obj_ptr, n_obj * OBJ_SIZE)
    objs = [dict(zip(OBJ_FIELDS, struct.unpack_from("<16H", raw, i * OBJ_SIZE)))
            for i in range(n_obj)]
    raw_rt = r.rd(SCENE_TAB, MAX_SCENES * 8)
    scenes = [struct.unpack_from("<4H", raw_rt, i * 8)
              for i in range(MAX_SCENES)]
    sptr = r.u32(SCRIPT_TAB)
    n_sc = r.u32(SCRIPT_TAB + 4)
    print("对象表 0x%08X x%d | 脚本表 0x%08X x%s | 当前场景 %s"
          % (obj_ptr, n_obj, sptr or 0, n_sc, cur_scene))

    # 场景 -> {K: [对象...]}  (对象 id 区间由 rgScene 第 4 个 WORD 给出)
    live = {}
    for s in range(1, MAX_SCENES + 1):
        lo = scenes[s - 1][3] + 1
        hi = scenes[s][3] if s < MAX_SCENES else n_obj
        d = {}
        for i in range(lo, min(hi, n_obj) + 1):
            o = objs[i - 1]
            d.setdefault(kof(o["x"], o["y"]), []).append(o)
        live[s] = d

    snap = load_json(os.path.join(BASE, "scene_info.json"), {})
    log = load_json(os.path.join(BASE, "chest_log.json"),
                    load_json(os.path.join(BASE, "pal98-bot", "chest_log.json"),
                              {})).get("chests", {})

    def probe(sc, K, c):
        """c 可为 None (台账有、快照没有)。返回 (verdict, 原因, 内存摘录)。"""
        at = live.get(sc, {}).get(K, [])
        if not at:
            return "unknown", "该格没有对象", "—"
        cand = ([o for o in at if o["wSpriteNum"] == c["spr"]
                 and o["wTriggerMode"] == c["mode"]] if c else at)
        if not cand:
            return "unknown", "身份对不上", " ".join(
                "st=%d spr=%d" % (signed(o["sState"]), o["wSpriteNum"])
                for o in at)
        why, dead, alive = [], 0, 0
        for o in cand:
            st, tr = signed(o["sState"]), o["wTriggerScript"]
            if st <= 0:
                dead += 1
                why.append("状态%d不可触发" % st)
            elif script_adds_item(r.rd, sptr, n_sc, tr) is True:
                alive += 1
                why.append("还能开")
            else:
                dead += 1
                why.append("脚本已空")
        v = "dead" if not alive else ("live" if not dead else "unknown")
        txt = " ".join("st=%d spr=%-3d trig=%-6d" % (signed(o["sState"]),
                                                     o["wSpriteNum"],
                                                     o["wTriggerScript"])
                       for o in cand)
        return v, "/".join(why[:2]), txt

    rows = []
    for sc, kv in sorted(log.items(), key=lambda x: int(x[0])):
        for k, e in sorted(kv.items()):
            a, b = (int(x) for x in k.split(","))
            c = None
            for x in (snap.get(sc) or {}).get("chests", []):
                if x["K"][0] == a and x["K"][1] == b:
                    c = x
                    break
            rows.append(("台账", int(sc), (a, b), e, c))
    for sc, v in sorted(snap.items(), key=lambda x: int(x[0])):
        for x in v.get("chests", []):
            K = (x["K"][0], x["K"][1])
            if not (log.get(sc) or {}).get("%d,%d" % K):
                rows.append(("快照", int(sc), K, None, x))

    if args.scene is not None:
        rows = [t for t in rows if t[1] == args.scene]
    elif not args.all:
        # 默认: 只看台账有记录的 (对账用)
        rows = [t for t in rows if t[0] == "台账"]

    stat = {}
    print("%-5s %-11s %-7s %-6s %-8s %-18s %s"
          % ("场景", "K", "来源", "台账", "内存判定", "原因", "内存实际"))
    for src, sc, K, e, c in rows:
        v, why, txt = probe(sc, K, c)
        tag = {"dead": "已空/白跑", "live": "还能开", "unknown": "拿不准"}[v]
        ok = "—" if e is None else ("已开" if e.get("ok") else "空")
        key = (src, ok, v)
        stat[key] = stat.get(key, 0) + 1
        print("%-5d %-11s %-7s %-6s %-8s %-18s %s"
              % (sc, "(%d,%d)" % K, src, ok, tag, why, txt))

    if stat:
        print()
        print("==== 汇总 (来源, 台账结论, 内存判定) -> 条数 ====")
        for k in sorted(stat, key=lambda x: (x[0], str(x[1]), x[2])):
            print("   %-5s 台账=%-4s 内存=%-8s %d 条" % (k[0], k[1],
                                                        k[2], stat[k]))
        fix = [k for k in stat if k[1] == "空" and k[2] == "live"]
        blind = [k for k in stat if k[1] == "—" and k[2] == "dead"]
        if fix:
            print("* 台账把这些箱子记成「空」, 但内存里**物品还在** -> 是当初走位/"
                  "丢键失败被误记; 机器人 v18.9 会自动划掉重开")
        if blind:
            print("* 没台账记录但内存已空 -> 早就开过的箱子, v18.9 起直接跳过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
