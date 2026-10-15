# -*- coding: utf-8 -*-
"""test_first_run.py — 一键启动 (start_bot.bat + tools/first_run.py) 回归测试。

覆盖:
  ① start_bot.bat 静态检查: 编码 GBK + CRLF + 无 BOM / chcp 936 /
     ★ 不用 goto 与标签 / ★ 解释器一律用绝对路径 (裸命令名加引号会让 py.exe
     报 errorlevel 101) / conda 系排在 py 启动器前 / 自己扫常见安装目录 /
     跳过 Microsoft Store 的 0 字节假 python /
     ★ 块内 echo 不含裸括号 (会让 cmd 提前闭合块, 报「此时不应有 xxx」) /
     括号配平 / ★ shift 剔除拖进来的参数 / %~dp0 存进 HERE /
     错误分支都有 pause / 标题不含大小写敏感的 "Pal";
  ② first_run.py 行为: 游戏目录候选顺序与去重 (**含"上两级"回归**) /
     check_game_dir 三种返回 / 记住游戏目录 / --yes 不读 stdin /
     缺依赖的检测分支 / 数据就绪时 step_data 不跑任何子进程 /
     轻量导入 (不拖 numpy / pal_bot, 保证依赖没装时也能跑) / run_tool 返回码;
  ③ .gitattributes 声明 *.bat 用 CRLF (GitHub 的 Download ZIP 也照它导出);
  ④ README 与实现一致: 提 start_bot.bat / 不再要求 yj2.dll / Python 版本一致;
  ⑤ 零副作用: game_dir.txt 已被 .gitignore 挡住。

零副作用: 所有写盘都在 tempfile 目录; 不动真仓库的 game_dir.txt。
"""
import argparse
import os
import sys
import tempfile

BASE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(BASE, "tools")
sys.path.insert(0, BASE)
sys.path.insert(0, TOOLS)

import first_run as FR                      # noqa: E402

OK, BAD = [], []


def check(name, cond, extra=""):
    (OK if cond else BAD).append(name)
    print(("  ok  " if cond else "FAIL  ") + name
          + (" | %s" % (extra,) if extra else ""))


TMP = tempfile.mkdtemp(prefix="test_first_run_")
BAT = os.path.join(BASE, "start_bot.bat")

def _has_bare_paren(t):
    """行里有没有**没转义**的 ( 或 ) —— ^( ^) 不算。"""
    j = 0
    while j < len(t):
        c = t[j]
        if c == "^" and j + 1 < len(t) and t[j + 1] in "()":
            j += 2
            continue
        if c in "()":
            return True
        j += 1
    return False


def bare_parens_in_blocks(lines):
    """找出「括号块内的 echo 行里含裸括号」的行 —— 那会让 cmd 提前闭合块。

    2026-10-16 真机踩到: 错误提示里写 "a) xxx" 这种裸右括号, cmd 会在那里
    闭合 if 块, 后面的行被当顶层命令执行 -> 报 "此时不应有 xxx"。
    全角括号（）不会触发, 所以提示里一律用全角。
    返回 (问题行列表, 最终块深度)。
    """
    bad = []
    depth = 0
    for i, ln in enumerate(lines, 1):
        t = ln.strip()
        if not t or t.startswith("rem"):
            continue
        was_in_block = depth > 0
        j = 0
        while j < len(t):
            c = t[j]
            if c == "^" and j + 1 < len(t) and t[j + 1] in "()":
                j += 2                      # ^( ^) 是转义过的, 不算
                continue
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
            j += 1
        if was_in_block and t.lower().startswith("echo") and _has_bare_paren(t):
            bad.append((i, t))
    return bad, depth


print("== 1. start_bot.bat 静态检查 ==")
raw = open(BAT, "rb").read()
src = raw.decode("gbk", "replace")
lines = src.splitlines()
code_lines = [l for l in lines if l.strip() and not l.strip().startswith("rem")]
check("文件存在", os.path.exists(BAT))
check("换行全是 CRLF (cmd 兼容)", raw.count(b"\n") == raw.count(b"\r\n")
      and raw.count(b"\n") > 20, "%d 行" % raw.count(b"\n"))
check("无 UTF-8 BOM (否则 cmd 会先报错)", not raw.startswith(b"\xef\xbb\xbf"))
check("★ 编码是 GBK/ANSI (不是 UTF-8 —— 中文被误解码会让行边界错位)",
      "没找到可用的 Python" in src,
      "utf-8 能解出中文 = 编码错了" if "没找到可用的 Python"
      in raw.decode("utf-8", "replace") else "ok")
check("首行 @echo off", lines[0].strip() == "@echo off", lines[0])
check("第二行 chcp 936 (跟 GBK 编码对齐)",
      lines[1].strip().startswith("chcp 936"), lines[1])
check("★ 不用 goto (cmd 解析批处理的老坑)",
      not any(l.strip().lower().startswith("goto") for l in lines))
check("★ 没有标签行 (以 : 开头的行)", not any(l.startswith(":") for l in lines))

# --- 找 Python 的几条硬规矩 (全都在 2026-10-16 那次事故里踩过) ---
check("★ 代码里不出现加了引号的裸命令名 \"py\" (会让 py.exe 参数解析错乱)",
      not any('"py"' in l for l in code_lines))
check("★ py 启动器用绝对路径 %SystemRoot%\\py.exe",
      "%SystemRoot%\\py.exe" in src)
check("★ 只有 py.exe 才追加 -3 参数",
      '"%PYEXE:~-6%"=="py.exe"' in src)
i_conda = src.find("anaconda3")
i_pyexe = src.find("%SystemRoot%\\py.exe")
check("★ conda 系排在 py 启动器之前 (conda 自带 numpy, 省一轮依赖安装)",
      0 <= i_conda < i_pyexe, "conda@%d py@%d" % (i_conda, i_pyexe))
for d in ("%USERPROFILE%\\anaconda3", "%USERPROFILE%\\miniconda3",
          "%ProgramData%\\Anaconda3"):
    check("扫过 %s" % d, d in src)
check("★ 扫 %LOCALAPPDATA%\\Programs\\Python\\Python3* (用户级安装)",
      "%LOCALAPPDATA%\\Programs\\Python\\Python3*" in src)
check("★ 扫 C:\\Python3* (系统级安装, 不进 PATH 也能找到)",
      "C:\\Python3*" in src)
check("再找 PATH 里的 python", "where python" in src)
check("★ 跳过 Microsoft Store 的 0 字节假 python (%%~zp 判大小)",
      '"%%~zp"=="0"' in src)
check("★ 解释器一律带引号执行 (%PYEXE% 是绝对路径)",
      '"%PYEXE%" %PYARGS%' in src)
check("用 %~x1/.exe 认「拖进来的解释器」", '"%~x1"==".exe"' in src)
check("★ 拖拽的第 1 个参数会被 shift 掉 (否则透传给 first_run 报错)",
      "if defined PYDROPPED shift" in src)
check("★ 用 %1..%9 而不是 %* (shift 不影响 %*, 会漏掉剔除)",
      "%*" not in src and "%1 %2 %3 %4 %5 %6 %7 %8 %9" in src)
dp0_lines = [l.strip() for l in code_lines if "%~dp0" in l]
check("★ %~dp0 只用在 cd 与 HERE 赋值这两处 (shift 会连 %0 一起移位)",
      len(dp0_lines) == 2 and dp0_lines[0].startswith("cd /d")
      and dp0_lines[1].startswith('set "HERE='), str(dp0_lines))
check("★ 记住解释器到 python_exe.txt", '> "%HERE%python_exe.txt" echo %PYEXE%' in src)
check("★ 下次优先读 python_exe.txt", 'set /p PYEXE=<"%HERE%python_exe.txt"' in src)

# --- cmd 语法层面的坑 ---
bad_parens, depth = bare_parens_in_blocks(lines)
check("★ 括号块内的 echo 不含裸括号 (会让 cmd 提前闭合块)",
      not bad_parens, str(bad_parens[:2]))
check("括号配平 (if/for 块都闭合)", depth == 0, "最终深度 %d" % depth)
check("调用 tools\\first_run.py", "tools\\first_run.py" in src)
check("失败分支会 pause (不会一闪而过)", src.count("pause") >= 2)
check("用 exit /b 返回码 (不是 exit)",
      "exit /b 1" in src and "exit /b 0" in src)
check("找不到 Python 时给下载地址", "python.org/downloads" in src)
check("提示勾选 Add to PATH", "Add python.exe to PATH" in src)
check("提示可以把 python.exe 拖到 bat 上", "拖到本文件上" in src)
check("失败时打印诊断信息 (PATH / where / py -0)",
      "set PATH" in src and "where python" in src)
title = next((l for l in lines if l.startswith("title ")), "")
check("★ 窗口标题不含大小写敏感的 'Pal' (找游戏窗口的判据)",
      bool(title) and "Pal" not in title, title.strip())
check(".bat 里没有裸的 python 命令 (一律走 %PYEXE%)",
      "\npython " not in src and " python -" not in src)

# --- 检查器自测: 拿两种已知坏写法喂进去, 必须报出来 ---
_bad_sample = ["if not defined X (", "    echo   a) 裸右括号", ")"]
check("★ 检查器能抓出块内裸右括号 (自测)", bare_parens_in_blocks(_bad_sample)[0])
_ok_sample = ["if not defined X (", "    echo   a. 全角（）没问题",
              "    echo   b^) 用 ^ 转义过的也没问题", ")"]
check("★ 检查器不误报全角括号 / 转义括号 (自测)",
      not bare_parens_in_blocks(_ok_sample)[0])

# --- UTF-8 源与产物必须一致 (改 .bat 一律改源再跑 build) ---
SRC_BAT = os.path.join(BASE, "start_bot.bat.utf8")
check("★ UTF-8 源在仓库里 (改 .bat 要改它, 再跑 tools/build_bat.py)",
      os.path.exists(SRC_BAT))
if os.path.exists(SRC_BAT):
    _s = open(SRC_BAT, encoding="utf-8").read()
    _want = _s.replace("\r\n", "\n").replace("\n", "\r\n").encode("gbk")
    check("★ 产物 == 由源构建的结果 (别手改 start_bot.bat)",
          _want == raw)
check("构建脚本在 tools/ 里", os.path.exists(os.path.join(TOOLS, "build_bat.py")))

print("== 2. first_run.py: 常量与轻量性 ==")
check("MIN_PY 是 3.9 (实测下限, 不是凭空写的 3.10)",
      FR.MIN_PY == (3, 9), FR.MIN_PY)
check("MIN_PY_HARD < MIN_PY", FR.MIN_PY_HARD < FR.MIN_PY)
check("依赖清单就是 requirements.txt 里那 4 个包",
      sorted({p for _, p in FR.DEPS})
      == sorted({"numpy", "Pillow", "psutil", "pywin32"}),
      sorted({p for _, p in FR.DEPS}))
src_fr = open(os.path.join(TOOLS, "first_run.py"), encoding="utf-8").read()
check("★ 不 import numpy / pal_bot (依赖没装时也得能跑)",
      "import numpy" not in src_fr and "import pal_bot" not in src_fr)
check("子进程一律用 sys.executable", src_fr.count("sys.executable") >= 5)
for kw in ("build_data.py", "build_scene_info.py", "scene_score.py", "bot_gui.py"):
    check("会调用 %s" % kw, kw in src_fr)
check("5 个开关都在 (--game/--detach/--no-launch/--no-score/--yes)",
      all(('"%s"' % k) in src_fr
          for k in ("--game", "--detach", "--no-launch", "--no-score", "--yes")))

print("== 3. 游戏目录: 候选顺序 / 校验 / 记住 ==")
tmp_txt = os.path.join(TMP, "game_dir.txt")
FR.GAME_TXT = tmp_txt
FR.ARGS = argparse.Namespace(game=None, yes=True, no_score=False, detach=False,
                            no_launch=True)
_old_env = os.environ.pop("PAL_GAME_DIR", None)

g_ok = os.path.join(TMP, "PAL_OK")            # 三个 MKF 齐全
g_bad = os.path.join(TMP, "PAL_BAD")          # 只缺 GOP.MKF
os.makedirs(g_ok)
os.makedirs(g_bad)
for n in FR.GAME_NEED:
    open(os.path.join(g_ok, n), "wb").close()
for n in FR.GAME_NEED[:2]:
    open(os.path.join(g_bad, n), "wb").close()

check("check_game_dir: 齐全 -> []", FR.check_game_dir(g_ok) == [])
check("check_game_dir: 缺一个 -> 报缺哪个",
      FR.check_game_dir(g_bad) == ["GOP.MKF"], FR.check_game_dir(g_bad))
check("check_game_dir: 路径不存在 -> None",
      FR.check_game_dir(os.path.join(TMP, "no_such_dir")) is None)
check("check_game_dir: 给的是文件 -> None",
      FR.check_game_dir(os.path.join(g_ok, "SSS.MKF")) is None)

c1 = FR.game_candidates()
parent = os.path.dirname(FR.REPO)
grand = os.path.dirname(parent)
third = os.path.dirname(grand)
check("候选里有仓库目录/上一级/上两级",
      (FR.REPO, "仓库目录") in c1 and (parent, "仓库上一级") in c1
      and (grand, "仓库上两级") in c1,
      [p for p, _ in c1])
check("★★ 上两级解析正确 (回归: 曾经少算一级跑到上三级)",
      grand in [p for p, _ in c1] and third not in [p for p, _ in c1])
check("无重复",
      len({os.path.normcase(p) for p, _ in c1}) == len(c1))
check("顺序: ... -> 仓库目录 -> 上一级 -> 上两级",
      [p for p, _ in c1][-3:] == [FR.REPO, parent, grand])

with open(tmp_txt, "w", encoding="utf-8") as f:
    f.write(g_ok + "\n")
c2 = FR.game_candidates()
check("game_dir.txt 排在最前",
      c2[0] == (os.path.abspath(g_ok), "game_dir.txt (上次记住的)"), c2[0])
FR.GAME_TXT = os.path.join(TMP, "还没生成.txt")
check("没有 game_dir.txt 时不该有该来源",
      not any("game_dir.txt" in how for _, how in FR.game_candidates()))
FR.ARGS = argparse.Namespace(game=g_bad, yes=True, no_score=False, detach=False,
                            no_launch=True)
check("--game 优先级最高",
      FR.game_candidates()[0] == (os.path.abspath(g_bad), "命令行 --game"))
os.environ["PAL_GAME_DIR"] = g_ok
FR.ARGS = argparse.Namespace(game=None, yes=True, no_score=False, detach=False,
                            no_launch=True)
check("认环境变量 PAL_GAME_DIR",
      (os.path.abspath(g_ok), "环境变量 PAL_GAME_DIR") in FR.game_candidates())
if _old_env is None:
    os.environ.pop("PAL_GAME_DIR", None)
else:
    os.environ["PAL_GAME_DIR"] = _old_env

FR.GAME_TXT = tmp_txt
if os.path.exists(tmp_txt):
    os.remove(tmp_txt)
FR.save_game_dir(g_ok)
check("save_game_dir 落盘内容正确",
      open(tmp_txt, encoding="utf-8").read().strip() == g_ok)
FR.save_game_dir(g_ok)
check("save_game_dir 可重复调用 (幂等)",
      open(tmp_txt, encoding="utf-8").read().strip() == g_ok)

print("== 4. 交互与分支 (都不碰真仓库) ==")
_had_input = "input" in FR.__dict__
_old_input = FR.__dict__.get("input")


def _boom(*a, **kw):
    raise AssertionError("不该读 stdin")


FR.__dict__["input"] = _boom
try:
    FR.ARGS = argparse.Namespace(game=None, yes=True, no_score=False,
                                detach=False, no_launch=True)
    check("--yes 时 _ask 不读 stdin, 直接返回默认值",
          FR._ask("游戏目录 (回车放弃): ") == "")
    check("--yes 时 _ask_yes 默认回答是", FR._ask_yes("现在启动游戏?") is True)
    FR.ARGS = argparse.Namespace(game=None, yes=False, no_score=False,
                                detach=False, no_launch=True)
    try:
        FR._ask("x: ")
        raised = False
    except AssertionError:
        raised = True
    check("非 --yes 时确实去读 stdin", raised)
finally:
    if _had_input:
        FR.__dict__["input"] = _old_input
    else:
        FR.__dict__.pop("input", None)

check("_console_utf8 可调用且不抛异常", FR._console_utf8() is None)
check("step_python 在本机返回 True", FR.step_python() is True)

_old_deps = FR.DEPS
FR.DEPS = FR.DEPS + (("no_such_module_xyz_123", "nopkg"),)
check("缺依赖能被检出 (含不存在的模块)",
      [m for m, _ in FR.missing_deps()] == ["no_such_module_xyz_123"],
      FR.missing_deps())
FR.DEPS = _old_deps
check("本机依赖齐全 (numpy/Pillow/psutil/pywin32)", FR.missing_deps() == [],
      FR.missing_deps())
check("★ 装完依赖会重跑 .pth (pywin32 的 win32gui 同进程才可见)",
      src_fr.rindex("refresh_import_paths()")
      > src_fr.index('"pip", "install"'))
check("refresh_import_paths 可调用且幂等",
      FR.refresh_import_paths() is None and FR.refresh_import_paths() is None)
check("重跑 .pth 后依赖判定不变 (本机已齐 -> 仍为 [])",
      FR.missing_deps() == [])

check("run_tool 成功 -> True",
      FR.run_tool("自检成功", ["-c", "pass"], "0 秒") is True)
check("run_tool 非零返回码 -> False",
      FR.run_tool("自检失败", ["-c", "import sys; sys.exit(3)"], "0 秒") is False)

_wins = FR.pal_windows()
check("pal_windows 返回列表且判据正确 (含 'Pal' 不含 '挂机机器人')",
      isinstance(_wins, list)
      and all("Pal" in t and "挂机机器人" not in t for _, t in _wins),
      _wins[:1] or "当前没有游戏窗口 (也算通过)")

_calls = []
_old_rt = FR.run_tool
FR.run_tool = lambda *a, **kw: _calls.append(a) or True
_old_repo = FR.REPO
FR.ARGS = argparse.Namespace(game=None, yes=True, no_score=False, detach=False,
                            no_launch=True)
ok_ready = FR.step_data(g_ok)
check("★ 数据齐备时 step_data 不跑任何子进程 (幂等关键)",
      ok_ready is True and _calls == [], "调用: %s" % _calls)
FR.REPO = os.path.join(TMP, "empty_repo")
os.makedirs(FR.REPO, exist_ok=True)
check("缺数据 + 没有游戏目录 -> 明确失败 (不硬启界面)",
      FR.step_data(None) is False)
FR.REPO = _old_repo
FR.run_tool = _old_rt

print("== 5. .gitattributes 与忽略规则 ==")
ga = os.path.join(BASE, ".gitattributes")
check(".gitattributes 存在", os.path.exists(ga))
g = open(ga, encoding="utf-8").read() if os.path.exists(ga) else ""
check("★ 声明 *.bat 检出/导出为 CRLF (Download ZIP 也照它走)",
      "*.bat text eol=crlf" in g)
gi = open(os.path.join(BASE, ".gitignore"), encoding="utf-8").read()
check("game_dir.txt 已忽略 (每台机器不同, 不入库)", "game_dir.txt" in gi)
check("python_exe.txt 已忽略 (每台机器不同, 不入库)", "python_exe.txt" in gi)

print("== 6. README 与实现一致 ==")
rd = open(os.path.join(BASE, "README.md"), encoding="utf-8").read()
check("README 讲了 start_bot.bat", "start_bot.bat" in rd)
check("★ README 已不再把 yj2.dll 列为必需项",
      "不再需要 `yj2.dll`" in rd and "生成全图可走域必须用" not in rd)
check("README 的 Python 版本与 MIN_PY 一致 (3.9+)",
      "Python | **3.9+" in rd and "3.10+" not in rd)
check("README 项目结构里有 tools/first_run.py", "tools/first_run.py" in rd)
check("README 项目结构里有 test_first_run.py", "test_first_run.py" in rd)

print("\n结果: %d ok / %d FAIL" % (len(OK), len(BAD)))
if BAD:
    for b in BAD:
        print("  FAIL:", b)
    sys.exit(1)
