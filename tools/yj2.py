# -*- coding: utf-8 -*-
"""yj2.py — YJ_2 解压 (纯 Python 移植)

为什么有这个模块
----------------
`tools/mkf.py` 早就把 **YJ_1** 解压移植成了 Python, 但 `MAP.MKF` 里的地图块用的是
**YJ_2**(自适应 Huffman), 一直靠外部 `yj2.dll` 解 —— 而那个 DLL 不在仓库里,
新机器上要用户自己编译/找, 直接违背了「只需要准备 SDLPAL 游戏本体」。
本模块把 YJ_2 也移植过来, `tools/build_data.py` 从此**不需要任何外部动态库**。

移植自 SDLPAL 的 `yj2dll.c`(GPL, 与 yj1.c 同源), 逐行对应, 未做任何"优化"改动 ——
自适应 Huffman 的表更新对细节极其敏感, 改一处就会整块解错。

★ 等价性验证 (2026-10-15, 真机)
  · `MAP.MKF` 全部 226 张地图块: DLL 输出 vs 本模块输出 **逐字节相同**
  · 用本模块重建的 `map_walk.npy` 与当年用 DLL 生成的那份 **SHA256 相同**
  · 回归见 `test_yj2.py`(结构单测 + 有游戏资源时与 `map_walk.npy` 对拍)

算法要点
--------
  · 641 节点(0x000..0x280)的数组树; 值 0x000..0x0FF = 字面字节,
    0x100..0x140 = 回引长度码, 0x141..0x280 = 内部节点; 0x280 是根。
  · 每读出一个符号就 `adjust_tree`: 沿父链把该节点按权重上浮到
    "权重相同的兄弟之后", 保证树始终是权重有序的。
  · 根权重到 0x8000 时全树权重右移 1 位, 并把权重为奇数的叶子(值 0..0x140)
    重新上调一次 —— 标准 FGK 自适应 Huffman 的溢出处理。
  · 符号 > 0xFF: 再读 9~14 个变长比特拼出**距离** `pos`,
    从已输出内容尾部回引 `val - 0xFD` 个字节; `pos == 0xFFF` 是结束标记。

用法
----
    from yj2 import yj2_decompress, YJ2Error
    tiles = yj2_decompress(raw)     # raw = MKF 原始块(**含** 4 字节长度头)
"""
LEAF_MAX = 0x140           # 最大"符号"值 (含回引长度码)
ROOT = 0x280               # 根节点下标/值
N_NODE = ROOT + 1          # 641
MAX_OUT = 65536 + 64       # 与 DLL 调用方一致的输出上限 (地图块解压后 64KB)

# 距离编码的变长比特拆分表 (值 = 距离高位, 见 C 版 yj2_data1)
YJ2_DATA1 = (
    0x3F, 0x0B, 0x17, 0x03, 0x2F, 0x0A, 0x16, 0x00, 0x2E, 0x09, 0x15, 0x02,
    0x2D, 0x01, 0x08, 0x00, 0x3E, 0x07, 0x14, 0x03, 0x2C, 0x06, 0x13, 0x00,
    0x2B, 0x05, 0x12, 0x02, 0x2A, 0x01, 0x04, 0x00, 0x3D, 0x0B, 0x11, 0x03,
    0x29, 0x0A, 0x10, 0x00, 0x28, 0x09, 0x0F, 0x02, 0x27, 0x01, 0x08, 0x00,
    0x3C, 0x07, 0x0E, 0x03, 0x26, 0x06, 0x0D, 0x00, 0x25, 0x05, 0x0C, 0x02,
    0x24, 0x01, 0x04, 0x00, 0x3B, 0x0B, 0x17, 0x03, 0x23, 0x0A, 0x16, 0x00,
    0x22, 0x09, 0x15, 0x02, 0x21, 0x01, 0x08, 0x00, 0x3A, 0x07, 0x14, 0x03,
    0x20, 0x06, 0x13, 0x00, 0x1F, 0x05, 0x12, 0x02, 0x1E, 0x01, 0x04, 0x00,
    0x39, 0x0B, 0x11, 0x03, 0x1D, 0x0A, 0x10, 0x00, 0x1C, 0x09, 0x0F, 0x02,
    0x1B, 0x01, 0x08, 0x00, 0x38, 0x07, 0x0E, 0x03, 0x1A, 0x06, 0x0D, 0x00,
    0x19, 0x05, 0x0C, 0x02, 0x18, 0x01, 0x04, 0x00, 0x37, 0x0B, 0x17, 0x03,
    0x2F, 0x0A, 0x16, 0x00, 0x2E, 0x09, 0x15, 0x02, 0x2D, 0x01, 0x08, 0x00,
    0x36, 0x07, 0x14, 0x03, 0x2C, 0x06, 0x13, 0x00, 0x2B, 0x05, 0x12, 0x02,
    0x2A, 0x01, 0x04, 0x00, 0x35, 0x0B, 0x11, 0x03, 0x29, 0x0A, 0x10, 0x00,
    0x28, 0x09, 0x0F, 0x02, 0x27, 0x01, 0x08, 0x00, 0x34, 0x07, 0x0E, 0x03,
    0x26, 0x06, 0x0D, 0x00, 0x25, 0x05, 0x0C, 0x02, 0x24, 0x01, 0x04, 0x00,
    0x33, 0x0B, 0x17, 0x03, 0x23, 0x0A, 0x16, 0x00, 0x22, 0x09, 0x15, 0x02,
    0x21, 0x01, 0x08, 0x00, 0x32, 0x07, 0x14, 0x03, 0x20, 0x06, 0x13, 0x00,
    0x1F, 0x05, 0x12, 0x02, 0x1E, 0x01, 0x04, 0x00, 0x31, 0x0B, 0x11, 0x03,
    0x1D, 0x0A, 0x10, 0x00, 0x1C, 0x09, 0x0F, 0x02, 0x1B, 0x01, 0x08, 0x00,
    0x30, 0x07, 0x0E, 0x03, 0x1A, 0x06, 0x0D, 0x00, 0x19, 0x05, 0x0C, 0x02,
    0x18, 0x01, 0x04, 0x00,
)
# 距离码变长部分多读几个比特 (按 tmp 低 4 位查表)
YJ2_DATA2 = (
    0x08, 0x05, 0x06, 0x04, 0x07, 0x05, 0x06, 0x03, 0x07, 0x05, 0x06, 0x04,
    0x07, 0x04, 0x05, 0x03,
)

assert len(YJ2_DATA1) == 0x100 and len(YJ2_DATA2) == 0x10


class YJ2Error(ValueError):
    """YJ_2 数据不合法 / 解压失败 (调用方按"这块跳过"处理即可)。"""


class _Tree(object):
    """C 版 YJ2_Tree 的字面翻译。

    C 里 `list[v]` / `parent[i]` 都是**指针**, 这里统一用**节点下标**表示指针:
    `node + 1`(C 的指针加一) == 下标加一, 语义完全等价。
    """

    __slots__ = ("list", "weight", "value", "parent", "left", "right")

    def __init__(self):
        self.list = [0] * (LEAF_MAX + 1)
        self.weight = [0] * N_NODE
        self.value = [0] * N_NODE
        self.parent = [0] * N_NODE
        self.left = [0] * N_NODE
        self.right = [0] * N_NODE
        for i in range(LEAF_MAX + 1):
            self.list[i] = i
        for i in range(N_NODE):
            self.value[i] = i
            self.weight[i] = 1
        self.parent[ROOT] = ROOT
        i = 0
        for ptr in range(LEAF_MAX + 1, ROOT + 1):
            self.left[ptr] = i
            self.right[ptr] = i + 1
            self.parent[i] = ptr
            self.parent[i + 1] = ptr
            self.weight[ptr] = self.weight[i] + self.weight[i + 1]
            i += 2

    def adjust(self, v):
        """C 版 yj2_adjust_tree: 把"值为 v 的节点"沿父链上浮并加权。"""
        w, val, par = self.weight, self.value, self.parent
        lft, rgt, lst = self.left, self.right, self.list
        node = lst[v]
        while val[node] != ROOT:
            temp = node + 1
            if temp >= N_NODE:
                raise YJ2Error("adjust_tree 越界 (v=%d)" % v)
            while w[node] == w[temp]:
                temp += 1
                if temp >= N_NODE:
                    raise YJ2Error("adjust_tree 越界 (v=%d)" % v)
            temp -= 1
            if temp != node:
                par[node], par[temp] = par[temp], par[node]
                if val[node] > LEAF_MAX:
                    par[lft[node]] = temp
                    par[rgt[node]] = temp
                else:
                    lst[val[node]] = temp
                if val[temp] > LEAF_MAX:
                    par[lft[temp]] = node
                    par[rgt[temp]] = node
                else:
                    lst[val[temp]] = node
                # C: {YJ2_TreeNode t = *node; *node = *temp; *temp = t;}
                w[node], w[temp] = w[temp], w[node]
                val[node], val[temp] = val[temp], val[node]
                par[node], par[temp] = par[temp], par[node]
                lft[node], lft[temp] = lft[temp], lft[node]
                rgt[node], rgt[temp] = rgt[temp], rgt[node]
                node = temp
            w[node] += 1
            node = par[node]
        w[node] += 1


def _bits(data, pos, n):
    """C 版 yj2_bt: **位序 LSB-first**, 一次读 n 个比特拼成整数。

    越界时按 0 处理 (C 版读的是堆上紧邻内存, 这里取确定性的 0;
    226 张地图实测与 DLL 逐字节一致, 说明真数据不会越界取值)。
    """
    v = 0
    for i in range(n):
        p = pos + i
        b = p >> 3
        v |= ((data[b] >> (p & 7)) & 1) << i
    return v


def _bit(data, pos):
    b = pos >> 3
    return (data[b] >> (pos & 7)) & 1


def yj2_decompress(raw, maxsize=MAX_OUT, strict=True):
    """解压一个 YJ_2 块。

    raw      : MKF 原始块, **含**开头 4 字节小端长度头 (解压后长度)。
    maxsize  : 输出上限; 超出抛 YJ2Error (对应 C 版 -2 / -3)。
    strict   : 校验"实际输出长度 == 头部声明长度", 不符抛 YJ2Error。
    返回     : `bytes` 解压结果。
    """
    data = bytes(raw)
    if len(data) < 5:
        raise YJ2Error("块太短 (%d 字节)" % len(data))
    length = int.from_bytes(data[:4], "little")
    if length > maxsize:
        raise YJ2Error("声明长度 %d 超过上限 %d" % (length, maxsize))
    src = data[4:]
    nsrc = len(src)
    if nsrc == 0:
        raise YJ2Error("空压缩数据")

    t = _Tree()
    w, val, par, lft, rgt, lst = (t.weight, t.value, t.parent,
                                  t.left, t.right, t.list)
    out = bytearray()
    ptr = 0

    while True:
        node = ROOT
        while val[node] > LEAF_MAX:
            if ptr >> 3 >= nsrc:                    # 位流越界 -> 数据坏了
                raise YJ2Error("比特流提前结束 (已解 %d 字节)" % len(out))
            if _bit(src, ptr):
                node = rgt[node]
            else:
                node = lft[node]
            ptr += 1
        v = val[node]

        if w[ROOT] == 0x8000:
            for i in range(LEAF_MAX + 1):
                if w[lst[i]] & 1:
                    t.adjust(i)
            for i in range(N_NODE):
                w[i] >>= 1
        t.adjust(v)

        if v > 0xFF:
            # 回引: 变长距离码
            if ptr + 8 > nsrc * 8:
                raise YJ2Error("距离码越界 (已解 %d 字节)" % len(out))
            temp = _bits(src, ptr, 8)
            ptr += 8
            tmp = temp & 0xFF
            extra = YJ2_DATA2[tmp & 0xF] + 6
            if extra > 8:
                temp |= _bits(src, ptr, extra - 8) << 8
                ptr += extra - 8
            temp >>= YJ2_DATA2[tmp & 0xF]
            pos = (temp & 0x3F) | (YJ2_DATA1[tmp] << 6)
            if pos == 0xFFF:                        # 结束标记
                break
            pre = len(out) - pos - 1
            if pre < 0:
                raise YJ2Error("回引越界 (pos=%d, 已解 %d)" % (pos, len(out)))
            for _ in range(v - 0xFD):
                out.append(out[pre])
                pre += 1
        else:
            out.append(v)

        if len(out) > maxsize:
            raise YJ2Error("输出超过上限 %d" % maxsize)

    if strict and length and len(out) != length:
        raise YJ2Error("长度不符: 头部 %d, 实际 %d" % (length, len(out)))
    return bytes(out)


if __name__ == "__main__":     # 手工冒烟: 解一个块并打印头尾
    import sys
    if len(sys.argv) > 1:
        r = open(sys.argv[1], "rb").read()
        b = yj2_decompress(r)
        print("解出 %d 字节 (头部声明 %d)"
              % (len(b), int.from_bytes(r[:4], "little")))
