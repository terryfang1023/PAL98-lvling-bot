# -*- coding: utf-8 -*-
"""test_speed_fix.py — 2026-09-30 速度/迁移修复的回归测试 (不需要游戏窗口)。

覆盖三个 bug:
  ① `_party_dir()` 读错内存索引 (51 -> 52)。后果: "转身" 永远检测不到,
     step() 的 TURN_EXT 追加预算从未生效 -> 换方向时按键被引擎吃成转身,
     0.46s 预算耗尽被误判 "真墙" 记入绕行 (实测 328 次/小时), 进而
     可达域 35↔2191 反复横跳 + 自愈 104 次白清。
  ② `pick_migrate_door()` 门距上限写死 80 格 -> 把"角落的门"静默排除,
     且同一门踩不触发时不会换下一个门。
  ③ `_pilot_tick()` 不读 migrate_cd -> 迁移失败后每 3 分钟重新武装,
     反复白跑 (实测一轮 13 次尝试 0 成功)。

铁律: 本测试用**假的按键通道**, 不碰游戏、不写日志。
"""
import io
import os
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import pal_bot as PB                                      # noqa: E402
import pal_bot_mem as PM                                  # noqa: E402
from pal_bot_mem3 import MemBot3                          # noqa: E402

OK, BAD = [], []


def check(name, cond, extra=""):
    (OK if cond else BAD).append(name)
    print("  %s %s %s" % ("PASS" if cond else "FAIL", name, extra))


# ======================================================================
print("=" * 66)
print("第 1 节: 朝向字段索引 (wPartyDirection @0x00C3C958)")
print("=" * 66)
check("IPD = (0x00C3C958 - READ_AT)//2", PM.IPD == (0x00C3C958 - PM.READ_AT) // 2,
      "IPD=%d" % PM.IPD)
check("IPD == 52 (v12 标定的 51 是错的)", PM.IPD == 52, "IPD=%d" % PM.IPD)
check("IPD 与 wNumScene(SCENE_ADDR) 相差 1 个 uint16",
      (PM.SCENE_ADDR - 0x00C3C958) == 2,
      "0x%X" % PM.SCENE_ADDR)
check("IPD 落在单次读取窗口内 (READ_N)",
      (PM.IPD + 1) * 2 <= PM.READ_N, "offset=%d READ_N=%d"
      % ((PM.IPD + 1) * 2, PM.READ_N))


# ======================================================================
# 假 bot: 按键通道 + 内存都可控
# ======================================================================
class FakeGame(object):
    """真机行为的精简模型:

      * g.key() 本身耗时 ~0.13s (keyup + 30ms + keydown + 70ms + keyup + 30ms)
        —— 这一点是关键: step() 里 _last_press 是在 key() **返回之后**才更新的,
        所以实际按键间隔 ≈ 0.33s, 而基础预算 BUDGET_F=0.46s 只够按 **2** 次。
      * 第 1 次按键被引擎吃成"转身"(朝向变了但没走);
      * 第 3 次按键才真的走一格。
      => 没有 TURN_EXT 追加预算时必然在 0.46s 处超时 -> 被误判成 "真墙"。
    """

    def __init__(self, bot):
        self.bot = bot
        self.press = 0

    def key(self, name, dur=0.07):
        time.sleep(0.13)                       # 真实 g.key() 的固定开销
        self.press += 1
        if self.press == 1:
            self.bot.dir_w[1] = 3              # 只转身: 朝向 2 -> 3
        elif self.press >= 3:
            self.bot.K = (self.bot.K[0] + 1, self.bot.K[1])   # up = (+1, 0)

    def attack(self, dur=0.07):
        """真 Game 的 attack() = 按当前配置的战斗攻击键 (默认 R)。

        本测试只走移动路径, 到不了这里; 但假对象必须与真接口同步 ——
        否则哪天 step() 的兜底路径开始按攻击键, 这里会静默 AttributeError。
        """
        self.key(PB.attack_key(), dur)


class FakeMem(object):
    """★ 假内存写死**真实布局**, 不跟随 PM.IPD —— 否则把常量改错也测不出来。

    真实布局 (2026-09-30 实测): 朝向在 W[52] (0x00C3C958), W[51] 是无关字段。
    """
    TRUE_DIR_IDX = 52

    def __init__(self, bot):
        self.bot = bot

    def raw(self):
        W = [0] * (PM.READ_N // 2)
        W[51] = 9                              # 无关常量 (旧代码误读的就是它)
        W[self.TRUE_DIR_IDX] = self.bot.dir_w[1]     # 真正的朝向
        return tuple(W)

    def state(self):
        return {"K": self.bot.K, "scene": self.bot.scene}


class FakeBot(object):
    LOCK_F = MemBot3.LOCK_F
    BUDGET_F = MemBot3.BUDGET_F
    TURN_EXT = MemBot3.TURN_EXT
    TURN_EXT_MAX = MemBot3.TURN_EXT_MAX

    def __init__(self):
        self.K = (0, 0)
        self.scene = 157
        self.dir_w = [None, 2]                 # 进入 step 时的朝向 = 2 (up)
        self.g = FakeGame(self)
        self.mem = FakeMem(self)
        self.log = lambda m: None
        self._n_retry = self._n_turn = self._n_moved = 0
        self._n_stall = self._n_shot = self._n_window = 0
        self._step_t = []
        self._stat_t = time.time()
        self._t_window = time.time()
        self._last_press = 0.0
        self._dump_n = 0
        self.stalled = 0

    # 复用被测实现
    _party_dir = MemBot3._party_dir
    step = MemBot3.step

    def _rate(self, dt, n_retry, stalled):
        pass                                    # 计时日志不参与断言

    def _stall(self, key, K0, sc0, n_retry):
        self.stalled += 1
        return ("blocked", K0, K0, sc0)


print()
print("=" * 66)
print("第 2 节: 换方向时 TURN_EXT 必须救回被判成'墙'的一步")
print("=" * 66)
b = FakeBot()
res = b.step("up")
check("修好后: 这一步被判 moved (不是 blocked)", res[0] == "moved", str(res[0]))
check("修好后: 转身计数 +1 (TURN_EXT 生效)", b._n_turn == 1, str(b._n_turn))
check("修好后: 没有进 _stall", b.stalled == 0, str(b.stalled))
check("修好后: 其实按了 3 次键 (第 1 次被转身吃掉)", b.g.press == 3,
      str(b.g.press))

# 反例对照: 把索引改回 v12 的 51 -> 必须复现旧 bug
_saved = PM.IPD
PM.IPD = 51
try:
    b2 = FakeBot()
    res2 = b2.step("up")
finally:
    PM.IPD = _saved
check("反例(索引=51): 同一步被误判 blocked", res2[0] == "blocked", str(res2[0]))
check("反例(索引=51): 转身计数恒为 0 (历代日志 '_n_turn=0' 的由来)",
      b2._n_turn == 0, str(b2._n_turn))
check("反例(索引=51): 进了 _stall (白烧 2 张截图 + 0.7s)", b2.stalled == 1,
      str(b2.stalled))

# 防跑飞上限
b3 = FakeBot()
b3.TURN_EXT_MAX = 0                        # 关掉追加 -> 应回到 blocked
res3 = b3.step("up")
check("追加次数上限可关 (上限=0 -> blocked)",
      res3[0] == "blocked" and b3.stalled == 1, str(res3[0]))
check("默认 TURN_EXT_MAX=2 (单步最坏 0.46+2x0.43≈1.32s, 有限)",
      MemBot3.TURN_EXT_MAX == 2, str(MemBot3.TURN_EXT_MAX))


# ======================================================================
print()
print("=" * 66)
print("第 3 节: 迁移选门 (角落的门不能被静默排除 + 失败门要换)")
print("=" * 66)
from pal_bot_mem5 import MemBot5                          # noqa: E402

SC157 = {(-96, 132), (-54, 82), (-21, 63), (30, 66)}      # 报告解出的 4 个门
CUR = (-14, 70)


def fake_bot(doors=SC157, fail=None):
    o = MemBot5.__new__(MemBot5)
    o.scene = 157
    o.map = 156
    o.report_doors = {157: set(doors)}
    o.known_doors = {}
    o.blocked = set()
    o._door_fail = dict(fail or {})
    o._kwalk_cache = {}
    o.grids = None
    o.log = lambda m: None
    # 门周围都给可走格, 把"地图可走性"从测试里摘掉
    o.kwalk = lambda m: {(d[0] + da, d[1] + db)
                         for d in doors for da in (-1, 0, 1) for db in (-1, 0, 1)}
    return o


b = fake_bot()
check("门距上限已提到 200 (旧的 80 会把 82 格的角落门排除)",
      MemBot5.MIGRATE_DOOR_MAX >= 132,
      "MIGRATE_DOOR_MAX=%d" % MemBot5.MIGRATE_DOOR_MAX)
d1 = b.pick_migrate_door(CUR)
check("默认取最近的门 K(-21,63)", d1 == (-21, 63), str(d1))

b = fake_bot(fail={(-21, 63): 2, (30, 66): 2})
d2 = b.pick_migrate_door(CUR)
check("前两个门各失败 2 次 -> 换到 K(-54,82)", d2 == (-54, 82), str(d2))

b = fake_bot(fail={(-21, 63): 2, (30, 66): 2, (-54, 82): 2, (-96, 132): 2})
d3 = b.pick_migrate_door(CUR)
check("全部失败过 -> 清空失败史重来 (不永久拉黑)", d3 == (-21, 63), str(d3))
check("此时失败史已清空", b._door_fail == {}, str(b._door_fail))

b = fake_bot(fail={(-21, 63): 1})
check("只失败 1 次的门仍优先 (DOOR_FAIL_MAX=2)",
      b.pick_migrate_door(CUR) == (-21, 63), "")

b = fake_bot()
b.kwalk = lambda m: set()                  # 门周围全是墙
check("门外 ±2 全不可走 -> 返回 None", b.pick_migrate_door(CUR) is None, "")

print()
print("=" * 66)
print("第 4 节: 迁移失败必须给出原因, 且要能触发调度器退避")
print("=" * 66)
b = fake_bot()
b.kwalk = lambda m: set()
b._door_fail = {}
b.step = lambda k: ("blocked", (0, 0), (0, 0), 157)
check("migrate_probe: 门外无可走格 -> False", b.migrate_probe((-21, 63)) is False)
check("migrate_probe: 写明原因 (原来静默返回, 看不出卡在哪)",
      "无可走格" in b._mig_why, b._mig_why)

b2 = fake_bot()
b2._door_fail = {}
b2._migrate_failed((-21, 63), "测试原因")
check("失败计数 +1", b2._door_fail == {(-21, 63): 1}, str(b2._door_fail))
b2._migrate_failed((-21, 63), "测试原因")
check("再失败 +1 -> 达到 DOOR_FAIL_MAX", b2._door_fail[(-21, 63)] == 2, "")

# _on_migrate_end 钩子: 失败 -> note_fail; 成功 -> 不动
class FakePilot(object):
    def __init__(self):
        self.fails = 0

    def note_fail(self, now):
        self.fails += 1


b3 = MemBot5.__new__(MemBot5)
b3.pilot = FakePilot()
b3.open_doors = False
b3._pilot_open_tmp = True
b3._open_doors_user = False
MemBot5._on_migrate_end(b3, False)
check("迁移失败 -> pilot.note_fail (退避)", b3.pilot.fails == 1,
      str(b3.pilot.fails))
check("迁移失败 -> 还原用户的开/关门设定", b3.open_doors is False,
      str(b3.open_doors))
b3.pilot.fails = 0
MemBot5._on_migrate_end(b3, True)
check("迁移成功 -> 不触发退避", b3.pilot.fails == 0, str(b3.pilot.fails))

print()
print("=" * 66)
print("第 5 节: 「一次键都没按」不能算作一次迁移尝试")
print("=" * 66)
# 实测日志: 12:03:15 / 12:06:10 / 12:09:07 各有 10 条「门未触发(第N次)」,
# 全部落在**同一秒**内 —— 中间一步都没走。真因: 可达域被误记的「被挡」
# 压塌后, 门邻格的 bfs_path 全部返回 None, migrate_probe 一次键都没按就
# return False, 而调用方照样 _mtries += 1 -> 10 次"重试"瞬间用完 -> 进冷却。
class _StateMem(object):
    def state(self):
        return {"K": (-14, 70), "scene": 157}


def probe_bot(have_near, have_path):
    o = MemBot5.__new__(MemBot5)
    o.scene = 157
    o.map = 156
    o.blocked = set()
    o._door_fail = {}
    o.log = lambda m: None
    o.mem = _StateMem()
    o.kwalk = lambda m: ({(d[0] + da, d[1] + db)
                          for d in [(-21, 63)] for da in (-1, 0, 1)
                          for db in (-1, 0, 1)} if have_near else set())
    o.bfs_path = lambda src, dsts, allow_doors=False: (
        [(src, list(dsts)[0])] if have_path else None)
    o.walk_path = lambda p: True
    o.step = lambda k: ("blocked", (0, 0), (0, 0), 157)
    return o


b = probe_bot(have_near=True, have_path=False)      # 门外有可走格, 但全都够不到
check("门外有可走格但全部不可达 -> False", b.migrate_probe((-21, 63)) is False)
check("标记 _mig_nopath=True (一次键都没按)", b._mig_nopath is True,
      str(b._mig_nopath))
check("原因写明走不进去", "走不进去" in b._mig_why, b._mig_why)

b = probe_bot(have_near=False, have_path=False)     # 门外全是墙
b.migrate_probe((-21, 63))
check("门外无可走格也算 _mig_nopath=True", b._mig_nopath is True, "")


def tick_bot(nopath, tries=0):
    o = MemBot5.__new__(MemBot5)
    o.scene = 157
    o.migrate = True
    o.migrate_cd = 0.0
    o.MIGRATE_COOLDOWN = 600.0
    o._mtries = tries
    o.steps_since_battle = 500
    o.logs = []
    o.log = o.logs.append
    o.mem = _StateMem()
    o.pick_migrate_door = lambda cur: (-21, 63)
    o.migrate_probe = lambda door: False
    o._mig_why = "测试原因"
    o._mig_nopath = nopath
    o._door_fail = {}
    o._on_migrate_end = lambda ok: o.logs.append("__end__%s" % ok)
    return o


# 走不到门: 不该累计重试次数, 该直接收尾
t = tick_bot(nopath=True)
PM.MemBot.tick(t)
check("不可达: 重试计数不涨 (旧实现会 +1)", t._mtries == 0, str(t._mtries))
check("不可达: 直接收尾本轮 (migrate=False)", t.migrate is False, str(t.migrate))
check("不可达: 仍记该门失败次数 (换下一扇门)",
      t._door_fail.get((-21, 63)) == 1, str(t._door_fail))
check("不可达: 日志写明「未按键, 不计入重试」",
      any("不计入重试" in x for x in t.logs), str(t.logs[-1:]))
check("不可达: 进了冷却", t.migrate_cd > time.time(), "")

# 真的踩了门(4 方向都试过): 保持原来的 10 次重试语义
t = tick_bot(nopath=False)
PM.MemBot.tick(t)
check("踩过门: 重试计数 +1 (旧语义不变)", t._mtries == 1, str(t._mtries))
check("踩过门: 保持 migrate=True 继续逼近", t.migrate is True, str(t.migrate))

t = tick_bot(nopath=False, tries=9)
PM.MemBot.tick(t)
check("踩过门: 第 10 次 -> 进冷却并清零", t.migrate is False and t._mtries == 0,
      "migrate=%s mtries=%s" % (t.migrate, t._mtries))

print()
print("=" * 66)
print("第 6 节: 上一轮遗留的 mem_stop.flag 启动时要自清 (正常停机语义不变)")
print("=" * 66)
import shutil                                             # noqa: E402
import tempfile                                           # noqa: E402

_tmp = tempfile.mkdtemp(prefix="stale_stop_")
_saved_base = PM.BASE
PM.BASE = _tmp
try:
    _flag = os.path.join(_tmp, "mem_stop.flag")

    def stale_bot():
        o = MemBot5.__new__(MemBot5)
        o.logs = []
        o.log = o.logs.append
        o._t_boot = time.time()
        return o

    o = stale_bot()
    check("没有标志文件 -> 返回 False", o._clear_stale_stop() is False)

    open(_flag, "w").close()
    os.utime(_flag, (o._t_boot - 3600, o._t_boot - 3600))   # 1 小时前
    check("陈旧标志(1 小时前) -> 被清掉 (True)", o._clear_stale_stop() is True)
    check("陈旧标志 -> 文件确实没了", not os.path.exists(_flag))
    check("陈旧标志 -> 日志写明「陈旧标志」",
          any("陈旧标志" in x for x in o.logs), str(o.logs[-1:]))

    o = stale_bot()
    open(_flag, "w").close()
    os.utime(_flag, (o._t_boot + 10, o._t_boot + 10))       # 启动后才写的
    check("运行中写的标志 -> 保留 (正常停机不受影响)",
          o._clear_stale_stop() is False)
    check("运行中写的标志 -> 文件还在", os.path.exists(_flag))

    os.utime(_flag, (o._t_boot - 30, o._t_boot - 30))       # 启动前 30s (仍在宽限内)
    check("启动前 30s 的标志 (宽限 60s 内) -> 保留",
          o._clear_stale_stop() is False)
    check("宽限期 = 60s", PM.MemBot.STALE_STOP_GRACE == 60.0,
          str(PM.MemBot.STALE_STOP_GRACE))
finally:
    PM.BASE = _saved_base
    shutil.rmtree(_tmp, ignore_errors=True)

print()
print("=" * 66)
print("第 7 节: 只读诊断不许污染运行日志 (NO_LOGFILE + run_report 分段)")
print("=" * 66)
import importlib.util                                    # noqa: E402

# ① MemBot.log() 会懒加载打开 bot_log_mem.txt 追加 —— 只读诊断必须能关掉它。
#    事故背景: pilot_probe/door_audit/dryrun5 一实例化 + enter_scene, 就会往
#    **正在跑的机器人**的日志里塞一整块启动头部 (含 run_report 用来定位
#    "运行段"的「[v13] 战斗判定」横幅) -> 多出假运行段, 归因走偏。
_t7 = tempfile.mkdtemp(prefix="nolog_")
_saved_base2 = PM.BASE
PM.BASE = _t7
try:
    _lp = os.path.join(_t7, "bot_log_mem.txt")

    def _mk():
        o = PM.MemBot.__new__(PM.MemBot)                 # 不走 __init__(免开窗口)
        o._logfh = None
        return o

    o = _mk()
    o.NO_LOGFILE = True
    o.log("只读诊断输出")
    check("NO_LOGFILE=True -> stdout 照打但不建日志文件",
          not os.path.exists(_lp), str(os.path.exists(_lp)))

    o = _mk()
    o.NO_LOGFILE = False
    o.log("正常运行输出")
    try:
        o._logfh.close()
    except Exception:
        pass
    _txt = io.open(_lp, encoding="utf-8").read() if os.path.exists(_lp) else ""
    check("NO_LOGFILE=False -> 正常落盘 (机器人行为不变)",
          os.path.exists(_lp) and "正常运行输出" in _txt, repr(_txt[:40]))
    check("类默认 NO_LOGFILE=False (不能误伤机器人的正常运行)",
          PM.MemBot.NO_LOGFILE is False, str(PM.MemBot.NO_LOGFILE))
    check("只读工具都置了 NO_LOGFILE",
          all("NO_LOGFILE = True" in io.open(
              os.path.join(BASE, "tools", f), encoding="utf-8").read()
              for f in ("pilot_probe.py", "door_audit.py", "dryrun5.py")),
          "pilot_probe/door_audit/dryrun5")
finally:
    PM.BASE = _saved_base2
    shutil.rmtree(_t7, ignore_errors=True)

# ② run_report 分段: 诊断块不能被当成运行段, 也不能撑长上一段
_spec = importlib.util.spec_from_file_location(
    "_rr", os.path.join(BASE, "tools", "run_report.py"))
RR = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(RR)

BAN = " [v13] 战斗判定 = is_battle 且画面在动"
_syn = [
    "11:00:00" + BAN,                       # 0 真运行段
    "11:00:30 [心跳] 场景1 K(0,0) 步数10 胜场1",
    "11:10:00 [心跳] 场景1 K(0,0) 步数99 胜场9",
    "12:00:00" + BAN,                       # 3 诊断块 (无心跳)
    "12:00:00 [MEM] 场景1 地图2 (probe)",
    "12:00:00 [调度] 进入场景 1 ((probe))",
    "12:30:00" + BAN,                       # 6 真运行段
    "12:30:30 [心跳] 场景2 K(1,1) 步数5 胜场0",
]
_raw = [i for i, ln in enumerate(_syn) if RR.BANNER in ln]
_st = RR.find_runs(_syn)
check("诊断块不被当成运行段 (原始横幅 3 处 -> 运行段 2 处)",
      _raw == [0, 3, 6] and _st == [0, 6], "raw=%s starts=%s" % (_raw, _st))
# 结束边界必须取"下一处任意横幅", 否则诊断块的行会算进上一段
_e0 = next(b for b in _raw if b > 0)
check("段结束边界取下一处任意横幅 (诊断块不撑长上一段)",
      _e0 == 3 and RR._seg_secs(_syn, 0, _e0) == 600.0,
      "e0=%s secs=%s" % (_e0, RR._seg_secs(_syn, 0, _e0)))
check("无心跳的最后一段(时长<120s)也被跳过",
      RR.find_runs(["00:00:00" + BAN, "00:00:02 [MEM] x"]) == [], "")

print()
print("=" * 66)
print("PASS %d  FAIL %d" % (len(OK), len(BAD)))
print("=" * 66)
if BAD:
    for n in BAD:
        print("   FAIL: %s" % n)
sys.exit(1 if BAD else 0)
