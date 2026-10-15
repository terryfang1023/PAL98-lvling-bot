# -*- coding: utf-8 -*-
"""test_fight.py — fight() 进场确认 + 主循环判定顺序回归 (v18.4/v18.5, 2026-10-04)

背景 (2026-10-04 场景221 事故, 用户"还是有战斗状态误抓"):
  真战斗中底部的指令菜单 (攻击/法术/物品/防御) 同样满足 ui_menu
  (red≈0.063 落在 0.04~0.30 菜单区间)。

  第一幕 (v18.4 修): 旧 _battle_confirm 每帧**先查 ui_menu 再查
  is_battle** -> 真战斗第一帧撞上指令菜单就按 esc 退出; 下一轮
  _stall 又判战斗再进 fight -> 永远撞上 -> 死循环。真机日志:
  05:50~08:43 卡死近 3 小时, "进场即菜单" 9359 次。

  第二幕 (v18.5 修): 进场确认修好后, **主循环里 ui_menu 仍排在
  is_battle 前面** —— 进场时指令菜单已开 (如战斗在等玩家选指令),
  每帧只按 esc, 攻击键一次发不出, 180s 超时判负; 快停复核又发现
  "战斗菜单出现" 再进战斗 -> F键0次 死循环 (真机: 两场 122s/180s,
  F键0次, "一次 F 都没发出")。

修复 (pal_bot_mem4):
  v18.4: _battle_confirm 抽出, is_battle 优先 + 连续 3 次菜单强制兜底;
  v18.5: 主循环同序翻转 —— is_battle(v2, HP/MP 数字) 真 -> 攻击键,
         即使 ui_menu 也真 (那就是指令菜单); ui_menu 真且无战斗特征
         (法术页/系统菜单) -> esc。

测试方式: MemBot4.__new__ 空壳 + 帧字典打桩
  (帧 = {"bt": bool, "menu": bool}; pal_bot.is_battle 被打桩读键),
  存图 BASE 重定向到临时目录, 零副作用。
"""
import os
import sys
import time
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pal_bot as PB                                        # noqa: E402
import pal_bot_mem4 as M4                                   # noqa: E402
from pal_bot_mem4 import MemBot4                            # noqa: E402

_TMPDIR = tempfile.mkdtemp(prefix="pal98_test_fight_")
M4.BASE = _TMPDIR                     # 存图/台账重定向, 不碰真实目录

_n = [0]


_fail = [0]


def check(name, cond, detail=""):
    _n[0] += 1
    if not cond:
        _fail[0] += 1
    tag = "ok" if cond else "FAIL"
    print("[%s] %s%s" % (tag, name, ("  | " + str(detail)) if detail and not cond else ""))
    return cond


class FakeGame:
    """按键记录 + 帧序列: shot() 逐帧弹出, 耗尽后恒返回 DEFAULT。"""

    def __init__(self, frames, default):
        self.frames = list(frames)
        self.default = default
        self.keys = []
        self.attacks = []
        self.esc_n = 0
        self.flushed = 0

    def shot(self):
        return self.frames.pop(0) if self.frames else dict(self.default)

    def key(self, name, dur=0.07):
        self.keys.append(name)

    def attack(self):
        self.attacks.append(1)

    def flush_keys(self):
        self.flushed += 1


class FakeMem:
    def __init__(self):
        self.K = (-30, 133)

    def state(self):
        return {"scene": 221, "K": self.K}


def shadow(frames, default=None):
    o = MemBot4.__new__(MemBot4)
    o.g = FakeGame(frames, default or {"bt": False, "menu": False})
    o.mem = FakeMem()
    o.band0 = lambda: None
    o.ui_menu = lambda a: a.get("menu", False)
    o.PROBE_N = 4
    o.PROBE_DT = 0.0                 # 测试不等真实时间
    o.MENUENTER_FORCE = 3
    o._menuenter_n = 0
    o._menuenter_streak = 0
    o.logs = []
    o.log = lambda m: o.logs.append(m)
    return o


# --------------------------------------------------------------------
def t_confirm_battle_first():
    print("== 进场确认: is_battle 优先 (v18.4 核心回归锁) ==")
    # ★ 事故指纹: 第一帧同时满足 menu+bt (战斗指令菜单骗过 ui_menu)。
    #   旧顺序 -> "进场即菜单" esc 退出; 新顺序必须 conf。
    o = shadow([{"bt": True, "menu": True}])
    kind, dt = o._battle_confirm()
    check("指令菜单+战斗特征 -> 判 conf (不是 menu)", kind == "conf", kind)
    check("连败清零", o._menuenter_streak == 0)
    check("确认阶段不按键", o.g.keys == [] and o.g.esc_n == 0)

    # 第一帧纯菜单、第二帧战斗 -> 也应 conf (等一帧而不是 esc)
    o = shadow([{"bt": False, "menu": True}, {"bt": True, "menu": True}])
    kind, _ = o._battle_confirm()
    check("菜单后一帧出现战斗 -> conf", kind == "conf", kind)


def t_confirm_menu_escape():
    print("== 进场确认: 全窗口只见菜单 -> menu (esc 由 fight 按) ==")
    o = shadow([{"bt": False, "menu": True}] * 4)
    kind, dt = o._battle_confirm()
    check("全程只有菜单 -> menu", kind == "menu", kind)
    check("计数 1 / 连败 1",
          o._menuenter_n == 1 and o._menuenter_streak == 1,
          (o._menuenter_n, o._menuenter_streak))
    check("battle_confirm 自身不按键", o.g.keys == [])
    # 留档分支: dict 帧没有 astype, 必须静默跳过而非炸掉 (健壮性回归锁)
    check("dict 帧下留档静默跳过不炸", kind == "menu")


def t_confirm_force_after_streak():
    print("== 进场确认: 连续 3 次菜单 -> 强制 conf 兜底 ==")
    o = shadow([{"bt": False, "menu": True}])
    o._menuenter_streak = 2               # 前两次已在 fight 层 esc 退出
    kind, _ = o._battle_confirm()
    check("第 3 次连续 -> 强制 conf", kind == "conf", kind)
    check("强制后连败清零", o._menuenter_streak == 0)
    check("日志说明兜底原因",
          any("强制进战斗循环" in m for m in o.logs), o.logs)

    # 连败 1/2 次时仍按 menu 退出 (不提前放行)
    o = shadow([{"bt": False, "menu": True}])
    o._menuenter_streak = 1
    kind, _ = o._battle_confirm()
    check("连败 2 (未达 3) 仍判 menu", kind == "menu", kind)


def t_confirm_none():
    print("== 进场确认: 全程既非战斗也非菜单 -> none ==")
    o = shadow([{"bt": False, "menu": False}] * 4)
    kind, dt = o._battle_confirm()
    check("返回 none", kind == "none", kind)
    check("不计数不按键", o._menuenter_n == 0 and o.g.keys == [])


def t_fight_branches():
    print("== fight 分支: menu -> esc 退出 / none -> 不按键 ==")

    def esc_counter(o):
        o._esc = 0
        orig = o.press_esc if hasattr(o, "press_esc") else None

        def pe():
            o._esc += 1
        o.press_esc = pe

    # menu 分支
    o = shadow([])
    esc_counter(o)
    o._battle_confirm = lambda: ("menu", 0.1)
    o._chase_tries, o._chase_goal = {}, None
    check("menu -> fight 返回 False",
          o.fight(why="测试") is False)
    check("menu -> 按了一次 esc", o._esc == 1, o._esc)

    # none 分支: 不按键、计误抓、记绕行
    o = shadow([])
    esc_counter(o)
    o._battle_confirm = lambda: ("none", 0.4)
    o._false_battle = o._false_streak = 0
    o._t_false = 0.0
    o.blocked = set()
    check("none -> fight 返回 False", o.fight(why="测试") is False)
    check("误抓计数 +1", o._false_battle == 1 and o._false_streak == 1)
    check("不按任何键 (一个键都不发)", o.g.keys == [] and o._esc == 0)
    check("卡格记绕行", (-30, 133) in o.blocked)


def t_fight_menu_attack_priority():
    print("== 主循环判定顺序 (v18.5 核心回归锁): 指令菜单开着也按攻击键 ==")
    # ★ 事故指纹: 进场时指令菜单已开 (bt+menu 同帧真)。
    #   v18.5 前主循环 ui_menu 先行 -> 每帧 esc, F键0次, 180s 超时。
    o = shadow(
        frames=[
            {"bt": False, "menu": False},     # 哨兵: 被"前3场留档"shot 消耗
            {"bt": True, "menu": True},       # 事故指纹: 指令菜单+战斗特征
            {"bt": True, "menu": True},       # 同上 (第二回合)
        ],
        default={"bt": False, "menu": False})  # 之后静止 -> 正常结束
    _fill_fight(o)
    o._battle_confirm = lambda: ("conf", 0.2)
    old_max = M4.BATTLE_MAX
    M4.BATTLE_MAX = 60.0
    try:
        ok = o.fight(why="测试")
    finally:
        M4.BATTLE_MAX = old_max
    check("返回 True", ok is True)
    check("指令菜单开着也发出了攻击键", len(o.g.attacks) >= 2, o.g.attacks)
    check("一次 esc 都没按 (menu 与 bt 同真时 menu 不优先)",
          o.g.keys.count("esc") == 0, o.g.keys)
    check("胜场 +1", o.battles_won == 1)
    check("没有『一次 F 都没发出』日志",
          not any("一次 F 都没发出" in m for m in o.logs), o.logs[-3:])


def _fill_fight(o):
    """fight() 主循环真路径所需的全部桩 (conf 之外的公共部分)。"""
    o._false_streak = 0
    o.n_battle = 0
    o.scene_battles = {}
    o.hotK = None
    o._save_hotspot = lambda: None
    o.battles_won = 0
    o.last_win = 0.0
    o.steps_since_battle = 0
    o._stuck_n = 0
    o.END_CONFIRM = 2
    o.F_ANIM_BIG = 1
    o._diffpix = lambda a, b: 0              # 画面静止
    o.px_thr = lambda x: x
    o._esc = 0
    o.press_esc = lambda: o.g.keys.append("esc")   # 打桩 (真方法会自行截屏)
    o.battle_screen = lambda: True           # 结算段直接通过
    o.after_battle_heal = lambda why="": None
    o._t_fight = 0.0
    o._patrol_r = 0
    o.logs = []
    o.log = lambda m: o.logs.append(m)


def t_fight_conf_full():
    print("== fight 真战斗全流程 (BATTLE_MAX 正常退出) ==")
    o = shadow(
        frames=[
            {"bt": False, "menu": False},     # 哨兵: 被 conf 后"前3场留档"的
                                              #   shot() 消耗 (dict 帧留档静默跳过)
            {"bt": False, "menu": True},      # 主循环: 系统菜单 -> esc
            {"bt": True, "menu": False},      # attack
            {"bt": True, "menu": False},      # attack
        ],
        default={"bt": False, "menu": False})  # 之后画面静止 -> 结束
    o._battle_confirm = lambda: ("conf", 0.2)   # 进场确认已由上节覆盖
    o._false_streak = 0
    o.n_battle = 0
    o.scene_battles = {}
    o.hotK = None
    o._save_hotspot = lambda: None
    o.battles_won = 0
    o.last_win = 0.0
    o.steps_since_battle = 0
    o._stuck_n = 0
    o.END_CONFIRM = 2
    o.F_ANIM_BIG = 1
    o._diffpix = lambda a, b: 0              # 画面静止
    o.px_thr = lambda x: x
    o._esc = 0
    o.press_esc = lambda: o.g.keys.append("esc")   # 打桩 (真方法会自行截屏)
    o.battle_screen = lambda: True           # 结算段直接通过
    o.after_battle_heal = lambda why="": None
    o._t_fight = 0.0
    o._patrol_r = 0
    o.logs = []
    o.log = lambda m: o.logs.append(m)
    old_max = M4.BATTLE_MAX
    M4.BATTLE_MAX = 60.0
    try:
        ok = o.fight(why="测试")
    finally:
        M4.BATTLE_MAX = old_max
    check("返回 True", ok is True)
    check("胜场 +1", o.battles_won == 1)
    check("出了 2 次攻击", len(o.g.attacks) == 2, o.g.attacks)
    check("主循环 esc 关过系统菜单", o.g.keys.count("esc") >= 1, o.g.keys)
    check("战斗日志完整",
          any("战斗开始" in m for m in o.logs)
          and any("战斗结束" in m for m in o.logs))


_REAL_IS_BATTLE = PB.is_battle


def t_settle_screen():
    print("== v18.7 _settle_screen: 等淡入散场再让存盘宏拍基准帧 ==")
    import numpy as np
    from pal_bot_heal import diff_frac as dfrac
    base = np.zeros((40, 60, 3), dtype=np.uint8)
    base[:, :, 0] = 10
    # 淡入帧必须**真超容差** (diff_frac tol=24): 200 -> 120 -> 10 (稳定)
    f_hi = base.copy(); f_hi[:, :, 0] = 200
    f_mid = base.copy(); f_mid[:, :, 0] = 120
    check("测试帧亮度差确实超容差",
          dfrac(f_hi, f_mid) > 0.015 and dfrac(f_mid, base) > 0.015
          and dfrac(base, base.copy()) == 0.0)

    # 淡入序列: 200 -> 120 -> 全亮 x2 -> 应在最后两帧稳定返回 True
    frames = [f_hi, f_mid, base, base]
    calls = {"n": 0}

    class G:
        @staticmethod
        def shot():
            a = frames[min(calls["n"], len(frames) - 1)]
            calls["n"] += 1
            return a

    o = shadow([])
    o.g = G()
    ok = o._settle_screen(timeout=5.0)
    check("淡入后稳定 -> True", ok is True)
    check("稳定时提前退出 (没耗满 timeout)", calls["n"] <= 4, calls["n"])
    check("末帧确为稳定地图", dfrac(frames[-1], base) == 0.0)

    # 一直闪 -> 超时返回 False (不挡主流程)
    calls["n"] = 0
    flicker = [f_hi, f_mid] * 8

    class G2:
        n = 0

        @staticmethod
        def shot():
            a = flicker[G2.n % len(flicker)]
            G2.n += 1
            return a

    o2 = shadow([])
    o2.g = G2()
    check("持续闪烁 -> 超时 False", o2._settle_screen(timeout=0.05) is False)

    # 假帧 (dict) diff 抛异常 -> 静默跳过, 不挡 fight 主流程
    o3 = shadow([])
    check("异常帧 -> False 不炸", o3._settle_screen(timeout=0.05) is False)


def t_real_is_battle_untouched():
    print("== 防呆: 真函数未被测试污染 ==")
    check("pal_bot.is_battle 已恢复为原函数", PB.is_battle is _REAL_IS_BATTLE)


def t_fight_watchdog():
    print("== v19.1 卡死保护: 攻击键连发却无画面进展 -> esc 退回指令层 ==")
    # 事故指纹 (2026-10-10 用户截图): 回车把指令菜单打成"选人/选目标",
    # R(重复) 在该等待态**无效**, 画面静止但 is_battle 恒真 (底部 HP/MP
    # 数字在) -> 旧代码一直按 R 到 180s 超时。按一下 esc 就能退回。
    # 帧的 "move" 键: True=画面在动 (由 _frames_moved 打桩读取)。
    o = shadow(
        frames=[{"bt": False, "menu": False}] +         # 哨兵
               [{"bt": True, "menu": False}] * 40,      # 卡在选人: 特征在, 画面静止
        default={"bt": False, "menu": False})
    _fill_fight(o)
    o._battle_confirm = lambda: ("conf", 0.2)
    o._frames_moved = lambda a, b: bool(b and b.get("move"))
    o.FIGHT_STILL_LIMIT = -1.0            # 测试不等真实时间: 静止立刻触发
    old_max = M4.BATTLE_MAX
    M4.BATTLE_MAX = 60.0
    try:
        ok = o.fight(why="测试")
    finally:
        M4.BATTLE_MAX = old_max
    check("流程走完返回 True", ok is True)
    check("卡死保护按了 esc", o.g.keys.count("esc") >= 1, o.g.keys)
    check("日志说明卡死原因", any("卡死保护" in m for m in o.logs),
          o.logs[-3:])
    check("攻击键仍在发 (esc 之后 R 恢复有效)", len(o.g.attacks) >= 1,
          len(o.g.attacks))
    check("胜场照常 +1", o.battles_won == 1)

    # 对照组: 画面一直在动 (正常战斗) -> 绝不触发保护
    o = shadow(
        frames=[{"bt": False, "menu": False}] +
               [{"bt": True, "menu": False, "move": True}] * 8,
        default={"bt": False, "menu": False})
    _fill_fight(o)
    o._battle_confirm = lambda: ("conf", 0.2)
    o._frames_moved = lambda a, b: bool(b and b.get("move"))
    o.FIGHT_STILL_LIMIT = -1.0            # 再苛刻也只在"静止"时触发
    old_max = M4.BATTLE_MAX
    M4.BATTLE_MAX = 60.0
    try:
        ok = o.fight(why="测试")
    finally:
        M4.BATTLE_MAX = old_max
    check("正常动画战斗 -> 返回 True", ok is True)
    check("正常动画战斗 -> 一个 esc 都没有",
          o.g.keys.count("esc") == 0, o.g.keys)
    check("没有卡死保护日志",
          not any("卡死保护" in m for m in o.logs))


def t_fight_esc_escalation():
    print("== v19.2 长战斗无进展兜底: R 连按 20 次 -> esc (不被待机动画骗过) ==")
    # 2026-10-12 真机 14 帧实测: 选人等待态的角色待机动画让帧间差高达
    # 6~13 万像素 (>> anim_min=300) -> v19.1 静止检测恒判"在动",
    # 178 场 180s 超时只有 13 场被救回。所以必须按 R 次数/时长兜底。
    o = shadow(
        frames=[{"bt": False, "menu": False}] +                   # 哨兵
               [{"bt": True, "menu": False, "move": True}] * 21,  # 动画不停
        default={"bt": False, "menu": False})
    _fill_fight(o)
    o._battle_confirm = lambda: ("conf", 0.2)
    o._frames_moved = lambda a, b: bool(b and b.get("move"))
    # KEY_F_GAP 保持 0.5s 真实节奏: 第 21 次循环 n_f=20 (约 10s), 此时
    # over-last_esc > ESC_EVERY(8s) 恰好开门 —— 与真机时序一致。
    o.KEY_F_GAP = 0.5
    old_max = M4.BATTLE_MAX
    M4.BATTLE_MAX = 60.0
    try:
        ok = o.fight(why="测试")
    finally:
        M4.BATTLE_MAX = old_max
    check("流程走完返回 True", ok is True)
    check("R 连按 20 次后按了兜底 esc", o.g.keys.count("esc") >= 1, o.g.keys)
    check("日志说明是次数兜底 (长战斗无进展)",
          any("长战斗无进展" in m for m in o.logs), o.logs[-3:])
    check("动画不停 -> v19.1 静止保护没抢着触发",
          not any("卡死保护" in m for m in o.logs), o.logs[-3:])
    check("兜底后攻击键仍在发", len(o.g.attacks) >= 18, len(o.g.attacks))
    check("胜场照常 +1", o.battles_won == 1)

    # 时间路径: R 次数不多但战斗超 LONG_BATTLE -> 也要 esc
    o = shadow(
        frames=[{"bt": False, "menu": False}] +
               [{"bt": True, "menu": False, "move": True}] * 5,
        default={"bt": False, "menu": False})
    _fill_fight(o)
    o._battle_confirm = lambda: ("conf", 0.2)
    o._frames_moved = lambda a, b: bool(b and b.get("move"))
    o.KEY_F_GAP = 0.0
    o.LONG_BATTLE = -1.0                  # 测试不等真实时间: 立刻超时
    o.ESC_EVERY = -1.0                    # 间隔门槛也放开
    old_max = M4.BATTLE_MAX
    M4.BATTLE_MAX = 60.0
    try:
        ok = o.fight(why="测试")
    finally:
        M4.BATTLE_MAX = old_max
    check("超 LONG_BATTLE -> 返回 True", ok is True)
    check("R 次数不多也按了兜底 esc (时间路径)",
          o.g.keys.count("esc") >= 1, o.g.keys)
    check("时间路径也有兜底日志",
          any("长战斗无进展" in m for m in o.logs), o.logs[-3:])


def t_settle_space_guard():
    print("== v19.2 结算 space 复查: 落进新战斗 -> esc 撤销误确认 ==")
    # 事故根源 (2026-10-12): 机器人从不按 enter, 但结算 space 与 enter 同为
    # "确认" —— 新战斗淡入时 battle_screen 还认不出, space 落进去把指令菜单
    # 打成"选人"等待态。按完复查一次, 真开打了立刻 esc 撤销。
    # 正常: 结算期没有新战斗 -> 只按 space, 绝不多按 esc
    o = shadow([{"bt": False, "menu": False}])
    _fill_fight(o)
    o._battle_confirm = lambda: ("conf", 0.2)
    o.battle_screen = lambda: False
    o.fight(why="测试")
    check("无新战斗 -> space 2 次照常", o.g.keys.count("space") == 2, o.g.keys)
    check("无新战斗 -> 不按 esc", o.g.keys.count("esc") == 0, o.g.keys)

    # space 之后真的开打了 -> 立刻 esc 撤销
    o = shadow([{"bt": False, "menu": False}])
    _fill_fight(o)
    o._battle_confirm = lambda: ("conf", 0.2)
    calls = {"n": 0}

    def bs():
        calls["n"] += 1
        return calls["n"] == 2            # 第 2 次调用 = space 之后的复查

    o.battle_screen = bs
    o.fight(why="测试")
    check("space 落进新战斗 -> 检测到了", calls["n"] >= 2)
    check("按了 esc 撤销误确认", o.g.keys.count("esc") == 1, o.g.keys)
    check("日志说明撤销原因",
          any("撤销误确认" in m for m in o.logs), o.logs[-3:])
    check("space 只发了一次 (不再发第二个)", o.g.keys.count("space") == 1,
          o.g.keys)


def t_frames_moved_robust():
    print("== _frames_moved: 真帧可判, 打桩帧/异常按'在动'处理 (fail-safe) ==")
    import numpy as np
    p = MemBot4.__new__(MemBot4)          # 裸影子: 只挂 _diffpix/px_thr
    p.px_thr = lambda x: 5
    a = np.zeros((12, 12, 3), np.uint8)
    b = a.copy()
    check("两帧全同 -> 没动", p._frames_moved(a, b) is False)
    c = a.copy()
    c[0:6, :] = 255                       # 一大块变化 -> 在动
    check("大块变化 -> 在动", p._frames_moved(a, c) is True)
    check("形状不一致 -> 按在动 (不触发保护)",
          p._frames_moved(a, np.zeros((5, 5, 3), np.uint8)) is True)
    check("dict 帧 (测试打桩) -> 按在动不炸",
          p._frames_moved({"x": 1}, {"y": 2}) is True)
    check("None 帧 -> 按在动不炸", p._frames_moved(None, a) is True)


def main():
    print("=" * 62)
    print("test_fight.py — fight 进场确认+主循环顺序 v18.5/v19.1 回归")
    print("=" * 62)
    # 打桩 pal_bot.is_battle (帧字典模式), 结束后恢复
    _real = PB.is_battle
    PB.is_battle = lambda a, b0=None: a.get("bt", False)
    try:
        t_confirm_battle_first()
        t_confirm_menu_escape()
        t_confirm_force_after_streak()
        t_confirm_none()
        t_fight_branches()
        t_fight_menu_attack_priority()
        t_fight_conf_full()
        t_fight_watchdog()
        t_fight_esc_escalation()
        t_settle_space_guard()
        t_frames_moved_robust()
        t_settle_screen()
    finally:
        PB.is_battle = _real
    t_real_is_battle_untouched()
    print("-" * 62)
    print("共 %d 项检查, %d 失败" % (_n[0], _fail[0]))
    print("OK" if _fail[0] == 0 else "FAIL x%d" % _fail[0])


if __name__ == "__main__":
    main()
