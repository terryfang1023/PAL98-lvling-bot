# -*- coding: utf-8 -*-
"""test_yj2.py — 纯 Python YJ_2 解压 (tools/yj2.py) 回归测试。

覆盖:
  ① 常数表: 长度 / 取值范围 / **与 SDLPAL yj2dll.c 源码逐值一致** (有源码时);
  ② 树结构: 641 节点布局 / 321 个"值->节点"映射 / 初始权重 / 内部节点孩子关系;
  ③ adjust (FGK 权值上浮): 根权重单调递增 / 不越界 / 反复调用稳定;
  ④ 错误处理: 太短 / 空数据 / 声明长度超限 / 随机字节 —— 一律抛 YJ2Error,
     **绝不死循环不超长**;
  ⑤ ★ 与真机对拍 (需要游戏资源): MAP.MKF 全部数据块解码长度 == 65536,
     且 (tiles & 0x2000) == map_walk.npy 对应层 —— 证明纯 Python 版与
     当年用 yj2.dll 生成的产物一致;
  ⑥ tools/build_data.py 已不再依赖任何外部动态库。

零副作用: 只读游戏资源; 不写任何文件 (对拍全部在内存里比)。
"""
import ctypes
import os
import struct
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(BASE, "tools")
sys.path.insert(0, BASE)
sys.path.insert(0, TOOLS)

import numpy as np                          # noqa: E402
import yj2 as Y2                            # noqa: E402

OK, BAD = [], []


def check(name, cond, extra=""):
    (OK if cond else BAD).append(name)
    print(("  ok  " if cond else "FAIL  ") + name
          + (" | %s" % (extra,) if extra else ""))


def find_game_dir():
    """仓库根 -> 上两级 (与 first_run.py 同一套候选), 只看有没有 MKF。"""
    cands = [BASE, os.path.dirname(BASE), os.path.dirname(os.path.dirname(BASE))]
    p = os.path.join(BASE, "game_dir.txt")
    if os.path.exists(p):
        try:
            t = open(p, encoding="utf-8", errors="replace").read().strip()
            if t:
                cands.insert(0, t)
        except Exception:
            pass
    for c in cands:
        if c and os.path.exists(os.path.join(c, "MAP.MKF")):
            return c
    return None


GAME = find_game_dir()

print("== 1. 常数表 ==")
check("YJ2_DATA1 长度 256", len(Y2.YJ2_DATA1) == 0x100, len(Y2.YJ2_DATA1))
check("YJ2_DATA2 长度 16", len(Y2.YJ2_DATA2) == 0x10, len(Y2.YJ2_DATA2))
check("data1 全部落在 6 位内 (<=0x3F)", max(Y2.YJ2_DATA1) <= 0x3F,
      max(Y2.YJ2_DATA1))
check("data1 用到 0x00 与 0x3F", min(Y2.YJ2_DATA1) == 0
      and max(Y2.YJ2_DATA1) == 0x3F)
check("data2 取值集合 == {3,4,5,6,7,8}", sorted(set(Y2.YJ2_DATA2))
      == [3, 4, 5, 6, 7, 8], sorted(set(Y2.YJ2_DATA2)))
check("LEAF_MAX=0x140 / ROOT=0x280 / N_NODE=641",
      (Y2.LEAF_MAX, Y2.ROOT, Y2.N_NODE) == (0x140, 0x280, 641))

src_c = None
if GAME:
    for p in (os.path.join(GAME, "_src", "sdlpal", "yj2dll.c"),
              os.path.join(os.path.dirname(BASE), "_src", "sdlpal", "yj2dll.c")):
        if os.path.exists(p):
            src_c = p
            break
if src_c:
    import re
    txt = open(src_c, encoding="utf-8", errors="replace").read()

    def grab(name):
        m = re.search(re.escape(name) + r"\s*\[[^\]]*\]\s*=\s*\{(.*?)\};", txt,
                      re.S)
        return [int(x, 16) for x in re.findall(r"0x([0-9a-fA-F]{2})",
                                               m.group(1))] if m else []
    check("★ data1 与 SDLPAL yj2dll.c 逐值一致",
          grab("yj2_data1") == list(Y2.YJ2_DATA1))
    check("★ data2 与 SDLPAL yj2dll.c 逐值一致",
          grab("yj2_data2") == list(Y2.YJ2_DATA2))
else:
    print("  (没找到 yj2dll.c 源码, 跳过常数源码比对)")

print("== 2. 树结构 ==")
t = Y2._Tree()
check("节点数组 641 项", all(len(getattr(t, k)) == 641
                            for k in ("weight", "value", "parent",
                                      "left", "right")))
check("值->节点映射 321 项", len(t.list) == 0x141)
check("初始 list[i] == i", all(t.list[i] == i for i in range(0x141)))
check("value[i] == i", all(t.value[i] == i for i in range(641)))
check("叶子权重全 1 (0x000..0x140)", all(t.weight[i] == 1
                                      for i in range(0x141)))
check("每个内部节点权重 == 左右孩子之和",
      all(t.weight[p] == t.weight[t.left[p]] + t.weight[t.right[p]]
          for p in range(0x141, 0x281)))
check("★ 根权重 == 321 (符号总数, 0x000..0x140)",
      t.weight[Y2.ROOT] == 0x141, t.weight[Y2.ROOT])
check("根的 parent 指向自己", t.parent[Y2.ROOT] == Y2.ROOT)
check("内部节点父指针/孩子成对",
      all(t.parent[i] == ptr and t.parent[i + 1] == ptr
          and t.left[ptr] == i and t.right[ptr] == i + 1
          for ptr, i in ((p, 2 * (p - 0x141)) for p in range(0x141, 0x281))))
check("根的孩子是最后两个内部节点",
      (t.left[Y2.ROOT], t.right[Y2.ROOT]) == (0x27E, 0x27F))

print("== 3. adjust (权值上浮) ==")
w0 = t.weight[Y2.ROOT]
t.adjust(0)
check("adjust 后根权重 +1", t.weight[Y2.ROOT] == w0 + 1,
      "%d -> %d" % (w0, t.weight[Y2.ROOT]))
ws = []
for i in range(500):
    t.adjust(i % 0x141)
    ws.append(t.weight[Y2.ROOT])
check("500 次 adjust 根权重单调不减", all(b >= a for a, b in zip(ws, ws[1:])))
check("500 次 adjust 根权重净增 500", t.weight[Y2.ROOT] == w0 + 1 + 500,
      t.weight[Y2.ROOT])
check("adjust 不抛异常且 list 映射仍然自洽",
      all(t.value[t.list[i]] == i for i in range(0x141)))

print("== 4. 错误处理 (必须抛错, 不能死循环) ==")


def raises(fn, *a, **kw):
    try:
        fn(*a, **kw)
    except Y2.YJ2Error:
        return True
    except Exception:
        return False
    return False


check("空输入 -> YJ2Error", raises(Y2.yj2_decompress, b""))
check("只有头没有数据 -> YJ2Error",
      raises(Y2.yj2_decompress, b"\x10\x00\x00\x00"))
check("声明长度超上限 -> YJ2Error",
      raises(Y2.yj2_decompress, b"\xff\xff\xff\x00" + b"\x00" * 64))
check("全零数据 + 声明 65536 -> YJ2Error",
      raises(Y2.yj2_decompress, struct.pack("<I", 65536) + b"\x00" * 64))
t0 = time.time()
rnd = struct.pack("<I", 65536) + bytes((i * 37 + 11) & 0xFF for i in range(4000))
ok = raises(Y2.yj2_decompress, rnd)
check("伪随机数据 -> 快速抛错 (不死循环)", ok and time.time() - t0 < 5.0,
      "%.2fs" % (time.time() - t0))
check("maxsize 参数生效 (声明 0x7FFFFF > 1000)",
      raises(Y2.yj2_decompress, b"\xff\xff\x7f\x00" + b"\x00" * 64,
             maxsize=1000))

print("== 5. ★ 与真机 map_walk.npy 对拍 ==")
if not GAME:
    print("  (没找到 MAP.MKF / 游戏资源, 跳过 —— 新机器上属于正常情况)")
else:
    mp_raw = os.path.join(GAME, "MAP.MKF")
    npy = os.path.join(BASE, "map_walk.npy")
    grids = np.load(npy) if os.path.exists(npy) else None
    from mkf import MKF                          # 复用项目自己的容器解析
    mp = MKF(mp_raw)
    print("  游戏目录 %s | MKF 块数 %d | 对拍基准 %s"
          % (GAME, mp.count, "map_walk.npy" if grids is not None else "(无, 只查长度)"))
    nblk = nok = nlen = 0
    mism = []
    bad_len = []
    wall0 = time.time()
    for m in range(1, mp.count):
        raw = mp.read_raw(m)
        if len(raw) < 8 or struct.unpack_from("<I", raw, 0)[0] != 65536:
            continue                                  # MKF 索引空洞
        nblk += 1
        try:
            b = Y2.yj2_decompress(raw)
        except Exception as ex:
            bad_len.append((m, "异常 %s" % ex))
            continue
        if len(b) != 65536:
            nlen += 1
            bad_len.append((m, len(b)))
            continue
        nok += 1
        if grids is not None and m < len(grids):
            tiles = np.frombuffer(b, dtype="<u4").reshape(128, 64, 2)
            if not np.array_equal((tiles & 0x2000) != 0, grids[m]):
                mism.append(m)
    dt = time.time() - wall0
    check("MAP.MKF 数据块全部解码成功 (%d 块, %.1fs)" % (nblk, dt),
          nok == nblk and nblk > 100, "成功 %d / 长度异常 %d" % (nok, nlen))
    check("每块解压后都是 65536 字节 (128x64x2 x4)", not bad_len, bad_len[:4])
    if grids is not None:
        check("★★ 全部 %d 张地图的墙体掩码与 map_walk.npy 完全一致" % nok,
              not mism, "不一致: %s" % (mism[:6] or "无"))
        frac = [float(grids[m].mean()) for m in (1, 2, 3, 50, 100)
                if m < len(grids)]
        check("墙体占比合理 (不是全墙/全空)",
              all(0.02 < f < 0.98 for f in frac),
              ["%.2f" % f for f in frac])
    else:
        print("  (没有 map_walk.npy, 只验长度; 生成一次后可做全量对拍)")

print("== 6. build_data.py 已无外部动态库依赖 ==")
src_bd = open(os.path.join(TOOLS, "build_data.py"), encoding="utf-8").read()
check("连 ctypes 都不再需要 (无任何动态库加载)",
      "ctypes" not in src_bd and "CDLL" not in src_bd)
check("不再有 find_yj2dll", "find_yj2dll" not in src_bd)
check("改用 tools/yj2.py", "from yj2 import" in src_bd)
check("用法说明仍是 <游戏目录> 一个参数",
      "build_data.py <游戏目录>" in src_bd)
check("文档里点名了 yj2.dll 已不需要",
      "yj2.dll" in open(os.path.join(TOOLS, "yj2.py"),
                        encoding="utf-8").read())

print("\n结果: %d ok / %d FAIL" % (len(OK), len(BAD)))
if BAD:
    for b in BAD:
        print("  FAIL:", b)
    sys.exit(1)
