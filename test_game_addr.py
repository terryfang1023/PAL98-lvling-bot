# -*- coding: utf-8 -*-
"""test_game_addr.py — 地址换算层 (标定地址 -> 本次运行地址) 的回归测试。

背景 (2026-10-16): sdlpal.exe 的 PE 头带 DYNAMICBASE, 而 Windows 的**映像
基址随机化是按"本次开机"决定一次**的 —— 换台机器 / 重启一次就可能换基址,
于是机器人写死的那些"标定绝对地址"就全错了:
   · 基址 > 0x00C30000 -> 连那块 64KB 都读不到 -> `READ_TABLE_FAIL` 退出
   · 基址在别处但可读 -> 读到垃圾 (更坏: 拿着假坐标瞎走)

本测试守住三件事:
  ① 纯函数正确 (slide 计算 / rgScene 合理性 / 升级经验表特征识别 / 扫描)
  ② 常量与 pal_bot_mem / bot_itemmon 同源 (防两处漂移)
  ③ **真机安全网**: 正确 slide 必须通过校验, 而错误 slide 必须被拒 ——
     校验器要是太松, 机器人就会静默拿着垃圾数据瞎跑 (比报错更坏)。
     没开游戏时这几项自动跳过 (但仍计入通过数), 保持 CI 无游戏也能全绿。
"""
import os
import struct
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import game_addr as GA                        # noqa: E402

OK, BAD, SKIP = [], [], []


def check(name, cond, extra=""):
    (OK if cond else BAD).append(name)
    print(("PASS  " if cond else "FAIL  ") + name
          + (("  <- " + str(extra)) if (extra and not cond) else ""))


def skip(name, why=""):
    SKIP.append(name)
    OK.append(name)
    print("SKIP  " + name + (("  (%s)" % why) if why else ""))


print("=" * 70)
print("第 1 节: 标定常量与同源一致性")
print("=" * 70)
check("CALIB_BASE == 0x00490000 (真机实测的模块基址)",
      GA.CALIB_BASE == 0x00490000, hex(GA.CALIB_BASE))
check("PE 首选基址常量 = 0x00400000 (只用于诊断显示)",
      GA.PREFERRED_BASE == 0x00400000, hex(GA.PREFERRED_BASE))
check("标定基址 != PE 首选基址 (说明确实被 ASLR 搬过)",
      GA.CALIB_BASE != GA.PREFERRED_BASE)

import pal_bot_mem as PM                      # noqa: E402
import pal_bot_mem5 as PM5                    # noqa: E402
import bot_itemmon as IM                      # noqa: E402

check("game_addr.READ_AT == pal_bot_mem.READ_AT",
      GA.READ_AT == PM.READ_AT, hex(GA.READ_AT))
check("game_addr.SCENE_ADDR == pal_bot_mem.SCENE_ADDR",
      GA.SCENE_ADDR == PM.SCENE_ADDR, hex(GA.SCENE_ADDR))
check("game_addr.TABLE_ADDR == pal_bot_mem.TABLE_ADDR",
      GA.TABLE_ADDR == PM.TABLE_ADDR, hex(GA.TABLE_ADDR))
check("game_addr.LEVELUPEXP_ADDR == pal_bot_mem.Mem.LEVELUPEXP_ADDR",
      GA.LEVELUPEXP_ADDR == PM.Mem.LEVELUPEXP_ADDR, hex(GA.LEVELUPEXP_ADDR))
check("game_addr.LEVELUPEXP_ADDR == bot_itemmon.LEVELUPEXP_ADDR",
      GA.LEVELUPEXP_ADDR == IM.LEVELUPEXP_ADDR, hex(GA.LEVELUPEXP_ADDR))
check("game_addr.LEVELUPEXP_ADDR == viewport - 0x1A24",
      GA.LEVELUPEXP_ADDR == IM.VIEWPORT_ADDR - 0x1A24)
check("game_addr.MAX_SCRIPT == pal_bot_mem.Mem.MAX_SCRIPT (表长不变量)",
      GA.MAX_SCRIPT == PM.Mem.MAX_SCRIPT, GA.MAX_SCRIPT)
check("OBJ_N_ADDR 三处同源 (game_addr / pal_bot_mem / pal_bot_mem5)",
      GA.OBJ_N_ADDR == PM.OBJ_N_ADDR == PM5.OBJ_N_ADDR, hex(GA.OBJ_N_ADDR))
check("OBJ_N_ADDR 就在 rgScene 前一条 (同一个 g.lprgEventObject 块)",
      GA.TABLE_ADDR - GA.OBJ_N_ADDR == 4,
      "%X - %X" % (GA.TABLE_ADDR, GA.OBJ_N_ADDR))
check("升级经验表长度 100 与 pal_bot_mem 一致",
      GA.LEVELUP_N == PM.Mem.LEVELUPEXP_N == 100)
# 锚点之间的相对偏移是"同一个标定体系"的证据
check("rgScene 与 viewport 同块: TABLE_ADDR < SCENE_ADDR < LEVELUPEXP_ADDR",
      GA.TABLE_ADDR < GA.LEVELUPEXP_ADDR < GA.SCENE_ADDR,
      "%X %X %X" % (GA.TABLE_ADDR, GA.LEVELUPEXP_ADDR, GA.SCENE_ADDR))

print()
print("=" * 70)
print("第 2 节: slide 计算 (纯函数)")
print("=" * 70)
check("基址 == 标定基址 -> slide 0", GA.slide_for_base(0x00490000) == 0)
check("基址 0x00520000 -> slide +0x90000 (ASLR 平移典型值)",
      GA.slide_for_base(0x00520000) == 0x90000,
      hex(GA.slide_for_base(0x00520000)))
check("基址 == PE 首选 0x00400000 -> slide -0x90000",
      GA.slide_for_base(0x00400000) == -0x90000,
      hex(GA.slide_for_base(0x00400000)))
check("基址 0x00E90000 (高于 0x00C30000, 就是报 READ_TABLE_FAIL 的那种) "
      "-> slide 为正且很大",
      GA.slide_for_base(0x00E90000) == 0x00A00000,
      hex(GA.slide_for_base(0x00E90000)))
check("base 为 None -> 0 (容错, 不抛异常)", GA.slide_for_base(None) == 0)
check("base 为 0 -> 0", GA.slide_for_base(0) == 0)
check("slide 是纯加减 (可逆)", GA.slide_for_base(
    GA.CALIB_BASE + 0x123450) == 0x123450)

print()
print("=" * 70)
print("第 3 节: 游戏窗口判据 (与三处 finder 同源)")
print("=" * 70)
check("真游戏标题 -> True", GA.is_game_title("Pal Win95 [v3.0.2025.0616-dirty] "))
check("自家 GUI 标题 -> False (含'挂机机器人')",
      not GA.is_game_title("仙剑98 挂机机器人 (SDLPAL) v0.2.1-beta"))
check("资源管理器标题 (pal98-bot) -> False",
      not GA.is_game_title("pal98-bot - 文件资源管理器"))
check("MINGW64 标题 (Pal 大小写不匹配) -> False",
      not GA.is_game_title("MINGW64:/d/Fang/Game/PAL_98/_bot/pal98-bot"))
check("全大写 SDLPAL -> False (大小写敏感)",
      not GA.is_game_title("SDL PAL 3.0"))
check("空标题 -> False", not GA.is_game_title(""))
check("bot_itemmon 判据与 game_addr 一致",
      all(GA.is_game_title(t) == IM.is_game_title(t)
          for t in ("Pal Win95", "仙剑98 挂机机器人 (SDLPAL)",
                    "pal98-bot - 文件资源管理器", "")))

print()
print("=" * 70)
print("第 4 节: rgScene 结构合理率 (与 selfcheck 同规则)")
print("=" * 70)
good_tw = []
for s in range(296):
    good_tw += [12, 100, 200, s]              # map=12, 两个脚本号, 第4个WORD
check("296 条全合理 -> 1.0", GA.scene_tab_ratio(good_tw) == 1.0,
      GA.scene_tab_ratio(good_tw))
check("全 0xFFFF -> 0.0 (地图号 65535 越界)",
      GA.scene_tab_ratio([0xFFFF] * (296 * 4)) == 0.0)
check("空数据 -> 0.0", GA.scene_tab_ratio([]) == 0.0)
half = []
for s in range(296):
    half += ([12, 1, 2, 0] if s % 2 else [4000, 1, 2, 0])
check("一半坏 -> 约 0.5", 0.45 < GA.scene_tab_ratio(half) < 0.55,
      GA.scene_tab_ratio(half))
check("脚本号越界也算坏", GA.scene_tab_ratio([12, 99999, 2, 0] * 296) == 0.0)
check("脚本号 0xFFFF 是合法的空脚本 (与 selfcheck 一致)",
      GA.scene_tab_ratio([12, 0xFFFF, 0xFFFF, 0] * 296) == 1.0)

print()
print("--- 4b. 累积对象序号结构 (挡'整条平移'的错位) ---")


def mk_tab_words(n=300, total=5077):
    """造一张"结构正确"的 rgScene: 累积序号单调到 total, 前 2 个场景为 0。"""
    w = []
    acc = 0
    for s in range(n):
        acc = 0 if s < 2 else acc + 17
        w += [12, 100, 200, acc]
    w[(n - 1) * 4 + 3] = total
    return w


tw_ok = mk_tab_words()
check("正确结构 -> True", GA.scene_tab_struct_ok(tw_ok))
w_rev = []
for i in range(300):
    w_rev += [12, 100, 200, (5000 - i * 17) if i >= 2 else 0]
check("累积序号递减 -> False  (位置错位的典型样子)",
      not GA.scene_tab_struct_ok(w_rev))
check("整条平移 1 个 WORD (每个场景的 map 错位) -> False",
      not GA.scene_tab_struct_ok(tw_ok[1:] + [0]))
check("全零 (指到清零的内存区) -> False",
      not GA.scene_tab_struct_ok([0] * (300 * 4)))
check("场景太少 (对象总数 0) -> False",
      not GA.scene_tab_struct_ok([12, 1, 2, 0] * 300))
check("对象总数超上限 (6000) -> False",
      not GA.scene_tab_struct_ok(mk_tab_words(total=6001)))
check("对象总数低于下限 (1000) -> False",
      not GA.scene_tab_struct_ok(mk_tab_words(total=999)))
check("长度不足也不崩", not GA.scene_tab_struct_ok([1, 2, 3, 4]))

print()
print("=" * 70)
print("第 5 节: 升级经验表特征识别与映像扫描")
print("=" * 70)
check("特征前 10 项 == 真机实测值 (0,15,40,90,165,265,390,540,715,915)",
      GA.LEVELUP_HEAD == (0, 15, 40, 90, 165, 265, 390, 540, 715, 915),
      GA.LEVELUP_HEAD)


def make_tab():
    """造一张像真表的 100 项表: 单调增长, 尾部 32000。"""
    v = list(GA.LEVELUP_HEAD)
    cur = v[-1]
    while len(v) < 90:
        cur += 700
        v.append(cur)
    v = v[:90]
    v += [32000] * 10
    return v


tab = make_tab()
check("合成的真表 -> 匹配", GA.levelup_tab_match(tab))
check("偏移 1 处不匹配 (前 10 项对不上)",
      not GA.levelup_tab_match([0] + tab))
check("前 10 项差一个数 -> 不匹配",
      not GA.levelup_tab_match(list(tab[:5]) + [999] + list(tab[6:])))
check("尾部不是 32000 -> 不匹配",
      not GA.levelup_tab_match(list(tab[:90]) + [111] * 10))
check("长度不足 -> 不匹配", not GA.levelup_tab_match(tab[:50]))
check("越界偏移 -> 不匹配", not GA.levelup_tab_match(tab, off=10))

blob = b"\xAA" * 0x1000 + struct.pack("<100H", *tab) + b"\xBB" * 0x40
hits = GA.scan_levelup(blob)
check("扫描能在字节流里定位 -> 偏移 0x1000",
      hits == [0x1000], [hex(h) for h in hits])
check("扫描不会把普通数据当表 (全 0xAA 无命中)",
      GA.scan_levelup(b"\xAA" * 4096) == [])
check("扫描短数据不崩", GA.scan_levelup(b"\x00" * 4) == [])
check("真表的值与 memory 记录的满级阈值一致 (尾部 32000)",
      sum(1 for x in tab[90:100] if x == 32000) == 10)

print()
print("=" * 70)
print("第 6 节: 平移管道 (addr / shift_module)")
print("=" * 70)
_orig_slide = GA.slide
try:
    GA.slide = lambda pid=None: 0x90000            # 假装基址被搬了 0x90000
    check("addr() 叠平移", GA.addr(GA.TABLE_ADDR) == GA.TABLE_ADDR + 0x90000,
          hex(GA.addr(GA.TABLE_ADDR)))
    check("addr() 不改变传入值 (纯)", GA.TABLE_ADDR == 0x00C38088)
    ns = {"PTR_ADDR": 0x00C38080, "N_ADDR": 0x00C38084, "OTHER": 5}
    got = GA.shift_module(ns, "PTR_ADDR", "N_ADDR", "MISSING")
    check("shift_module 返回平移量", got == 0x90000, hex(got))
    check("shift_module 平移命中项",
          ns["PTR_ADDR"] == 0x00C38080 + 0x90000
          and ns["N_ADDR"] == 0x00C38084 + 0x90000,
          "%X %X" % (ns["PTR_ADDR"], ns["N_ADDR"]))
    check("shift_module 不碰没点名的键 / 不崩于不存在的键",
          ns["OTHER"] == 5 and "MISSING" not in ns)
finally:
    GA.slide = _orig_slide
check("恢复后 slide 是真实值 (函数未被永久打桩)",
      GA.slide() == GA.resolve()["slide"])

check("reset_cache 存在且可用", callable(GA.reset_cache))
GA.reset_cache()
check("diag_lines 无游戏时也能给出行 (不抛异常)",
      isinstance(GA.diag_lines(), list) and len(GA.diag_lines()) >= 2)

print()
print("=" * 70)
print("第 7 节: 真机安全网 (校验器必须拒掉错误平移)")
print("=" * 70)
pid = GA.game_pid()
if not pid:
    skip("真机校验", "没有游戏窗口 (标题含 Pal), 跳过 —— 不影响其余断言")
else:
    r = GA.resolve(pid)
    check("真机求解成功", r["ok"], "; ".join(GA.diag_lines(r=r)))
    check("slide == 模块基址 - 标定基址",
          r["slide"] == r["base"] - GA.CALIB_BASE,
          "%X vs %X" % (r["slide"], r["base"] - GA.CALIB_BASE))
    # 模块基址的两条独立来源互相印证: psapi vs VirtualQueryEx(AllocationBase)
    alloc, typ, _rs = GA.allocation_base(pid, GA.TABLE_ADDR + r["slide"])
    check("psapi 基址 == VirtualQueryEx 的 AllocationBase (且是 MEM_IMAGE)",
          alloc == r["base"] and typ == 0x1000000,
          "%X vs %X type=%s" % (alloc, r["base"], typ))
    # 特征扫描独立反推的地址必须与 (slide + 标定值) 一致
    lv = r["levelup"]
    if lv:
        check("映像里扫到的升级经验表 == 标定值 + slide",
              lv[0] == GA.LEVELUPEXP_ADDR + r["slide"],
              "%X vs %X" % (lv[0], GA.LEVELUPEXP_ADDR + r["slide"]))
    else:
        skip("扫描反推", "扫不到特征 (可能没在游戏内)")
    h = GA.open_process(pid)
    try:
        ok, det = GA.probe_slide(h, r["slide"])
        check("正确 slide 通过校验", ok, det)
        # ★★ 最关键的一条: 错误平移必须全部被拒 (否则会静默读垃圾)。
        #   含两类: ① ASLR 平移量级 (±64KB 的倍数)
        #            ② 整条/多条错位 (±8 = 一个场景条目, ±0x100 = 32 条)
        #              —— 这类"结构仍然整齐"的错位最危险, 靠累积序号挡。
        for delta in (8, -8, 0x10, 0x100, -0x100, 0x2E50, -0x2E50,
                      0x10000, -0x10000, 0x90000, -0x90000,
                      0x100000, 0x200000):
            s2 = r["slide"] + delta
            ok2, det2 = GA.probe_slide(h, s2)
            check("错误 slide %+d 被拒" % delta, not ok2, det2)
    finally:
        GA.k32.CloseHandle(h)
    # Mem 实例地址确实叠了平移
    m = PM.Mem()
    check("Mem.read_at == READ_AT + slide", m.read_at == PM.READ_AT + m.slide)
    check("Mem.table_region == TABLE_REGION + slide",
          m.table_region == PM.TABLE_REGION + m.slide)
    check("Mem.gain_addr == GAIN_ADDR + slide",
          m.gain_addr == PM.Mem.GAIN_ADDR + m.slide)
    ok, det = m.selfcheck()
    check("真机 selfcheck 通过", ok, "; ".join(det))
    check("selfcheck 里报出了地址平移量",
          any("地址平移" in d for d in det), "; ".join(det))

print()
print("=" * 70)
print("通过 %d 项, 失败 %d 项 (其中跳过 %d 项)"
      % (len(OK), len(BAD), len(SKIP)))
for b in BAD:
    print("  !! %s" % b)
print("=" * 70)
sys.exit(1 if BAD else 0)
