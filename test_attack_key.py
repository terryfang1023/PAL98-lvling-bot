# -*- coding: utf-8 -*-
"""test_attack_key.py — 战斗攻击键可配置 (2026-10-01) 的回归测试。

背景: 换到新地图后, 游戏里"重复出手"的键从 F 变成了 R。
硬编码的按键必须变成用户可调项, 而且:
  * 默认值要跟 SDLPAL 官方键表一致 —— R = kKeyRepeat(重复上次行动);
    (旧代码写 F 并注释成"F=重复", 与 input.c 的 g_KeyMap 不符: F 是 kKeyForce)
  * 不能在战斗代码里残留任何写死的按键;
  * GUI 改一次就要落盘, 下次启动还在;
  * 配置损坏/键名非法都不许让机器人起不来。

铁律: 本测试**不碰游戏、不写运行目录** —— 按键通道是假的,
      配置文件全部落在临时目录。
"""
import json
import os
import sys
import tempfile

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import pal_bot as PB                                      # noqa: E402
import botcfg                                             # noqa: E402

OK, BAD = [], []


def check(name, cond, extra=""):
    (OK if cond else BAD).append(name)
    print("  %s %s %s" % ("PASS" if cond else "FAIL", name, extra))


TMP = tempfile.mkdtemp(prefix="attack_key_test_")
CFG = os.path.join(TMP, "bot_config.json")
REAL_CFG = botcfg.CONFIG_PATH


def _cfg_digest():
    """真实 bot_config.json 的内容摘要 (不存在 -> None)。"""
    p = os.path.join(BASE, "bot_config.json")
    try:
        with open(p, "rb") as f:
            return f.read()
    except OSError:
        return None


_CFG_DIGEST_BEFORE = _cfg_digest()


def cleanup():
    import shutil
    shutil.rmtree(TMP, ignore_errors=True)


# ======================================================================
print("=" * 66)
print("第 1 节: 按键表 (SDLPAL input.c g_KeyMap 对齐)")
print("=" * 66)
# R = kKeyRepeat (重复上次行动) —— 挂机要的就是它
check("R 的 VK = 0x52", PB.key_spec("r")[0] == 0x52, hex(PB.key_spec("r")[0]))
check("R 的扫描码 = 0x13", PB.key_spec("r")[1] == 0x13, hex(PB.key_spec("r")[1]))
check("R 不是扩展键", PB.key_spec("r")[2] is False)
check("大小写等价 (R == r)", PB.key_spec("R") == PB.key_spec("r"))
# F = kKeyForce (法术): 保留可用, 但已不是默认
check("F 仍可用 (兼容旧配置)", PB.key_spec("f") == (0x46, 0x21, False),
      str(PB.key_spec("f")))
check("A 仍可用 (kKeyAuto 自动战斗)", PB.key_spec("a") == (0x41, 0x1E, False))
check("空格仍可用 (kKeySearch 确认)", PB.key_spec("space") == (0x20, 0x39, False))
check("方向键仍是扩展键", PB.key_spec("up")[2] is True)


print("=" * 66)
print("第 2 节: 任意单字母/数字兜底 (用户随手改键也能发出去)")
print("=" * 66)
# 抽样核对 PC 键盘 Set 1 扫描码: 与 KEYS 表里已登记的键必须**逐个一致**
FIXED = {"a": 0x1E, "d": 0x20, "e": 0x12, "f": 0x21, "q": 0x10,
         "r": 0x13, "s": 0x1F, "w": 0x11, "z": 0x2C, "m": 0x32}
check("兜底表与 KEYS 表逐位一致",
      all(PB.key_spec(k) == (ord(k.upper()), sc, False)
          for k, sc in FIXED.items()),
      str({k: PB.key_spec(k)[1] for k in FIXED}))
check("未登记的字母也能解析 (z)", PB.key_spec("z") == (0x5A, 0x2C, False))
check("数字键也有扫描码 (5)", PB.key_spec("5") == (0x35, 0x06, False))
check("known_keys 含 26 个字母",
      all(c in PB.known_keys() for c in "abcdefghijklmnopqrstuvwxyz"))
for bad in ("ctrl", "", "  ", "rr", "F1", "回車", None, "shift"):
    try:
        PB.key_spec(bad)
        check("非法键被拒 %r" % (bad,), False, "竟然解析成功了")
    except ValueError:
        check("非法键被拒 %r" % (bad,), True)


print("=" * 66)
print("第 3 节: set_attack_key / attack() 真的发出配置的键")
print("=" * 66)
sent = []


class FakeWin32api:
    """拦下 PostMessage, 记录 (msg, vk) 序列。"""

    WM_KEYDOWN, WM_KEYUP = PB.WM_KEYDOWN, PB.WM_KEYUP

    @staticmethod
    def PostMessage(hwnd, msg, vk, lp):
        sent.append((msg, vk, lp))


real_api = PB.win32api
PB.win32api = FakeWin32api
try:
    check("默认攻击键 = r (SDLPAL kKeyRepeat)", PB.attack_key() == "r",
          PB.attack_key())

    g = PB.Game(12345)                     # 假 hwnd: PostMessage 已被拦下
    sent[:] = []
    g.attack()
    vks = [vk for (msg, vk, _) in sent if msg == PB.WM_KEYDOWN]
    check("attack() 发出的 keydown 是 R (0x52)", vks == [0x52], str(vks))
    check("attack() 有按下也有抬起",
          any(m == PB.WM_KEYUP for (m, _, _) in sent) and bool(vks))

    PB.set_attack_key("g")
    check("set_attack_key 返回规范化键名", PB.attack_key() == "g")
    sent[:] = []
    g.attack()
    vks = [vk for (msg, vk, _) in sent if msg == PB.WM_KEYDOWN]
    check("改键后 attack() 立刻换成 G (0x47)", vks == [0x47], str(vks))

    PB.set_attack_key("SPACE")
    sent[:] = []
    g.attack()
    vks = [vk for (msg, vk, _) in sent if msg == PB.WM_KEYDOWN]
    check("具名键也能当攻击键 (space=0x20)", vks == [0x20], str(vks))

    # 非法值必须**不改**当前设置 (不能把机器人改成不可用状态)
    PB.set_attack_key("r")
    try:
        PB.set_attack_key("ctrl")
        check("非法键不改设置", False, "居然接受了 ctrl")
    except ValueError:
        check("非法键不改设置", PB.attack_key() == "r", PB.attack_key())

    print("=" * 66)
    print("第 4 节: flush_keys 清得掉当前攻击键 (防卡键走丢)")
    print("=" * 66)
    for key in ("r", "g", "space", "f"):
        PB.set_attack_key(key)
        sent[:] = []
        g.flush_keys()
        ups = {vk for (m, vk, _) in sent if m == PB.WM_KEYUP}
        want = PB.key_spec(key)[0]
        check("flush_keys 含当前攻击键 %s" % key, want in ups,
              "抬起集合=%s" % sorted(ups))
    check("flush_keys 同时清掉旧版 F (兼容)", 0x46 in ups)

    print("=" * 66)
    print("第 5 节: 配置文件 (botcfg) —— 读写/优先级/容错")
    print("=" * 66)
    botcfg.save({"attack_key": "g"}, CFG)
    check("save 后能读回", botcfg.load(CFG).get("attack_key") == "g")
    check("文件是合法 JSON",
          json.load(open(CFG, encoding="utf-8"))["attack_key"] == "g")
    check("get 命中文件值", botcfg.get("attack_key", CFG) == "g")
    check("缺失键回退默认", botcfg.get("nope", CFG) is None)
    botcfg.put("attack_key", "space", CFG)
    check("put 覆盖并保留其它键",
          botcfg.load(CFG)["attack_key"] == "space")
    botcfg.put("another", 1, CFG)
    check("put 不冲掉别的键",
          botcfg.load(CFG).get("attack_key") == "space"
          and botcfg.load(CFG).get("another") == 1)

    check("resolve: 文件优先于默认",
          botcfg.resolve("attack_key", CFG) == ("space", "bot_config.json"),
          str(botcfg.resolve("attack_key", CFG)))
    os.environ["PAL_ATTACK_KEY"] = "q"
    check("resolve: 环境变量优先于文件",
          botcfg.resolve("attack_key", CFG) == ("q", "环境变量 PAL_ATTACK_KEY"),
          str(botcfg.resolve("attack_key", CFG)))
    check("env_name 命名", botcfg.env_name("attack_key") == "PAL_ATTACK_KEY")
    del os.environ["PAL_ATTACK_KEY"]
    check("删掉环境变量后回到文件",
          botcfg.resolve("attack_key", CFG)[0] == "space")

    # 容错: 缺失 / 损坏 / 不是对象 / 值是空串
    NOPE = os.path.join(TMP, "no_such.json")
    check("文件不存在 -> 默认", botcfg.resolve("attack_key", NOPE)
          == ("r", "默认值"), str(botcfg.resolve("attack_key", NOPE)))
    check("文件不存在 -> load 空 dict", botcfg.load(NOPE) == {})
    for junk in ('{"attack_key": ', "not json at all", "[1,2,3]", "null"):
        with open(NOPE, "w", encoding="utf-8") as f:
            f.write(junk)
        check("损坏内容不抛异常 (%s)" % junk[:12],
              botcfg.resolve("attack_key", NOPE)[0] == "r"
              and botcfg.load(NOPE) == {})
    botcfg.save({"attack_key": "   "}, NOPE)
    check("值是空串 -> 默认", botcfg.resolve("attack_key", NOPE)[0] == "r")

    print("=" * 66)
    print("第 6 节: init_attack_key (启动装载, 永不抛异常)")
    print("=" * 66)
    PB.set_attack_key("r")
    os.environ["PAL_ATTACK_KEY"] = "g"
    k, src = PB.init_attack_key(CFG)
    check("环境变量生效", (k, PB.attack_key()) == ("g", "g"), "%s / %s" % (k, src))
    check("来源写明是环境变量", "环境变量" in src, src)
    del os.environ["PAL_ATTACK_KEY"]

    botcfg.save({"attack_key": "space"}, CFG)
    k, src = PB.init_attack_key(CFG)
    check("文件生效", (k, PB.attack_key()) == ("space", "space"), "%s / %s" % (k, src))

    botcfg.save({"attack_key": "ctrl"}, CFG)          # 手改坏的配置
    k, src = PB.init_attack_key(CFG)
    check("配置里的非法值 -> 回退默认 r", k == "r", "%s / %s" % (k, src))
    check("回退时来源写明原因", "无效" in src or "默认值" in src, src)
    check("回退后机器人可用 (attack 不炸)", PB.attack_key() == "r")

    k, src = PB.init_attack_key(NOPE)                 # 损坏的文件
    check("损坏文件 -> 回退默认", k == "r", "%s / %s" % (k, src))

    print("=" * 66)
    print("第 7 节: 战斗代码里不许残留写死的攻击键")
    print("=" * 66)
    import io
    import re
    # 只盯"攻击键"这两个历史取值: f (旧硬编码) 与 r (现在的默认)。
    # 不盯 a/space/esc/方向键 —— 那些是**别的**动作, 有意写死才有意义:
    #   key("a")     = 卡在法术页时的自动战斗兜底 (mem2)
    #   key("space") = 推进战后结算 / 开宝箱
    #   key("esc")   = 关菜单
    hard = []
    for fn in ("pal_bot.py", "pal_bot_mem.py", "pal_bot_mem2.py",
               "pal_bot_mem3.py", "pal_bot_mem4.py", "pal_bot_mem5.py"):
        p = os.path.join(BASE, fn)
        if not os.path.exists(p):
            continue
        src = io.open(p, encoding="utf-8").read()
        for i, line in enumerate(src.splitlines(), 1):
            code = line.split("#", 1)[0]          # 先去掉注释
            if re.search(r"""\.key\(\s*['"](f|r)['"]""", code):
                hard.append("%s:%d %s" % (fn, i, line.strip()))
    check("没有 .key('f')/.key('r') 硬编码攻击键", not hard, " ; ".join(hard))
    check("attack() 是唯一出口 (pal_bot 内部也用 attack)",
          ".attack()" in io.open(os.path.join(BASE, "pal_bot.py"),
                                 encoding="utf-8").read())

    # attack() 必须是唯一出口: 战斗路径全部走 g.attack()
    src4 = io.open(os.path.join(BASE, "pal_bot_mem4.py"), encoding="utf-8").read()
    check("mem4 战斗循环改用 g.attack()", ".attack()" in src4)
    src2 = io.open(os.path.join(BASE, "pal_bot_mem2.py"), encoding="utf-8").read()
    check("mem2 战斗循环改用 g.attack()", ".attack()" in src2)
    check("mem2 的 seq 用 PB.attack_key()", "PB.attack_key()" in src2)
    src5 = io.open(os.path.join(BASE, "pal_bot_mem5.py"), encoding="utf-8").read()
    check("mem5 启动时装载攻击键", "init_attack_key()" in src5)
    srcg = io.open(os.path.join(BASE, "bot_gui.py"), encoding="utf-8").read()
    check("GUI 通过环境变量把攻击键钉给子进程",
          'env_name("attack_key")' in srcg)
    check("GUI 落盘攻击键", 'botcfg.put("attack_key"' in srcg)
    check("GUI 不导入 pal_bot (DPI 会缩小界面)", "import pal_bot" not in srcg)

finally:
    PB.win32api = real_api
    PB.set_attack_key(PB.ATTACK_KEY_DEFAULT)
    os.environ.pop("PAL_ATTACK_KEY", None)

# 收尾: 真实配置文件绝不能被本测试碰过。
# (2026-10-02 起允许它存在 —— 用户用 GUI 启动就会合法落盘; 断言从"不存在"
#  改成"测试前后内容不变", 防的是测试污染, 不是禁止用户使用)
check("运行目录的 bot_config.json 未被本测试改动",
      botcfg.CONFIG_PATH == REAL_CFG
      and _cfg_digest() == _CFG_DIGEST_BEFORE,
      "digest %s -> %s" % (_CFG_DIGEST_BEFORE, _cfg_digest()))
cleanup()

print("\n" + "=" * 66)
print("PASS %d  FAIL %d" % (len(OK), len(BAD)))
if BAD:
    print("失败项:", ", ".join(BAD))
print("=" * 66)
sys.exit(1 if BAD else 0)
