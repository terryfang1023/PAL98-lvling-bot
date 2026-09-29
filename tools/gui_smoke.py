# -*- coding: utf-8 -*-
"""GUI 离线自检 —— 隐藏窗口走 BotGUI 的真实「开始」路径, 但把子进程换成假的,
所以**不操作游戏、秒级完成**。检查三件事:

  ① 参数拼装: 捕获 Popen 收到的 argv, 逐项对照 pal_bot_mem5.py 的 argv 语义;
     并验证勾选/取消「要素巡回」分别对应空串与 "巡逻" (第 8 参数)。
  ② 状态栏解析: 注入 bot 真实格式的统计行, 确认胜场与要素统计能解析出来。
  ③ 停止路径: 点「停止」是否真的写出 mem_stop.flag。

用法:  python tools/gui_smoke.py        (退出码 0 = 全过)
"""
import os
import sys
import time
import types
import io

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
os.chdir(BASE)

import tkinter as tk                        # noqa: E402
import bot_gui as G                         # noqa: E402

OK, BAD = [], []


def check(name, cond, extra=""):
    (OK if cond else BAD).append(name)
    print("  %s %s%s" % ("OK  " if cond else "FAIL", name,
                         ("  <%s>" % extra) if extra else ""))


class FakeProc:
    """假装在跑的子进程: stdout 立刻 EOF, poll() 恒为 None。"""

    def __init__(self, args):
        self.args = args
        self.stdout = io.StringIO()
        self.returncode = None

    def poll(self):
        return None

    def kill(self):
        self.returncode = -9

    def terminate(self):
        self.returncode = -15

    def wait(self, timeout=None):
        return 0


def main():
    root = tk.Tk()
    root.withdraw()
    app = G.BotGUI(root)

    cap = {}
    real_subprocess = G.subprocess
    # ★ 只替换 bot_gui 模块看到的那个名字, 不动全局 subprocess 模块
    G.subprocess = types.SimpleNamespace(
        Popen=lambda args, **kw: (cap.__setitem__("args", args),
                                  cap.__setitem__("kw", kw),
                                  FakeProc(args))[-1],
        PIPE=real_subprocess.PIPE,
        STDOUT=real_subprocess.STDOUT,
        CREATE_NO_WINDOW=getattr(real_subprocess, "CREATE_NO_WINDOW", 0))

    try:
        print("① 参数拼装 (要素巡回 = 勾选)")
        app.v_maxb.set("0")
        app.v_mins.set("1")
        app.v_stall.set("20")
        app.v_elem.set(True)
        app.start()
        a = cap.get("args") or []
        check("Popen 被调用", bool(a), " ".join(a[3:]))
        # GUI 传 [exe, -u, script, 8 个业务参数]; python 侧 sys.argv[1:] == a[3:]
        sa = list(a[3:]) + [""] * (8 - len(a[3:]))
        check("脚本名是 pal_bot_mem5.py", len(a) > 2 and a[2] == "pal_bot_mem5.py",
              a[2] if len(a) > 2 else "")
        check("承接口 + 静默管道",
              cap.get("kw", {}).get("stdout") is real_subprocess.PIPE,
              str(cap.get("kw", {}).get("stdout")))
        check("第 2 参数(秒) = 60", sa[1] == "60", "实际 %r" % sa[1])
        check("半径起/上限可转整数", sa[2].isdigit() and sa[3].isdigit(),
              "%s / %s" % (sa[2], sa[3]))
        check("方向偏置合法", sa[4] in G.BIAS_CHOICES, "%r" % sa[4])
        check("穿门参数合法", sa[5] in ("", "穿门"), "%r" % sa[5])
        check("要素开 -> 第 8 参数为空", sa[7] == "", "实际 %r" % sa[7])
        check("要素开 -> elem_mode 未被关 (等价于 bot 侧不写 巡逻)", sa[7] != "巡逻")

        check("停止写出 mem_stop.flag",
              (app.stop() or True) and os.path.exists(G.STOP_FLAG))
        if os.path.exists(G.STOP_FLAG):
            os.remove(G.STOP_FLAG)

        print("② 参数拼装 (要素巡回 = 取消)")
        app.proc = None                      # 假装上一轮已退出
        app.v_elem.set(False)
        cap.clear()
        app.start()
        a2 = cap.get("args") or []
        sa2 = list(a2[3:]) + [""] * (8 - len(a2[3:]))
        check("要素关 -> 第 8 参数为 巡逻", sa2[7] == "巡逻", "实际 %r" % sa2[7])

        print("③ 状态栏解析")
        if os.path.exists(G.STOP_FLAG):
            os.remove(G.STOP_FLAG)
        app.q.put("[战斗结束] #7 (胜)")
        app.q.put("[要素] 怪点命中 4 / 走到没打 0 / 开箱 1(试2) / 回退巡逻 0")
        for _ in range(6):
            root.update()
            time.sleep(0.05)
        check("胜场解析 = 7", app.wins == 7, "实际 %d" % app.wins)
        check("要素统计解析 = (4, 0, 1)", app.elem == (4, 0, 1), str(app.elem))
        check("状态栏文本含要素统计",
              "怪点命中" in app.v_status.get(), app.v_status.get())

        print("④ 无关行不误匹配")
        app.q.put("[要素] 清单载入: 136 个场景 / 怪点 1968 / 宝箱 1178")
        for _ in range(4):
            root.update()
            time.sleep(0.05)
        check("清单行不污染统计", app.elem == (4, 0, 1), str(app.elem))
    finally:
        G.subprocess = real_subprocess
        if os.path.exists(G.STOP_FLAG):
            os.remove(G.STOP_FLAG)
        try:
            root.destroy()
        except Exception:
            pass

    print("\n通过 %d 项, 失败 %d 项" % (len(OK), len(BAD)))
    if BAD:
        print("失败项:", ", ".join(BAD))
    print("GUI SMOKE", "OK" if not BAD else "FAIL")
    return 0 if not BAD else 1


if __name__ == "__main__":
    sys.exit(main())
