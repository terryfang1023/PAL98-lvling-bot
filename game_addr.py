# -*- coding: utf-8 -*-
"""game_addr.py — 「标定地址 -> 本次运行真实地址」的地址换算层。

★ 为什么需要它 (2026-10-16 在另一台机器上踩到, 表现为机器人一起来就
  `READ_TABLE_FAIL` 退出码 1):

  sdlpal.exe 的 PE 头里 `ImageBase = 0x00400000`, 而且 `DllCharacteristics`
  带 `DYNAMICBASE` (ASLR)。Windows 的**映像基址随机化是按「本次开机」决定
  一次**的 (32 位进程约 8 bit 熵 ≈ 256 个位置), 所以:

    - 同一台机器重启一次、或者换一台机器, 游戏就可能被加载到**另一个基址**;
    - 但映像内部的相对布局 (RVA) 不变 —— 变的只有"基址"这一个数。

  本机 (2026-10-16) 实测 uptime **44 天**, 日志里 126 次启动全都"内存基址
  OK" —— 因为那 44 天里基址一直是 0x00490000, 恰好等于当初标定用的基址。
  **一重启就会变**。

  机器人所有内存地址都是按"基址 = 0x00490000"那一次标定的**绝对地址**,
  于是换基址后:
    - 基址 > 0x00C30000  -> 连 `0x00C30000` 那 64KB 都读不到 -> READ_TABLE_FAIL
    - 基址在别处但仍可读 -> 读得到但是垃圾 (更坏: 拿着假坐标瞎走几千步)

★ 解法: 每次读"静态标定地址"时加一个平移量

      slide    = 游戏模块的实际基址 - CALIB_BASE
      真实地址 = 标定地址 + slide

  模块基址用 `psapi` 取 (64 位 Python 看 32 位目标也能用), 取不到时退回
  `VirtualQueryEx` 的 `AllocationBase`。**取到后必须校验** (读 rgScene[300]
  看结构合理率 + 读场景号看范围), 不通过就换候选: `0` -> 用"升级经验表"
  数值特征在映像里反推的 slide。全不通过才报错, 并把现场证据打出来。

⚠ 本模块**只依赖 ctypes**, 绝不 import pal_bot / numpy —— 因为 GUI 进程要用它
  (bot_itemmon 是 GUI 的只读人物面板), 而 GUI 进程禁止 import pal_bot
  (pal_bot 导入时会设 DPI 感知, 把 tkinter 控件缩小)。

⚠ 只读: 本模块只 OpenProcess(PROCESS_VM_READ) / ReadProcessMemory /
  VirtualQueryEx, 不写进程内存、不按键、不落盘。
"""
import ctypes
import ctypes.wintypes as wt
import os
import struct

k32 = ctypes.windll.kernel32
u32 = ctypes.windll.user32
psapi = ctypes.windll.psapi

OPEN_OK = 0x0010 | 0x0400        # PROCESS_VM_READ | PROCESS_QUERY_INFORMATION
LIST_MODULES_ALL = 0x03

# ----------------------------------------------------------------------
# 标定常量 —— 与 pal_bot_mem.py / bot_itemmon.py / pal_bot_mem5.py 同源,
# 由 test_game_addr.py 断言一致 (避免多处漂移)。
# ----------------------------------------------------------------------
CALIB_BASE = 0x00490000          # ★ 标定那次游戏模块的加载基址 (不是 PE 里的 0x00400000!)
PREFERRED_BASE = 0x00400000      # PE 头里的 ImageBase, 只用于诊断显示

READ_AT = 0x00C3C8F0             # GLOBALVARS 窗口起点 (120B)
SCENE_ADDR = 0x00C3C95A          # wNumScene
TABLE_ADDR = 0x00C38088          # rgScene[300] (每场景 8B)
OBJ_N_ADDR = 0x00C38084          # nEventObject (事件对象总数; 与 rgScene 同块)
LEVELUPEXP_ADDR = 0x00C3AED8     # rgLevelUpExp[100] (WORD)
MAX_SCRIPT = 43503               # lprgScriptEntry 条数 (实测)
OBJ_MAX_SCENES = 300
LEVELUP_N = 100

# rgLevelUpExp 的数值特征 (前 10 项) —— 与游戏数据有关、与 exe 无关, 所以在
# 映像里扫描它就能**独立反推** globals 的位置 (不依赖记忆中的 CALIB_BASE)。
LEVELUP_HEAD = (0, 15, 40, 90, 165, 265, 390, 540, 715, 915)

TYPES = {0x1000000: "MEM_IMAGE", 0x20000: "MEM_MAPPED", 0x40000: "MEM_PRIVATE"}

_cache = {}                      # pid -> resolve() 的结果


class MBI(ctypes.Structure):
    _fields_ = [("BaseAddress", ctypes.c_void_p),
                ("AllocationBase", ctypes.c_void_p),
                ("AllocationProtect", wt.DWORD),
                ("RegionSize", ctypes.c_size_t),
                ("State", wt.DWORD),
                ("Protect", wt.DWORD),
                ("Type", wt.DWORD)]


class MODULEINFO(ctypes.Structure):
    _fields_ = [("lpBaseOfDll", ctypes.c_void_p),
                ("SizeOfImage", wt.DWORD),
                ("EntryPoint", ctypes.c_void_p)]


# ----------------------------------------------------------------------
# 纯函数 (便于回归测试)
# ----------------------------------------------------------------------
def is_game_title(title):
    """窗口标题是不是游戏窗口 —— 「含 'Pal' 且不是自家 GUI (含'挂机机器人')」。

    与 pal_bot_mem.find_pal_pid / pal_bot_mem5._game_pids /
    bot_itemmon.is_game_title 同一判据 (★ 大小写敏感: 游戏真标题里是 "Pal",
    自家 GUI 里只有全大写的 "SDLPAL")。
    """
    return "Pal" in title and "挂机机器人" not in title


def slide_for_base(base):
    """模块基址 -> 平移量 (纯函数)。base 为 None/假值 -> 0。"""
    if not base:
        return 0
    return int(base) - CALIB_BASE


def scene_tab_ratio(words):
    """rgScene 原始 WORD 序列 -> 结构合理率 (纯函数)。

    规则与 pal_bot_mem.Mem.selfcheck 完全一致: 每条 8B = 4 个 WORD,
    第 1 个是地图号 (<=255), 第 2/3 个是脚本号 (<=MAX_SCRIPT 或 0xFFFF)。
    取前 296 条里"三个字段都合理"的比例。

    ⚠ 这个判据**单用不够**: 表是均匀结构, 平移 8 字节(整条) 或多条之后,
      读到的仍是"每 8 字节一个 {小整数, 小整数, 小整数}" -> 比例照样 100%,
      但每个场景的 map 号都错位了。必须配 scene_tab_struct_ok 一起用。
    """
    good = tot = 0
    n = len(words) // 4
    for s in range(min(n, OBJ_MAX_SCENES)):
        i = s * 4
        mv, s1, s2 = words[i], words[i + 1], words[i + 2]
        tot += 1
        if mv <= 255 and (s1 <= MAX_SCRIPT or s1 == 0xFFFF) \
                and (s2 <= MAX_SCRIPT or s2 == 0xFFFF):
            good += 1
    return (good / float(tot)) if tot else 0.0


# 事件对象总数 (nEventObject) 的合理区间 —— 由游戏数据 SSS.MKF#0 决定
# (= 5077 个对象), 与 exe 无关, 所以换机器/换 exe 都不变。
OBJ_TOTAL_MIN = 1000
OBJ_TOTAL_MAX = 6000
OBJ_NONZERO_MIN = 100


def scene_tab_struct_ok(words, min_total=OBJ_TOTAL_MIN, max_total=OBJ_TOTAL_MAX):
    """rgScene 的"累积对象序号"结构是否成立 —— 纯函数, **强判据** (2026-10-16)。

    第 4 个 WORD = 该场景事件对象的**末序号**(全局 1-based 累积值)。真机实测:
        idxs[2..294] 单调不减, max = 5077 (== nEventObject), 之后空场景为 0
    ("0" 出现在前两个场景 —— 那是"场景 1/2 没有对象"。

    为什么需要它: 单靠"每个字段是不是小整数"挡不住"整条/多条平移"的错位
    (表是均匀结构)。而累积序号一旦错位就会**破坏单调性**或让数量离谱。
    实测: 正确 slide -> True; ±8 / ±0x100 / ±0x2E50 -> 全 False。
    """
    n = len(words) // 4
    idxs = [words[i * 4 + 3] for i in range(min(n, OBJ_MAX_SCENES))]
    nz = [i for i, v in enumerate(idxs) if v]
    if len(nz) < OBJ_NONZERO_MIN:
        return False
    lo, hi = nz[0], nz[-1]
    if any(idxs[i] > idxs[i + 1] for i in range(lo, hi)):
        return False
    return min_total <= max(idxs) <= max_total


def levelup_tab_match(words, off=0):
    """words[off:off+100] 是不是"升级经验表" —— 纯函数, 用于映像扫描。

    判据 (三重, 抗误报):
      ① 前 10 项 == LEVELUP_HEAD (0,15,40,90,165,265,390,540,715,915)
      ② 到第 89 项为止单调不减
      ③ 尾部 [90..99] 至少 8 项 == 32000 (满级阈值)
    """
    if off < 0 or off + LEVELUP_N > len(words):
        return False
    v = words[off:off + LEVELUP_N]
    if tuple(v[:10]) != LEVELUP_HEAD:
        return False
    if any(v[i] > v[i + 1] for i in range(1, 89)):
        return False
    return sum(1 for x in v[90:100] if x == 32000) >= 8


def scan_levelup(data):
    """在原始字节里扫"升级经验表" -> 命中偏移列表 (纯函数)。"""
    if not data or len(data) < LEVELUP_N * 2:
        return []
    words = struct.unpack_from("<%dH" % (len(data) // 2), data)
    hits = []
    for off in range(0, len(words) - LEVELUP_N):
        if words[off] == 0 and words[off + 1] == 15 \
                and levelup_tab_match(words, off):
            hits.append(off * 2)
    return hits


# ----------------------------------------------------------------------
# 进程 / 模块信息 (只有 OpenProcess + ReadProcessMemory + VirtualQueryEx)
# ----------------------------------------------------------------------
def open_process(pid):
    return k32.OpenProcess(OPEN_OK, False, int(pid))


def read_handle(h, addr, size):
    """已有句柄读一段内存; 失败返回 None。"""
    if not h:
        return None
    buf = (ctypes.c_char * size)()
    got = ctypes.c_size_t()
    if not k32.ReadProcessMemory(h, ctypes.c_void_p(int(addr)), buf, size,
                                 ctypes.byref(got)):
        return None
    return buf.raw[:got.value]


def read(pid, addr, size):
    """按 pid 读一段内存 (自开自关句柄)。失败返回 None。"""
    h = open_process(pid)
    if not h:
        return None
    try:
        return read_handle(h, addr, size)
    finally:
        k32.CloseHandle(h)


def enum_windows():
    """所有顶层窗口 -> [(hwnd, pid, 标题)] (标题为空也返回, 便于诊断)。"""
    out = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def cb(hwnd, lp):
        pid = wt.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        n = u32.GetWindowTextLengthW(hwnd)
        t = ""
        if n:
            b = ctypes.create_unicode_buffer(n + 1)
            u32.GetWindowTextW(hwnd, b, n + 1)
            t = b.value
        if t:
            out.append((hwnd, pid.value, t))
        return True
    u32.EnumWindows(cb, 0)
    return out


def enum_game_pids():
    """标题含 Pal 且不是自家 GUI 的窗口所属 pid (升序去重)。"""
    return sorted({p for _h, p, t in enum_windows() if is_game_title(t)})


def process_image_path(h):
    """进程映像路径 (QueryFullProcessImageNameW); 失败返回 None。"""
    if not h:
        return None
    buf = ctypes.create_unicode_buffer(1024)
    size = wt.DWORD(1024)
    if not k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
        return None
    return buf.value


def module_base(pid, h=None):
    """游戏主模块 (sdlpal.exe) 的真实加载基址 -> (base, size, path)。

    64 位 Python 看 32 位目标同样可用 (psapi 返回的是目标自己的模块列表)。
    取不到返回 (None, None, path)。**认名字**: 混编列表里可能既有 32 位又有
    64 位模块, 只认与进程映像同名的那个, 避免拿到别的 DLL 的基址。
    """
    own = False
    if h is None:
        h = open_process(pid)
        own = True
    if not h:
        return None, None, None
    try:
        path = process_image_path(h)
        base_name = os.path.basename(path or "").lower()
        arr = (wt.HMODULE * 512)()
        need = wt.DWORD()
        if not psapi.EnumProcessModulesEx(h, arr, ctypes.sizeof(arr),
                                          ctypes.byref(need), LIST_MODULES_ALL):
            return None, None, path
        n = min(need.value // ctypes.sizeof(wt.HMODULE), 512)
        cands = []
        for i in range(n):
            nm = ctypes.create_unicode_buffer(512)
            try:
                psapi.GetModuleBaseNameW(h, arr[i], nm, 512)
            except Exception:
                continue                      # 超出 32 位的混编项, 跳过
            base_name_i = (nm.value or "").lower()
            if not base_name_i:
                continue
            if base_name and base_name_i != base_name:
                continue
            mi = MODULEINFO()
            try:
                if not psapi.GetModuleInformation(h, arr[i], ctypes.byref(mi),
                                                  ctypes.sizeof(mi)):
                    continue
            except Exception:
                continue
            cands.append((int(mi.lpBaseOfDll or 0), int(mi.SizeOfImage or 0),
                          os.path.basename(nm.value)))
        if cands:
            # 主模块是列表里第一个"与映像同名"的项
            return cands[0]
        return None, None, path
    finally:
        if own:
            k32.CloseHandle(h)


def allocation_base(pid, addr, h=None):
    """VirtualQueryEx: 地址所在区域的 (AllocationBase, Type, RegionSize)。

    标定地址那块是 MEM_IMAGE, 它的 AllocationBase 就是模块基址 —— 这是
    psapi 不可用时的兜底 (>0 且 Type==MEM_IMAGE 才可信)。
    """
    own = False
    if h is None:
        h = open_process(pid)
        own = True
    if not h:
        return None, None, None
    try:
        mbi = MBI()
        r = k32.VirtualQueryEx(h, ctypes.c_void_p(int(addr)),
                               ctypes.byref(mbi), ctypes.sizeof(mbi))
        if not r:
            return None, None, None
        return int(mbi.AllocationBase or 0), int(mbi.Type or 0), \
            int(mbi.RegionSize or 0)
    finally:
        if own:
            k32.CloseHandle(h)


# ----------------------------------------------------------------------
# slide 校验 / 求解
# ----------------------------------------------------------------------
def probe_slide(h, slide):
    """校验一个平移量: (ok, 明细文本)。只读 rgScene + nEventObject + 场景号。

    四条判据必须**同时**成立 (少一条就会把错位的地址放进来):
      ① rgScene[300] 结构合理率 >= 0.90   (与 selfcheck 同规则)
      ② 累积对象序号结构成立 (scene_tab_struct_ok) —— 挡"顺序错乱"的错位
      ③ ★ **累积序号最大值 == nEventObject**  —— 挡"整条平移"的错位
         (表是均匀结构, 平移 8 字节的整数倍时 ①②都看不出来; 但末尾的
          nEventObject 是个 4 字节整数, 平移后读到的就不是它了)
      ④ wNumScene 落在 1..300
    """
    raw = read_handle(h, TABLE_ADDR + slide, OBJ_MAX_SCENES * 8)
    if not raw or len(raw) < OBJ_MAX_SCENES * 8:
        return False, "rgScene @0x%08X 读不到" % (TABLE_ADDR + slide)
    words = struct.unpack_from("<%dH" % (len(raw) // 2), raw)
    ratio = scene_tab_ratio(words)
    struct_ok = scene_tab_struct_ok(words)
    idxs = [words[i * 4 + 3]
            for i in range(min(len(words) // 4, OBJ_MAX_SCENES))]
    nz = [v for v in idxs if v]
    mx = max(nz) if nz else 0
    nob_raw = read_handle(h, OBJ_N_ADDR + slide, 4)
    nobj = struct.unpack("<I", nob_raw)[0] if nob_raw and len(nob_raw) == 4 \
        else -1
    total_ok = (mx == nobj and OBJ_TOTAL_MIN <= nobj <= OBJ_TOTAL_MAX)
    sc_raw = read_handle(h, SCENE_ADDR + slide, 2)
    sc = struct.unpack("<H", sc_raw)[0] if sc_raw and len(sc_raw) == 2 else -1
    ok = ratio >= 0.90 and struct_ok and total_ok and 1 <= sc <= 300
    return ok, ("rgScene 合理率 %.1f%% (需>=90%%), 序号结构 %s, "
                "累积末序号 %s == nEventObject %s %s, 场景号 %d (需 1..300)"
                % (ratio * 100, "√" if struct_ok else "×", mx, nobj,
                   "√" if total_ok else "×", sc))


def locate_levelup(h, base, size):
    """在映像 [base, base+size) 里扫"升级经验表" -> [地址] (诊断/兜底用)。"""
    if not base or not size:
        return []
    data = read_handle(h, base, int(size))
    if not data:
        return []
    return [base + off for off in scan_levelup(data)]


def resolve(pid=None, h=None, use_cache=True):
    """求当前游戏进程的平移量 -> dict。

    返回 {"ok":bool, "slide":int, "base":int, "size":int, "path":str,
          "pid":int, "how":str, "why":str, "cands":[(slide, ok, 明细)],
          "levelup":[地址]}

    ok=False 时 slide 恒为 0 (读到的就是标定地址, 与老版本行为一致)。
    """
    own = False
    if pid is None:
        pids = enum_game_pids()
        if not pids:
            return _fail(None, None, None, "没有窗口标题含 Pal 的游戏进程", [])
        pid = pids[0]
    if h is None:
        h = open_process(pid)
        own = True
    try:
        if use_cache and pid in _cache and _cache[pid].get("ok"):
            return _cache[pid]
        if not h:
            return _fail(pid, None, None, "OpenProcess 失败 (权限/已退出)", [])
        path = process_image_path(h)
        base, size, _nm = module_base(pid, h)
        base2, typ, _rs = allocation_base(pid, TABLE_ADDR, h)
        if not base and base2 and typ == 0x1000000:
            base, size = base2, 0            # 兜底: 映像视图的 AllocationBase
        # ---- 候选平移量 ----
        cands = []
        if base:
            cands.append((slide_for_base(base), "模块基址 0x%08X - 标定基址" % base))
        cands.append((0, "不平移 (老版本行为 / 数据被放在绝对地址)"))
        if base and size:
            for a in locate_levelup(h, base, size):
                s = a - LEVELUPEXP_ADDR
                if all(s != c for c, _w in cands):
                    cands.append((s, "升级经验表特征 @0x%08X 反推" % a))
        # ---- 逐个校验 ----
        tried = []
        chosen = None
        for s, how in cands:
            ok, detail = probe_slide(h, s)
            tried.append((s, ok, "%s; %s" % (how, detail)))
            if ok and chosen is None:
                chosen = (s, how)
            if ok:
                break
        lv = locate_levelup(h, base, size) if (base and size) else []
        if chosen:
            res = {"ok": True, "slide": chosen[0], "base": base or 0,
                   "size": size or 0, "path": path, "pid": pid,
                   "how": chosen[1], "why": "", "cands": tried, "levelup": lv}
        else:
            res = _fail(pid, base, size, "所有候选平移量都过不了校验 (换 exe?)",
                        tried)
            res["path"] = path
            res["levelup"] = lv
        if use_cache and pid:
            _cache[pid] = res
        return res
    finally:
        if own and h:
            k32.CloseHandle(h)


def _fail(pid, base, size, why, tried):
    return {"ok": False, "slide": 0, "base": base or 0, "size": size or 0,
            "path": None, "pid": pid, "how": "未采用", "why": why,
            "cands": tried, "levelup": []}


def game_pid():
    """当前游戏 pid (没有 -> None)。"""
    pids = enum_game_pids()
    return pids[0] if pids else None


def slide(pid=None):
    """平移量; 拿不到/校验不过 -> 0 (行为与老版本一致)。"""
    r = resolve(pid)
    return r["slide"] if r.get("ok") else 0


def addr(x, pid=None):
    """标定地址 -> 本次运行的真实地址。"""
    return int(x) + slide(pid)


def shift_module(ns, *names):
    """把一批"模块级标定地址常量"就地换成运行时地址 (给 tools/*.py 用)。

    用法 (放在常量定义之后、函数定义之前):
        game_addr.shift_module(globals(), "PTR_ADDR", "N_ADDR", "SCENE_TAB")

    诊断脚本一般是"游戏开着才跑", 所以模块导入时求一次 slide 就够。
    返回实际用的平移量 (便于打印)。
    """
    s = slide()
    for n in names:
        if n in ns:
            ns[n] = ns[n] + s
    return s


def reset_cache():
    """清缓存 (测试/游戏重启后强制重新求解)。"""
    _cache.clear()


def diag_lines(pid=None, r=None):
    """人类可读的诊断行 (READ_TABLE_FAIL / env_check 都用它)。"""
    if r is None:
        r = resolve(pid)
    out = []
    out.append("游戏进程 pid=%s  映像=%s" % (r.get("pid"), r.get("path") or "?"))
    if r.get("base"):
        out.append("主模块实际基址 = 0x%08X  (PE ImageBase 常为 0x%08X, "
                   "模块大小 0x%X)" % (r["base"], PREFERRED_BASE, r.get("size") or 0))
        out.append("标定基址 = 0x%08X -> 平移量 slide = 0x%X (%+d)"
                   % (CALIB_BASE, slide_for_base(r["base"]),
                      slide_for_base(r["base"])))
    else:
        out.append("读不到主模块基址 (psapi/VirtualQueryEx 都失败)")
    for s, ok, detail in r.get("cands") or []:
        out.append("  候选 slide=0x%-8X %s  %s"
                   % (s, "√" if ok else "×", detail))
    if r.get("levelup"):
        out.append("映像里的升级经验表地址: %s"
                   % ", ".join("0x%08X" % a for a in r["levelup"][:4]))
    out.append("结论: %s" % ("可用, slide=0x%X (%s)" % (r["slide"], r["how"])
                            if r.get("ok") else (r.get("why") or "失败")))
    return out


def main():
    print("=" * 74)
    print("窗口里标题含 Pal 的候选")
    print("=" * 74)
    for hwnd, pid, title in enum_windows():
        if "Pal" in title or "pal" in title.lower():
            print("  hwnd=0x%08X pid=%-8d %r%s"
                  % (hwnd, pid, title,
                     "  <= 会被当成游戏" if is_game_title(title) else ""))
    print()
    print("=" * 74)
    print("地址换算求解")
    print("=" * 74)
    for ln in diag_lines():
        print("  " + ln)
    print()
    r = resolve()
    if r.get("ok"):
        print("真实地址 (标定地址 + slide):")
        for nm, a in (("READ_AT  ", READ_AT), ("SCENE_ADDR", SCENE_ADDR),
                      ("TABLE_ADDR", TABLE_ADDR),
                      ("LEVELUPEXP", LEVELUPEXP_ADDR)):
            print("  %s 0x%08X -> 0x%08X" % (nm, a, a + r["slide"]))
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
