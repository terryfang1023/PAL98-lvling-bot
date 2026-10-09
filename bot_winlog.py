# -*- coding: utf-8 -*-
"""win_total.json —— 跨重启累计的「总胜场」, 可由用户手动清空。

为什么要单独一个文件
--------------------
机器人进程里的 `battles_won` **每局归零**(它回答的是"本次启动赢了多少场"),
用户还想要一个**一直积累**的总数, 并且**想清就清**。两者语义不同, 不能共用一个变量。

文件格式 (人可读, 删掉等于清零):
    {"version": 1, "total": 1057, "t": "2026-10-08 12:40:01"}

清空怎么做到 (机器人正在跑的时候 GUI 不能直接改这个文件)
------------------------------------------------------
机器人是这份文件的**唯一写者**; GUI 想清空就创建一个 `win_reset.flag`
(与 mem_stop.flag 同一套"标志文件"约定, 但文件名不同, 互不干扰):
    下一个胜场到来时, 机器人先看到 flag -> 把 total 清 0 (并删掉 flag) -> 再 +1。
这样没有"GUI 写完被机器人下一次累加覆盖回去"的竞态。

本模块不 import pal_bot (GUI 进程要直接用), 也不 import botcfg (写文件只需原子替换)。
"""
import json
import os
import tempfile
import time

BASE = os.path.dirname(os.path.abspath(__file__))
WINS_PATH = os.path.join(BASE, "win_total.json")
RESET_FLAG = os.path.join(BASE, "win_reset.flag")

VERSION = 1


def _atomic_write_json(path, obj):
    """同目录原子写 (tmp + os.replace), 失败返回 False。"""
    try:
        d = os.path.dirname(os.path.abspath(path)) or "."
        fd, tmp = tempfile.mkstemp(prefix=".tmpwin_", dir=d)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
            return True
        except Exception:
            try:
                os.unlink(tmp)
            except Exception:
                pass
            return False
    except Exception:
        return False


class WinLog:
    """总胜场的读写 + 重置请求。

    path / flag 缺省 = 模块级常量 (调用时刻读, 便于测试临时改)。
    """

    def __init__(self, path=None, flag=None):
        self.path = path or WINS_PATH
        self.flag = flag or RESET_FLAG

    # ---------------- 读 ----------------
    def total(self):
        """当前总胜场 (文件缺失/损坏 -> 0)。"""
        try:
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)
            v = int(d.get("total", 0))
            return v if v > 0 else 0
        except Exception:
            return 0

    def stamp(self):
        """最近一次写入的时间戳 (没有返回 "")。"""
        try:
            with open(self.path, encoding="utf-8") as f:
                return str(json.load(f).get("t") or "")
        except Exception:
            return ""

    # ---------------- 写 ----------------
    def save_total(self, n):
        """直接写一个值 (机器人内部用)。返回是否写成功。"""
        try:
            n = max(0, int(n))
        except Exception:
            return False
        return _atomic_write_json(self.path, {
            "version": VERSION, "total": n,
            "t": time.strftime("%Y-%m-%d %H:%M:%S")})

    def reset(self):
        """清 0 并落盘 (机器人停机时 GUI 可直接调)。返回 0 (新值)。"""
        self.clear_request()
        self.save_total(0)
        return 0

    # ---------------- 重置请求 (跨进程) ----------------
    def pending_reset(self):
        """有没有待兑现的清空请求。"""
        try:
            return os.path.exists(self.flag)
        except Exception:
            return False

    def request_reset(self):
        """GUI 侧: 请求清空 (创建标志文件)。返回是否创建成功。"""
        try:
            d = os.path.dirname(os.path.abspath(self.flag)) or "."
            if not os.path.isdir(d):
                os.makedirs(d, exist_ok=True)
            with open(self.flag, "w", encoding="utf-8") as f:
                f.write("%s\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
            return True
        except Exception:
            return False

    def clear_request(self):
        """删掉标志文件 (兑现后 / 用户取消时)。返回是否删掉了。"""
        try:
            if os.path.exists(self.flag):
                os.unlink(self.flag)
                return True
        except Exception:
            pass
        return False

    # ---------------- 机器人主链路 ----------------
    def add(self, n=1):
        """赢了一场: 兑现清空请求(若有) 再累加, 落盘, 返回新总数。

        机器人是唯一写者, 所以"先读文件、再写回"不会与 GUI 打架:
        GUI 只能通过 win_reset.flag 参与, 不会与这里产生竞态。
        """
        tot = self.total()
        if self.pending_reset():
            tot = 0
            self.clear_request()
        tot += max(0, int(n))
        self.save_total(tot)
        return tot
