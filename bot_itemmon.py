# -*- coding: utf-8 -*-
"""bot_itemmon.py — 物品数量只读监视器 (GUI 用, 独立于 pal_bot)。

为什么独立: GUI 进程禁止 import pal_bot (它会设 DPI 感知, 把 tkinter 控件
缩小 —— 见 bot_gui.py 头部说明), 所以这里用 ctypes 自带一份最小读取实现。

数据来源 (2026-10-02 真机标定, 全部只读):
  - 游戏进程 = 标题含 "Pal" 的窗口 (游戏真标题 = `Pal Win95 [v3.0.2025.0616-dirty]`;
    自家 GUI 标题含 "挂机机器人", find_pid 里显式排除, 见那个函数的说明)
  - sdlpal GLOBALVARS: viewport @0x00C3C8FC (pal_bot_mem.py 同源锚点)
  - rgInventory = viewport + 0x340 = 0x00C3CC3C
    (源码布局算出; tools/item_scan.py dump 实测 104 条目 ID/数量全部合理)
  - 傀儡虫物品 ID: tools/item_scan.py watch 真机差分锁定 (见 ITEM_ID 注释)

★ 2026-10-16: 下边所有地址都是**标定值** (标定那次游戏模块加载在
  game_addr.CALIB_BASE)。sdlpal.exe 开了 ASLR, 而 Windows 的映像基址随机化
  **按"本次开机"决定一次** —— 换台机器 / 重启一次就可能换基址, 标定值就全
  错了。所以真正读进程时一律叠 `game_addr.slide(pid)` (见 read_raw / _rd)。
"""
import ctypes
import ctypes.wintypes as wt
import struct
import game_addr                     # 只读地址换算 (纯 ctypes, 不引 pal_bot)

# --- 锚点 (与 pal_bot_mem.py / tools/item_scan.py 同源, 勿单方面改动) ---
VIEWPORT_ADDR = 0x00C3C8FC
INV_DELTA = 0x340
INV_ADDR = VIEWPORT_ADDR + INV_DELTA
MAX_INVENTORY = 256
ENTRY = struct.Struct("<HHH")           # wItem, nAmount, nAmountInUse

# ★ 傀儡虫: 2026-10-02 双重确认 —— ① tools/item_scan.py watch 真机差分
#   (刷怪时数量增长) ② WORD.DAT 词表 word[0x098] = "傀儡虫" (名称按物品 ID 索引,
#   与 sdlpal itemmenu.c 的 PAL_GetWord(wObject) 一致)
ITEM_ID = 0x098
ITEM_NAME = "傀儡虫"

# WORD.DAT: 定长 10 字节 GBK 记录, 词索引 = 物品 ID (sdlpal text.c:
# nWords = filesize / gConfig.dwWordLength, 中文版 dwWordLength=10;
# 本机实测 5650B = 565 词整除, word[0x05F]="还魂香" 等全部吻合)
WORD_REC = 10
WORD_PAD = b"\x00 \xa1\xa1\xff"         # 记录内填充/全角空格/0xFF 都裁掉

# ----------------------------------------------------------------------
# 人物等级/经验 (2026-10-02 真机标定, 只读)
# ----------------------------------------------------------------------
# 标定方法 (tools/role_scan.py 可复现):
#   ① rgwName: 扫内存找"连续 6 个 WORD 都是人物名词条 ID"的数组
#      (名字词条来自 WORD.DAT: 李逍遥=0x24 赵灵儿=0x25 林月如=0x26
#       巫后=0x28 阿奴=0x27 盖罗娇=0x29) -> 命中 0x00C3AAFC = viewport-0x1E00
#   ② rgwLevel = rgwName+0x24 (按 sdlpal global.h 的 PLAYERROLES 布局:
#      avatar/spriteInBattle/spriteNum/name 各 6 词之后), 读数
#      [59,42,51,48,41,40] 六人全部合理
#   ③ Exp.rgPrimaryExp = viewport+0x80 (0x00C3C97C): 每条 8B
#      {wExp,wReserved,wLevel,wCount}, 前 5 条 wLevel 与 ② 独立一致
#      -> 双结构交叉验证, 地址可信
#      ★ 必须是 8B 步长 (每条 EXPERIENCE 8 字节)! 2026-10-02 踩坑:
#        曾按 2B 步长读 -> 灵儿(第2位)读到逍遥的 wReserved(恒 0),
#        阿奴(第5位)读到灵儿的经验 → "灵儿经验永远 0" 的假故障。
#   ④ 队伍表 rgParty = viewport+0x0C (每人 10B, 首 WORD=wPlayerRole),
#      wMaxPartyMemberIndex = viewport+0x0A; 实测 3 人 [1,0,4] 与画面一致
#   ⑤ rgLevelUpExp = viewport-0x1A24 (0x00C3AED8): 100 条 WORD, 升到
#      "下一级"所需经验 [0,15,40,90,165,265,...], 索引 = 当前等级
#      (即 lv42 要攒够 v[42] 才升 43)。真机交叉验证: 6 名角色全部满足
#      wExp < v[等级] (阿奴 lv42 exp21189 < v[42]=21540 差 351 升级,
#      而 v[41]=20515 < 21189 → 排除"索引是 level-1"的假说)
# 注: 本构建 MAX_PLAYER_ROLES=6 (master 源码是 5, win95 构建不同 ——
#     又一次证明"源码只能当假设, 地址必须真机反查")
MAX_ROLES = 6
MAX_LEVELS = 100                              # rgLevelUpExp 条数 (真机实测)
ROLES_NAME_ADDR = VIEWPORT_ADDR - 0x1E00      # rgwName[6]   0x00C3AAFC
ROLES_LEVEL_ADDR = ROLES_NAME_ADDR + 0x24     # rgwLevel[6]  0x00C3AB20
ROLES_MAXHP_ADDR = ROLES_NAME_ADDR + 0x30     # rgwMaxHP[6]  0x00C3AB2C
ROLES_HP_ADDR = ROLES_NAME_ADDR + 0x3C        # rgwHP[6]     0x00C3AB38
EXP_ADDR = VIEWPORT_ADDR + 0x80               # Exp.rgPrimaryExp[6] 0x00C3C97C
EXP_STRIDE = 8                                # EXPERIENCE = {wExp,wRes,wLevel,wCount}
EXP_LEVEL_OFF = 4                             # 组内 wLevel 偏移 (校验用)
MONEY_ADDR = EXP_ADDR - 4                     # gpGlobals->dwCash 0x00C3C978
#   ★ 金钱标定 (2026-10-13, 三重证据, tools/_diag/probe_money2.py 可复现):
#     ① SDLPAL 源码 global.h: GLOBALVARS 里 dwCash 定义在 wNumScene 之后、
#        Exp(ALLEXPERIENCE) 之前; 与实测的 rgParty(+0x0C)/wPartyDirection(+0x5C)/
#        wNumScene(+0x5E)/Exp(+0x80) 联立, dwCash 恰好 = viewport+0x7C (本地址);
#     ② 差分实测: 挂机中 30s×3 帧采样, 该 DWORD 与三人经验**同一时刻** +120
#        (战斗结算同步写入); 其余变化的地址全是 8B 步长的经验数组成员;
#     ③ 量级交叉验证: 值 636773 ÷ 累计胜场 6749 ≈ 94 文/场, 正是 PAL 敌人的
#        给钱区间 (普通小怪几十~一百多文)。
#     与 EXP_ADDR 连续 (dwCash 4B + rgPrimaryExp 48B), 机器人一次读 52B。
LEVELUPEXP_ADDR = VIEWPORT_ADDR - 0x1A24      # rgLevelUpExp[100] 0x00C3AED8
PARTY_N_ADDR = VIEWPORT_ADDR + 0x0A           # wMaxPartyMemberIndex
PARTY_ADDR = VIEWPORT_ADDR + 0x0C             # rgParty[6], 步长 10B
PARTY_STRIDE = 10
# 备用: 静态角色名表 (WORD.DAT 读不到时兜底显示)
ROLE_NAME_IDS = (0x24, 0x25, 0x26, 0x28, 0x27, 0x29)
ROLE_NAMES_FALLBACK = ("李逍遥", "赵灵儿", "林月如", "巫后", "阿奴", "盖罗娇")

OPEN_OK = 0x0010                        # PROCESS_VM_READ (只读!)
k32 = ctypes.windll.kernel32
u32 = ctypes.windll.user32


def parse_inventory(raw):
    """解析物品栏原始字节 -> [(idx, wItem, nAmount, nAmountInUse)] (非空条目)。

    纯函数, 供测试; 截断到完整条目, 长度不足按实际处理。
    """
    out = []
    for i in range(len(raw) // ENTRY.size):
        w, n, u = ENTRY.unpack_from(raw, i * ENTRY.size)
        if w or n:
            out.append((i, w, n, u))
    return out


def is_game_title(title):
    """窗口标题是不是游戏窗口 —— 「含 'Pal' 且不是自家 GUI」。

    ★ 2026-10-12: 显式排除自家 GUI (标题含 "挂机机器人")。以前只靠**大小写**
      侥幸不撞车: 游戏真标题 = `Pal Win95 [v3.0.2025.0616-dirty]`, 而 GUI 标题里
      只有全大写的 "SDLPAL" -> `"Pal" in 标题` 恰好为假。GUI 标题一旦改成含
      "Pal" (见 botcfg.APP_VERSION 的告警), 这里就会把 GUI 当成游戏去读内存。

    抽成纯函数是为了能测 (ctypes 回调没法单独测); 机器人侧同样的判据内联在
    `pal_bot_mem.find_pal_pid` / `pal_bot_mem5._game_pids` 里 (那两处刻意不
    引本模块, 保持 GUI 侧零耦合)。
    """
    return "Pal" in title and "挂机机器人" not in title


def find_pid():
    """标题含 "Pal" 的窗口所属进程 pid 列表 (升序去重); 判据见 is_game_title。"""
    res = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def cb(hwnd, lp):
        pid = wt.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        n = u32.GetWindowTextLengthW(hwnd)
        if n:
            b = ctypes.create_unicode_buffer(n + 1)
            u32.GetWindowTextW(hwnd, b, n + 1)
            if is_game_title(b.value):
                res.append(pid.value)
        return True
    u32.EnumWindows(cb, 0)
    return sorted(set(res))


def load_names(word_dat=None):
    """读 WORD.DAT -> {物品ID: 名称}。找不到/解码失败返回 {} (显示 0x??? 兜底)。

    查找顺序与 wingeom 的 sdlpal.cfg 相同: 本目录 / 上两级。
    """
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    if word_dat:
        cands = [word_dat]
    else:
        cands = [os.path.join(d, "WORD.DAT")
                 for d in (here, os.path.dirname(here),
                           os.path.dirname(os.path.dirname(here)))]
    for p in cands:
        try:
            if not os.path.exists(p):
                continue
            raw = open(p, "rb").read()
            out = {}
            for i in range(len(raw) // WORD_REC):
                b = raw[i * WORD_REC:(i + 1) * WORD_REC]
                s = b.split(b"\x00")[0].decode("gbk", errors="replace")
                s = s.strip("".join(WORD_PAD.decode("latin1")))
                if s:
                    out[i] = s
            if out:
                return out
        except Exception:
            continue
    return {}


class ItemReader:
    """只读物品栏。轮询入口 amount(): 返回数量, 读不到返回 None。"""

    def __init__(self, item_id=ITEM_ID):
        self.item_id = item_id
        self.h = None
        self.pid = None

    def _ensure(self):
        """句柄有效就复用; 进程换了 (游戏重启) 自动重开。"""
        if self.h is not None:
            return True
        pids = find_pid()
        if not pids:
            return False
        self.pid = pids[0]
        self.h = k32.OpenProcess(OPEN_OK, False, self.pid)
        return self.h is not None

    def _drop(self):
        if self.h is not None:
            try:
                k32.CloseHandle(self.h)
            except Exception:
                pass
        self.h = None
        self.pid = None

    def read_raw(self):
        """读整栏原始字节; 失败返回 None (并丢句柄, 下次重开)。"""
        if not self._ensure():
            return None
        size = ENTRY.size * MAX_INVENTORY
        buf = (ctypes.c_char * size)()
        got = ctypes.c_size_t()
        # ★ 2026-10-16: INV_ADDR 是**标定值**, 必须叠上本次运行的基址平移
        #   (ASLR 每次开机随机一个基址; 详情见 game_addr 模块头)。
        if not k32.ReadProcessMemory(self.h,
                                     ctypes.c_void_p(
                                         INV_ADDR + game_addr.slide(self.pid)),
                                     buf, size, ctypes.byref(got)):
            self._drop()                 # 游戏可能退了/重启了, 下次重开
            return None
        return buf.raw[:got.value]

    def entries(self):
        raw = self.read_raw()
        return None if raw is None else parse_inventory(raw)

    def amount(self, item_id=None):
        """指定物品的当前数量; 游戏没开/读失败 -> None。"""
        eid = self.item_id if item_id is None else item_id
        rows = self.entries()
        if rows is None:
            return None
        for (_i, w, n, _u) in rows:
            if w == eid:
                return n
        return 0                        # 栏里有数据但没有该物品 = 0 个


# ----------------------------------------------------------------------
# 人物等级/经验 (纯解析函数 + RoleReader, 供 GUI 右侧面板)
# ----------------------------------------------------------------------
U16 = struct.Struct("<H")


def parse_party(raw):
    """队伍原始字节 -> 在队角色序号列表 (纯函数, 供测试)。

    raw = [wMaxPartyMemberIndex] + rgParty[6]×10B。
    成员数 = wMaxPartyMemberIndex + 1; 角色序号取每人首 WORD, 越界忽略。
    """
    if not raw or len(raw) < 2 + PARTY_STRIDE:
        return []
    nmax = U16.unpack_from(raw, 0)[0]
    if nmax >= MAX_ROLES:               # 读歪了的护栏
        return []
    out = []
    for i in range(nmax + 1):
        off = 2 + i * PARTY_STRIDE
        if off + 2 > len(raw):
            break
        r = U16.unpack_from(raw, off)[0]
        if r < MAX_ROLES:
            out.append(r)
    return out


def parse_exp_blob(raw):
    """Exp.rgPrimaryExp 原始字节 -> [(wExp,wReserved,wLevel,wCount)] (纯函数)。

    ★ 步长 8B: 这是历史上读错的地方(2B 步长会让奇数位角色读到别人的
    wReserved=0), 任何调用方都必须用 EXP_STRIDE。
    """
    if not raw:
        return []
    n = min(len(raw) // EXP_STRIDE, MAX_ROLES)
    return [struct.unpack_from("<4H", raw, i * EXP_STRIDE) for i in range(n)]


def parse_levelup_exp(raw):
    """rgLevelUpExp 原始字节 -> [int] (纯函数)。索引 = 当前等级。"""
    if not raw:
        return []
    n = min(len(raw) // 2, MAX_LEVELS)
    if n <= 0:
        return []
    return list(struct.unpack_from("<%dH" % n, raw, 0))


def next_exp_for(level, table):
    """当前等级 -> 升下一级所需经验。表缺/满级越界 -> 0 (表示未知)。"""
    if not table or level <= 0 or level >= len(table):
        return 0
    return table[level]


def exp_text(row):
    """经验的显示文本: 有升级阈值 -> '1725/23665'; 否则只显示当前值。"""
    exp = row.get("exp") or 0
    need = row.get("next_exp") or 0
    return "%d/%d" % (exp, need) if need else str(exp)


def parse_roles(raw_name, raw_lv, raw_exp, raw_hp=None, raw_maxhp=None,
                raw_next=None):
    """各数组原始字节 -> [{role,name_id,level,exp,next_exp,pct,hp,maxhp}]

    raw_exp 必须是 **Exp.rgPrimaryExp 的原始字节 (8B/条)**;
    raw_next 是 rgLevelUpExp (100 条 WORD, 索引=当前等级), 可缺省 -> next_exp=0。
    任一数组缺失/长度不够 -> 按实际能读到的条数截断。
    """
    n = min(len(raw_name or b"") // 2, len(raw_lv or b"") // 2,
            len(raw_exp or b"") // EXP_STRIDE, MAX_ROLES)
    hp_n = len(raw_hp or b"") // 2
    mh_n = len(raw_maxhp or b"") // 2
    table = parse_levelup_exp(raw_next)
    out = []
    for i in range(n):
        level = U16.unpack_from(raw_lv, i * 2)[0]
        exp = U16.unpack_from(raw_exp, i * EXP_STRIDE)[0]
        need = next_exp_for(level, table)
        out.append({
            "role": i,
            "name_id": U16.unpack_from(raw_name, i * 2)[0],
            "level": level,
            "exp": exp,
            "next_exp": need,
            "pct": min(100, exp * 100 // need) if need else 0,
            "hp": U16.unpack_from(raw_hp, i * 2)[0] if i < hp_n else 0,
            "maxhp": U16.unpack_from(raw_maxhp, i * 2)[0] if i < mh_n else 0,
        })
    return out


def role_display_name(name_id, word_names=None):
    """词条 ID -> 显示名。WORD.DAT 优先, 缺了用静态表兜底。"""
    if word_names:
        s = word_names.get(name_id)
        if s:
            return s
    try:
        return ROLE_NAMES_FALLBACK[ROLE_NAME_IDS.index(name_id)]
    except ValueError:
        return "人物 0x%02X" % name_id


class RoleReader:
    """只读队伍等级/经验。轮询入口 read(): 游戏没开/读失败 -> None。"""

    def __init__(self):
        self.h = None
        self.pid = None

    def _ensure(self):
        if self.h is not None:
            return True
        pids = find_pid()
        if not pids:
            return False
        self.pid = pids[0]
        self.h = k32.OpenProcess(OPEN_OK, False, self.pid)
        return self.h is not None

    def _drop(self):
        if self.h is not None:
            try:
                k32.CloseHandle(self.h)
            except Exception:
                pass
        self.h = None
        self.pid = None

    def _rd(self, addr, size):
        """按**静态标定地址**读 (自动叠本次运行的基址平移)。

        ★ 本函数的 addr 一律是上边那些常量。若将来要读"游戏自己写进内存的
          指针"(例如 rgParty 里的地址), 那个指针已经是运行时的真实地址,
          **不能再叠平移** —— 那种情况请直接用 k32.ReadProcessMemory。
        """
        if not self._ensure():
            return None
        addr = addr + game_addr.slide(self.pid)
        buf = (ctypes.c_char * size)()
        got = ctypes.c_size_t()
        if not k32.ReadProcessMemory(self.h, ctypes.c_void_p(addr),
                                     buf, size, ctypes.byref(got)):
            self._drop()                # 游戏退了/重启了, 下次重开
            return None
        if got.value < size:
            return None
        return buf.raw

    def read(self, word_names=None):
        """返回 {"members": [{role,name,level,exp,hp,maxhp,...}], ...}。

        只返回**在队**成员 (按 rgParty 顺序); 读不到返回 None。
        """
        raw_p = self._rd(PARTY_N_ADDR, 2 + PARTY_STRIDE * MAX_ROLES)
        raw_name = self._rd(ROLES_NAME_ADDR, 2 * MAX_ROLES)
        raw_lv = self._rd(ROLES_LEVEL_ADDR, 2 * MAX_ROLES)
        raw_exp = self._rd(EXP_ADDR, EXP_STRIDE * MAX_ROLES)   # ★ 8B/条
        raw_hp = self._rd(ROLES_HP_ADDR, 2 * MAX_ROLES)
        raw_mh = self._rd(ROLES_MAXHP_ADDR, 2 * MAX_ROLES)
        raw_next = self._rd(LEVELUPEXP_ADDR, 2 * MAX_LEVELS)   # 可缺省
        if None in (raw_p, raw_name, raw_lv, raw_exp):
            return None
        party = parse_party(raw_p)
        roles = parse_roles(raw_name, raw_lv, raw_exp, raw_hp, raw_mh, raw_next)
        if not party or not roles:
            return None
        members = []
        for r in party:
            if r >= len(roles):
                continue
            row = dict(roles[r])
            row["name"] = role_display_name(row["name_id"], word_names)
            members.append(row)
        return {"members": members} if members else None
