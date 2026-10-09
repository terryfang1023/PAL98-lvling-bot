# -*- coding: utf-8 -*-
"""回归测试: 要素台账 + 总胜场拆分 + 离线要素清单 (v19.0, 2026-10-08)。

用户三个诉求:
  1. "游戏里对每张地图所有的怪点/宝箱点/可采集物 是否有编号、名字?"
     -> 引擎**没有**编号/名字, 由 tools/build_scene_info.py 从**离线 SSS.MKF**
        派生: id = 事件对象全局序号 (1..5077), 编号 = `场景号-序号`;
        怪点名字 = 0x0007 队伍号 -> DATA.MKF#2 -> 敌人对象ID -> WORD.DAT;
        拾取点名字 = 0x001F 物品ID -> WORD.DAT。
  2. "想让机器人对每次战斗/开箱/采集有记录, 最好通过编号, 这样能知道整个地图
     打了哪些怪点、打了多少、哪些没被打到过。"
     -> bot_featlog.FeatLog (feat_log.json) 按编号记; tools/feat_report.py 出
        覆盖率报告 (打过几个 / 还差哪几个)。
  3. "胜场数分两部分: 本次启动后的胜场 + 累计总胜场, 总胜场可手动清空。"
     -> bot_winlog.WinLog (win_total.json) + win_reset.flag 跨进程清空协议。

★ 为什么数据源必须是**离线 SSS.MKF** (本轮真机取证):
  宝箱打开时引擎把它的 wTriggerScript 回写成 36509 (play.c:153) ->
  活内存里再也认不出"它曾经是宝箱"。实测同一个场景 221: 9/30 生成时 22 个
  拾取点, 跑三天后只剩 9 个 (全表 1152 -> 497)。离线 SSS.MKF 是游戏原始数据,
  与「9/30 脚本被改写前」的快照逐点一致, 并且 100% 覆盖 chest_log 的 92 个
  真机开箱坐标 (离线 92/92, 活内存只有 42/92)。

本文件覆盖:
  1. bot_featlog   : 编号编解码 / 记账累加 / 查询 / 清空 / 上限 / 坏数据容错
  2. bot_winlog    : 累计读写 / 清空协议 (跨进程) / 原子写 / 坏数据容错
  3. 胜场拆分链路  : 源码级断言 (只有 battles_won 真涨才记 / GUI 解析 / 两个按钮)
  4. build_scene_info 纯函数: parse_enemy_teams / team_label / walk_ops /
                     scene_ranges (含场景 296..300 空表项保护) / classify / stats
  5. feat_report   : 台账与清单索引合并 (编号优先, 坐标兜底)
  6. 真机清单抽查  : scene_info.json 有编号、关键场景要素数正确、
                     chest_log 指纹 100% 覆盖 (文件不在则跳过)
  7. 配置 / GUI    : feat_log 开关三层优先级 + 界面控件存在
  8. 零副作用      : 全程不碰运行标志/配置文件 (跑完比对 mtime+size)

不启动游戏、不按键、不碰任何运行标志。
"""
import json
import os
import struct
import sys
import tempfile

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "tools"))

import botcfg                                                  # noqa: E402
import bot_featlog as FL                                       # noqa: E402
import bot_winlog as WL                                        # noqa: E402
import bot_chestlog as CL                                      # noqa: E402
import build_scene_info as B                                   # noqa: E402
import feat_report as RPT                                      # noqa: E402

OK = []
BAD = []
TMP = tempfile.mkdtemp(prefix="featlog_test_")
_N = [0]


def check(name, cond, extra=""):
    (OK if cond else BAD).append(name)
    print("  %s %s %s" % ("PASS" if cond else "FAIL", name, extra))


def tpath(name):
    _N[0] += 1
    return os.path.join(TMP, "%02d_%s" % (_N[0], name))


def new_featlog():
    return FL.FeatLog(tpath("feat.json"))


def new_winlog():
    p = tpath("win.json")
    return WL.WinLog(path=p, flag=p + ".flag"), p


# ----------------------------------------------------------------------
# 0. 零副作用快照 (跑完再比一次)
# ----------------------------------------------------------------------
WATCH = ["mem_stop.flag", "bot_config.json", "win_total.json", "win_reset.flag",
         "feat_log.json", "chest_log.json", "scene_info.json"]


def snap():
    out = {}
    for f in WATCH:
        p = os.path.join(BASE, f)
        try:
            st = os.stat(p)
            out[f] = (st.st_size, int(st.st_mtime))
        except OSError:
            out[f] = None
    return out


BEFORE = snap()


# ======================================================================
# 1. bot_featlog
# ======================================================================
print("\n[1] bot_featlog —— 编号 + 台账")

check("code 补零成 4 位", FL.code(221, 3813) == "221-3813")
check("code 小序号也补零", FL.code(2, 28) == "2-0028")
check("code 非法参数 -> 空串",
      FL.code(None, 1) == "" and FL.code("x", 1) == "" and FL.code(1, None) == "")
check("parse_code 往返", FL.parse_code(FL.code(221, 3813)) == (221, 3813))
check("parse_code 坏数据 -> None",
      FL.parse_code("abc") is None and FL.parse_code("") is None
      and FL.parse_code(None) is None)
check("norm_scene 规范化", FL.norm_scene("221") == "221"
      and FL.norm_scene(221) == "221" and FL.norm_scene("x") is None)
check("norm_oid 拒绝非正数",
      FL.norm_oid(0) is None and FL.norm_oid(-3) is None
      and FL.norm_oid("3813") == "3813")
check("KINDS 三档都在", FL.KINDS == ("mon", "pick", "gather"))

fl = new_featlog()
check("新台账为空", fl.count() == 0 and fl.total_marks() == 0)
e = fl.mark(221, 3813, kind="mon", K=[-33, 94], name="小毒蝎×3 / 五毒巨蝎",
            when="2026-10-08 12:00:00")
check("mark 建立条目", e and e["n"] == 1 and e["k"] == "mon"
      and e["K"] == [-33, 94] and e["nm"] == "小毒蝎×3 / 五毒巨蝎")
check("mark 后可查", fl.has(221, 3813) and fl.hits(221, 3813) == 1)
check("字符串场景/序号等价", fl.has("221", "3813"))
e2 = fl.mark(221, 3813, K=[-34, 95], name="小毒蝎×3", when="2026-10-08 12:05:00")
check("重复命中累加 n", e2["n"] == 2)
check("重复命中刷新 K/名字/时间",
      e2["K"] == [-34, 95] and e2["nm"] == "小毒蝎×3"
      and e2["t"] == "2026-10-08 12:05:00")
check("非法要素不记", fl.mark(221, 0) is None and fl.mark(None, 3) is None)
check("未知类型回落 mon", fl.mark(221, 3900, kind="zzz")["k"] == "mon")
check("pick/gather 类型可用",
      fl.mark(221, 3799, kind="pick", name="吸星锁")["k"] == "pick"
      and fl.mark(221, 3800, kind="gather")["k"] == "gather")
check("count/total_marks", fl.count() == 4 and fl.total_marks() == 5)
check("keys()", (221, 3813) in fl.keys() and (221, 3900) in fl.keys())

rows = fl.rows()
check("rows 按场景+序号排序",
      [r["oid"] for r in rows] == sorted(r["oid"] for r in rows))
r0 = [r for r in rows if r["oid"] == 3813][0]
check("rows 带编号/K文本/类型中文",
      r0["code"] == "221-3813" and r0["Ktxt"] == "(-34,95)"
      and r0["kind_txt"] == "怪点" and r0["n"] == 2)
check("rows 的 pick 类型中文",
      [r for r in rows if r["oid"] == 3799][0]["kind_txt"] == "拾取")
st = fl.stat()
check("stat 汇总", st["n_feat"] == 4 and st["n_marks"] == 5
      and st["n_mon"] == 2 and st["n_scene"] == 1)
check("summary 可读", "已记 4 个要素" in fl.summary()
      and "累计命中 5 次" in fl.summary())

fl2 = FL.FeatLog(fl.path)
check("落盘后可重读", fl2.count() == 4 and fl2.hits(221, 3813) == 2)

check("forget 删一条", fl.forget(221, 3800) and not fl.has(221, 3800))
check("forget 不存在的 -> False", fl.forget(221, 99999) is False)
check("clear 清空并返回条数", fl.clear() == 3 and fl.count() == 0)

# ---- 坏数据容错 ----
p_bad = tpath("bad.json")
with open(p_bad, "w", encoding="utf-8") as f:
    f.write("{ this is not json")
check("损坏 JSON -> 当空台账, 不抛异常", FL.FeatLog(p_bad).count() == 0)

p_bad2 = tpath("bad2.json")
with open(p_bad2, "w", encoding="utf-8") as f:
    json.dump({"version": 1, "feats": [1, 2, 3]}, f)
check("feats 不是 dict -> 当空台账", FL.FeatLog(p_bad2).count() == 0)

p_bad3 = tpath("bad3.json")
with open(p_bad3, "w", encoding="utf-8") as f:
    json.dump({"version": 1, "feats": {
        "221": {"3813": {"n": "七", "K": [1], "k": "???"},
                "xx": {"n": 3},
                "0": {"n": 3}}}}, f)
flb = FL.FeatLog(p_bad3)
check("坏 n -> 回落 1", flb.hits(221, 3813) == 1)
check("坏 K 长度 -> None", flb.get(221, 3813)["K"] is None)
check("坏类型 -> mon", flb.get(221, 3813)["k"] == "mon")
check("坏序号/0 号被丢掉", not flb.has(221, "xx") and not flb.has(221, 0)
      and flb.count() == 1)

# ---- 上限护栏 ----
flp = new_featlog()
for i in range(1, 21):
    flp.mark(5, i, when="2026-10-08 12:00:%02d" % (i % 60))
check("先造 20 条", flp.count() == 20)
flp.prune(cap=8)
check("prune 丢最旧、留 8 条", flp.count() == 8)
check("prune 留下的是时间最新的",
      flp.has(5, 20) and flp.has(5, 19) and not flp.has(5, 1))


# ======================================================================
# 2. bot_winlog —— 累计总胜场 + 清空协议
# ======================================================================
print("\n[2] bot_winlog —— 累计总胜场 / 清空协议")

wl, wp = new_winlog()
check("文件不存在 -> 0", wl.total() == 0 and wl.stamp() == "")
check("初始没有清空请求", wl.pending_reset() is False)
check("save_total 写入", wl.save_total(1057) and wl.total() == 1057)
check("stamp 有时间戳", len(wl.stamp()) >= 10)
check("add 累加", wl.add(1) == 1058 and wl.add(3) == 1061)
check("save_total 负数归 0", wl.save_total(-5) and wl.total() == 0)
check("save_total 坏值 -> False", wl.save_total("x") is False)

wl.save_total(100)
check("request_reset 建标志", wl.request_reset() and wl.pending_reset())
check("有待清空时 total 还是旧值", wl.total() == 100)
check("★ 下一个胜场先兑现清空再 +1", wl.add(1) == 1)
check("★ 兑现后标志被删", wl.pending_reset() is False)
check("★ 之后再累加正常", wl.add(1) == 2)

wl.save_total(50)
check("reset() 直接清 0", wl.reset() == 0 and wl.total() == 0)
wl.save_total(9)
wl.request_reset()
wl.reset()
check("reset() 顺手清掉待清空标志",
      wl.total() == 0 and wl.pending_reset() is False)
check("clear_request 无标志时 -> False", wl.clear_request() is False)

p_bad = tpath("winbad.json")
with open(p_bad, "w", encoding="utf-8") as f:
    f.write("not json at all")
check("损坏文件 -> 0", WL.WinLog(path=p_bad).total() == 0)
with open(p_bad, "w", encoding="utf-8") as f:
    json.dump({"total": -7}, f)
check("负数 -> 0", WL.WinLog(path=p_bad).total() == 0)
with open(p_bad, "w", encoding="utf-8") as f:
    json.dump({"total": "888"}, f)
check("字符串数字也认", WL.WinLog(path=p_bad).total() == 888)

d_bad = os.path.join(TMP, "no_such_dir_x", "a", "w.json")
check("写不进去的路径 -> False 不抛异常",
      WL.WinLog(path=d_bad).save_total(1) is False)
check("原子写没留下临时文件",
      not [n for n in os.listdir(TMP) if n.startswith(".tmpwin_")])


# ======================================================================
# 3. 胜场拆分链路 (源码级断言)
# ======================================================================
print("\n[3] 胜场拆分链路")

m5 = open(os.path.join(BASE, "pal_bot_mem5.py"), encoding="utf-8").read()
gui = open(os.path.join(BASE, "bot_gui.py"), encoding="utf-8").read()
check("fight 里先取 won0 快照", "won0 = getattr(self, \"battles_won\", 0)" in m5)
check("★ 只有 battles_won 真的涨了才记胜场",
      "getattr(self, \"battles_won\", 0) > won0" in m5)
check("胜场同时记台账 + 累计总胜场",
      "_note_win" in m5 and "wl.add(1)" in m5
      and 'fl.mark(sc, oid, kind="mon"' in m5)
check("★ 开箱/采集也按编号记 (kind=pick)",
      "kind=\"pick\"" in m5 and "feat_pick" in m5)
check("日志格式 `[胜场] 本次 X / 累计 Y`",
      "[胜场] 本次 %d / 累计 %s" in m5)
check("GUI 解析该格式",
      r'\[胜场\]\s*本次\s*(\d+)\s*/\s*累计\s*(\d+)' in gui)
check("GUI 不再把「战斗结束 #n」当胜场",
      "战斗结束\\]\\s*#" not in gui)
check("GUI 底部有「本次胜场」与「累计总胜场」两个显示位",
      "本次胜场" in gui and "累计总胜场" in gui
      and "v_wins_local" in gui and "v_wins_total" in gui)
check("GUI 有「清空累计胜场」按钮", "清空累计胜场" in gui
      and "def wins_reset" in gui)
check("GUI 跑着时走 win_reset.flag 协议",
      "request_reset()" in gui and "pending_reset()" in gui)
check("GUI 待清空时有提示", "(待清空)" in gui)
check("GUI 没跑时先确认再直接清", "askyesno" in gui and "wl.reset()" in gui)
check("GUI 有「要素覆盖率」按钮 + 工具入口",
      "要素覆盖率" in gui and "feat_report.py" in gui
      and "def feat_report" in gui)


# ======================================================================
# 4. build_scene_info 纯函数
# ======================================================================
print("\n[4] build_scene_info 纯函数")

# ---- parse_enemy_teams ----
raw = struct.pack("<5H", 3, 3, 0xFFFF, 0, 7)
teams = B.parse_enemy_teams(raw + struct.pack("<5H", 0xFFFF, 0, 0, 0xFFFF, 0))
check("parse_enemy_teams 过滤空槽", teams[0] == [3, 3, 7])
check("parse_enemy_teams 全空的队伍不入表", 1 not in teams)
check("parse_enemy_teams 空输入 -> {}", B.parse_enemy_teams(b"") == {})

# ---- team_label ----
nm = {3: "史莱姆", 7: "蛹"}
check("team_label 同名合并计数",
      B.team_label([3, 3, 7], nm) == "史莱姆×2 / 蛹")
check("team_label 未知名字兜底",
      B.team_label([999], nm) == "敌0x3E7")
check("team_label 空 -> 空串", B.team_label([], nm) == ""
      and B.team_label(None, nm) == "")
check("team_label 超长截断",
      B.team_label([3, 7, 1, 2, 4, 5, 6, 8], nm).endswith("…"))

# ---- walk_ops ----
SC = [None] * 64
for i in range(len(SC)):
    SC[i] = (0x0000, 0, 0, 0)
SC[1] = (0x0007, 5, 0, 0); SC[2] = (0x0000, 0, 0, 0)          # 直线打怪
SC[3] = (0x001F, 152, 0, 0); SC[4] = (0x0000, 0, 0, 0)        # 直线加物品
SC[9] = (0x0003, 20, 0, 0)                                    # JUMP -> 20
SC[20] = (0x001F, 100, 0, 0); SC[21] = (0x0000, 0, 0, 0)
SC[10] = (0x0004, 30, 0, 0); SC[11] = (0x0000, 0, 0, 0)       # CALL 30
SC[30] = (0x0007, 7, 0, 0); SC[31] = (0x0000, 0, 0, 0)
SC[12] = (0x000A, 40, 0, 0); SC[13] = (0x0000, 0, 0, 0)       # GOTOIFNO 40
SC[40] = (0x001F, 200, 0, 0); SC[41] = (0x0000, 0, 0, 0)
SC[14] = (0x0006, 50, 0, 0); SC[15] = (0x0000, 0, 0, 0)       # RANDJUMP 50
SC[50] = (0xFFFF, 0, 0, 0); SC[51] = (0x0000, 0, 0, 0)
SC[16] = (0x0001, 0, 0, 0); SC[17] = (0x0002, 99, 0, 0)       # STOP_NEXT
SC[18] = (0x001F, 9, 0, 0)
SC[60] = (0x0003, 60, 0, 0)                                   # 自跳死循环
WANT = {B.OP_BATTLE, B.OP_GIVEITEM, B.OP_CHANGESCENE, B.OP_TEXT, B.OP_CHASE}

h = B.walk_ops(SC, len(SC), 1, WANT)
check("walk_ops 直线命中 START_BATTLE", B.OP_BATTLE in h and h[B.OP_BATTLE][0] == 5)
check("walk_ops 不把没走的指令算进来", B.OP_GIVEITEM not in h
      and B.OP_CHANGESCENE not in h)
h3 = B.walk_ops(SC, len(SC), 3, WANT)
check("walk_ops 直线命中 ADD_ITEM", B.OP_GIVEITEM in h3 and h3[B.OP_GIVEITEM][0] == 152)
check("walk_ops 跟随 JUMP", B.walk_ops(SC, len(SC), 9, WANT)[B.OP_GIVEITEM][0] == 100)
check("walk_ops 跟进 CALL",
      B.walk_ops(SC, len(SC), 10, WANT)[B.OP_BATTLE][0] == 7)
check("walk_ops 跟进条件跳转",
      B.walk_ops(SC, len(SC), 12, WANT)[B.OP_GIVEITEM][0] == 200)
check("walk_ops 跟进概率跳转",
      B.walk_ops(SC, len(SC), 14, WANT)[B.OP_TEXT] is not None)
check("walk_ops STOP_ENTRY 截断",
      B.OP_GIVEITEM not in B.walk_ops(SC, len(SC), 16, WANT))
check("walk_ops 只留第一次遇到的操作数",
      B.walk_ops(SC, len(SC), 10, WANT)[B.OP_BATTLE][0] == 7)
check("walk_ops 环不会死循环",
      B.walk_ops(SC, len(SC), 60, WANT) == {})
check("walk_ops 越界 entry -> 空", B.walk_ops(SC, len(SC), 0, WANT) == {}
      and B.walk_ops(SC, len(SC), 9999, WANT) == {})
BIG = [(0x0001, 0, 0, 0)] * 500 + [(0x0007, 1, 0, 0)]
check("walk_ops 有节点上限 (maxnodes)",
      B.OP_BATTLE not in B.walk_ops(BIG, len(BIG), 0, WANT, maxnodes=10))

# ---- kof ----
check("kof 与 pal_bot_mem5 同式",
      B.kof(1344, 800) == ((1344 - 1600) // 32, (1344 + 1600 - 32) // 32))

# ---- scene_ranges ----
scenes = ([(99, 0, 0, 0), (12, 0, 0, 3), (13, 0, 0, 5), (14, 0, 0, 6),
           (15, 0, 0, 7)] + [(0, 0, 0, 0)] * 295)
rng = B.scene_ranges(scenes, 7)
check("scene_ranges 1-based 闭区间", rng[1] == (1, 3) and rng[2] == (4, 5)
      and rng[3] == (6, 6) and rng[4] == (7, 7))
check("scene_ranges 空表项被单调性挡掉", 5 not in rng and 300 not in rng)
sc_bad = [(0, 0, 0, 0), (0, 0, 0, 5), (0, 0, 0, 0), (0, 0, 0, 0)] + \
         [(0, 0, 0, 0)] * 296
check("scene_ranges 后面全 0 的表项不漏进最后一个场景",
      list(B.scene_ranges(sc_bad, 5).items()) == [(1, (1, 5))])

# ---- classify ----
def mkobj(spr=0, mode=5, state=2, trig=0, auto=0, x=0, y=0):
    o = dict.fromkeys(B.OBJ_FIELDS, 0)
    o["wSpriteNum"] = spr
    o["wTriggerMode"] = mode
    o["sState"] = state
    o["wTriggerScript"] = trig
    o["wAutoScript"] = auto
    o["x"] = x
    o["y"] = y
    o["nSpriteFrames"] = 1
    return o


SC2 = [None] * 64
for i in range(len(SC2)):
    SC2[i] = (0x0000, 0, 0, 0)
SC2[1] = (0x0007, 5, 0, 0)     # 怪
SC2[3] = (0x001F, 152, 0, 0)   # 拾取
SC2[5] = (0x0059, 10, 0, 0)    # 换景
SC2[7] = (0xFFFF, 1, 0, 0)     # 文本
objs = [mkobj(spr=10, mode=5, trig=1, x=1344, y=800),
        mkobj(spr=10, mode=1, trig=3, x=640, y=496),
        mkobj(spr=0, mode=1, trig=5, x=100, y=100),
        mkobj(spr=0, mode=5, trig=5, x=200, y=200),
        mkobj(spr=0, mode=0, trig=7, x=300, y=300),
        mkobj(spr=10, mode=5, trig=1, x=400, y=400),
        mkobj(spr=0, mode=0, state=0, trig=0, auto=0, x=0, y=0)]
per = B.classify(objs, scenes, SC2, len(SC2), 7,
                 teams={5: [3, 3, 7]}, names={3: "史莱姆", 7: "蛹", 152: "傀儡虫"})
perk = B.classify(objs, scenes, SC2, len(SC2), 7,
                  teams={5: [3, 3, 7]},
                  names={3: "史莱姆", 7: "蛹", 152: "傀儡虫"},
                  keep_empty=True)
check("classify 场景1 有 1 怪 1 拾 1 绳",
      len(per[1]["monsters"]) == 1 and len(per[1]["chests"]) == 1
      and len(per[1]["ropes"]) == 1)
check("classify 怪点带编号/队伍/名字",
      per[1]["monsters"][0]["id"] == 1
      and per[1]["monsters"][0]["team"] == 5
      and per[1]["monsters"][0]["ename"] == "史莱姆×2 / 蛹")
check("classify 拾取点带物品ID/名字",
      per[1]["chests"][0]["id"] == 2 and per[1]["chests"][0]["item"] == 152
      and per[1]["chests"][0]["iname"] == "傀儡虫")
check("classify 绳子的 K 从坐标算出",
      tuple(per[1]["ropes"][0]["K"]) == B.kof(100, 100))
check("classify 搜索触发换景 = 绳子, 走近触发 = 传送",
      len(perk[2]["ropes"]) == 0 and len(perk[2]["portals"]) == 1)
check("classify 石碑归 signs", len(perk[2]["signs"]) == 1)
check("classify 怪物点带 chase 标记",
      per[3]["monsters"][0]["chase"] is False)
check("classify 全空对象被跳过", perk[4]["n_objects"] == 1
      and not perk[4]["monsters"] and not perk[4]["chests"])
check("classify map 号取前一条场景表项",
      per[1]["map"] == 99 and perk[2]["map"] == 12)
check("★ 默认只留有怪/拾取/绳的场景 (只有门和石碑的场景不落盘)",
      2 not in per and 4 not in per)
check("classify keep_empty 会多留场景", len(perk) > len(per))
stt = B.stats(per)
check("stats 统计", stt["monsters"] == 2 and stt["chests"] == 1
      and stt["ropes"] == 1 and stt["named_mon"] == 2
      and stt["named_chest"] == 1)


# ======================================================================
# 5. feat_report 汇总
# ======================================================================
print("\n[5] feat_report —— 台账 x 清单 对账")

feat_rows = [{"scene": 221, "oid": 3813, "n": 12, "kind": "mon"},
             {"scene": 221, "oid": 3810, "n": 3, "kind": "mon"},
             {"scene": 221, "oid": 3799, "n": 1, "kind": "pick"},
             {"scene": 221, "oid": 0, "n": 7, "kind": "mon", "K": [-99, 99]}]
chest_rows = [{"scene": 221, "a": -74, "b": 112, "n": 1, "item": "鬼头杖",
               "oid": 3789},
              {"scene": 221, "a": -17, "b": 61, "n": 2, "item": "鬼牙刀",
               "oid": 0}]
by_oid, by_K, by_coid, by_Kmon = RPT.build_index(feat_rows, chest_rows)
check("build_index by_oid 取次数", by_oid[(221, 3813)] == 12)
check("build_index by_K 取坐标", by_K[(221, -74, 112)] == (1, "鬼头杖"))
check("build_index by_coid 只收带 oid 的", by_coid.get((221, 3789))[1] == "鬼头杖"
      and (221, 0) not in by_coid and (221, 3800) not in by_coid)
check("build_index by_Kmon 收按坐标记的要素", by_Kmon[(221, -99, 99)] == 7)

info = {"map": 218,
        "monsters": [{"id": 3813, "K": [-33, 94], "ename": "小毒蝎×3"},
                     {"id": 3810, "K": [2, 67], "ename": "土蜘蛛"},
                     {"id": 3812, "K": [-14, 96], "ename": "五毒巨蛇"}],
        "chests": [{"id": 3789, "K": [-74, 112], "iname": "鬼头杖"},
                   {"id": 3790, "K": [-42, 132], "iname": "凤凰羽毛"},
                   {"id": 3799, "K": [-8, 89], "iname": "吸星锁"}]}
rows = RPT.scene_rows(221, info, by_oid, by_K, by_coid, by_Kmon)
mons = [r for r in rows if r["kind"] == "mon"]
picks = [r for r in rows if r["kind"] == "pick"]
check("mon 行按编号对账",
      [r for r in mons if r["oid"] == 3813][0]["n"] == 12
      and [r for r in mons if r["oid"] == 3813][0]["src"] == "编号")
check("mon 没打过的 n=0", [r for r in mons if r["oid"] == 3812][0]["n"] == 0)
check("mon 行带编号字符串与名字",
      mons[0]["code"] == "221-3813" and mons[0]["name"] == "小毒蝎×3")
check("pick 行优先按编号对账",
      [r for r in picks if r["oid"] == 3799][0]["n"] == 1)
check("pick 行按坐标兜底 (没有 oid 的老记录)",
      [r for r in picks if r["oid"] == 3790][0]["n"] == 0
      and [r for r in picks if r["oid"] == 3789][0]["src"] == "编号")
check("mon 老记录按坐标兜底 (编号查不到时)",
      RPT.scene_rows(221, {"monsters": [{"id": 99999, "K": [-99, 99],
                                         "ename": "?"}]},
                     by_oid, by_K, by_coid, by_Kmon)[0]["n"] == 7)
check("★ build_index 契约: 4 个索引, by_oid 键是 2 元组",
      isinstance(RPT.build_index([], []), tuple)
      and len(RPT.build_index([], [])) == 4)


# ======================================================================
# 6. 真机清单抽查 (文件不在就跳过)
# ======================================================================
print("\n[6] 真机清单抽查")

SC_INFO = os.path.join(BASE, "scene_info.json")
GAME = r"D:\Fang\Game\PAL_98"
try:
    si = json.load(open(SC_INFO, encoding="utf-8"))
except Exception:
    si = None
if not si:
    print("  SKIP scene_info.json 不存在")
else:
    cnt = B.stats(si)
    check("清单有编号 (怪点全部带 id)",
          cnt["monsters"] > 0 and cnt["named_mon"] == cnt["monsters"],
          "怪点 %d" % cnt["monsters"])
    check("清单名字覆盖率高",
          cnt["named_chest"] >= cnt["chests"] * 0.95,
          "拾取有名 %d/%d" % (cnt["named_chest"], cnt["chests"]))
    check("场景 300 不在清单里 (旧版重复统计 bug)",
          "300" not in si)
    check("场景 221: 13 个怪点 / 22 个拾取点",
          len(si.get("221", {}).get("monsters", [])) == 13
          and len(si.get("221", {}).get("chests", [])) == 22)
    check("场景 221 怪点编号连续 3810..3822",
          sorted(m["id"] for m in si["221"]["monsters"]) ==
          list(range(3810, 3823)))
    check("场景 221 有 3 个黑洞 (spr=0 mode=5, 含 K(12,68))",
          len(si["221"]["portals"]) == 3
          and all(p["spr"] == 0 and p["mode"] >= 4
                  for p in si["221"]["portals"])
          and any(tuple(p["K"]) == (12, 68) for p in si["221"]["portals"]))
    check("首尾怪点名字是中文敌名",
          si["221"]["monsters"][0]["ename"] and
          any("\u4e00" <= c <= "\u9fff" for c in si["221"]["monsters"][0]["ename"]))

    # ---- 离线 SSS.MKF 生成 vs 真机指纹 (chest_log) ----
    if os.path.exists(os.path.join(GAME, "SSS.MKF")):
        objs_f, scenes_f, SC_f, n_obj_f, n_sc_f = B.load_offline(GAME)
        rng_f = B.scene_ranges(scenes_f, n_obj_f)
        sm = min(rng_f)
        ks = sorted(rng_f)
        check("离线对象表 = 5077 个", n_obj_f == 5077, "%d" % n_obj_f)
        check("离线场景区间首尾相接、无重叠无空洞",
              rng_f[ks[0]][0] == 1 and rng_f[ks[-1]][1] == n_obj_f
              and all(rng_f[ks[i]][1] + 1 == rng_f[ks[i + 1]][0]
                      for i in range(len(ks) - 1)),
              "共 %d 个场景, 场景号 %d..%d" % (len(ks), ks[0], ks[-1]))
        check("场景 1 是空表项 (无对象) 被跳过", 1 not in rng_f and sm == 2)
        check("场景 296..300 (空表项) 不在区间里",
              not any(s >= 296 for s in rng_f),
              "最大场景 %s" % max(rng_f))
        # 用 chest_log 的真机开箱坐标当指纹, 校验清单覆盖率
        try:
            cl = CL.ChestLog(os.path.join(BASE, "chest_log.json"))
            fps = [(int(s), (int(a), int(b)))
                   for (s, a, b) in
                   [(r["scene"], r["a"], r["b"]) for r in cl.rows()]]
        except Exception:
            fps = []
        if fps:
            off = B.classify(objs_f, scenes_f, SC_f, n_sc_f, n_obj_f,
                             names={})
            hit = 0
            for sc, K in fps:
                if any(tuple(c["K"]) == K for c in off.get(sc, {}).get("chests", [])):
                    hit += 1
            check("★ 离线清单 100% 覆盖真机开箱指纹",
                  hit == len(fps), "%d/%d" % (hit, len(fps)))
        else:
            print("  SKIP chest_log.json 无记录")


# ======================================================================
# 7. 配置 / GUI
# ======================================================================
print("\n[7] 配置 / GUI")

check("DEFAULTS 有 feat_log", botcfg.DEFAULTS.get("feat_log") is True)
check("resolve_bool('feat_log') 默认开",
      botcfg.resolve_bool("feat_log", tpath("empty_cfg.json"))[0] is True)
os.environ["PAL_FEAT_LOG"] = "0"
check("环境变量 PAL_FEAT_LOG=0 能关掉",
      botcfg.resolve_bool("feat_log", tpath("empty_cfg.json"))[0] is False)
os.environ.pop("PAL_FEAT_LOG", None)
check("env_name 正确", botcfg.env_name("feat_log") == "PAL_FEAT_LOG")
check("GUI 有「要素台账」勾选框", "要素台账 (按编号记每次战斗" in gui)
check("GUI 把 feat_log 写成环境变量",
      'env_name("feat_log")' in gui and 'botcfg.put("feat_log"' in gui)
check("GUI 启动日志带：要素台账=", "要素台账=%s" in gui)
bad = "import " + "pal_bot\n"
check("GUI 进程没有直接 import 主程序 (DPI 会缩控件)",
      bad not in gui and "import pal_bot_mem5" not in gui)


# ======================================================================
# 8. 零副作用
# ======================================================================
print("\n[8] 零副作用")
AFTER = snap()
same = [f for f in WATCH if BEFORE[f] == AFTER[f]]
diff = [f for f in WATCH if BEFORE[f] != AFTER[f]]
check("跑测试期间没有改动运行目录里的任何标志/配置文件",
      not diff, "变了: %s" % diff if diff else "%d 个文件纹丝不动" % len(same))


print()
print("=" * 70)
print("通过 %d 项, 失败 %d 项" % (len(OK), len(BAD)))
if BAD:
    for n in BAD:
        print("  FAIL:", n)
    sys.exit(1)
print("全部通过 ✅")
