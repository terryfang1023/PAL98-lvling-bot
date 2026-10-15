# -*- coding: utf-8 -*-
"""heal_once.py — 菜单宏「单次实测」工具 (2026-10-01, 2026-10-13 起用于存盘)。

用途: **校准 / 验证按键序列** (当前这项功能是"战后自动存盘", 见 GUI「存盘测试」)。
      菜单宏就是"按一串键", 序列对不对只有在真机上跑一遍才知道 ——
      这个工具把这一步单独拿出来:

        * 每步都打印校验结果 (第一下 esc 后菜单有没有出来 / 收尾后画面有没有
          回到地图 / 补了几次 esc / 兜底逃生有没有触发);
        * --dry 只发键不做校验, 用来肉眼看游戏的反应;
        * --save 把 起始帧 / 开菜单后 / 收尾 存成 PNG (写系统临时目录),
          可对着图确认"到底停在哪一页";
        * --seq 临时换一条序列试, 不改 bot_config.json。

★ 会真的按键盘 —— **必须在机器人停止时用**。检测到 pal_bot_mem*.py 还在跑
  就直接拒绝执行 (两个进程抢键盘 = 谁也说不清哪一步是谁按的), 要强行跑加
  --force。本工具不写 mem_stop.flag / .pal_bot.lock / bot_log_mem.txt,
  唯一可写文件是 --save 的截图 (临时目录)。

用法:
  python tools/heal_once.py
  python tools/heal_once.py --seq "esc,下,下,回车,回车,回车"
  python tools/heal_once.py --dry --save
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import botcfg                                            # noqa: E402
from pal_bot import list_pal_windows, Game, is_battle     # noqa: E402
from pal_bot_heal import HealMacro, diff_frac             # noqa: E402
from pal_bot_mem2 import MemBot2                         # noqa: E402


def _bot_running():
    """有没有 pal_bot_mem*.py 在跑 (只查进程表, 不碰任何文件)。"""
    try:
        import psutil
        me = os.getpid()
        for p in psutil.process_iter(["pid", "name"]):
            try:
                if p.info["pid"] == me or not p.info["name"]:
                    continue
                if "python" not in p.info["name"].lower():
                    continue
                cl = p.cmdline()
                if any(str(x).replace("\\", "/").endswith(
                        ("pal_bot_mem.py", "pal_bot_mem2.py", "pal_bot_mem3.py",
                         "pal_bot_mem4.py", "pal_bot_mem5.py")) for x in cl):
                    return p.info["pid"]
            except Exception:
                continue
    except Exception:
        return None
    return None


def _make_escape(g, log):
    """兜底关菜单: 连按 esc 直到菜单特征消失 (给"起始就在菜单"的情况用)。"""
    def esc():
        for i in range(4):
            g.key("esc")
            time.sleep(0.7)
            a = g.shot()
            red, tan = MemBot2._menu_feat(a)
            if not MemBot2._menu_like(red, tan):
                log("  [兜底] esc x%d 后菜单特征消失 (red=%.3f tan=%.3f)"
                    % (i + 1, red, tan))
                return True
        return False
    return esc


def main(argv):
    seq_txt = None
    if "--seq" in argv:
        i = argv.index("--seq")
        if i + 1 < len(argv):
            seq_txt = argv[i + 1]
    dry = "--dry" in argv
    save = "--save" in argv

    pid = _bot_running()
    if pid and "--force" not in argv:
        print("!! 机器人 (pid=%s) 还在跑 —— 两个进程抢键盘没法判断是谁按的。\n"
              "   先在 GUI 点「停止」, 或者确认无误后加 --force。" % pid)
        return 2

    wins = list_pal_windows()
    if not wins:
        print("未找到游戏窗口 (标题含 Pal) —— 游戏没开?")
        return 1
    # 优先挑真游戏窗口 (标题含 Pal Win95); list_pal_windows 已排除自家 GUI
    wins.sort(key=lambda wt: 0 if "win95" in wt[1].lower() else 1)
    hwnd, title = wins[0]
    if len(wins) > 1:
        print("找到 %d 个候选窗口, 用「%s」(其余: %s)"
              % (len(wins), title.strip(),
                 " / ".join(t.strip() for _, t in wins[1:])))
    g = Game(hwnd)

    # ---- 序列: --seq > bot_config.json > 默认 ----
    if seq_txt:
        keys, src = botcfg.parse_seq(seq_txt), "命令行 --seq"
    else:
        txt, src = botcfg.resolve("heal_seq")
        keys = botcfg.parse_seq(txt)
    gap, gsrc = botcfg.resolve_float("heal_gap")

    print("窗口 hwnd=%s 「%s」" % (hwnd, title))
    print("序列 (%s): %s" % (src, " ".join(keys)))
    print("间隔: %.2fs (%s)" % (gap, gsrc))

    frames = []

    def shot():
        a = g.shot()
        frames.append(a)
        return a

    a0 = shot()
    red, tan = MemBot2._menu_feat(a0)
    print("起始画面: 战斗=%s  菜单特征 red=%.3f tan=%.3f (菜单上限 %.2f)"
          % (is_battle(a0), red, tan, MemBot2.MENU_RED_CAP))

    if dry:
        print("\n[dry] 只发键, 不做校验 —— 盯着游戏画面看它走到哪一步:")
        for i, k in enumerate(keys, 1):
            g.key(k)
            time.sleep(max(0.05, gap))
            print("  %d/%d 按 %s" % (i, len(keys), k))
        g.flush_keys()
        if save:
            out = os.path.join(tempfile.gettempdir(), "heal_dry_end.png")
            _save(frames[-1], out)
        return 0

    macro = HealMacro(shot=shot, key=g.key, log=lambda m: print("  " + str(m)),
                      is_battle=lambda a: is_battle(a),
                      menu_feat=MemBot2._menu_feat,
                      menu_like=MemBot2._menu_like,
                      escape=_make_escape(g, print), gap=gap)
    res = macro.run(keys, why="单次实测")
    g.flush_keys()

    print("\n===== 结果 =====")
    print("按下的键 (%d): %s" % (len(res["sent"]), " ".join(res["sent"])))
    print("开菜单校验: %s" % ("✓ 画面变了" if res["opened"] else "✗ 按了没反应"))
    print("收尾校验  : %s (补 esc %d 次)"
          % ("✓ 已回到地图" if res["closed"] else "✗ 还在菜单/子页", res["esc_extra"]))
    print("耗时: %.1fs" % res["elapsed"])
    print("判定: %s%s" % ("✅ 成功" if res["ok"] else "❌ 失败: ", res["reason"]))
    print("\n如果失败, 请对着游戏画面把实际路径告诉开发者 (或自己改"
          "GUI 里的序列), 例如:")
    print("    多按了一下「下」走过头  ->  序列里去掉一个 下 再试")
    print("    停在「系统」子菜单没进去  ->  补一个 回车")
    print("    ⚠ 存档路径上「退出」「读档」是危险项, 务必确认每一步停在哪"
          "一行再按回车")
    if save:
        print("\n---- 截图 (系统临时目录) ----")
        out = os.path.join(tempfile.gettempdir(), "heal_%02d_%s.png")
        names = ["start", "after_open"]
        for i, fr in enumerate(frames):
            tag = names[i] if i < len(names) else ("frame%02d" % i)
            _save(fr, out % (i, tag))
        _save(frames[-1], os.path.join(tempfile.gettempdir(), "heal_end.png"))
    return 0 if res["ok"] else 1


def _save(a, path):
    try:
        from PIL import Image
        Image.fromarray(a.astype("uint8")).convert("RGB").save(path)
        print("  %s" % path)
    except Exception as e:
        print("  截图失败: %s" % e)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
