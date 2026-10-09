# -*- coding: utf-8 -*-
"""role_scan.py — 人物等级/经验只读探针 (标定/验证用, 零副作用)。

用法:
    python tools/role_scan.py          # 打印当前在队成员 等级/经验/体力

只读进程内存 (PROCESS_VM_READ), 不按键、不写任何文件 —— 机器人正在跑也能用。
锚点标定记录见 bot_itemmon.py 头部注释 (2026-10-02 真机标定):
    rgwName   = viewport - 0x1E00   (名字词条 ID 数组, 定位锚)
    rgwLevel  = rgwName + 0x24
    Exp.rgPrimaryExp = viewport + 0x80   (8B/条: wExp,wReserved,wLevel,wCount)
    rgParty   = viewport + 0x0C (10B/人), wMaxPartyMemberIndex = viewport + 0x0A
交叉验证: Exp.rgPrimaryExp 的 wLevel 与 rgwLevel 独立一致 -> 地址可信。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot_itemmon as IM                    # noqa: E402


def main():
    names = IM.load_names()
    r = IM.RoleReader()
    d = r.read(names)
    if d is None:
        print("读不到人物数据 (游戏没开? 标题含 'Pal' 的窗口存在?)")
        return 1
    print("在队成员 %d 人 (按游戏内顺序):" % len(d["members"]))
    for m in d["members"]:
        print("  %-6s 等级 Lv%-3d 经验 %-6d 体力 %d/%d  (角色%d, 名字词条 0x%02X)"
              % (m["name"], m["level"], m["exp"], m["hp"], m["maxhp"],
                 m["role"], m["name_id"]))
    lv = [m["level"] for m in d["members"]]
    if any(not (1 <= v <= 999) for v in lv):
        print("⚠ 有等级读数超出常理 —— 若换过 exe, 请重新标定锚点")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
