# -*- coding: utf-8 -*-
"""仙剑98 挂机机器人 — 图形界面 (tkinter, 零额外依赖)

用法:
    python bot_gui.py             # 打开界面
    python bot_gui.py --selftest  # 自检 (不开窗口, 供 CI/排障用)

界面即参数面板: 选好参数点"开始"即启动 pal_bot_mem4.py 子进程;
"停止"写入 mem_stop.flag (优雅停机, 清键退出), "强制停止"杀进程并冲洗按键。
"""
import os
import re
import sys
import queue
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext

BASE = os.path.dirname(os.path.abspath(__file__))

# 机器人启动必需的数据文件 (由 tools/build_data.py 从游戏数据生成)
REQUIRED_DATA = ["map_walk.npy", "gyt_scene_report.txt"]
OPTIONAL_DATA = ["map_walk2.npy"]          # scene_check.py 体检用
STOP_FLAG = os.path.join(BASE, "mem_stop.flag")

BIAS_CHOICES = ["", "上", "下", "左", "右", "右上", "右下", "左上", "左下"]


class BotGUI:

    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("仙剑98 挂机机器人 (SDLPAL)")
        root.minsize(860, 560)

        self.proc = None                 # bot 子进程
        self.q = queue.Queue()           # 子进程日志队列
        self.wins = 0                    # 解析出的胜场
        self.t_start = None

        self._build_params()
        self._build_buttons()
        self._build_log()
        self._build_status()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(250, self._poll)

    # ---------- 界面 ----------
    def _build_params(self):
        f = ttk.LabelFrame(self.root, text="运行参数")
        f.pack(fill="x", padx=10, pady=(10, 4))

        def entry(parent, label, width=8, init=""):
            ttk.Label(parent, text=label).pack(side="left", padx=(10, 2))
            var = tk.StringVar(value=init)
            e = ttk.Entry(parent, textvariable=var, width=width)
            e.pack(side="left")
            return var

        r1 = ttk.Frame(f); r1.pack(fill="x", pady=4)
        self.v_maxb = entry(r1, "胜场上限 (0=不限)", 8, "0")
        self.v_mins = entry(r1, "运行时长/分钟 (0=不限)", 10, "0")
        self.v_r0 = entry(r1, "巡逻半径起", 6, "28")
        self.v_rmax = entry(r1, "巡逻半径上限", 6, "90")

        r2 = ttk.Frame(f); r2.pack(fill="x", pady=(0, 6))
        ttk.Label(r2, text="方向偏置 (优先往该方向探索)").pack(
            side="left", padx=(10, 2))
        self.v_bias = tk.StringVar(value="")
        cb = ttk.Combobox(r2, textvariable=self.v_bias, width=8,
                          values=BIAS_CHOICES, state="readonly")
        cb.pack(side="left")
        self.v_open = tk.BooleanVar(value=False)
        ttk.Checkbutton(r2, text="穿门模式 (不避开传送阵/楼梯, 踩到就去下一层)",
                        variable=self.v_open).pack(side="left", padx=(16, 0))

        r3 = ttk.Frame(f); r3.pack(fill="x", pady=(0, 6))
        self.v_exe = entry(r3, "Python 解释器 (bot 子进程使用)", 46,
                           sys.executable)
        ttk.Label(r3, text="").pack(side="left")

    def _build_buttons(self):
        f = ttk.Frame(self.root)
        f.pack(fill="x", padx=10, pady=4)
        self.b_start = ttk.Button(f, text="▶ 开始", command=self.start)
        self.b_start.pack(side="left", padx=(0, 6))
        self.b_stop = ttk.Button(f, text="■ 停止 (优雅)", command=self.stop,
                                 state="disabled")
        self.b_stop.pack(side="left", padx=6)
        self.b_kill = ttk.Button(f, text="⛔ 强制停止", command=self.force_stop,
                                 state="disabled")
        self.b_kill.pack(side="left", padx=6)
        ttk.Button(f, text="场景体检", command=self.scene_check).pack(
            side="left", padx=6)
        ttk.Button(f, text="检测游戏窗口", command=self.detect_window).pack(
            side="left", padx=6)
        ttk.Button(f, text="清空日志", command=self.clear_log).pack(
            side="right", padx=6)

    def _build_log(self):
        f = ttk.LabelFrame(self.root, text="运行日志")
        f.pack(fill="both", expand=True, padx=10, pady=4)
        self.log = scrolledtext.ScrolledText(f, height=18, state="disabled",
                                             font=("Consolas", 9))
        self.log.pack(fill="both", expand=True)

    def _build_status(self):
        self.v_status = tk.StringVar(value="已停止")
        ttk.Label(self.root, textvariable=self.v_status, anchor="w",
                  relief="sunken").pack(fill="x", side="bottom")

    # ---------- 动作 ----------
    def _running(self):
        return self.proc is not None and self.proc.poll() is None

    def _err(self, title, msg):
        messagebox.showerror(title, msg, parent=self.root)

    def start(self):
        if self._running():
            self._err("提示", "机器人已在运行中。")
            return
        # 1. 数据文件检查
        missing = [n for n in REQUIRED_DATA
                   if not os.path.exists(os.path.join(BASE, n))]
        if missing:
            self._err("缺少地图数据",
                      "未找到: %s\n\n请先用游戏数据生成:\n"
                      "  python tools/build_data.py <游戏目录>\n"
                      "详见 README.md「数据文件生成」一节。" % ", ".join(missing))
            return
        # 2. 参数合法性
        try:
            maxb = max(0, int(self.v_maxb.get() or "0"))
            mins = max(0, int(self.v_mins.get() or "0"))
            r0 = int(self.v_r0.get() or "28")
            rmax = int(self.v_rmax.get() or "90")
        except ValueError:
            self._err("参数错误", "胜场/时长/半径必须是整数。")
            return
        exe = self.v_exe.get().strip() or sys.executable
        if not os.path.exists(exe):
            self._err("参数错误", "Python 解释器路径不存在:\n%s" % exe)
            return
        secs = mins * 60
        args = [exe, "-u", "pal_bot_mem4.py", str(maxb), str(secs),
                str(r0), str(rmax), self.v_bias.get(),
                "穿门" if self.v_open.get() else ""]
        # 3. 启动
        try:
            self.proc = subprocess.Popen(
                args, cwd=BASE,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW)
        except Exception as ex:
            self._err("启动失败", str(ex))
            return
        self.wins = 0
        self.t_start = time.time()
        threading.Thread(target=self._reader, daemon=True).start()
        self._set_buttons(True)
        self.log_line("[GUI] 已启动: %s" % " ".join(args[1:]))

    def stop(self):
        if not self._running():
            return
        with open(STOP_FLAG, "w") as f:
            f.write("")
        self.v_status.set("停止中… (等待 bot 清键退出)")

    def force_stop(self):
        if not self._running():
            return
        if not messagebox.askyesno(
                "强制停止",
                "直接杀死 bot 进程可能留下按住的方向键 (队伍自己走丢)。\n"
                "将自动冲洗游戏按键, 确认继续?", parent=self.root):
            return
        try:
            self.proc.terminate()
        except Exception:
            pass
        self._flush_game_keys()

    def scene_check(self):
        """运行 scene_check.py, 输出进日志。"""
        if not os.path.exists(os.path.join(BASE, "map_walk2.npy")):
            self._err("缺少数据", "场景体检需要 map_walk2.npy\n"
                      "请先运行: python tools/build_data.py <游戏目录>")
            return
        exe = self.v_exe.get().strip() or sys.executable
        try:
            r = subprocess.run([exe, "scene_check.py"], cwd=BASE,
                               capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=60)
            self.log_line("[体检] %s" % (r.stdout or r.stderr or "(无输出)"))
        except Exception as ex:
            self._err("体检失败", str(ex))

    def detect_window(self):
        try:
            sys.path.insert(0, BASE)
            from pal_bot import find_game_window
            h = find_game_window()
            if h:
                self.log_line("[GUI] 找到游戏窗口: %d 个 (hwnd=%s)"
                              % (len(h), h))
                messagebox.showinfo("检测游戏窗口",
                                    "找到 %d 个游戏窗口, 可以开始。" % len(h),
                                    parent=self.root)
            else:
                self.log_line("[GUI] 未找到游戏窗口! 请先启动游戏 (SDLPAL)")
                messagebox.showwarning(
                    "检测游戏窗口",
                    "未找到标题含 Pal 的游戏窗口。\n请先启动游戏再开始。",
                    parent=self.root)
        except Exception as ex:
            self._err("检测失败", str(ex))

    def clear_log(self):
        self.log.config(state="normal")
        self.log.delete("1.0", "end")
        self.log.config(state="disabled")

    def on_close(self):
        if self._running():
            if not messagebox.askyesno(
                    "退出", "机器人还在运行, 停止并退出吗?",
                    parent=self.root):
                return
            self.stop()
            for _ in range(40):          # 最多等 10 秒优雅退出
                if not self._running():
                    break
                self.root.after(250, lambda: None)
                self.root.update()
            if self._running():
                self.proc.terminate()
                self._flush_game_keys()
        self.root.destroy()

    # ---------- 后台 ----------
    def _reader(self):
        try:
            for line in self.proc.stdout:
                self.q.put(line.rstrip())
        except Exception:
            pass
        self.q.put(None)

    def _poll(self):
        # 收日志
        try:
            while True:
                line = self.q.get_nowait()
                if line is None:
                    code = self.proc.poll()
                    self.log_line("[GUI] 机器人已退出 (退出码 %s)"
                                  % ("0" if code == 0 else code))
                    self._set_buttons(False)
                    if os.path.exists(STOP_FLAG):
                        try:
                            os.remove(STOP_FLAG)
                        except OSError:
                            pass
                    continue
                m = re.search(r"战斗结束\]\s*#(\d+)", line)
                if m:
                    self.wins = max(self.wins, int(m.group(1)))
                self.log_line(line)
        except queue.Empty:
            pass
        # 状态栏
        if self._running():
            el = int(time.time() - self.t_start) if self.t_start else 0
            self.v_status.set("运行中 %02d:%02d:%02d | 胜场 %d"
                              % (el // 3600, el // 60 % 60, el % 60,
                                 self.wins))
        self.root.after(250, self._poll)

    def _set_buttons(self, running):
        self.b_start.config(state="disabled" if running else "normal")
        self.b_stop.config(state="normal" if running else "disabled")
        self.b_kill.config(state="normal" if running else "disabled")
        if running:
            self.v_status.set("运行中…")

    def log_line(self, text):
        self.log.config(state="normal")
        self.log.insert("end", text + "\n")
        if int(self.log.index("end-1c").split(".")[0]) > 3000:
            self.log.delete("1.0", "1000.0")   # 防止无限膨胀
        self.log.see("end")
        self.log.config(state="disabled")

    @staticmethod
    def _flush_game_keys():
        """强制停止后冲洗游戏窗口全部按键, 防卡键走丢。"""
        try:
            sys.path.insert(0, BASE)
            from pal_bot import find_game_window, Game
            import time as _t
            for hwnd in find_game_window():
                g = Game(hwnd)
                for _ in range(3):
                    g.flush_keys()
                    _t.sleep(0.1)
            print("[GUI] 已冲洗游戏按键")
        except Exception as ex:
            print("[GUI] 冲洗按键失败: %s" % ex)


def selftest():
    """不开界面的自检: 数据文件 / 参数拼装 / 停止标志路径。"""
    root = tk.Tk()
    root.withdraw()
    app = BotGUI(root)
    missing = [n for n in REQUIRED_DATA
               if not os.path.exists(os.path.join(BASE, n))]
    print("SELFTEST 数据文件缺失:", missing or "无")
    args = [sys.executable, "-u", "pal_bot_mem4.py", "0", "3600", "28",
            "90", "右上", "穿门"]
    print("SELFTEST 示例命令行:", " ".join(args))
    print("SELFTEST 停止标志:", STOP_FLAG)
    print("SELFTEST OK")
    root.destroy()
    return 0 if not missing else 1


def main():
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except Exception:
        pass
    BotGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
