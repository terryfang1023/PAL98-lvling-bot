# -*- coding: utf-8 -*-
"""test_chestlog.py — 开箱台账 (bot_chestlog.py) + mem5 接入 离线回归。

不需要游戏: ① 纯函数/持久化用临时文件直接测;
          ② open_chest 的记账路径用 shadow() 造 MemBot5 空壳 (假 g/mem/menu)。
"""
import json
import os
import struct
import sys
import tempfile

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import bot_chestlog as CL                                     # noqa: E402
import botcfg                                                 # noqa: E402
from pal_bot_mem5 import MemBot5                              # noqa: E402

OK = []
BAD = []
TMP = tempfile.mkdtemp(prefix="chestlog_test_")


def check(name, cond, extra=""):
    (OK if cond else BAD).append(name)
    print("  %s %s %s" % ("PASS" if cond else "FAIL", name, extra))


_N = [0]


def new_log(name="a.json"):
    """每次调用一个**全新**文件 (避免用例之间互相污染)。"""
    _N[0] += 1
    return CL.ChestLog(os.path.join(TMP, "%02d_%s" % (_N[0], name)))


def reopen(path):
    """重新读同一个台账文件 = 模拟机器人重启。"""
    return CL.ChestLog(path)


def raw(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class FakeMem(object):

    def __init__(self, K=(0, 0), scene=220):
        self.K = K
        self.scene = scene

    def state(self):
        return {"K": tuple(self.K), "scene": self.scene}


class FakeGame(object):
    """记录按键的假手柄; shot() 返回假画面; on_key 可挂"按键副作用"。"""

    def __init__(self, warm=0.30):
        self.keys = []
        self.warm = warm
        self.on_key = None              # 模拟: 按空格后箱子真的开了(物品到手)

    def flush_keys(self):
        pass

    def key(self, name, hold=0.0):
        self.keys.append(name)
        if self.on_key:
            self.on_key(name)

    def shot(self):
        return b"fake-frame"


class InvBox(object):
    """可变的物品栏盒子 (测试随时改 rows 模拟物品到手)。"""

    def __init__(self, rows):
        self.rows = rows
        self.calls = 0

    def entries(self):
        self.calls += 1
        return self.rows


def shadow(scene=220, K=(0, 0), memo=True, entries=None, after=None,
           warm=0.30):
    """造一个只跑 open_chest 的 MemBot5 空壳 (假 g/mem/菜单特征)。

    entries: 开箱前的物品栏行; after: 按空格后变成的行 (None = 不变化)。
    """
    o = MemBot5.__new__(MemBot5)
    o.scene = scene
    o.mem = FakeMem(K, scene)
    o.g = FakeGame(warm=warm)
    o.stat = {"mon_hit": 0, "mon_visit": 0, "chest_ok": 0, "chest_try": 0,
              "chest_empty": 0, "fallback": 0, "scene_chg": 0,
              "rope_ok": 0, "rope_try": 0}
    o.chest_cd = {}
    o.chest_try = {}
    o.chest_dead = set()
    o.chest_memo = memo
    o.chest_log = new_log("mem5_%s.json" % ("on" if memo else "off"))
    o._inv_names = {0x098: "傀儡虫", 0x076: "还魂香"}
    o._inv_reader = InvBox(entries) if entries is not None else None
    o.logs = []
    o.log = lambda m: o.logs.append(m)
    o.blocked = set()
    o.CHEST_SETTLE = 0
    o.CHEST_DLG_WAIT = 0.02         # tmax = 0.06s -> 每次采样一次, 测试秒过
    o.CHEST_TRY_N = 1               # v18.2 默认: 碰到就记 (用例可自行调大)
    o.CHEST_CD = 0
    if after is not None:
        box = o._inv_reader

        def _open(name, _b=box, _a=after):
            if name == "space":
                _b.rows = _a
        o.g.on_key = _open
    return o


# ======================================================================
print("第 1 节: 键/场景 规范化 (纯函数)")
check("kk: (a,b) -> 'a,b'", CL.kk((-53, 152)) == "-53,152")
check("parse_kk: 反解", CL.parse_kk("-53,152") == (-53, 152))
check("parse_kk: 坏数据 -> None",
      CL.parse_kk("abc") is None and CL.parse_kk("") is None
      and CL.parse_kk(None) is None and CL.parse_kk("1,2,3") is None)
check("norm_scene: int/str 都认",
      CL.norm_scene(220) == "220" and CL.norm_scene("220") == "220")
check("norm_scene: 坏值 -> None",
      CL.norm_scene("x") is None and CL.norm_scene(None) is None)
check("CHESTLOG_PATH 与 bot_config.json 同目录",
      os.path.dirname(CL.CHESTLOG_PATH) == os.path.dirname(botcfg.CONFIG_PATH))
check("默认上限是护栏值 (不是 0)",
      isinstance(CL.MAX_CHESTS, int) and CL.MAX_CHESTS >= 1000)

# ======================================================================
print()
print("第 2 节: 记录 / 查询 / 落盘")
lg = new_log()
check("空台账: count 0 且文件不存在", lg.count() == 0
      and not os.path.exists(lg.path))
e = lg.mark(220, (-53, 152), item="傀儡虫", dn=3)
check("mark: 返回条目", isinstance(e, dict) and e["n"] == 1
      and e["item"] == "傀儡虫" and e["dn"] == 3)
check("mark: 立即落盘", os.path.exists(lg.path))
check("mark: has/keys 生效",
      lg.has(220, (-53, 152)) and lg.keys() == {(220, -53, 152)})
check("mark: 场景号也用字符串可查", lg.has("220", (-53, 152)))
check("mark: 没记过的箱子 has=False",
      lg.has(220, (0, 0)) is False and lg.has(999, (-53, 152)) is False)
lg.mark(220, (1, 2), item="还魂香", dn=1)
lg.mark(185, (-8, 9))
check("count: 3 个箱子 / 2 个场景",
      lg.count() == 3 and lg.stat()["n_scene"] == 2, str(lg.stat()))
check("total_marks: 3 次", lg.total_marks() == 3)
check("summary 文本含数字", "3 个箱子" in lg.summary(), lg.summary())

check("重复 mark 同一箱 -> n 累增, 不新增条目",
      lg.mark(220, (-53, 152))["n"] == 2 and lg.count() == 3
      and lg.total_marks() == 4)
check("重复 mark 不带 item -> 之前记的开出物保留",
      lg.get(220, (-53, 152))["item"] == "傀儡虫"
      and lg.get(220, (-53, 152))["dn"] == 3)
check("get: 取回条目", lg.get(185, (-8, 9))["n"] == 1)
check("get: 不存在 -> None", lg.get(185, (0, 0)) is None)

check("mark: 场景坏值 -> None (不脏数据)", lg.mark("x", (1, 2)) is None)
check("mark: 坐标坏值 -> None",
      lg.mark(1, None) is None and lg.mark(1, ("a", 2)) is None)
check("坏 mark 没写进台账", lg.count() == 3)

rows = lg.rows()
check("rows: 按 场景->K 排序",
      [(r["scene"], r["a"], r["b"]) for r in rows]
      == [(185, -8, 9), (220, -53, 152), (220, 1, 2)],
      str([(r["scene"], r["a"], r["b"]) for r in rows]))
check("rows: K 显示成 '(a,b)'", rows[1]["K"] == "(-53,152)")
check("rows: 带时间戳", len(rows[1]["t"]) >= 16, rows[1]["t"])

check("forget: 删掉一条",
      lg.forget(185, (-8, 9)) is True and lg.count() == 2
      and not lg.has(185, (-8, 9)))
check("forget: 删不存在的 -> False", lg.forget(185, (-8, 9)) is False)

# ======================================================================
print()
print("第 3 节: 重启后还能记住 (核心诉求)")
lg2 = reopen(lg.path)                  # 新对象 = 模拟机器人重启
check("重载: 2 个箱子 (含刚才 forget 掉的已不在)", lg2.count() == 2,
      str(lg2.count()))
check("重载: keys 原样", lg2.keys() == {(220, -53, 152), (220, 1, 2)},
      str(lg2.keys()))
check("重载: item/次数 保留",
      lg2.get(220, (-53, 152))["item"] == "傀儡虫"
      and lg2.get(220, (-53, 152))["n"] == 2)
d = raw(lg2.path)
check("文件格式: version + chests 两级",
      d.get("version") == CL.VERSION and isinstance(d.get("chests"), dict)
      and set(d["chests"]) == {"220"})
check("文件格式: '场景' -> 'a,b' -> 条目",
      d["chests"]["220"]["-53,152"]["item"] == "傀儡虫")

lg3 = new_log("clear.json")
lg3.mark(1, (0, 0))
check("clear: 返回清掉的条数并落盘",
      lg3.clear() == 1 and lg3.count() == 0
      and raw(lg3.path)["chests"] == {})

# 容错: 损坏 / 结构不对 / 半条记录
bad = os.path.join(TMP, "bad.json")
with open(bad, "w", encoding="utf-8") as f:
    f.write("{ this is not json")
check("损坏文件 -> 空台账不炸", CL.ChestLog(bad).count() == 0)
with open(bad, "w", encoding="utf-8") as f:
    json.dump({"version": 1, "chests": "oops"}, f)
check("chests 不是 dict -> 空台账", CL.ChestLog(bad).count() == 0)
with open(bad, "w", encoding="utf-8") as f:
    json.dump({"chests": {"220": {"-53,152": {"t": "x", "n": "??"},
                                  "badkey": {"n": 1},
                                  "1,2": {"n": 5, "item": None}}}}, f)
b = CL.ChestLog(bad)
check("脏条目被清掉, 好条目留下",
      b.count() == 2 and b.has(220, (1, 2)) and not b.has(220, ("badkey"))
      and b.get(220, (-53, 152))["n"] == 1, str(sorted(b.keys())))
check("无 version 字段也能读", b.data["version"] == CL.VERSION)

# 上限护栏
pr = new_log("prune.json")
for i in range(12):
    pr.mark(1, (i, i))
check("prune: 未超上限不动", pr.prune(cap=50) == 0 and pr.count() == 12)
check("prune: 超上限丢最旧的",
      pr.prune(cap=5) == 7 and pr.count() == 5
      and not pr.has(1, (0, 0)) and pr.has(1, (11, 11)),
      str(sorted(pr.keys())))

# ======================================================================
print()
print("第 4 节: mem5 接入 —— open_chest 成功 -> 记账 + 本局不再去")
import pal_bot as PB                                          # noqa: E402
WARM = {"v": 0.30}                    # 假画面特征值 (0.30 = 弹出对话框)
_real_warm = PB.menu_warm
PB.menu_warm = lambda img: WARM["v"]  # 不打桩就得喂真 ndarray, 这里只测记账路径

B4 = [(0, 0x098, 5, 0)]               # 开箱前: 傀儡虫 ×5
AF = [(0, 0x098, 8, 0)]               # 开箱后: 傀儡虫 ×8
o = shadow(scene=220, K=(0, 0), entries=B4, after=AF)
o.open_chest((1, 0), "down", (0, 0))
check("成功: 计入 stat.chest_ok", o.stat["chest_ok"] == 1)
check("★ 开过的箱子立即可跳过 (本局不再去)",
      (220, 1, 0) in o.chest_dead, str(o.chest_dead))
check("★ 写进台账 (跨重启)", o.chest_log.has(220, (1, 0)))
check("★ 记下开出的东西 (物品栏差分 5->8)",
      o.chest_log.get(220, (1, 0))["item"] == "傀儡虫"
      and o.chest_log.get(220, (1, 0))["dn"] == 3
      and o.chest_log.get(220, (1, 0))["ok"] is True,
      str(o.chest_log.get(220, (1, 0))))
check("日志里有记账行",
      any("已记账" in m for m in o.logs), str(o.logs[-2:]))
check("记账行里带台账累计摘要",
      any("已记账" in m and "已记 1 个箱子" in m for m in o.logs),
      str(o.logs[-1:]))
check("按过空格 (真去开了)", "space" in o.g.keys)

# ★ 关键回归: 对话框没认出来, 但东西真的到手了 -> 也算成功 (旧实现会误记失败)
WARM["v"] = 0.0                       # 画面里没有对话框
o_w = shadow(scene=220, K=(0, 0), entries=B4, after=AF)
o_w.open_chest((6, 0), "down", (0, 0))
check("★ 画面没对话框但物品栏涨了 -> 判成功 (判据=物品到手)",
      o_w.stat["chest_ok"] == 1
      and o_w.chest_log.get(220, (6, 0))["item"] == "傀儡虫",
      str((o_w.stat["chest_ok"], o_w.chest_log.get(220, (6, 0)))))
check("成功日志写明判据",
      any("判据=物品到手" in m for m in o_w.logs), str(o_w.logs[-2:]))
check("这种箱子也进 chest_dead", (220, 6, 0) in o_w.chest_dead)
WARM["v"] = 0.30

o2 = shadow(scene=220, K=(0, 0), entries=B4, after=B4)     # 有对话框但物品没变
o2.open_chest((2, 0), "down", (0, 0))
check("开出的是空对话 (物品没变) -> 记已开但 item 为空",
      o2.chest_log.has(220, (2, 0))
      and o2.chest_log.get(220, (2, 0))["item"] == ""
      and o2.chest_log.get(220, (2, 0))["ok"] is True)

o3 = shadow(scene=220, K=(0, 0), memo=False)
o3.open_chest((3, 0), "down", (0, 0))
check("台账关: 不写文件", o3.chest_log.count() == 0)
check("台账关: 本局仍然跳过 (不受开关影响)",
      (220, 3, 0) in o3.chest_dead)
check("台账关: 连物品栏读取器都不建 (省内存读)", o3._inv_reader is None)

o4 = shadow(scene=220, K=(0, 0), entries=B4)   # 物品栏读出来是 None
o4._inv_reader = InvBox(None)
o4.open_chest((4, 0), "down", (0, 0))
check("物品栏读不到: 仍按对话框判成功, item 空",
      o4.chest_log.has(220, (4, 0))
      and o4.chest_log.get(220, (4, 0))["item"] == "")

# 失败路径: ★v18.2 用户要求「碰到就记」—— 一次没反应就记成"空/已开"
# (场景里本来就已经打开、或早就被开过的箱子, 就是这个表现)
WARM["v"] = 0.0                       # 按空格既没对话框, 物品栏也不变
o5 = shadow(scene=220, K=(0, 0), entries=B4)
o5.CHEST_TRY_N = 1                    # = 生产默认值
o5.open_chest((5, 0), "down", (0, 0))
e5 = o5.chest_log.get(220, (5, 0))
check("碰一次没反应 -> 立刻记「空/已开」(碰到就记)",
      e5 is not None and e5["ok"] is False and e5["n"] == 1, str(e5))
check("空箱也进本局黑名单", (220, 5, 0) in o5.chest_dead)
check("stat.chest_empty 计数", o5.stat["chest_empty"] == 1)
check("日志说明以后不再去",
      any("已记账(空" in m and "以后不再去" in m for m in o5.logs),
      str(o5.logs[-1:]))
check("日志明确判为「已开着/空箱」",
      any("已开着/空箱" in m for m in o5.logs), str(o5.logs))
# 想更保守: bot_config.json 把 chest_try_n 调大 -> 仍然试满才记
o6 = shadow(scene=220, K=(0, 0), entries=B4)
o6.CHEST_TRY_N = 3
for i in range(2):
    o6.open_chest((5, 0), "down", (0, 0))
check("chest_try_n=3 只试 2 次 -> 台账里还没有它", o6.chest_log.count() == 0)
check("失败进冷却/计数",
      o6.chest_try.get((220, 5, 0)) == 2
      and o6.chest_cd.get((220, 5, 0), 0) >= 0)
check("未放弃不进 chest_dead", (220, 5, 0) not in o6.chest_dead)
o6b = shadow(scene=220, K=(0, 0), entries=B4)
o6b.CHEST_TRY_N = 3
for i in range(3):
    o6b.open_chest((5, 0), "down", (0, 0))
e6 = o6b.chest_log.get(220, (5, 0))
check("chest_try_n=3 试满 3 次 -> 记 n=3",
      e6 is not None and e6["ok"] is False and e6["n"] == 3, str(e6))
WARM["v"] = 0.30
PB.menu_warm = _real_warm

check("台账摘要区分 打开/空",
      (o5.chest_log.mark(220, (7, 0), item="还魂香", dn=1) is not None)
      and "打开 1" in o5.chest_log.summary()
      and "空 1" in o5.chest_log.summary(), o5.chest_log.summary())
check("rows 带状态列",
      [r["state"] for r in o5.chest_log.rows()] == ["空/打不开", "已开"],
      str(o5.chest_log.rows()))
check("forget_empty 只清空箱, 已开的留着",
      o5.chest_log.forget_empty() == 1
      and o5.chest_log.count() == 1 and o5.chest_log.has(220, (7, 0))
      and not o5.chest_log.has(220, (5, 0)))

# ======================================================================
print()
print("第 5 节: 开局 seed (MemBot5.chest_memo_start 真代码路径)")
lg4 = new_log("seed.json")
lg4.mark(220, (-53, 152))
lg4.mark(185, (7, 8))
s1 = shadow(scene=220, K=(0, 0))
s1.chest_log = reopen(lg4.path)       # 换成一个"上次跑留下的"台账 (真读盘)
n_seed = s1.chest_memo_start()
check("seed: 返回载入条数 2", n_seed == 2, str(n_seed))
check("★ seed: 台账里的箱子进 chest_dead (重启不再白跑)",
      s1.chest_dead == {(220, -53, 152), (185, 7, 8)}, str(s1.chest_dead))
check("seed: 没记过的箱子仍会被尝试",
      (220, 0, 0) not in s1.chest_dead and (220, 1, 0) not in s1.chest_dead)
check("seed: 日志写明跳过了几个",
      any("开箱台账 = 开" in m and "2 个箱子" in m for m in s1.logs),
      str(s1.logs))
s2 = shadow(scene=220, K=(0, 0), memo=False)
s2.chest_log = lg4
check("seed: 开关关 -> 一条都不载入",
      s2.chest_memo_start() == 0 and s2.chest_dead == set())
check("seed: 关掉时也有日志说明",
      any("开箱台账 = 关" in m for m in s2.logs), str(s2.logs))
s3 = shadow(scene=220, K=(0, 0))
s3.chest_log = None                   # 台账对象坏掉
check("seed: 台账坏掉不炸 (返回 0)",
      s3.chest_memo_start() == 0
      and any("台账载入失败" in m for m in s3.logs))

print()
print("=" * 66)
print("结果: PASS %d / FAIL %d" % (len(OK), len(BAD)))
if BAD:
    print("失败项:")
    for n in BAD:
        print("  - " + n)
sys.exit(1 if BAD else 0)
