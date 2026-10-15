# -*- coding: utf-8 -*-
"""make_icon.py — 生成 pal98-bot 的程序图标 (assets/pal98bot.ico + .png)。

设计
----
仙剑98 的挂机机器人 —— 图标 = **一把竖剑 + 右下角循环箭头 (挂机)**,
底色用游戏本体的砖红/深棕面板色 (148,32,24 → 62,28,13, 见 MEMORY §1 的
真机标定), 剑身用 HP 暖白 (212,200,168) 系, 金护手 (230,182,88),
循环箭头用战斗界面 MP 绿 (80,184,148)。

为什么自己写 ICO 容器
--------------------
1) PIL 的多尺寸 ICO 是**把大图缩小**, 16px 会糊成一团; 这里每个尺寸都按目标
   像素**原生重画**(4x 超采样后降采样, 只为了抗锯齿), 小尺寸才干净。
2) 用 32bpp **DIB(BMP) 条目**而不是 PNG 条目 —— Windows 的 LoadImage
   (tkinter `wm iconbitmap` 走的就是它) 对 PNG 压缩的 ICO 条目支持不稳。

尺寸: 16/20/24/32/48/64/128/256 (Windows 任务栏 16/32, Alt+Tab 48/64,
     资源管理器大图标 128/256)。

用法:  python tools/make_icon.py
"""
import os
import struct

from PIL import Image, ImageDraw

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(BASE, "assets")
SS = 4                       # 超采样倍数 (只用于抗锯齿)

# ---- 调色板 (取自游戏本体真机标定 + 界面用色) ----
BRICK = (148, 32, 24)        # 砖红面板
DARKBROWN = (62, 28, 13)     # 深棕面板
BRICK_L = (176, 54, 38)      # 渐变亮端
WARMWHITE = (212, 200, 168)  # HP 暖白 (剑身高光)
STEEL = (238, 232, 214)      # 剑身
STEEL_SH = (186, 176, 150)   # 剑身暗侧
GOLD = (230, 182, 88)        # 护手/剑柄头
GOLD_D = (166, 118, 42)      # 金色暗边
GRIP = (74, 40, 20)          # 剑柄缠绳
GRIP_D = (48, 24, 10)
MPGREEN = (80, 184, 148)     # MP 绿 -> 循环箭头
MPGREEN_D = (46, 138, 106)


def _lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def _background(n):
    """对角渐变 (亮 -> 暗) 的整幅底图, 尺寸 n×n。"""
    g = Image.new("RGB", (n, n))
    px = []
    den = float(2 * (n - 1)) or 1.0
    for y in range(n):
        for x in range(n):
            px.append(_lerp(BRICK_L, DARKBROWN, (x + y) / den))
    g.putdata(px)
    return g


def draw_icon(size):
    """按目标像素 size 原生绘制 (4x 超采样后降采样只为抗锯齿)。"""
    S = size * SS
    big = _bg_icon(S)
    return big.resize((size, size), Image.LANCZOS)


def _bg_icon(S):
    """尺寸 S(=size*SS) 的画布上画整枚图标。"""
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    # --- 圆角底 (对角渐变 + 圆角遮罩) ---
    r = max(2, int(round(S * 0.185)))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=r, fill=255)
    img.paste(_background(S), (0, 0), mask)

    d = ImageDraw.Draw(img)
    cx = S / 2.0

    # --- 内描边 (金) ---
    ins = S * 0.045
    w = max(1, int(round(S * 0.022)))
    d.rounded_rectangle([ins, ins, S - 1 - ins, S - 1 - ins],
                        radius=max(1, int(round(r - ins))), outline=GOLD_D + (255,),
                        width=w)

    # --- 剑 ---
    tip = 0.125 * S
    guard_y = 0.545 * S
    bw = 0.080 * S                      # 剑身半宽
    # 剑身 (菱形头 -> 直身)
    d.polygon([(cx, tip), (cx + bw, tip + 0.085 * S), (cx + bw, guard_y),
               (cx - bw, guard_y), (cx - bw, tip + 0.085 * S)], fill=STEEL)
    # 右暗侧 (立体感)
    d.polygon([(cx, tip), (cx + bw, tip + 0.085 * S), (cx + bw, guard_y),
               (cx, guard_y)], fill=STEEL_SH)
    if S >= 48:                          # 血槽 (小尺寸会糊, 只在大的画)
        d.line([(cx, tip + 0.10 * S), (cx, guard_y - 0.03 * S)],
               fill=WARMWHITE, width=max(1, int(round(S * 0.014))))
    # 护手
    gy0, gy1 = guard_y, guard_y + 0.062 * S
    d.rounded_rectangle([cx - 0.225 * S, gy0, cx + 0.225 * S, gy1],
                        radius=max(1, int(round(S * 0.022))), fill=GOLD)
    d.rectangle([cx - 0.225 * S, gy1 - 0.020 * S, cx + 0.225 * S, gy1], fill=GOLD_D)
    # 剑柄
    hy0, hy1 = gy1, 0.775 * S
    d.rectangle([cx - 0.047 * S, hy0, cx + 0.047 * S, hy1], fill=GRIP)
    if S >= 48:
        for k in (0.28, 0.62):
            y = hy0 + (hy1 - hy0) * k
            d.line([(cx - 0.047 * S, y), (cx + 0.047 * S, y)], fill=GRIP_D,
                   width=max(1, int(round(S * 0.016))))
    # 剑柄头
    pr = 0.058 * S
    d.ellipse([cx - pr, hy1 - pr * 0.15, cx + pr, hy1 + pr * 1.85], fill=GOLD)
    if S >= 48:
        d.ellipse([cx - pr, hy1 - pr * 0.15, cx + pr, hy1 + pr * 1.85],
                  outline=GOLD_D, width=max(1, int(round(S * 0.012))))

    # --- 右下角循环箭头 = 挂机 (24px 以下画不下, 直接省掉) ---
    if size_of(S) >= 32:
        acx, acy, ar = 0.795 * S, 0.795 * S, 0.135 * S
        aw = max(1, int(round(0.052 * S)))
        d.arc([acx - ar, acy - ar, acx + ar, acy + ar], start=118, end=392,
              fill=MPGREEN, width=aw)
        # 箭头尖 (弧的收尾处, 指向右下方)
        import math
        a = math.radians(392 % 360)
        tx, ty = acx + ar * math.cos(a), acy + ar * math.sin(a)
        k = 0.062 * S
        d.polygon([(tx + k * 1.15, ty - k * 0.15), (tx - k * 0.9, ty - k * 0.75),
                   (tx - k * 0.35, ty + k * 1.05)], fill=MPGREEN_D)
    return img


def size_of(S):
    """由画布边长反推目标尺寸 (SS 倍)。"""
    return int(round(S / float(SS)))


# ----------------------------------------------------------------------
#  ICO 容器 (32bpp DIB 条目, 每个尺寸独立绘制)
# ----------------------------------------------------------------------
def _dib_entry(img):
    """RGBA -> ICO 里的 DIB 数据 (BITMAPINFOHEADER + BGRA 倒序 + AND 掩码)。"""
    w, h = img.size
    px = img.convert("RGBA").load()
    body = bytearray()
    for y in range(h - 1, -1, -1):                    # DIB 自下而上
        for x in range(w):
            r, g, b, a = px[x, y]
            body += bytes((b, g, r, a))
    mask_row = ((w + 31) // 32) * 4                   # 1bpp, 每行 4 字节对齐
    body += bytes(mask_row * h)                       # 全 0 = 全显 (alpha 生效)
    hdr = struct.pack("<IiiHHIIiiII", 40, w, h * 2, 1, 32, 0,
                      len(body), 0, 0, 0, 0)
    return bytes(hdr) + bytes(body)


def write_ico(path, sizes):
    imgs = [(s, draw_icon(s)) for s in sizes]
    entries, payloads, off = [], [], 6 + 16 * len(imgs)
    for s, im in imgs:
        data = _dib_entry(im)
        w = 0 if s >= 256 else s                      # 256 在 ICO 里记 0
        h = 0 if s >= 256 else s
        entries.append(struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32,
                                   len(data), off))
        payloads.append(data)
        off += len(data)
    with open(path, "wb") as f:
        f.write(struct.pack("<HHH", 0, 1, len(imgs)))
        for e in entries:
            f.write(e)
        for p in payloads:
            f.write(p)
    return path


def main():
    if not os.path.isdir(ASSETS):
        os.makedirs(ASSETS)
    ico = os.path.join(ASSETS, "pal98bot.ico")
    png = os.path.join(ASSETS, "pal98bot.png")
    sizes = [16, 20, 24, 32, 48, 64, 128, 256]
    write_ico(ico, sizes)
    draw_icon(256).save(png)
    print("写出 %s (%.1f KB, %d 个尺寸: %s)"
          % (ico, os.path.getsize(ico) / 1024.0, len(sizes), sizes))
    print("写出 %s" % png)


if __name__ == "__main__":
    main()
