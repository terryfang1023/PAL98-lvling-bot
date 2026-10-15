# -*- coding: utf-8 -*-
"""exp_log.json —— 战斗收益台账 (每场经验/金钱 + 会话累计 + 每小时平均)。

为什么是文件而不是 GUI 直接读内存
--------------------------------
GUI 与机器人是两个进程, 游戏内存句柄在机器人手里。机器人每赢一场把
「战后快照 - 战前快照」写进这个文件, GUI 只读 —— 与 win_total.json 同一套
约定: **机器人是唯一写者**, GUI 绝不写, 所以没有竞态。

地址标定
--------
金钱 dwCash = 0x00C3C978 (= bot_itemmon.EXP_ADDR - 4)。三重证据:
  ① SDLPAL 源码 global.h: GLOBALVARS 里 dwCash 在 wNumScene 之后、Exp 之前;
  ② 差分实测 (probe_money2.py): 与三人经验同一时刻 +120 (战斗结算同步写);
  ③ 量级: 636773 ÷ 6749 场 ≈ 94 文/场, 正是 PAL 敌人给钱区间。

口径 (★ v3: 等级感知的累计经验)
--------------------------------
  exp   = **单人**经验 —— 每个在队角色各拿一份, 取各槽增量最大值
          (求和会 3 倍虚高, 用户实测: 真值 52 -> 显示 156)。
  ★ 关键 (2026-10-14 源码 battle.c + 真机双重取证): 游戏里的 `wExp` 是
    **当前等级内**的经验 —— 升级 `dwExp -= rgLevelUpExp[等级]`, 满级(99)之后
    引擎只扣阈值不升级, wExp 在 [0,32000) 里循环。所以 wExp **本来就会掉**,
    不能直接相减, 必须先换算成累计经验:
        累计 = wExp + Σ_{l<level} rgLevelUpExp[l]        (见 total_exp)
    阈值表从游戏内存读 (Mem.levelup_table), 索引 = 当前等级。
  单场   = 战后快照 - 战前快照 (快照在每场 fight 开始时拍, 开箱/剧情收益
          不混进单场);
  会话累计 = **逐场累加**已入账的单场收益 (单调, 永不因异常重置; 开箱/剧情
          收益算在"单场"里由下一场带出) —— 比"当前 - 基线"抗干扰:
          基线法一旦遇到升级/满级循环/读档就整段作废, 累计会被反复清零;
  每小时平均 = 会话累计 ÷ 运行小时数 (GUI 算, 文件里只给 elapsed 秒)。

异常防线: 单场增量仍为负或超上限 (读档/换版本/内存读到垃圾) -> **只跳过本场**,
累计不重置, 并记 bad 计数 (日志会带原始快照, 便于事后归因)。

本模块不 import pal_bot* (GUI 进程要用读取侧), 也不 import botcfg。
"""
import json
import os
import struct
import tempfile
import time

BASE = os.path.dirname(os.path.abspath(__file__))
EXPLOG_PATH = os.path.join(BASE, "exp_log.json")

VERSION = 3                # 3 = 等级感知累计经验 + 会话累计改为逐场累加
                           #   (v1 六人求和 3 倍虚高; v2 直接相减, 升级就误判成读档)
MAX_BATTLES = 200          # battles 环形上限 (新的在前)
GAIN_N = 4 + 8 * 6         # dwCash(4B) + Exp.rgPrimaryExp[6](48B) = 52B
ROLES = 6
MAX_LEVELS = 99            # PAL 最高等级 (SDLPAL MAX_LEVELS); 满级后 wExp 会循环
MAX_ONE_BATTLE = 60000     # 单场每人经验合理上限 (超过 -> 脏数据)
# 单场收益的合理性上限 (PAL 单场经验上限 9999/人; 金钱类似量级)。
# 超过一律视为脏数据作废 —— 漏记一段好过显示假数。
MAX_DELTA = 99999


def parse_snapshot(raw):
    """52B 原始字节 (dwCash + rgPrimaryExp[6]) -> 快照 dict; 读不全 -> None。

    布局 (SDLPAL global.h): [0:4]=dwCash (DWORD), 之后 6 条
    EXPERIENCE { WORD wExp; WORD wReserved; WORD wLevel; WORD wCount; } (8B/条)。

    ★ v3: 一定要把 **wLevel** 也解析出来 —— 它是换算"累计经验"的必需项
      (见 total_exp; 等级内经验在升级时会掉, 必须补回已跨过的阈值)。
    """
    if not raw or len(raw) < GAIN_N:
        return None
    cash = struct.unpack_from("<I", raw, 0)[0]
    exps, levels = [], []
    for i in range(ROLES):
        e, _res, lv, _cnt = struct.unpack_from("<4H", raw, 4 + i * 8)
        exps.append(e)
        levels.append(lv)
    return {"cash": cash, "exps": exps, "levels": levels}


def total_exp(exp, level, table):
    """「等级内经验」->「累计经验」= exp + Σ_{l<level} table[l] (纯函数)。

    ★ 为什么必须换算 (2026-10-14 源码 + 真机双重取证):
      SDLPAL `battle.c` 里 `wExp` 是**当前等级内**已积累的经验 —— 战斗结算执行
      `dwExp += 所得; while (dwExp >= rgLevelUpExp[等级]) dwExp -= rgLevelUpExp[等级];`
      升级时 wExp 被减掉一整个阈值; 满级(99)之后引擎**只扣阈值不升级**
      (扣减在 `if (level < MAX_LEVELS)` 之外), 于是 wExp 在 [0, 32000) 里循环。
      => wExp **本来就会周期性往下掉**, 直接相减必然被误判成"读档回退"。
      加上已跨过的等级阈值之和, 才是单调的"这一路一共攒了多少经验"。
      真机实测: 队伍等级 99/84/83, rgLevelUpExp[83..99] 全是 32000。

    table 缺失 (阈值表读不到) -> 退回原值 (只影响升级那一刻的那场读数)。
    """
    if not table:
        return int(exp)
    lv = int(level)
    if lv > MAX_LEVELS:
        lv = MAX_LEVELS
    s = 0
    for l in range(1, lv):
        if l < len(table):
            s += int(table[l])
    return int(exp) + s


def snap_delta(pre, cur, table=None):
    """两个快照 -> (单人经验增量, 金钱增量); 无法解释的脏数据 -> None。

    ★ 经验取**各槽增量的最大值** = 每个在队角色实际拿到的那一份。求和会是
      3 倍 (三人在队各拿一份), 见模块头口径说明。
    脏数据判据: 金钱增量越界, 或任一槽的经验增量**仍为负 / 超上限**。
      - 升级造成的"掉"由 total_exp 的累计口径消掉, 正常不算脏;
      - 满级只扣阈值不升级 -> 掉幅是阈值的整数倍, 这里加回来 (最多 8 次);
      - 补不回来的负增量 = 真回退 (读档/换人/换版本) -> 本场作废。
    """
    if not pre or not cur:
        return None
    d_cash = int(cur["cash"]) - int(pre["cash"])
    if not (0 <= d_cash <= MAX_DELTA):
        return None
    n = min(len(pre.get("exps") or []), len(cur.get("exps") or []))
    if n <= 0:
        return None
    gains = []
    for i in range(n):
        e0, e1 = int(pre["exps"][i]), int(cur["exps"][i])
        l0 = int((pre.get("levels") or [0] * n)[i])
        l1 = int((cur.get("levels") or [0] * n)[i])
        d = total_exp(e1, l1, table) - total_exp(e0, l0, table)
        if d < 0 and table and l0 == l1:
            # 满级循环: 引擎扣了 k 个阈值但等级不变
            t = int(table[l1]) if 0 <= l1 < len(table) else 0
            if t > 0:
                k = int(round(-d / float(t)))
                if 0 < k <= 8 and abs(d + k * t) <= MAX_ONE_BATTLE:
                    d += k * t
        gains.append(d)
    if any(x < 0 or x > MAX_ONE_BATTLE for x in gains):
        return None
    return (max(gains), d_cash)


def _atomic_write_json(path, obj):
    """同目录原子写 (tmp + os.replace), 失败返回 False。同 bot_winlog。"""
    try:
        d = os.path.dirname(os.path.abspath(path)) or "."
        fd, tmp = tempfile.mkstemp(prefix=".tmpgain_", dir=d)
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


class GainLog:
    """exp_log.json 的读写。机器人 write/record; GUI 只 load。

    path 缺省 = 模块级常量 (调用时刻取, 便于自检脚本重定向临时目录)。
    """

    def __init__(self, path=None):
        self.path = path or EXPLOG_PATH

    # ---------------- 读 (GUI 侧) ----------------
    def load(self):
        """读整个台账; 文件缺失/损坏 -> None (调用方保持原显示)。"""
        try:
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else None
        except Exception:
            return None

    # ---------------- 写 (机器人侧, 唯一写者) ----------------
    def write(self, obj):
        """整体落盘 (机器人自己组装完整 dict 时用)。"""
        return _atomic_write_json(self.path, obj)

    def record(self, wins, elapsed, t_start, d_exp, d_cash, last, bad=0):
        """追加一场并落盘: 保留旧 battles 环形 (新的在前), 组装完整 dict。

        last = {"n":胜场号, "t":"HH:MM:SS", "exp":本场经验, "cash":本场金钱}
        bad  = 会话内被跳过(脏数据)的场次数 (GUI 用来提示"时均偏低"的可能原因)
        返回写完整 dict 是否成功。

        ★ 口径升级: 旧文件 version != 当前 VERSION -> 旧 battles/累计全部丢弃
          (v1 经验是六人求和 3 倍虚高; v2 是"当前-基线"的差值, 升级/满级 /
           读档一发生就被判脏数据清零 —— 留着会让时均明显偏小)。
        d_exp/d_cash 由调用方给**会话累计**(逐场累加), 这里只落盘不再自算。
        """
        old = self.load() or {}
        if old.get("version") != VERSION:
            old = {}
        battles = list(old.get("battles") or [])
        battles.insert(0, dict(last))
        del battles[MAX_BATTLES:]
        obj = {
            "version": VERSION,
            "t_start": t_start,
            "elapsed": round(max(0.0, float(elapsed)), 1),
            "wins": max(0, int(wins)),
            "d_exp": max(0, int(d_exp)),
            "d_cash": max(0, int(d_cash)),
            "bad": max(0, int(bad)),
            "last": dict(last),
            "battles": battles,
        }
        return self.write(obj)


def rate_per_hour(total, elapsed):
    """会话累计 -> 每小时平均 (纯函数, GUI 显示用)。

    elapsed <= 0 -> 0; 不足一分钟没意义, 由调用方决定显示策略。
    """
    try:
        el = float(elapsed)
    except Exception:
        return 0
    if el <= 0:
        return 0
    return int(round(total * 3600.0 / el))
