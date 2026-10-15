# -*- coding: utf-8 -*-
"""test_itemmon.py — 物品栏监视回归 (bot_itemmon.py / tools/item_scan.py)。

覆盖:
  ① rgInventory 地址锚点与结构常量 (与 pal_bot_mem.py 同源, 改动必须三处同步)
  ② parse_inventory 纯函数: 正常/截断/全空/长度不齐
  ③ WORD.DAT 名称表: 合成记录解析 + 真机文件 (存在才测) + 傀儡虫 ID 双重确认
  ④ ItemReader 逻辑: 注入假字节流验证 entries/amount/失败重开语义
  ⑤ tools/item_scan.py 与 bot_itemmon 的地址必须一致 (防止只改一处)
  ⑥ 游戏窗口判据 is_game_title: 真机游戏标题命中 / 自家 GUI 标题被排除
     (含「万一 GUI 标题混进 'Pal'」的兜底, 2026-10-12 加)

真机标定记录 (2026-10-02, 勿凭推算改动):
  - rgInventory = viewport + 0x340: 布局推算 + dump 实测 104 条目全合理
  - 傀儡虫 = 0x098: watch 真机差分 + word[0x098]="傀儡虫" 双重确认

用法:  python test_itemmon.py     (退出码 0 = 全过)
"""
import os
import struct
import sys
import tempfile

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "tools"))

import bot_itemmon as IM                     # noqa: E402

OK, BAD = [], []


def check(name, cond, extra=""):
    (OK if cond else BAD).append(name)
    print(("PASS  " if cond else "FAIL  ") + name + (("  <- " + str(extra)) if (extra and not cond) else ""))


print("=" * 66)
print("第 1 节: 地址锚点与结构常量")
print("=" * 66)
check("INV_ADDR = viewport + 0x340",
      IM.INV_ADDR == IM.VIEWPORT_ADDR + 0x340,
      hex(IM.INV_ADDR))
check("INV_ADDR == 0x00C3CC3C (真机标定值)", IM.INV_ADDR == 0x00C3CC3C,
      hex(IM.INV_ADDR))
check("VIEWPORT_ADDR 与 pal_bot_mem.READ_AT 体系同源",
      IM.VIEWPORT_ADDR == 0x00C3C8FC)
check("ENTRY 6 字节 (wItem,nAmount,nAmountInUse)", IM.ENTRY.size == 6)
check("MAX_INVENTORY == 256 (sdlpal palcommon.h)", IM.MAX_INVENTORY == 256)
check("ITEM_ID == 0x098 (傀儡虫, 真机双重确认)", IM.ITEM_ID == 0x098)

# tools/item_scan.py 必须与 bot_itemmon 同源 (只改一处 = 经典事故)
spec = open(os.path.join(BASE, "tools", "item_scan.py"),
            encoding="utf-8").read()
check("item_scan.py 的 INV_DELTA 与 bot_itemmon 一致",
      "INV_DELTA = 0x340" in spec and "INV_DELTA = 0x340" in
      open(os.path.join(BASE, "bot_itemmon.py"), encoding="utf-8").read())
check("item_scan.py 的 VIEWPORT_ADDR 一致",
      "VIEWPORT_ADDR = 0x00C3C8FC" in spec)

print()
print("=" * 66)
print("第 2 节: parse_inventory 纯函数")
print("=" * 66)
blob = struct.pack("<HHH", 0x098, 21, 0) + struct.pack("<HHH", 0x05F, 3, 1)
rows = IM.parse_inventory(blob)
check("两条非空条目", rows == [(0, 0x098, 21, 0), (1, 0x05F, 3, 1)], rows)
check("全零 -> 空列表", IM.parse_inventory(b"\x00" * 12) == [])
check("截断到半条不崩", IM.parse_inventory(blob[:-2]) == [(0, 0x098, 21, 0)])
check("空字节流 -> 空", IM.parse_inventory(b"") == [])
blob_gap = (struct.pack("<HHH", 0x098, 21, 0) + b"\x00" * 6 +
            struct.pack("<HHH", 0x05F, 3, 1))
check("中间空槽保留索引", IM.parse_inventory(blob_gap) ==
      [(0, 0x098, 21, 0), (2, 0x05F, 3, 1)])

print()
print("=" * 66)
print("第 3 节: WORD.DAT 名称表")
print("=" * 66)
TD = tempfile.mkdtemp(prefix="itemmon_test_")
fake = os.path.join(TD, "WORD.DAT")
recs = [("还魂香".encode("gbk") + b"\x00" * 10)[:10],
        ("傀儡虫".encode("gbk") + b"\x00" * 10)[:10],
        b"\x00" * 10,
        ("ABC".encode("ascii") + b"\x00" * 10)[:10]]
open(fake, "wb").write(b"".join(recs))
names = IM.load_names(fake)
check("合成词表: 3 个非空词", len(names) == 3, names)
check("word[0]=还魂香", names.get(0) == "还魂香", names.get(0))
check("word[1]=傀儡虫", names.get(1) == "傀儡虫", names.get(1))
check("全零词被跳过", 2 not in names)
check("半角词保留", names.get(3) == "ABC", names.get(3))
check("文件不存在 -> {}", IM.load_names(os.path.join(TD, "无.pth")) == {})

# 真机 WORD.DAT: 仓库目录 (BASE/../../) 与母目录 (BASE/../) 都探一下
REAL_WORD = None
for cand in (os.path.normpath(os.path.join(BASE, "..", "..", "WORD.DAT")),
             os.path.normpath(os.path.join(BASE, "..", "WORD.DAT"))):
    if os.path.exists(cand):
        REAL_WORD = cand
        break
if REAL_WORD:
    rn = IM.load_names(REAL_WORD)
    check("真机词表非空 (>500 词)", len(rn) > 500, len(rn))
    check("真机: word[0x098] == 傀儡虫", rn.get(0x098) == "傀儡虫",
          rn.get(0x098))
    check("真机: word[0x05F] == 还魂香", rn.get(0x05F) == "还魂香",
          rn.get(0x05F))
    check("真机: 傀儡虫 ID 与 ITEM_ID 一致", rn.get(IM.ITEM_ID) == "傀儡虫")
    check("自动探测也能找到真机词表", IM.load_names().get(0x098) == "傀儡虫")
else:
    print("SKIP  真机 WORD.DAT 不在本机路径 (%s)" % REAL_WORD)

print()
print("=" * 66)
print("第 4 节: ItemReader 逻辑 (注入假字节流)")
print("=" * 66)
blob_full = (blob + b"\x00" * (IM.ENTRY.size * IM.MAX_INVENTORY - len(blob)))
r = IM.ItemReader(item_id=0x098)
r.read_raw = lambda: blob_full
check("entries 两条", len(r.entries()) == 2)
check("amount(傀儡虫)=21", r.amount(0x098) == 21)
check("amount(默认 item_id)=21", r.amount() == 21)
check("没有的物品 -> 0", r.amount(0x123) == 0)
r.read_raw = lambda: None
check("读失败 -> None (不抛异常)", r.amount() is None)
r.read_raw = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
try:
    r.amount()
    check("read_raw 抛异常会传染 (应在上层捕获)", False)
except RuntimeError:
    check("read_raw 异常原样上抛 (由 GUI try/except 兜底)", True)

print()
print("=" * 66)
print("第 5 节: 人物等级/经验 (RoleReader, 2026-10-02 真机标定)")
print("=" * 66)
check("ROLES_NAME_ADDR = viewport - 0x1E00 (0x00C3AAFC)",
      IM.ROLES_NAME_ADDR == IM.VIEWPORT_ADDR - 0x1E00
      and IM.ROLES_NAME_ADDR == 0x00C3AAFC, hex(IM.ROLES_NAME_ADDR))
check("ROLES_LEVEL_ADDR = rgwName + 0x24 (0x00C3AB20)",
      IM.ROLES_LEVEL_ADDR == IM.ROLES_NAME_ADDR + 0x24
      and IM.ROLES_LEVEL_ADDR == 0x00C3AB20, hex(IM.ROLES_LEVEL_ADDR))
check("ROLES_HP/MaxHP 偏移 (rgwName+0x3C/+0x30)",
      IM.ROLES_HP_ADDR == IM.ROLES_NAME_ADDR + 0x3C
      and IM.ROLES_MAXHP_ADDR == IM.ROLES_NAME_ADDR + 0x30)
check("EXP_ADDR = viewport + 0x80 (0x00C3C97C)",
      IM.EXP_ADDR == IM.VIEWPORT_ADDR + 0x80
      and IM.EXP_ADDR == 0x00C3C97C, hex(IM.EXP_ADDR))
check("PARTY_N/ADDR = viewport + 0x0A/0x0C",
      IM.PARTY_N_ADDR == IM.VIEWPORT_ADDR + 0x0A
      and IM.PARTY_ADDR == IM.VIEWPORT_ADDR + 0x0C)
check("MAX_ROLES == 6 (本构建真机实测, master 源码是 5!)",
      IM.MAX_ROLES == 6)
check("ROLE_NAME_IDS 与静态兜底一一对应",
      [IM.role_display_name(i) for i in IM.ROLE_NAME_IDS] ==
      list(IM.ROLE_NAMES_FALLBACK))
check("role_display_name: WORD.DAT 优先",
      IM.role_display_name(0x24, {0x24: "李逍遥"}) == "李逍遥")
check("role_display_name: 未知 ID 兜底",
      IM.role_display_name(0x999).startswith("人物"))

# parse_party: [wMaxPartyMemberIndex] + rgParty[6]x10B
party_raw = struct.pack("<H", 2) + b"".join(
    struct.pack("<H", r) + b"\x00" * 8 for r in (1, 0, 4, 0, 0, 0))
check("parse_party: 3 人 [1,0,4]", IM.parse_party(party_raw) == [1, 0, 4])
check("parse_party: nmax 越界护栏",
      IM.parse_party(struct.pack("<H", 200) + party_raw[2:]) == [])
check("parse_party: 空数据", IM.parse_party(b"") == [])
bad_role = struct.pack("<H", 1) + struct.pack("<H", 777) + b"\x00" * 8
check("parse_party: 角色序号越界剔除", IM.parse_party(bad_role) == [])

# parse_roles: 名字/等级/经验/(HP/MaxHP) 数组 -> 行
# ★ 经验是 EXPERIENCE 结构, 每条 8B {wExp, wReserved, wLevel, wCount};
#   2026-10-02 之前按 2B 步长读 → 灵儿读到逍遥的 wReserved(恒 0)
def pack_exp(vals, levels):
    return b"".join(struct.pack("<4H", e, 0, levels[i], 0)
                    for i, e in enumerate(vals))


check("EXP_STRIDE 钉死 8 (EXPERIENCE = 4 个 WORD)",
      IM.EXP_STRIDE == 8 and IM.EXP_LEVEL_OFF == 4)
names = struct.pack("<6H", 0x24, 0x25, 0x26, 0x28, 0x27, 0x29)
lvs = struct.pack("<6H", 60, 44, 51, 48, 42, 40)
LV_T = (60, 44, 51, 48, 42, 40)
EX_T = (10268, 1725, 27276, 23150, 21189, 0)          # 真机实测
exps = pack_exp(EX_T, LV_T)
hps = struct.pack("<6H", 829, 785, 685, 791, 582, 438)
mhs = struct.pack("<6H", 975, 818, 875, 810, 685, 3600)
check("parse_exp_blob: 6 条 (wExp,wRes,wLevel,wCount)",
      len(IM.parse_exp_blob(exps)) == 6
      and IM.parse_exp_blob(exps)[2] == (27276, 0, 51, 0))
check("parse_exp_blob: 内嵌 wLevel 与 rgwLevel 交叉一致",
      [r[2] for r in IM.parse_exp_blob(exps)] == list(LV_T))
check("parse_exp_blob: 空/None -> []",
      IM.parse_exp_blob(b"") == [] and IM.parse_exp_blob(None) == [])
check("2B 步长读法会得到 灵儿=0 (复现历史 bug, 证明修复有效)",
      struct.unpack_from("<6H", exps, 0)[1] == 0)

# rgLevelUpExp: 100 条 WORD, 索引 = 当前等级 (真机实测片段)
NEXT_REAL = {40: 19515, 41: 20515, 42: 21540, 43: 22590, 44: 23665,
             45: 24765, 60: 32000}
next_tab = [0] * IM.MAX_LEVELS
for _k, _v in NEXT_REAL.items():
    next_tab[_k] = _v
next_raw = struct.pack("<%dH" % IM.MAX_LEVELS, *next_tab)
check("parse_levelup_exp: 100 条", len(IM.parse_levelup_exp(next_raw)) == 100)
check("parse_levelup_exp: 空/None/不足 -> []",
      IM.parse_levelup_exp(b"") == [] and IM.parse_levelup_exp(None) == []
      and IM.parse_levelup_exp(b"\x01") == [])
check("next_exp_for: 索引=当前等级 (阿奴 lv42 -> v[42]=21540)",
      IM.next_exp_for(42, next_tab) == 21540)
check("next_exp_for: 不是 level-1 (排除 v[41]=20515 的假说)",
      IM.next_exp_for(42, next_tab) != 20515)
check("next_exp_for: lv0/越界/空表 -> 0",
      IM.next_exp_for(0, next_tab) == 0 and IM.next_exp_for(999, next_tab) == 0
      and IM.next_exp_for(42, []) == 0)

rows = IM.parse_roles(names, lvs, exps, hps, mhs, next_raw)
check("parse_roles: 6 行", len(rows) == 6)
check("parse_roles: 首行 李逍遥ID/60/10268/829/975",
      rows[0]["name_id"] == 0x24 and rows[0]["level"] == 60
      and rows[0]["exp"] == 10268 and rows[0]["hp"] == 829
      and rows[0]["maxhp"] == 975)
check("parse_roles: ★赵灵儿经验 = 1725 (不是 0)",
      rows[1]["exp"] == 1725, str(rows[1]["exp"]))
check("parse_roles: 缺 HP/升级表不炸",
      IM.parse_roles(names, lvs, exps)[0]["hp"] == 0
      and IM.parse_roles(names, lvs, exps)[0]["next_exp"] == 0)
check("parse_roles: next_exp 按等级取 (阿奴 lv42 -> 21540)",
      rows[4]["next_exp"] == 21540)
check("parse_roles: pct = exp/next (阿奴 21189/21540 -> 98%)",
      rows[4]["pct"] == 98 and rows[1]["pct"] == 7)
check("parse_roles: exp > next 时 pct 封顶 100",
      IM.parse_roles(names, lvs, pack_exp((0, 60000, 0, 0, 0, 0), LV_T),
                     None, None, next_raw)[1]["pct"] == 100)
check("parse_roles: 截断数组按实际条数 (8B/条)",
      len(IM.parse_roles(names, lvs, exps[:8])) == 1
      and len(IM.parse_roles(names, lvs, exps[:16])) == 2)
check("exp_text: 有阈值 -> '1725/23665'", IM.exp_text(rows[1]) == "1725/23665")
check("exp_text: 无阈值 -> 只显示当前值",
      IM.exp_text({"exp": 123}) == "123"
      and IM.exp_text({"exp": 0, "next_exp": 0}) == "0")
check("exp_text: 字段缺失兜底", IM.exp_text({}) == "0")

# RoleReader.read (注入假 _rd)
rr = IM.RoleReader()
seq = {
    IM.PARTY_N_ADDR: party_raw,
    IM.ROLES_NAME_ADDR: names,
    IM.ROLES_LEVEL_ADDR: lvs,
    IM.EXP_ADDR: exps,
    IM.ROLES_HP_ADDR: hps,
    IM.ROLES_MAXHP_ADDR: mhs,
    IM.LEVELUPEXP_ADDR: next_raw,
}
rr._rd = lambda addr, size=None, _s=seq: _s.get(addr)
d = rr.read()
check("read: 按队伍顺序 3 人",
      [m["name"] for m in d["members"]] == ["赵灵儿", "李逍遥", "阿奴"],
      str([m["name"] for m in d["members"]]))
check("read: 等级/经验对位 (逍遥 60/10268)",
      d["members"][1]["level"] == 60 and d["members"][1]["exp"] == 10268)
check("read: ★灵儿读到自己的经验 (1725, 带升级阈值 23665)",
      d["members"][0]["exp"] == 1725
      and d["members"][0]["next_exp"] == 23665,
      str((d["members"][0]["exp"], d["members"][0]["next_exp"])))
check("read: 经验列文本", [IM.exp_text(m) for m in d["members"]]
      == ["1725/23665", "10268/32000", "21189/21540"],
      str([IM.exp_text(m) for m in d["members"]]))
seq2 = dict(seq)
seq2[IM.LEVELUPEXP_ADDR] = None
rr._rd = lambda addr, size=None, _s=seq2: _s.get(addr)
check("read: 缺升级表 -> 仍返回成员, next_exp=0",
      rr.read()["members"][0]["exp"] == 1725
      and rr.read()["members"][0]["next_exp"] == 0)
rr._rd = lambda addr, size=None: None
check("read: 读失败 -> None", rr.read() is None)

print("=" * 66)
print("第 6 节: 游戏窗口判据 is_game_title (2026-10-12)")
print("=" * 66)
# 为什么要测: 机器人 / GUI 是靠「窗口标题含 'Pal'」找到游戏的 (游戏真标题实测 =
#   `Pal Win95 [v3.0.2025.0616-dirty]`)。自家 GUI 标题里只有**全大写**的 "SDLPAL",
#   所以以前 `"Pal" in 标题` 恰好为假 —— 纯属侥幸。GUI 标题一旦(v19.x 加版本号那样)
#   混进大小写敏感的 "Pal", 就会被当成游戏窗口去读内存。这里把排除钉死。
# 真机实测标题 (2026-10-12 EnumWindows 抓的, 尾部有空格照抄):
GAME_TITLE = "Pal Win95 [v3.0.2025.0616-dirty] "
check("真机游戏标题 -> 判为游戏窗口", IM.is_game_title(GAME_TITLE), GAME_TITLE)
check("自家 GUI 标题 (带版本号) -> 不判为游戏窗口",
      not IM.is_game_title("仙剑98 挂机机器人 (SDLPAL) v0.1.0-beta"))
check("自家 GUI 标题 (旧版, 不带版本号) -> 不判为游戏窗口",
      not IM.is_game_title("仙剑98 挂机机器人 (SDLPAL)"))
check("★ GUI 标题里就算真混进 'Pal' 也被排除 (以前只靠大小写侥幸)",
      not IM.is_game_title("仙剑98 挂机机器人 (Pal98) — v0.1.0-beta"),
      "含 'Pal' 但含「挂机机器人」-> 必须为假")
check("实测过的其它含 pal 的窗口 (MINGW64/bash) -> 不算游戏",
      not IM.is_game_title("MINGW64:/d/Fang/Game/PAL_98/_bot/pal98-bot"))
check("空标题 -> 不算游戏", not IM.is_game_title(""))
check("大小写敏感: 全大写 'PAL' 不算 (真机游戏标题是 'Pal Win95')",
      not IM.is_game_title("PAL98 emulator"))

print()
print("=" * 66)
print("结果: PASS %d / FAIL %d" % (len(OK), len(BAD)))
if BAD:
    print("失败项:")
    for n in BAD:
        print("  - " + n)
sys.exit(1 if BAD else 0)
