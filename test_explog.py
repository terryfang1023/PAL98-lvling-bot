# -*- coding: utf-8 -*-
"""test_explog.py — 战斗收益台账 (exp_log.json) 回归测试。

覆盖:
  ① parse_snapshot: 52B (dwCash + rgPrimaryExp[6]) 解析 / 截断容错 / ★ 解析出 wLevel;
  ② total_exp: 「等级内经验」->「累计经验」换算 (升级会掉阈值, 别当回退);
  ③ ★★ snap_delta: 经验必须是"每人所得"(各槽增量最大值) —— 求和会 3 倍虚高;
     ★★ 升级 / 满级循环 / 真读档 三种"wExp 往下掉"的区分;
  ④ rate_per_hour: 时均换算 / 零与负时长;
  ⑤ GainLog: 原子写往返 / record 追加 + 200 条环形截断 / bad 计数 /
     损坏文件容错 / ★ 口径升级 (v1 求和、v2 基线相减的旧数据都被丢弃) /
     自检零副作用;
  ⑥ ★ 地址不变量: pal_bot_mem.Mem.GAIN_ADDR + 4 == bot_itemmon.EXP_ADDR,
     LEVELUPEXP_ADDR 与 itemmon 一致, GAIN_N/表长与三处定义锁死;
  ⑦ 源码级: 机器人侧"逐场累加 + 不重置基线", GUI 只读。

零副作用: 本脚本所有写盘都在 tempfile 目录; 结束时删除。
"""
import json
import os
import struct
import sys
import tempfile

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import bot_explog as EL                     # noqa: E402

OK, BAD = [], []


def check(name, cond, extra=""):
    (OK if cond else BAD).append(name)
    print(("  ok  " if cond else "FAIL  ") + name
          + (" | %s" % (extra,) if extra else ""))


TMP = tempfile.mkdtemp(prefix="test_explog_")

# 合成阈值表 (索引 = 当前等级; 索引 0 占位)。TAB 每级 1000 便于心算,
# TAB32 用真机高档位的量级 (rgLevelUpExp[83..99] 实测全是 32000, 满级后
# 引擎只扣阈值不升级 -> 必须补回)。
TAB = [0] + [1000] * 99
TAB32 = [0] + [32000] * 99


def mk_raw(cash, exps, levels=None):
    """构造 52B 快照字节。levels 缺省 = 全 1 (此时累计经验 == 等级内经验)。"""
    lv = list(levels) if levels is not None else [1] * len(exps)
    b = struct.pack("<I", cash)
    for e, l in zip(exps, lv):
        b += struct.pack("<4H", e, 0, l, 0)
    assert len(b) == EL.GAIN_N, len(b)
    return b


def snap(cash, exps, levels=None):
    """直接构造快照 dict (绕过字节层, 专测 snap_delta / total_exp)。"""
    return {"cash": cash, "exps": list(exps),
            "levels": list(levels) if levels is not None else [1] * len(exps)}


print("== 1. parse_snapshot 解析 ==")
raw = mk_raw(636773, [15242, 13725, 14373, 0, 0, 0], [99, 84, 51, 1, 1, 1])
r = EL.parse_snapshot(raw)
check("→ {cash, exps[6], levels[6]}",
      r == {"cash": 636773, "exps": [15242, 13725, 14373, 0, 0, 0],
            "levels": [99, 84, 51, 1, 1, 1]}, r)
check("★ 解析出 wLevel (换算累计经验的必需项)",
      isinstance(r, dict) and r["levels"][1] == 84)
check("字节不足 -> None", EL.parse_snapshot(raw[:-1]) is None)
check("None -> None", EL.parse_snapshot(None) is None)
check("空 -> None", EL.parse_snapshot(b"") is None)
check("多给的字节只取前 52B", EL.parse_snapshot(raw + b"\xff" * 8)["cash"] == 636773)
check("GAIN_N = 52", EL.GAIN_N == 4 + 8 * 6)
check("ROLES = 6", EL.ROLES == 6)
check("★ 口径版本 = 3", EL.VERSION == 3)
check("MAX_LEVELS = 99", EL.MAX_LEVELS == 99)

print("== 2. total_exp: 等级内经验 -> 累计经验 ==")
check("等级 1 无下界阈值 -> 原值", EL.total_exp(123, 1, TAB) == 123)
check("等级 3 (1000/级) -> 123 + 2000", EL.total_exp(123, 3, TAB) == 2123)
check("★ 缺表 -> 退回原值", EL.total_exp(123, 3, None) == 123)
check("空表 -> 退回原值", EL.total_exp(123, 3, []) == 123)
check("等级 0 -> 原值", EL.total_exp(123, 0, TAB) == 123)
check("等级超 MAX_LEVELS 被夹住",
      EL.total_exp(0, 200, TAB) == EL.total_exp(0, 99, TAB))
check("表比等级短 -> 只加已有的 (不越界)",
      EL.total_exp(10, 5, [0, 1, 2]) == 13)
check("返回值是 int", isinstance(EL.total_exp(1, 2, TAB), int))

print("== 3. snap_delta: 经验 = 每人所得 (各槽增量最大) ==")
# ★ 这一节钉死用户实测的 bug: 三人同涨 62, 旧写法 sum 得 186 (3 倍虚高)
pre = snap(600000, [100000, 98000, 99000, 0, 0, 0])
cur = snap(600120, [100062, 98062, 99062, 0, 0, 0])
check("★★ 三人在队各 +62 -> 单人 62 (不是 186)",
      EL.snap_delta(pre, cur, TAB) == (62, 120), EL.snap_delta(pre, cur, TAB))
check("★ 求和会是 186 (旧口径确实 3 倍虚高)",
      sum(cur["exps"]) - sum(pre["exps"]) == 186)
check("只有一人涨 -> 取那一人",
      EL.snap_delta(pre, snap(600000, [100062, 98000, 99000, 0, 0, 0]), TAB)
      == (62, 0))
check("真机样本 delta=[62,62,62,0,0,0] -> 62",
      EL.snap_delta(snap(644497, [19557, 18040, 18688, 0, 0, 0]),
                    snap(644617, [19619, 18102, 18750, 0, 0, 0]), TAB)
      == (62, 120))
check("零增量合法 (纯开箱那场)", EL.snap_delta(pre, pre, TAB) == (0, 0))
check("金钱倒退 -> None", EL.snap_delta(pre, snap(599999, cur["exps"]), TAB) is None)
check("金钱超上限 -> None",
      EL.snap_delta(pre, snap(pre["cash"] + EL.MAX_DELTA + 1, cur["exps"]), TAB)
      is None)
check("单槽超上限 -> None",
      EL.snap_delta(pre,
                    snap(600120, [pre["exps"][0] + EL.MAX_ONE_BATTLE + 1]
                         + cur["exps"][1:]), TAB) is None)
check("★ 某槽倒退 (另一槽在涨) -> 整场作废",
      EL.snap_delta(pre, snap(600120, [100062, 97999, 99062, 0, 0, 0]), TAB)
      is None)
check("缺快照 -> None", EL.snap_delta(None, cur, TAB) is None
      and EL.snap_delta(pre, None, TAB) is None)
check("槽数不等也不崩 (按短的那个比)",
      EL.snap_delta(pre, snap(600000, [100062]), TAB) in (None, (62, 0)))
check("★ 不传表也能用 (旧签名兼容)", EL.snap_delta(pre, cur) == (62, 120))

print("== 3b. ★★ 升级 / 满级 / 真读档: wExp 往下掉的三种区分 ==")
# 真机取证: 6 槽 wExp [20501,14328,27276,23150,21662,0], 等级 99/84/51/48/83/40。
# ① 普通升级: 900 -> 100 且 3 -> 5 级 (跨 2 级)。累计口径:
#    战前 900+2000 = 2900, 战后 100+4000 = 4100 -> 净得 1200 (真值) ✓
p1 = snap(600000, [900, 0, 0, 0, 0, 0], [3, 1, 1, 1, 1, 1])
c1 = snap(600000, [100, 0, 0, 0, 0, 0], [5, 1, 1, 1, 1, 1])
check("★ 升级 (900->100, 3->5 级) 净得 1200, 不是 -800",
      EL.snap_delta(p1, c1, TAB) == (1200, 0), EL.snap_delta(p1, c1, TAB))
check("★ 旧写法(直接相减) 会得 -800 -> 被误判成读档并清空累计",
      100 - 900 == -800)
check("★ 缺阈值表时该场退化成 -800 -> None (只丢这一场, 累计不重置)",
      EL.snap_delta(p1, c1, None) is None)
# ② 满级循环: 等级不变 (99), 引擎只扣阈值不升级 -> 补回阈值
p2 = snap(700000, [31900, 0, 0, 0, 0, 0], [99, 1, 1, 1, 1, 1])
c2 = snap(700000, [1000, 0, 0, 0, 0, 0], [99, 1, 1, 1, 1, 1])
check("★ 满级(99)只扣阈值 -> 补回得 1000+32000-31900 = 1100",
      EL.snap_delta(p2, c2, TAB32) == (1100, 0), EL.snap_delta(p2, c2, TAB32))
check("满级不变不掉 (正常 +) 不受影响",
      EL.snap_delta(snap(1, [100], [99]), snap(1, [400], [99]), TAB32)
      == (300, 0))
# ③ 真读档: 等级也掉了 -> 补不回 -> 作废 (这一场才该丢)
check("★ 真回退 (等级也掉了 = 读档) -> None",
      EL.snap_delta(snap(1, [20000], [50]), snap(1, [1000], [3]), TAB) is None)
check("等级掉但差值仍为正且未超限 -> 照常采纳",
      EL.snap_delta(snap(1, [0], [5]), snap(1, [30000], [1]), TAB)
      == (26000, 0))

print("== 4. rate_per_hour ==")
check("3600s 1000 -> 1000", EL.rate_per_hour(1000, 3600) == 1000)
check("1800s 1000 -> 2000", EL.rate_per_hour(1000, 1800) == 2000)
check("0s -> 0", EL.rate_per_hour(1000, 0) == 0)
check("负 -> 0", EL.rate_per_hour(1000, -5) == 0)
check("60s 100 -> 6000", EL.rate_per_hour(100, 60) == 6000)

print("== 5. GainLog 读写 ==")
path = os.path.join(TMP, "exp_log.json")
g = EL.GainLog(path)
check("缺文件 load -> None", g.load() is None)

last = {"n": 1, "t": "12:00:00", "exp": 62, "cash": 120}
check("record 第 1 场",
      g.record(wins=1, elapsed=30.0, t_start="2026-10-13 12:00:00",
               d_exp=62, d_cash=120, last=last))
d = g.load()
check("往返字段", d["wins"] == 1 and d["d_exp"] == 62 and d["d_cash"] == 120
      and d["elapsed"] == 30.0 and d["version"] == EL.VERSION, d)
check("★ bad 缺省 0", d["bad"] == 0)
check("battles 1 条", len(d["battles"]) == 1 and d["battles"][0] == last)
check("last 同步", d["last"] == last)

for i in range(2, 260):                    # 灌 259 场, 触发环形截断
    g.record(wins=i, elapsed=30.0 * i, t_start="x",
             d_exp=62 * i, d_cash=120 * i,
             last={"n": i, "t": "%02d:%02d:00" % (i // 60 % 24, i % 60),
                   "exp": 62, "cash": 120})
d = g.load()
check("环形截断到 200", len(d["battles"]) == EL.MAX_BATTLES)
check("新的在前", d["battles"][0]["n"] == 259 and d["battles"][1]["n"] == 258)
check("★ 累计值由调用方给 (不被 battles 影响)", d["d_exp"] == 62 * 259)

check("★ bad 计数落盘",
      g.record(wins=260, elapsed=1.0, t_start="x", d_exp=1, d_cash=1, bad=7,
               last={"n": 260, "t": "t", "exp": 1, "cash": 1})
      and g.load()["bad"] == 7)
check("bad 缺省时归零 (旧调用方不会带出脏值)",
      g.record(wins=261, elapsed=1.0, t_start="x", d_exp=1, d_cash=1,
               last={"n": 261, "t": "t", "exp": 1, "cash": 1})
      and g.load()["bad"] == 0)

with open(path, "w", encoding="utf-8") as f:
    f.write("{corrupted!!!")
check("损坏文件 load -> None", g.load() is None)
check("损坏后仍可写",
      g.record(wins=1, elapsed=1.0, t_start="x", d_exp=1, d_cash=1,
               last={"n": 1, "t": "t", "exp": 1, "cash": 1}))
check("损坏后读回", g.load()["wins"] == 1)

print("== 5b. 口径升级: v1(求和)/v2(基线相减) 的旧数据必须丢弃 ==")
for v in (1, 2):
    old = {"version": v, "t_start": "old", "elapsed": 10.0, "wins": 99,
           "d_exp": 9999, "d_cash": 999,
           "last": {"n": 99, "t": "x", "exp": 156, "cash": 96},
           "battles": [{"n": 99, "t": "x", "exp": 156, "cash": 96}]}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(old, f)
    g.record(wins=1, elapsed=1.0, t_start="new", d_exp=62, d_cash=120,
             last={"n": 1, "t": "t", "exp": 62, "cash": 120})
    d = g.load()
    check("v%d 旧 battles 被丢弃 (虚高/被清零的行不能留)" % v,
          len(d["battles"]) == 1 and d["battles"][0]["n"] == 1, d["battles"])
    check("v%d 累计值重新起算" % v, d["d_exp"] == 62 and d["d_cash"] == 120, d)
    check("v%d version 升到 3" % v, d["version"] == 3)

print("== 6. 零副作用: 临时目录隔离 ==")
check("全部写在 TMP", os.path.dirname(os.path.abspath(path)) == TMP)
# 运行目录那份 exp_log.json 是**机器人**写的 (它每赢一场就重写, 只增不删),
# 所以不能断言"文件不存在" —— 那会在机器人跑着时误报。判据改成: 它里面
# 不含有本测试写进去的独特标记 (t_start="new")。只读, 不碰。
_real = os.path.join(BASE, "exp_log.json")
_touched = False
if os.path.exists(_real):
    try:
        with open(_real, encoding="utf-8") as f:
            _touched = '"t_start":"new"' in f.read()
    except Exception:
        pass
check("★ 运行目录的 exp_log.json 不是本测试写的 (机器人并发写不算)",
      not _touched, _real)
check("GainLog() 默认仍落运行目录 (常量没被测试改写)",
      os.path.dirname(os.path.abspath(EL.EXPLOG_PATH)) == BASE)

print("== 7. ★ 地址不变量 (多处定义锁死) ==")
import bot_itemmon as IM                   # noqa: E402
import pal_bot_mem as PM                   # noqa: E402

check("Mem.GAIN_ADDR == bot_itemmon.MONEY_ADDR",
      PM.Mem.GAIN_ADDR == IM.MONEY_ADDR,
      "0x%08X vs 0x%08X" % (PM.Mem.GAIN_ADDR, IM.MONEY_ADDR))
check("MONEY_ADDR == EXP_ADDR - 4", IM.MONEY_ADDR == IM.EXP_ADDR - 4,
      "0x%08X / 0x%08X" % (IM.MONEY_ADDR, IM.EXP_ADDR))
check("Mem.GAIN_N == bot_explog.GAIN_N", PM.Mem.GAIN_N == EL.GAIN_N)
check("★ Mem.LEVELUPEXP_ADDR == bot_itemmon.LEVELUPEXP_ADDR",
      PM.Mem.LEVELUPEXP_ADDR == IM.LEVELUPEXP_ADDR,
      "0x%08X vs 0x%08X" % (PM.Mem.LEVELUPEXP_ADDR, IM.LEVELUPEXP_ADDR))
check("LEVELUPEXP_ADDR == viewport - 0x1A24",
      IM.LEVELUPEXP_ADDR == IM.VIEWPORT_ADDR - 0x1A24)
check("★ Mem.LEVELUPEXP_N == bot_itemmon.MAX_LEVELS (阈值表 100 条)",
      PM.Mem.LEVELUPEXP_N == IM.MAX_LEVELS)
check("parse_snapshot 与 itemmon 的口径一致 (8B 步长)",
      EL.parse_snapshot(mk_raw(1, [7, 0, 0, 0, 0, 0]))["exps"]
      == [7, 0, 0, 0, 0, 0])
_msrc = open(os.path.join(BASE, "pal_bot_mem.py"), encoding="utf-8").read()
check("Mem.read_gain 用的是 parse_snapshot (不是旧的求和)",
      "parse_snapshot" in _msrc)
check("★ Mem 里有 levelup_table (阈值表读取入口)",
      "def levelup_table" in _msrc)

print("== 8. mem5 挂钩存在性 (源码级) ==")
src5 = open(os.path.join(BASE, "pal_bot_mem5.py"), encoding="utf-8").read()
check("fight 开头拍战前快照", "self._gain_pre = self.mem.read_gain()" in src5)
check("_note_win 链路挂了 _gain_record", "self._gain_record()" in src5)
check("★ 传阈值表给 snap_delta", "table=tab" in src5)
check("★ 脏数据只跳过本场, 累计不重置", "累计不重置" in src5)
check("★ 已彻底移除基线法 (_gain_base)", "_gain_base" not in src5)
check("★ 会话累计经验逐场累加",
      "_gain_exp_tot" in src5 and "+ max(0, d_exp)" in src5)
check("★ 会话累计金钱逐场累加",
      "_gain_cash_tot" in src5 and "+ max(0, d_cash)" in src5)
check("bad 计数上报", 'bad=getattr(self, "_gain_bad", 0)' in src5)
check("机器人侧用 GainLog.record", "gain_log.record(" in src5)
check("★ 启动时读阈值表 (读不到可惰性重读)", "levelup_table" in src5)
srcg = open(os.path.join(BASE, "bot_gui.py"), encoding="utf-8").read()
check("GUI 只读 (load) 不写", "gain_log.record" not in srcg
      and ".load()" in srcg)
check("GUI 左栏有树表", "tree_gain" in srcg)
check("★ GUI 显示跳过场次 (时均偏低的线索)",
      '"bad"' in srcg and "跳过 %d 场" in srcg)

print("== 9. ★ 全仓库不再有旧口径的残留 ==")
bad = []
for fn in ("bot_explog.py", "pal_bot_mem.py", "pal_bot_mem5.py", "bot_gui.py"):
    s = open(os.path.join(BASE, fn), encoding="utf-8").read()
    for kw in ("sum_gain", "calc_delta", "parse_gain"):
        if kw in s:
            bad.append("%s: %s" % (fn, kw))
check("旧函数名 sum_gain / calc_delta / parse_gain 已全部清除", not bad, bad)

print("\n结果: %d ok / %d FAIL" % (len(OK), len(BAD)))
if BAD:
    for b in BAD:
        print("  FAIL:", b)
    sys.exit(1)
