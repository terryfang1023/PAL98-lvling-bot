# -*- coding: utf-8 -*-
"""从仙剑98游戏数据文件生成机器人所需的地图数据。

生成:
    map_walk.npy          全地图可走域 (bool [map][y][x][h], True=墙)
    map_walk2.npy         同上, 只含有效地图段 (场景体检用)
    gyt_scene_report.txt  场景->地图对照 + 全场景门清单 (寻路门禁用)

用法:
    python tools/build_data.py <游戏目录>

<游戏目录> 需包含:
    SSS.MKF / MAP.MKF / GOP.MKF   仙剑98柔情版游戏资源 (用户自备)
    yj2.dll                        YJ_2 解压动态库, 来自开源项目 SDLPAL
                                   (https://github.com/sdlpal/sdlpal, GPL),
                                   自行编译或从其发行包获取, 放到游戏目录
                                   或本 tools 目录均可。

约需 10~60 秒 (取决于磁盘速度)。
"""
import ctypes
import os
import re
import struct
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mkf import MKF                     # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 仓库根


def find_yj2dll(game):
    for p in (os.path.join(game, "yj2.dll"),
              os.path.join(HERE, "yj2.dll"),
              os.path.join(HERE, "tools", "yj2.dll"),
              os.path.join(game, "_src", "sdlpal", "yj2.dll")):
        if os.path.exists(p):
            return p
    return None


def main():
    game = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(HERE)
    for f in ("SSS.MKF", "MAP.MKF", "GOP.MKF"):
        if not os.path.exists(os.path.join(game, f)):
            print("错误: %s 下缺少 %s" % (game, f))
            sys.exit(1)
    dllpath = find_yj2dll(game)
    if not dllpath:
        print("错误: 找不到 yj2.dll (YJ_2 解压库)。\n"
              "请从开源项目 SDLPAL (github.com/sdlpal/sdlpal) 编译获取,\n"
              "放到游戏目录或本仓库目录。详见 README.md。")
        sys.exit(1)
    dll = ctypes.CDLL(dllpath)
    dll.yj2_decompress.argtypes = [ctypes.c_char_p, ctypes.c_char_p,
                                   ctypes.c_int]
    dll.yj2_decompress.restype = ctypes.c_int
    _buf = ctypes.create_string_buffer(65536 + 64)

    def yj2(raw):
        n = dll.yj2_decompress(raw, _buf, 65536 + 64)
        if n <= 0:
            raise ValueError("yj2 fail %d" % n)
        return _buf.raw[:n]

    os.chdir(HERE)
    t0 = time.time()

    # ============ 1. SSS.MKF: 场景表 / 事件对象 / 脚本 ============
    sss = MKF(os.path.join(game, "SSS.MKF"))
    obj_raw = sss.read_raw(0)          # EVENTOBJECT, 32B each
    scn_raw = sss.read_raw(1)          # SCENE, 8B each
    scr_raw = sss.read_raw(4)          # SCRIPTENTRY, 8B each
    N_OBJ, N_SCN, N_SCR = (len(x) // n for x, n in
                           ((obj_raw, 32), (scn_raw, 8), (scr_raw, 8)))
    print("场景 %d, 事件对象 %d, 脚本指令 %d" % (N_SCN, N_OBJ, N_SCR))

    scenes = np.frombuffer(scn_raw, dtype="<u4").reshape(N_SCN, 2)
    scene_mapnum = scenes[:, 0] & 0xFFFF

    objs = np.frombuffer(obj_raw, dtype="<u2").reshape(N_OBJ, 16)
    obj_x, obj_y = objs[:, 1], objs[:, 2]
    obj_trig, obj_mode = objs[:, 4], objs[:, 7]

    scripts = np.frombuffer(scr_raw, dtype="<u2").reshape(N_SCR, 4)

    def scan_script(entry, max_steps=80):
        """从 1-based 入口线性扫描, 收集 0x0059 (切换场景) 的目标。"""
        out, e = [], entry - 1
        for _ in range(max_steps):
            if e < 0 or e >= N_SCR:
                break
            op = scripts[e, 0]
            if op == 0x0059:
                out.append(int(scripts[e, 1]))
            elif op == 0x0055:                      # 无条件跳转
                ne = int(scripts[e, 1]) - 1
                if 0 <= ne < N_SCR and ne > e:
                    e = ne
                    continue
                break
            e += 1
        return out

    # ============ 2. 门清单 ============
    doors = []                    # (scene, obj_idx, x, y, mode, dests)
    for n in range(1, N_SCN):
        lo = int(scenes[n - 1, 1] >> 16)
        hi = int(scenes[n, 1] >> 16) if n < N_SCN else N_OBJ
        for i in range(lo, min(hi, N_OBJ)):
            if obj_trig[i] == 0:
                continue
            dests = scan_script(int(obj_trig[i]))
            if dests:
                doors.append((n, i, int(obj_x[i]), int(obj_y[i]),
                              int(obj_mode[i]), dests))
    print("含换图脚本的触发对象: %d 个" % len(doors))

    # ============ 3. 全地图可走域 (0x2000 = 墙) ============
    mp = MKF(os.path.join(game, "MAP.MKF"))
    gop = MKF(os.path.join(game, "GOP.MKF"))
    nmaps = min(mp.count, gop.count)
    blocked = np.zeros((nmaps, 128, 64, 2), dtype=bool)
    ok = 0
    for mnum in range(1, nmaps):
        raw = mp.read_raw(mnum)
        if len(raw) < 8 or struct.unpack_from("<I", raw, 0)[0] != 65536:
            continue                                # MKF 索引空洞
        try:
            tiles = np.frombuffer(yj2(raw),
                                  dtype="<u4").reshape(128, 64, 2)
            blocked[mnum] = (tiles & 0x2000) != 0
            ok += 1
        except Exception as ex:
            print("地图 %d 解压失败: %s" % (mnum, ex))
    np.save("map_walk.npy", blocked)
    nreal = min(226, nmaps - 1)                     # 有效地图段 (游戏惯例)
    np.save("map_walk2.npy", blocked[:nreal + 1])
    print("已解码 %d/%d 张地图 (%.1fs) -> map_walk.npy / map_walk2.npy"
          % (ok, nmaps - 1, time.time() - t0))

    # ============ 4. 报告 (格式与机器人解析器兼容) ============
    with open("gyt_scene_report.txt", "w", encoding="utf-8") as f:
        f.write("=== 场景->地图对照 (由 tools/build_data.py 生成) ===\n")
        for n in range(1, N_SCN):
            mnum = int(scene_mapnum[n - 1])
            if mnum > 0:
                f.write("场景%3d  地图%3d\n" % (n, mnum))
        f.write("\n=== 门清单(换图对象) ===\n")
        for (n, i, x, y, mode, dests) in doors:
            f.write("场景%3d 对象%4d px(%5d,%5d) mode=%d state=0 -> 场景%s\n"
                    % (n, i, x, y, mode, dests))
    print("报告已写 gyt_scene_report.txt (%.1fs 总耗时)" % (time.time() - t0))
    print("完成! 现在可以运行: python bot_gui.py")


if __name__ == "__main__":
    main()
