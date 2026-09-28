# -*- coding: utf-8 -*-
"""MKF 容器读取 + YJ_1 解压 (Python 移植自 SDLPAL palcommon.c / yj1.c)
用法:
    mkf = MKF('MAP.MKF')
    raw = mkf.read(3)          # 原始块(含 'YJ_1' 头则解压)
"""
import struct

class MKF:
    def __init__(self, path):
        self.data = open(path, 'rb').read()
        self.count = struct.unpack_from('<I', self.data, 0)[0]
        self.offsets = struct.unpack_from('<%dI' % (self.count + 1), self.data, 0)
        # 注: SDLPAL 的偏移表读法: fseek(4*i) 处的 DWORD = 块 i 的偏移

    def chunk_size(self, i):
        return self.offsets[i + 1] - self.offsets[i]

    def read_raw(self, i):
        return self.data[self.offsets[i]:self.offsets[i + 1]]

    def read(self, i):
        """读取块, 若为 YJ_1 压缩则解压"""
        raw = self.read_raw(i)
        if raw[:4] == b'YJ_1':
            return yj1_decompress(raw)
        return raw


def yj1_decompress(src):
    """移植自 SDLPAL yj1.c 的 YJ1_Decompress"""
    (sig, unclen, complen, blockcnt, unknown, treelen) = struct.unpack_from('<IIIHBB', src, 0)
    assert sig == 0x315F4A59, 'not YJ_1'
    treelen2 = treelen * 2

    # ---- 位读取器: 每 16 位小端字, MSB 在前 ----
    class BitReader:
        __slots__ = ('buf', 'pos')
        def __init__(self, buf, pos=0):
            self.buf = buf
            self.pos = pos
        def get(self, count):
            if count == 0:
                return 0
            p = self.pos
            w0 = self.buf[p >> 4] if False else None  # 占位
            # 按 16 位字组织
            wi = p >> 4
            b = p & 0xf
            w0 = self.buf[wi] | (self.buf[wi + 1] << 8) if wi + 1 < len(self.buf) else 0
            w0 &= 0xffff
            if count > 16 - b:
                c2 = count + b - 16
                mask = 0xffff >> b
                wi2 = (p + count) >> 4
                # 与 C 代码一致: 第二个字取 self.buf[(wi+1)*2..] 即 wi+1 的字
                w1 = self.buf[wi + 1] | (self.buf[wi + 2] << 8) if wi + 2 < len(self.buf) else 0
                w1 &= 0xffff
                self.pos += count
                return ((w0 & mask) << c2) | (w1 >> (16 - c2))
            else:
                self.pos += count
                return ((w0 << b) & 0xffff) >> (16 - count)

    # ---- 构建霍夫曼树 ----
    # C: root[0] 内部节点; root[i].leaf = !bit_i; root[i].value = src[15+i]
    #    非叶: left = root + value*2 + 1, right = left+1
    flag_br = BitReader(src, 0)
    flag_buf = src[16 + treelen2:]           # 位流从 16+tree_len 字节处开始
    flag_br = BitReader(flag_buf)

    N = treelen2 + 1
    leaf = [False] * N
    value = [0] * N
    for i in range(1, N):
        leaf[i] = not flag_br.get(1)
        value[i] = src[15 + i]

    def decode_byte(br):
        node = 0
        while not leaf[node]:
            b = br.get(1)
            v = value[node]
            child = v * 2 + 1 + (1 if b else 0)
            node = child
        return value[node]

    # 注意: C 代码 root[0].left=root+1, root[0].right=root+2; 非叶节点 i 的孩子由 value 决定
    # root[0] 的 value=0, 因此按公式 child=0*2+1=1 ✓ 与 C 初始化一致

    pos = 16 + treelen2 + (((treelen2 & 0xf) if False else (treelen2 >> 4) + (1 if treelen2 & 0xf else 0)) << 1)
    # C: src += 16 + tree_len + (((tree_len & 0xf) ? (tree_len >> 4) + 1 : (tree_len >> 4)) << 1)
    #    其中 tree_len = treelen2, flag 字节数按 16 位字对齐
    flagwords = (treelen2 >> 4) + (1 if treelen2 & 0xf else 0)
    pos = 16 + treelen2 + (flagwords << 1)

    dest = bytearray()
    for _ in range(blockcnt):
        hul, hcl = struct.unpack_from('<HH', src, pos)
        if hcl == 0:
            dest += src[pos + 4: pos + 4 + hul]
            pos += 4 + hul
            continue
        # 块头 24 字节: u16 UncompressedLength, u16 CompressedLength,
        # LZSSRepeatTable[4](u16), LZSSOffsetCodeLengthTable[4](u8),
        # LZSSRepeatCodeLengthTable[3](u8), CodeCountCodeLengthTable[3](u8), CodeCountTable[2](u8)
        rep_tab = struct.unpack_from('<4H', src, pos + 4)
        off_codelen = src[pos + 12: pos + 16]
        rep_codelen = src[pos + 16: pos + 19]
        cnt_codelen = src[pos + 19: pos + 22]
        cnt_tab = src[pos + 22: pos + 24]

        body = BitReader(src[pos + 24:])
        while True:
            # get_loop
            if body.get(1):
                loop = cnt_tab[0]
            else:
                t = body.get(2)
                if t:
                    loop = body.get(cnt_codelen[t - 1])
                else:
                    loop = cnt_tab[1]
            if loop == 0:
                break
            for _ in range(loop):
                dest.append(decode_byte(body))
            # get_loop
            if body.get(1):
                loop = cnt_tab[0]
            else:
                t = body.get(2)
                if t:
                    loop = body.get(cnt_codelen[t - 1])
                else:
                    loop = cnt_tab[1]
            if loop == 0:
                break
            for _ in range(loop):
                # get_count
                t = body.get(2)
                if t != 0:
                    if body.get(1):
                        count = body.get(rep_codelen[t - 1])
                    else:
                        count = rep_tab[t]
                else:
                    count = rep_tab[0]
                p = body.get(2)
                p = body.get(off_codelen[p])
                for _ in range(count):
                    dest.append(dest[-p])
        pos += hcl

    return bytes(dest[:unclen])


if __name__ == '__main__':
    import sys
    m = MKF(sys.argv[1] if len(sys.argv) > 1 else r'D:\Fang\Game\PAL_98\MAP.MKF')
    print('块数:', m.count)
    for i in range(1, min(4, m.count)):
        raw = m.read_raw(i)
        d = m.read(i)
        print('块 %d: 原始 %dB, 解压 %dB, 头16B: %s' % (i, len(raw), len(d), d[:16].hex()))
