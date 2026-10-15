# -*- coding: utf-8 -*-
"""first_run.py — 一键前置: 装依赖 -> 找游戏 -> 生成数据 -> 打开机器人界面

由 `start_bot.bat` 双击调用, 也可以直接 `python tools/first_run.py` 跑。

做的事 (每一步都能单独跳过, **可反复双击**):
    ① 检查 Python 版本 (>=3.10)
    ② 缺哪个依赖才装 (numpy / Pillow / psutil / pywin32)
    ③ 定位游戏目录: --game -> PAL_GAME_DIR -> game_dir.txt -> 仓库根/上一级/上两级
       -> 还找不到就让你把文件夹拖进窗口; 找到后记进 game_dir.txt
    ④ 缺哪个生成物才生成 (map_walk.npy / gyt_scene_report.txt / map_walk2.npy /
       scene_info.json / scene_score.json)
    ⑤ 没检测到游戏窗口时可顺手帮你启动 sdlpal.exe
    ⑥ 打开 bot_gui.py

设计原则 (与项目其它工具一致):
  · **幂等**: 第二次双击几乎立刻进界面 (全部走"已就绪"分支)
  · **不瞎走**: 关键文件没生成出来就明确报错, 不硬启界面
  · **零副作用**: 只写自己的生成物与 `game_dir.txt`, 不碰台账 / 不停机器人

用法:
    python tools/first_run.py                     # 全新机器: 全套前置 + 开界面
    python tools/first_run.py --game "D:\\PAL98"   # 指定游戏目录
    python tools/first_run.py --no-launch          # 只做前置, 不开界面 (装机/排查)
    python tools/first_run.py --detach             # 后台启动界面, 本窗口可直接关
    python tools/first_run.py --yes                # 所有询问都回答"是"
    python tools/first_run.py --no-score           # 不生成 scene_score.json (省 20 秒)
"""
import argparse
import ctypes
import importlib.util
import os
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(REPO, "tools")
REQ = os.path.join(REPO, "requirements.txt")
GAME_TXT = os.path.join(REPO, "game_dir.txt")
GUI = os.path.join(REPO, "bot_gui.py")

MIN_PY = (3, 9)          # 实测下限: 3.9.19 上全量 68 个文件都能编译 (2026-10-15)
MIN_PY_HARD = (3, 7)     # 再低就直接拦 (需要 f-string / reconfigure / from_bytes)
PY_URL = "https://www.python.org/downloads/windows/"
# 生成地图数据要的游戏资源 (三者缺一不可)
GAME_NEED = ("SSS.MKF", "MAP.MKF", "GOP.MKF")
# 界面点"开始"必需的数据文件 (与 bot_gui.REQUIRED_DATA 一致)
DATA_NEED = ("map_walk.npy", "gyt_scene_report.txt")
# 模块名 -> pip 包名
DEPS = (("numpy", "numpy"), ("PIL", "Pillow"), ("psutil", "psutil"),
        ("win32gui", "pywin32"), ("win32process", "pywin32"))

OK, SKIP, WARN, ERR, RUN = "[OK]  ", "[跳过]", "[注意]", "[错误]", "[运行]"
ARGS = None


# ---------------------------------------------------------------- 基础设施
def _console_utf8():
    """把控制台切到 UTF-8 并让 stdout 用 UTF-8 —— 否则中文/拖进来的路径会乱码。"""
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    try:
        k = ctypes.windll.kernel32
        k.SetConsoleOutputCP(65001)
        k.SetConsoleCP(65001)
    except Exception:
        pass


def say(fmt, *a):
    print(fmt % a if a else fmt, flush=True)


def _ask(prompt, default=""):
    """问一句。非交互 (管道/重定向) 或 --yes 时直接返回 default。"""
    if ARGS is not None and ARGS.yes:
        say("      %s -> %s (--yes 自动回答)", prompt, default or "放弃")
        return default
    try:
        r = input(prompt)
    except (EOFError, KeyboardInterrupt):
        say()
        return default
    return r


def _ask_yes(prompt):
    r = _ask("%s [Y/n] " % prompt, default="y")
    return (r or "y").strip().lower() in ("", "y", "yes", "是")


# ---------------------------------------------------------------- ① Python
def step_python():
    v = sys.version_info
    if v[:2] < MIN_PY_HARD:
        say("%s 当前 Python %d.%d 太旧, 至少要 %d.%d",
            ERR, v[0], v[1], MIN_PY_HARD[0], MIN_PY_HARD[1])
        say("      下载: %s", PY_URL)
        say("      安装时**务必**勾选 Add python.exe to PATH")
        return False
    bits = 64 if sys.maxsize > 2 ** 32 else 32
    say("%s Python %d.%d.%d (%d 位)  %s", OK, v[0], v[1], v[2], bits, sys.executable)
    if v[:2] < MIN_PY:
        say("      %s 低于实测下限 %d.%d, 若报语法错请升级 Python",
            WARN, MIN_PY[0], MIN_PY[1])
    if bits == 32:
        say("      %s 32 位 Python 没实测过; 万一读不到内存请换 64 位", WARN)
    return True


# ---------------------------------------------------------------- ② 依赖
def missing_deps():
    out = []
    for mod, pkg in DEPS:
        try:
            if importlib.util.find_spec(mod) is None:
                out.append((mod, pkg))
        except Exception:
            out.append((mod, pkg))
    return out


def refresh_import_paths():
    """让"刚 pip 装上的包"马上能被本进程看见。

    ★ 为什么必须有这个 (2026-10-15 真机踩到): **pywin32 是靠 `.pth` 文件**把
    `site-packages\\win32` 加进 `sys.path` 的, 而 `.pth` 只在**解释器启动时**
    处理一次 —— 于是同一个进程里 `pip install pywin32` 之后, `win32gui` 依然
    导不到: 实测 pip 返回 0, 我们却报"还缺 win32gui/win32process", 新机器上
    第一次双击就变成"前置失败"(第二次才对)。这里手动把 site 目录的 `.pth`
    重跑一遍 + 清 import 缓存, 让本进程也能立刻用。
    """
    try:
        import importlib
        import site
        dirs = []
        for getter in ("getsitepackages", "getusersitepackages"):
            try:
                v = getattr(site, getter)()
                dirs.extend(v if isinstance(v, (list, tuple)) else [v])
            except Exception:
                pass
        for p in dirs:
            if p and os.path.isdir(p):
                try:
                    site.addsitedir(p)       # 会重新执行目录下的 .pth
                except Exception:
                    pass
        importlib.invalidate_caches()
        for m in ("win32", "win32gui", "win32process", "pythoncom", "pywintypes"):
            sys.modules.pop(m, None)
    except Exception:
        pass


def step_deps():
    miss = missing_deps()
    if not miss:
        say("%s 依赖已就绪 (numpy / Pillow / psutil / pywin32)", SKIP)
        return True
    pkgs = sorted({p for _, p in miss})
    say("%s 缺少依赖: %s -> 开始安装 (首次约 10~60 秒)", RUN, ", ".join(pkgs))
    if not os.path.exists(REQ):
        say("%s 找不到 %s", ERR, REQ)
        return False
    rc = subprocess.call([sys.executable, "-m", "pip", "install",
                          "--disable-pip-version-check", "-r", REQ], cwd=REPO)
    refresh_import_paths()        # ★ pywin32 的 .pth 要手动重跑, 见函数注释
    miss = missing_deps()
    if rc != 0 or miss:
        say("%s 依赖没装上 (pip 返回 %d, 还缺 %s)", ERR, rc,
            ", ".join(m for m, _ in miss) or "-")
        say("      常见原因: 没联网 / Python 装在 Program Files 需要管理员权限 / pip 缺失")
        say("      国内可换镜像重试 (复制这一行到命令行):")
        say('        "%s" -m pip install -r "%s" -i https://pypi.tuna.tsinghua.edu.cn/simple',
            sys.executable, REQ)
        return False
    say("%s 依赖安装完成", OK)
    return True


# ---------------------------------------------------------------- ③ 游戏目录
def check_game_dir(p):
    """返回 None=不是目录, []=合格, ["x.MKF",...]=缺文件。"""
    if not p or not os.path.isdir(p):
        return None
    miss = [f for f in GAME_NEED if not os.path.exists(os.path.join(p, f))]
    return miss


def save_game_dir(p):
    try:
        old = ""
        if os.path.exists(GAME_TXT):
            old = open(GAME_TXT, encoding="utf-8", errors="replace").read().strip()
        if old != p:
            with open(GAME_TXT, "w", encoding="utf-8") as f:
                f.write(p + "\n")
            say("      已记住游戏目录 (下次不用再问): %s", GAME_TXT)
    except Exception as ex:
        say("      %s 记不住游戏目录 (%s), 不影响本次运行", WARN, ex)


def game_candidates():
    """按优先级列出 (路径, 来源说明)。"""
    out = []
    if ARGS and ARGS.game:
        out.append((os.path.abspath(ARGS.game), "命令行 --game"))
    env = os.environ.get("PAL_GAME_DIR")
    if env:
        out.append((os.path.abspath(env), "环境变量 PAL_GAME_DIR"))
    if os.path.exists(GAME_TXT):
        try:
            t = open(GAME_TXT, encoding="utf-8", errors="replace").read()
            t = t.strip().strip('"').strip("'")
            if t:
                out.append((os.path.abspath(t), "game_dir.txt (上次记住的)"))
        except Exception:
            pass
    p = REPO
    for i, name in ((0, "仓库目录"), (1, "仓库上一级"), (2, "仓库上两级")):
        q = p
        for _ in range(i):
            q = os.path.dirname(q)
        if q:
            out.append((q, name))
    # 去重保序
    seen, uniq = set(), []
    for q, how in out:
        k = os.path.normcase(q)
        if k not in seen:
            seen.add(k)
            uniq.append((q, how))
    return uniq


def step_find_game():
    near_miss = []
    for p, how in game_candidates():
        miss = check_game_dir(p)
        if miss == []:
            say("%s 游戏目录: %s   (%s)", OK, p, how)
            save_game_dir(p)
            depth = 0
            q = REPO
            while q and os.path.normcase(q) != os.path.normcase(p):
                q = os.path.dirname(q)
                depth += 1
                if depth > 4:
                    break
            if depth > 2:
                say("      %s 游戏目录在仓库的上 %d 级 (超过 2 级): 生成数据没问题,", WARN, depth)
                say("      但运行时『物品名/窗口参数』会退化成占位符; 建议把 pal98-bot")
                say("      直接放进游戏目录, 或设 PAL_GAME_DIR 环境变量 (详见 README)")
            return p
        if miss:
            near_miss.append((p, how, miss))
    for p, how, miss in near_miss[:4]:
        say("%s 候选 %s (%s) 里缺少: %s", WARN, p, how, ", ".join(miss))

    say("")
    say("找不到游戏目录 —— 请告诉我《仙剑98柔情版》装在哪")
    say("  · 把游戏文件夹**拖进这个窗口**再按回车, 或粘贴完整路径")
    say("  · 该目录里要有: %s", "  ".join(GAME_NEED))
    for _ in range(3):
        raw = _ask("游戏目录 (直接回车放弃): ")
        p = (raw or "").strip().strip('"').strip("'")
        if not p:
            break
        miss = check_game_dir(p)
        if miss == []:
            say("%s 游戏目录: %s", OK, p)
            save_game_dir(p)
            return p
        if miss is None:
            say("%s 这不像一个存在的文件夹: %s", WARN, p)
        else:
            say("%s %s 里缺少: %s", WARN, p, ", ".join(miss))
    say("%s 没有可用的游戏目录 -> 跳过数据生成", WARN)
    say("      数据文件已经存在的老机器不受影响; 新机器请先准备游戏本体")
    return None


# ---------------------------------------------------------------- ④ 生成数据
def run_tool(title, argv, secs):
    say("%s %s (约 %s) ...", RUN, title, secs)
    t = time.time()
    try:
        rc = subprocess.call([sys.executable] + argv, cwd=REPO)
    except Exception as ex:
        say("%s %s 起不来: %s", ERR, title, ex)
        return False
    if rc != 0:
        say("%s %s 失败 (返回码 %d), 上面的输出就是原因", ERR, title, rc)
        return False
    say("%s %s 完成 (%.1f 秒)", OK, title, time.time() - t)
    return True


def step_data(game):
    exist = lambda n: os.path.exists(os.path.join(REPO, n))       # noqa: E731
    # 地图 + 门清单 (三件套, 由 build_data.py 一次生成)
    trio = [n for n in ("map_walk.npy", "gyt_scene_report.txt",
                        "map_walk2.npy") if not exist(n)]
    need_game = bool(trio)
    if need_game and not game:
        say("%s 缺地图数据 (%s), 但没有游戏目录 -> 没法生成",
            ERR, ", ".join(trio))
        say("      生成地图数据需要游戏目录里的 %s", " / ".join(GAME_NEED))
        return False
    if need_game:
        say("%s 缺: %s", RUN, ", ".join(trio))
        if not run_tool("生成地图数据 (解码 MAP.MKF)",
                        [os.path.join(TOOLS, "build_data.py"), game], "20 秒"):
            say("      检查: 游戏目录里 %s 是否齐全", " / ".join(GAME_NEED))
            return False
    else:
        say("%s 地图数据已就绪 (map_walk.npy / gyt_scene_report.txt)", SKIP)

    # 要素清单 (老人机缺它也能跑, 只是退回 v15 巡逻模式)
    if exist("scene_info.json"):
        say("%s 要素清单已就绪 (scene_info.json)", SKIP)
    elif game:
        run_tool("生成要素清单 (离线读 SSS.MKF)",
                 [os.path.join(TOOLS, "build_scene_info.py"), "--game", game],
                 "3 秒")
    else:
        say("%s 无游戏目录 -> 跳过要素清单 (界面可稍后点「刷新怪点/宝箱」)", WARN)

    # 场景评分 (可选: 影响「自动换场景」的耐心值)
    if ARGS.no_score:
        say("%s 按要求不生成场景评分", SKIP)
    elif exist("scene_score.json"):
        say("%s 场景评分已就绪 (scene_score.json)", SKIP)
    elif exist("scene_info.json"):
        run_tool("生成场景评分", [os.path.join(TOOLS, "scene_score.py")], "25 秒")
    else:
        say("%s 没有要素清单 -> 跳过场景评分", WARN)

    miss = [n for n in DATA_NEED if not exist(n)]
    if miss:
        say("%s 必须的数据文件还是缺: %s", ERR, ", ".join(miss))
        return False
    return True


# ---------------------------------------------------------------- ⑤ 游戏窗口
def pal_windows():
    """标题含 'Pal' 且不含 '挂机机器人' 的窗口 (与机器人同一判据)。"""
    found = []
    user32 = ctypes.windll.user32
    proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def cb(h, _l):
        n = user32.GetWindowTextLengthW(h)
        if n > 0:
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(h, buf, n + 1)
            t = buf.value
            if "Pal" in t and "挂机机器人" not in t:
                found.append((int(h), t))
        return True
    user32.EnumWindows(proc(cb), 0)
    return found


def step_game(game):
    w = pal_windows()
    if w:
        say("%s 游戏已在运行: %s", OK, w[0][1].strip())
        return True
    say("%s 没检测到游戏窗口 (标题含 'Pal')", WARN)
    exe = None
    if game:
        for n in ("sdlpal.exe", "sdlpal.hud.exe"):
            if os.path.exists(os.path.join(game, n)):
                exe = os.path.join(game, n)
                break
    if exe and _ask_yes("现在帮你启动游戏 (%s) ?" % os.path.basename(exe)):
        try:
            subprocess.Popen([exe], cwd=game)
        except Exception as ex:
            say("%s 启动游戏失败: %s", WARN, ex)
            return False
        say("%s 等游戏窗口出现 (最多 30 秒) ...", RUN)
        for _ in range(30):
            time.sleep(1)
            if pal_windows():
                say("%s 游戏窗口已出现", OK)
                return True
        say("%s 等不到窗口, 请手动双击游戏; 界面里可随时点「检测游戏窗口」", WARN)
        return False
    say("      那就手动启动游戏; 进界面后把队伍走到想刷的地图, 再点「▶ 开始」")
    return False


# ---------------------------------------------------------------- ⑥ 开界面
def step_launch(detach):
    if not os.path.exists(GUI):
        say("%s 找不到 %s", ERR, GUI)
        return 1
    if detach:
        pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        exe = pyw if os.path.exists(pyw) else sys.executable
        flags = 0x00000008 | 0x00000200          # DETACHED_PROCESS|NEW_GROUP
        p = subprocess.Popen([exe, GUI], cwd=REPO, creationflags=flags,
                             stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        time.sleep(3)
        if p.poll() is not None:
            say("%s 界面启动后立刻退出 (返回码 %s)", ERR, p.returncode)
            say("      手动跑一次看报错: python bot_gui.py")
            return 1
        say("%s 机器人界面已后台启动 —— 这个窗口可以直接关掉", OK)
        return 0
    say("%s 正在打开机器人界面 …(关掉界面后本窗口自动结束)", RUN)
    try:
        return subprocess.call([sys.executable, GUI], cwd=REPO)
    except KeyboardInterrupt:
        return 0


# ---------------------------------------------------------------- 主流程
def main():
    global ARGS
    ap = argparse.ArgumentParser(
        description="仙剑98 挂机机器人 一键启动 (装依赖/找游戏/生成数据/开界面)")
    ap.add_argument("--game", help="游戏目录 (含 SSS.MKF/MAP.MKF/GOP.MKF)")
    ap.add_argument("--detach", action="store_true", help="后台启动界面, 本窗口可关")
    ap.add_argument("--no-launch", action="store_true", help="只做前置, 不开界面")
    ap.add_argument("--no-score", action="store_true", help="跳过场景评分 (省 20 秒)")
    ap.add_argument("--yes", action="store_true", help="所有询问都自动确认")
    ARGS = ap.parse_args()
    _console_utf8()

    say("=" * 64)
    say(" 仙剑98 挂机机器人 —— 一键启动 (仓库: %s)", REPO)
    say("=" * 64)
    if not step_python():
        return 1
    if not step_deps():
        return 1
    game = step_find_game()
    if not step_data(game):
        return 1
    step_game(game)
    if ARGS.no_launch:
        say("")
        say("%s 前置全部完成 (--no-launch 不开界面)。", OK)
        say("      以后双击 start_bot.bat 即可直接进界面; 界面里点「▶ 开始」开始挂机。")
        return 0
    say("")
    return step_launch(ARGS.detach)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        say("\n已取消。")
        sys.exit(1)
