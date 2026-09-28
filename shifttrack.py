# -*- coding: utf-8 -*-
"""场景位移测量: FFT-NCC 模板匹配, 纯numpy实现 (无cv2环境)
用法: patch(小图)在img(大图)中找最佳匹配位置, 得到场景滚动位移"""
import numpy as np


def _winsum(a, h, w):
    """所有 h×w 窗口的元素和, 返回 (H-h+1, W-w+1)"""
    c = np.cumsum(np.cumsum(a, axis=0), axis=1)
    c = np.pad(c, ((1, 0), (1, 0)))
    return c[h:, w:] - c[:-h, w:] - c[h:, :-w] + c[:-h, :-w]


def match_shift(img, tmpl, gray=True):
    """在 img 中定位 tmpl, 返回 (dx, dy, score)
    (dx, dy) = tmpl 在 img 中的左上角坐标; score = NCC (0~1)
    输入为 int16 RGB (H,W,3) 或 2D 灰度"""
    a = img
    t = tmpl
    if gray and a.ndim == 3:
        a = a.mean(axis=2)
    if gray and t.ndim == 3:
        t = t.mean(axis=2)
    a = a.astype(np.float32)
    t = t.astype(np.float32)
    H, W = a.shape
    h, w = t.shape
    t0 = t - t.mean()
    tnorm = np.sqrt(float((t0 * t0).sum())) + 1e-6
    # 互相关 (FFT加速): corr[d] = sum img[d+x] * t0[x]
    FH, FW = H + h, W + w
    Fi = np.fft.rfft2(a, (FH, FW))
    Ft = np.fft.rfft2(t0[::-1, ::-1], (FH, FW))
    corr = np.fft.irfft2(Fi * Ft, (FH, FW))
    # corr[i,j] 对应 tmpl 左上角放在 img 的 (i-h+1, j-w+1)?
    # 直接线性验证: 取 corr 中最大值位置 p, 位移 = p - (h-1, w-1)
    # 先只看有效区 (全卷积的非负重叠部分)
    ch, cw = FH - h + 1, FW - w + 1
    valid = corr[:ch, :cw]
    # 局部方差归一化 (只在 tmpl 完全落在 img 内的区域有效)
    ok = slice(h - 1, H), slice(w - 1, W)
    s1 = _winsum(a, h, w)
    s2 = _winsum(a * a, h, w)
    var = s2 - s1 * s1 / (h * w)
    var = np.maximum(var, 1e-3)
    # corr 索引 i 对应窗口左上 (i-h+1, j-w+1) => 窗口和 s1[i-h+1, j-w+1]
    region = valid[ok]
    ncc = region / (np.sqrt(var) * tnorm)
    idx = np.unravel_index(np.argmax(ncc), ncc.shape)
    # region[i,j] = corr[i+h-1, j+w-1] 对应 tmpl 左上角放在 (i, j)
    top = int(idx[0])
    left = int(idx[1])
    return left, top, float(ncc[idx])


def ncc_map(img, tmpl, gray=True):
    """计算模板在img中所有位置的NCC图。
    返回 ncc 二维数组, ncc[y, x] = 模板左上角放在 (x, y) 处的得分。
    只在模板完全落在img内的区域有效。"""
    a = img
    t = tmpl
    if gray and a.ndim == 3:
        a = a.mean(axis=2)
    if gray and t.ndim == 3:
        t = t.mean(axis=2)
    a = a.astype(np.float32)
    t = t.astype(np.float32)
    H, W = a.shape
    h, w = t.shape
    t0 = t - t.mean()
    tnorm = np.sqrt(float((t0 * t0).sum())) + 1e-6
    FH, FW = H + h, W + w
    Fi = np.fft.rfft2(a, (FH, FW))
    Ft = np.fft.rfft2(t0[::-1, ::-1], (FH, FW))
    corr = np.fft.irfft2(Fi * Ft, (FH, FW))
    s1 = _winsum(a, h, w)
    s2 = _winsum(a * a, h, w)
    var = s2 - s1 * s1 / (h * w)
    var = np.maximum(var, 1e-3)
    return corr[h - 1:H, w - 1:W] / (np.sqrt(var) * tnorm)


def ncc_two(img, tmpl, d1, d2, gray=True):
    """一次FFT同时计算模板在img中两个偏移处的NCC得分。
    d=(dx,dy)为模板左上角在img中的位置。
    返回 (score1, score2)。用于"移动假设 vs 未动假设"的直接对比,
    避免周期纹理导致的混叠错峰。"""
    a = img
    t = tmpl
    if gray and a.ndim == 3:
        a = a.mean(axis=2)
    if gray and t.ndim == 3:
        t = t.mean(axis=2)
    a = a.astype(np.float32)
    t = t.astype(np.float32)
    H, W = a.shape
    h, w = t.shape
    t0 = t - t.mean()
    tnorm = np.sqrt(float((t0 * t0).sum())) + 1e-6
    FH, FW = H + h, W + w
    Fi = np.fft.rfft2(a, (FH, FW))
    Ft = np.fft.rfft2(t0[::-1, ::-1], (FH, FW))
    corr = np.fft.irfft2(Fi * Ft, (FH, FW))
    s1 = _winsum(a, h, w)
    s2 = _winsum(a * a, h, w)
    var = s2 - s1 * s1 / (h * w)
    var = np.maximum(var, 1e-3)
    ncc = corr[h - 1:H, w - 1:W] / (np.sqrt(var) * tnorm)
    def at(d):
        x, y = d
        if 0 <= y < ncc.shape[0] and 0 <= x < ncc.shape[1]:
            return float(ncc[y, x])
        return -1.0
    return at(d1), at(d2)


if __name__ == "__main__":
    # 自测: 随机场景 + 已知位移
    rng = np.random.RandomState(7)
    img = rng.randint(0, 255, (300, 400, 3)).astype(np.int16)
    dy0, dx0 = 57, 123
    tmpl = img[dy0:dy0 + 80, dx0:dx0 + 110]
    dx, dy, sc = match_shift(img, tmpl)
    print("测试1 纯噪声: expect dx=%d dy=%d -> got dx=%d dy=%d score=%.3f" % (dx0, dy0, dx, dy, sc))
    assert (dx, dy) == (dx0, dy0) and sc > 0.99
    # 自测2: 真实感场景(渐变+纹理) 平移
    base = rng.randint(0, 100, (300, 400)).astype(np.float32)
    for k in range(6):
        base[k * 50:(k * 50) + 25, :] += 60
    for k in range(8):
        base[:, k * 50:(k * 50) + 25] += 40
    img2 = np.stack([base, base * 0.9, base * 0.8], axis=2).astype(np.int16)
    shifted = np.roll(np.roll(img2, 30, axis=1), -16, axis=0)
    tmpl2 = img2[50:170, 60:220]
    dx2, dy2, sc2 = match_shift(shifted, tmpl2)
    # 返回绝对位置, 位移 = 位置 - 原位置(60, 50)
    print("测试2 场景平移(30,-16): got 位移 dx=%d dy=%d score=%.3f" % (dx2 - 60, dy2 - 50, sc2))
    assert (dx2 - 60, dy2 - 50) == (30, -16) and sc2 > 0.95
    print("自测通过")
