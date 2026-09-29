# -*- coding: utf-8 -*-
"""exp_encounter.py — 判定「遇敌机制」的决定性实验。

问题: 野外遇敌是"固定怪点被踩到触发"还是"按步数随机触发"?
方法: 在**同一格** up/down 往返走 N 步。
   - 若同一格反复踩也会进战斗  => 存在随机遇敌(与位置无关)
   - 若 300 步同格往返 0 战斗   => 战斗必须去特定点触发(固定怪点)

安全性: 不移动位置(原地往返), 只按方向键; 检测到战斗立刻停止并冲洗按键。
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import pal_bot as PB            # noqa: E402
import pal_bot_mem as MB        # noqa: E402

ROUNDS = int(sys.argv[1]) if len(sys.argv) > 1 else 150


def main():
    hwnd = PB.find_game_window()[0]
    g = PB.Game(hwnd)
    mem = MB.Mem()
    st0 = mem.state()
    print("起始状态:", st0, flush=True)
    if st0 is None:
        print("读内存失败")
        return 1

    Ks = []
    battles = []
    t0 = time.time()
    for i in range(ROUNDS):
        d = "up" if i % 2 == 0 else "down"
        g.key(d)
        time.sleep(0.20)                 # 跨步节奏锁 (实测 0.20s 才不被吞键)
        st = mem.state()
        if st:
            Ks.append((st["scene"], st["K"]))
        # 每 5 步(10 次按键)看一眼是否进战斗
        if i % 5 == 4:
            a = g.shot()
            if PB.is_battle(a):
                battles.append((i + 1, st["K"] if st else None))
                print("!!! 第 %d 步(约 %d 格位移)检测到战斗, K=%s"
                      % (i + 1, i + 1, st["K"] if st else None), flush=True)
                break
            if PB.menu_warm(a) > 0.25:
                print("   (第 %d 步出现对话框/菜单特征 %.2f)"
                      % (i + 1, PB.menu_warm(a)), flush=True)

    g.flush_keys()
    time.sleep(0.1)
    g.flush_keys()

    el = time.time() - t0
    sc = set(s for s, _ in Ks)
    kk = [tuple(k) for _, k in Ks]
    uniq = sorted(set(kk))
    print("\n===== 实验结论 =====", flush=True)
    print("按键 %d 次, 耗时 %.1fs, 场景=%s" % (ROUNDS, el, sc), flush=True)
    print("经过的不同格位: %d 个 -> %s" % (len(uniq), uniq[:12]), flush=True)
    print("检测到战斗: %d 次 %s" % (len(battles), battles), flush=True)
    if len(uniq) <= 2 and len(kk) > 20:
        print("=> 确实在原地往返(同一格反复踩了 %d 次)" % len(kk), flush=True)
        if not battles:
            print("=> 【固定点触发】同一格反复踩 %d 次 0 战斗，"
                  "说明遇敌必须去特定格/怪点，不存在按步数随机遇敌。" % len(kk),
                  flush=True)
        else:
            print("=> 【存在随机遇敌】同一格反复踩也触发了战斗。", flush=True)
    else:
        print("=> 位移过多(%d 个格位)，实验结果不干净，需要重做。" % len(uniq),
              flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
