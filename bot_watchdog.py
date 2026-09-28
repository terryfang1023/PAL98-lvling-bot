# -*- coding: utf-8 -*-
"""bot_watchdog.py -- 看门狗: bot 异常死亡(命令行窗口被关/崩溃)时补发全键抬起。

背景 (2026-09-26 实测):
    命令行窗口被关 -> bot 进程直接死, atexit 不执行 -> 没人 flush_keys ->
    方向键卡在按下状态 -> 队伍在地图上自己一直走, 跑丢甚至穿门。

用法 (由 pal_bot_mem4.py 以 DETACHED_PROCESS 方式拉起, 也可手动):
    python bot_watchdog.py <bot_pid> <game_hwnd>

行为:
    每 2 秒轮询 bot pid; bot 存活 -> 继续守;
    bot 死亡 -> 对游戏窗口 flush_keys x3 后退出;
    游戏窗口关闭 / 守满 24 小时 -> 直接退出。
"""
import os
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

MAX_GUARD = 24 * 3600      # 最长守护 24 小时, 防僵尸看门狗
POLL = 2.0                 # 轮询间隔


def main():
    import psutil

    bot_pid = int(sys.argv[1])
    hwnd = int(sys.argv[2])
    deadline = time.time() + MAX_GUARD

    while time.time() < deadline:
        time.sleep(POLL)
        try:
            proc = psutil.Process(bot_pid)
            if proc.status() == psutil.STATUS_ZOMBIE:
                raise psutil.NoSuchProcess(bot_pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            break                      # bot 没了 -> 去清键
        except Exception:
            continue                   # 查询失败 -> 下轮再试
        try:
            import win32gui
            if not win32gui.IsWindow(hwnd):
                return                 # 游戏都关了, 没什么可守的
        except Exception:
            pass

    # bot 已死 (或守护到期) -> 冲洗游戏窗口按键
    try:
        import win32gui
        if not win32gui.IsWindow(hwnd):
            return
        from pal_bot import Game
        g = Game(hwnd)
        for _ in range(3):
            g.flush_keys()
    except Exception:
        pass


if __name__ == "__main__":
    main()
