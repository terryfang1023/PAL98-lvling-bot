# -*- coding: utf-8 -*-
"""start_bot.bat 的构建: UTF-8 源 -> 仓库里的 GBK 产物。

为什么要有这一步 (2026-10-16 真机踩坑):
    中文 Windows 的 cmd 默认代码页是 936(GBK), 批处理文件必须跟它一致。
    如果 .bat 用 UTF-8 存、再 chcp 65001, 中文会被"间接误解码" -> 行边界
    错位 -> rem 注释的片段被当成命令执行, 表现为:
      * "此时不应有 xxx"
      * 或明明装了 Python 却说"没找到 Python"
    所以仓库里那份 .bat 必须是 GBK 字节。

怎么改 .bat:
    1. 编辑 start_bot.bat.utf8           (UTF-8, 可以直接用编辑器改)
    2. python tools/build_bat.py         (生成/更新 start_bot.bat)

用法:
    python tools/build_bat.py            # 源 -> 产物 (日常)
    python tools/build_bat.py --check    # 只比对是否一致, 不写盘
    python tools/build_bat.py --init     # 反方向: 从现成 .bat 导出源 (备用)

★ 产物不要手改 —— test_first_run.py 会断言「产物 == 源构建的结果」。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
SRC = os.path.join(BASE, "start_bot.bat.utf8")
DST = os.path.join(BASE, "start_bot.bat")


def to_gbk_bytes(s):
    """UTF-8 文本 -> GBK 字节(CRLF 换行); 有编不出来的字符时返回 None。"""
    bad = []
    for i, ch in enumerate(s):
        if ord(ch) < 128:
            continue
        try:
            ch.encode("gbk")
        except UnicodeEncodeError:
            bad.append((s[:i].count("\n") + 1, ch, hex(ord(ch))))
    if bad:
        print("!! 有 %d 个字符无法用 GBK 编码 (换成常用汉字或全角标点):"
              % len(bad))
        for ln, ch, cp in bad[:20]:
            print("   行 %d: %r (%s)" % (ln, ch, cp))
        return None
    body = s.replace("\r\n", "\n").replace("\n", "\r\n")
    return body.encode("gbk")


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""

    if mode == "--init":
        s = open(DST, "rb").read().decode("gbk").replace("\r\n", "\n")
        open(SRC, "w", encoding="utf-8", newline="").write(s)
        print("已从 %s 导出 UTF-8 源: %s (%d 行)" % (DST, SRC, s.count("\n")))
        return

    if not os.path.exists(SRC):
        print("找不到源文件: %s" % SRC)
        sys.exit(1)

    src = open(SRC, encoding="utf-8").read()
    payload = to_gbk_bytes(src)
    if payload is None:
        sys.exit(1)

    cur = open(DST, "rb").read() if os.path.exists(DST) else b""
    same = (cur == payload)
    print("源  : %s (%d 行)" % (SRC, src.count("\n")))
    print("产物: %s" % DST)
    print("  GBK 字节 %d | CRLF %d | LF-only %d | BOM %s"
          % (len(payload), payload.count(b"\r\n"),
             payload.count(b"\n") - payload.count(b"\r\n"),
             payload[:3] == b"\xef\xbb\xbf"))
    if mode == "--check":
        print("  与现有产物一致:", same)
        return
    if same:
        print("  无需改动 (已是最新)")
        return
    open(DST, "wb").write(payload)
    back = open(DST, "rb").read().replace(b"\r\n", b"\n").decode("gbk")
    print("  已写入 | 回读一致:", back == src.replace("\r\n", "\n"))


main()
