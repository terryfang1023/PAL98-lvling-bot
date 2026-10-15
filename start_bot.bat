@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title 仙剑98 挂机机器人

rem ============================================================================
rem  双击我 = 装依赖 -> 找游戏目录 -> 生成数据 -> 打开机器人界面
rem
rem  游戏目录放在本目录 (或上一级 / 上两级) 时不用填路径;
rem  放别处的话第一次运行会问一次, 之后记在 game_dir.txt 里, 再双击直通界面。
rem
rem  全部实际逻辑都在 tools\first_run.py 里 (中文提示也在那边),
rem  这里只负责找到 Python 并把它拉起来 —— 所以刻意不用 goto/标签,
rem  避免 cmd.exe 解析 LF-only 批处理时的已知毛病。
rem ============================================================================

set "PYEXE="
set "PYARGS="

rem ① python.org 的 py 启动器 (最可靠)
py -3 -c "import sys" >nul 2>nul && (set "PYEXE=py" & set "PYARGS=-3")

rem ② PATH 里的 python (跳过 Microsoft Store 的假 python.exe)
if not defined PYEXE (
    for /f "delims=" %%p in ('where python 2^>nul') do (
        if not defined PYEXE (
            echo %%p | findstr /i "WindowsApps" >nul || set "PYEXE=%%p"
        )
    )
)
if defined PYEXE (
    "%PYEXE%" %PYARGS% -c "import sys" >nul 2>nul || (set "PYEXE=" & set "PYARGS=")
)

if not defined PYEXE (
    echo.
    echo   [错误] 没找到可用的 Python  ——  需要 Python 3.9 以上, 建议 64 位
    echo.
    echo   1^) 打开  https://www.python.org/downloads/windows/
    echo   2^) 下载安装 Python 3 的 Windows installer ^(64-bit^)
    echo   3^) 安装第一屏务必勾选  Add python.exe to PATH
    echo   4^) 装完重新双击本文件
    echo.
    echo   已经装过 Python 还是报这个? 说明它没进 PATH:
    echo     重装并勾选 Add to PATH, 或直接用 py 启动器 ^(python.org 版自带^)。
    echo.
    pause
    exit /b 1
)

if not exist "%~dp0tools\first_run.py" (
    echo.
    echo   [错误] 找不到 tools\first_run.py
    echo   请把整个 pal98-bot 文件夹一起解压/复制, 不要只拿这一个 bat 文件。
    echo.
    pause
    exit /b 1
)

"%PYEXE%" %PYARGS% "%~dp0tools\first_run.py" %*
if errorlevel 1 (
    echo.
    echo   前置步骤没有成功 —— 请把上面的报错截图发给作者,
    echo   或看 README.md 的「常见问题」一节。
    echo.
    pause
    exit /b 1
)

exit /b 0
