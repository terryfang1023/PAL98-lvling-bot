# -*- coding: utf-8 -*-
"""bot_config.json —— 用户可调选项的持久化 (在 GUI 里调一次, 以后都算数)。

为什么单独开一个文件, 而不是塞进 scene_info.json 或环境变量:
  * `scene_info.json` / `hotspot.json` / `mem_doors.json` / `scene_score.json`
    都是从游戏内存或运行过程**派生**出来的数据, 工具会整个覆写 ->
    用户偏好放进去会被冲掉。
  * 环境变量不落盘, 关掉终端就忘 —— 但用户要的是"在界面里调, 下次打开还在"。
  * 所以分工是: 派生的数据归派生文件, **人的选择**归 bot_config.json。

取值优先级 (高 -> 低):
    环境变量 PAL_<KEY>   >   bot_config.json   >   DEFAULTS

环境变量那一层的意义: GUI 启动 bot 子进程时, 用它把"界面上这一刻的值"
直接钉给子进程, 免得仓库目录/母目录两份 bot_config.json 互相不一致时
"界面显示 r, 实际跑 f"。

容错: 文件缺失 / 内容损坏 / 类型不对, 一律静默回退默认值 ——
      机器人绝不能因为一个用户配置文件读不动就起不来。
"""
import json
import os
import tempfile

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE, "bot_config.json")

# ----------------------------------------------------------------------
# 版本号 —— GUI 标题栏 / 底部状态区显示的就是它 (2026-10-12 加)
# ----------------------------------------------------------------------
# 口径: 这里是**对外的公开发布号**(语义化版本, 见 GitHub Release / tag);
#       README 与源码注释里那些 `v16 / v18.9 / v19.2` 是**内部开发代号**,
#       两者不是一回事, 别混用。
# 升级版本时改这里一处即可 (GUI 内部只读它, 不另抄一份字符串)。
#
# ⚠️ 这个字符串里**绝不能出现 "Pal"**(严格区分大小写, 就是 P-a-l 三个字符):
#    机器人 / GUI 找游戏窗口用的判据是「标题含 'Pal'」(游戏真标题 =
#    `Pal Win95 [v3.0.2025.0616-dirty]`, 见 pal_bot_mem.find_pal_pid)。
#    自家 GUI 窗口标题里只有全大写的 "SDLPAL", 所以现在**没有**撞车;
#    一旦标题里混进大小写敏感的 "Pal", 机器人就会把 GUI 当成游戏窗口去读内存。
#    (pal_bot.list_pal_windows 那条路径另有 "挂机机器人" 排除; 2026-10-12 又给
#     几处 ctypes finder 补齐了同样的兜底 —— 见 pal_bot_mem.find_pal_pid /
#     pal_bot_mem5._game_pids / bot_itemmon.is_game_title。但别去试探这条底线,
#     标题保持全大写 "SDLPAL" 最省事。)
APP_VERSION = "v0.2.0-beta"

# ----------------------------------------------------------------------
# 战斗中反复按的那个键 (attack_key) 的候选值
# ----------------------------------------------------------------------
# ★ SDLPAL 官方键表 (input.c 的 g_KeyMap, 2026-10-01 核对源码):
#     r = kKeyRepeat     重复上次行动   ← 挂机"一路平A"要的就是这个
#     a = kKeyAuto       自动战斗 (不用每回合输入)
#     f = kKeyForce      法术 / Force
#     d = kKeyDefend     防御
#     q = kKeyFlee       逃跑
#     w = kKeyThrowItem  掷物品
#     e = kKeyUseItem    使用物品
#     s = kKeyStatus     状态
#     空格 / 回车 = kKeySearch 确认 (战斗里 = 选中当前菜单项; 默认第 1 项是"攻击")
#
# ⚠️ 旧代码写的是 F, 注释还写着 "F=重复最强攻击" —— **那条注释是错的**:
#    官方键表里 F 是 kKeyForce(法术), 重复是 R。
#    换地图 / 换版本后按键对不上时, 先来这里或 GUI 里换一个再跑。
ATTACK_KEY_CHOICES = ["r", "f", "a", "space", "enter", "d", "q", "e", "w", "s"]

# 具名键 (与 pal_bot.KEYS 的具名键保持同一套; 其余按"单个字母/数字"处理)
NAMED_KEYS = ("space", "enter", "esc", "up", "down", "left", "right")

# 中文键名 -> 规范键名。让人在 GUI 的序列框里直接写
#   "右,回车,回车"   或   "right,enter,enter"
# 都认 (两种混写也行)。
CN_KEY = {
    "上": "up", "下": "down", "左": "left", "右": "right",
    "回车": "enter", "确认": "enter", "确定": "enter",
    "空格": "space", "空格键": "space", "space键": "space",
    "退出": "esc", "取消": "esc", "返回": "esc",
}

# 宏序列默认值: 用户 2026-10-01 实测给出的补血菜单路径
#   (2026-10-13 起这项功能改成"战后自动存盘", 序列需要在 GUI 里按存档路径重设;
#    这里保留原值, 免得旧配置被判成"无效序列"而静默停用)
#   ESC 开菜单(光标默认在第二项"仙术") -> 回车(人物菜单直选"巫后")
#   -> 右 -> 回车 -> 回车 -> 右 -> 回车 -> ESC 关菜单
SEQ_DEFAULT = "esc,enter,right,enter,enter,right,enter,esc"

DEFAULTS = {
    "attack_key": "r",
    # ---- 战后自动存盘 (菜单宏, 见 pal_bot_heal.py; 键名沿用 heal_* 历史名) ----
    "heal_after_battle": True,     # 打完一场就跑一次菜单宏 (存盘)
    "heal_seq": SEQ_DEFAULT,       # 按键序列 (逗号分隔, 中文键名也认)
                                   #   存盘路径需重设, 见 GUI「存盘测试」
    "heal_every": 1,               # 每 N 场补一次 (1 = 每场都补)
    "heal_gap": 0.30,              # 相邻两键的间隔 (秒); 太小吃菜单会丢键
    # ---- 绳子攀爬 (试炼窟等多层地图, 见 pal_bot_mem5.rope_probe) ----
    "rope": True,                  # 允许用绳子换层 (站在绳上按空格)
    "rope_hunt": True,             # ★ 绳子最高优先级: 见到近处可爬的绳子就去爬
    "rope_hunt_d": 10,             # "遇到"的距离: 绳子在 N 格内才主动去爬
    "rope_hunt_cd": 120,           # 爬成功后 N 秒内不再主动爬 (防两层间横跳)
    # ---- 开箱台账 (chest_log.json, 见 bot_chestlog.py) ----
    "chest_memo": True,            # 记住开过的箱子: 跨重启跳过, 不再白跑一趟
    "chest_try_n": 1,              # 同一箱子试 N 次没反应就记「已开着/空箱」
                                   #   1 = 碰到就记 (场景里早就打开的箱子也入账)
    # ---- 空箱从内存判断 (2026-10-04, 见 pal_bot_mem5.chest_verdict) ----
    # 开过的箱子会把触发脚本改写成"箱子已空"提示 (再也走不到加物品指令),
    # 隐形箱 (sState<=0) 也按不出东西 —— 这两类直接忽略, 连走都不用走。
    "chest_mem": True,             # 关掉 = 退回"走过去试一次没反应才算空"
    # ---- 走没走过的路 (2026-10-04) ----
    # 三档一起上: 寻路避重 (见 pal_bot_mem.REVISIT_LAMBDA) + 怪点轮转
    # (pal_bot_mem5.MON_FRESH_W) + 定期探索配额 (pal_bot_mem5.EXPLORE_*)。
    "revisit_lambda": 0.15,        # 寻路避重强度: 每"走过一次"折算多少格距离。
                                   #   0 = 关掉, 永远走最短路径(老行为);
                                   #   0.15 只在有多条近似等长路时才绕开熟路。
    "explore": True,               # 定期去一次没走过的区域 (覆盖率优先)
    "explore_every": 25,           # 每 N 场战斗触发一次探索 (0 = 不探索)
    # ---- 要素台账 (feat_log.json, 2026-10-08, 见 bot_featlog.py) ----
    # 按「要素编号」记每一次战斗 (编号 = 场景号-对象序号, 由 build_scene_info 派生;
    # 名字 = 敌人名/物品名, 来自游戏 WORD.DAT)。用 tools/feat_report.py 出报告:
    # 每张地图哪些怪点打过/各打了几次/哪些从来没打过。
    "feat_log": True,              # 关掉 = 不记要素台账 (省一点 IO)
}


def norm_key(name):
    """规范化 + 语法校验一个按键名, 返回小写键名; 非法抛 ValueError。

    认三种写法: 中文键名 (右/回车), 具名键 (right/enter), 单个字母/数字 (r/1)。
    只做**语法**检查, 不查 VK/扫描码 ——
    能不能真的发出去由 pal_bot.key_spec() 定。这样 GUI 侧校验不必导入
    pal_bot (pal_bot 导入时会设 DPI 感知, 把 tkinter 控件缩小)。
    """
    n = str(name if name is not None else "").strip().lower()
    if n in CN_KEY:
        return CN_KEY[n]
    if n in NAMED_KEYS:
        return n
    if len(n) == 1 and n.isascii() and (n.isalpha() or n.isdigit()):
        return n
    raise ValueError("必须是单个字母/数字, 中文键名或 %s 之一 (收到 %r)"
                     % ("/".join(NAMED_KEYS), name))


def parse_seq(text):
    """把"按键序列"文本解析成键名列表; 有一项不认识就抛 ValueError。

    分隔符随便写: 逗号 / 顿号 / 空格 / 加号 / 竖线 / 箭头 -> 都认。
    例: "esc,enter,right,enter,enter,right,enter,esc"
        "esc 回车 右 回车 回车 右 回车 esc"
        "ESC → 回车 → 右 → 回车 → 回车 → 右 → 回车 → ESC"
    """
    s = str(text if text is not None else "")
    for arrow in ("->", "=>", "→", "⇒"):
        s = s.replace(arrow, ",")
    for ch in "，、；;,+ \t\r\n\u3000/|":
        s = s.replace(ch, ",")
    keys = []
    for i, tok in enumerate(s.split(","), 1):
        tok = tok.strip()
        if not tok:
            continue
        try:
            keys.append(norm_key(tok))
        except ValueError as e:
            raise ValueError("第 %d 项 %r 不认识 —— %s" % (i, tok, e))
    return keys


def format_seq(keys):
    """键名列表 -> 便于阅读的字符串 (存盘/回显用)。"""
    return ",".join(str(k) for k in (keys or []))


def env_name(key):
    """某项对应的环境变量名 (attack_key -> PAL_ATTACK_KEY)。"""
    return "PAL_" + str(key).upper()


def load(path=None):
    """读配置文件。缺失/损坏/不是 JSON 对象 -> 返回空 dict (调用方兜默认值)。"""
    p = path or CONFIG_PATH
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return {}
    return d if isinstance(d, dict) else {}


def save(cfg, path=None):
    """原子写: 先写同目录临时文件, 再 os.replace 顶上去。

    为什么不用 open(p, "w") 直接写: 写到一半断电/被杀 -> 留下半个 JSON,
    下次启动就读不出来 (虽然容错会兜住, 但用户设置就静默丢了)。
    replace 在同一分区上是原子的, 不存在"半个文件"的中间态。
    """
    p = path or CONFIG_PATH
    d = os.path.dirname(os.path.abspath(p)) or "."
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    fd, tmp = tempfile.mkstemp(prefix=".botcfg_", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, p)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return p


def get(key, path=None):
    """取一项 (文件里没有/是空串 -> DEFAULTS)。不看环境变量。"""
    v = load(path).get(key)
    if v is None or (isinstance(v, str) and not v.strip()):
        return DEFAULTS.get(key)
    return v


def put(key, value, path=None):
    """改一项并落盘 (保留文件里其它键)。返回写入的路径。"""
    cfg = load(path)
    cfg[key] = value
    return save(cfg, path)


def resolve(key, path=None):
    """按 环境变量 > 配置文件 > 默认值 取值。返回 (值, 来源说明)。"""
    ev = os.environ.get(env_name(key))
    if ev is not None and str(ev).strip() != "":
        return str(ev).strip(), "环境变量 %s" % env_name(key)
    v = load(path).get(key)
    if v is not None and str(v).strip() != "":
        return v, os.path.basename(path or CONFIG_PATH)
    return DEFAULTS.get(key), "默认值"


# ----------------------------------------------------------------------
# 类型转换: 环境变量那一层永远是字符串, JSON 里则可能是 bool/int/float
#   -> 统一走这三个函数, 两边都不会读错。
# ----------------------------------------------------------------------
_TRUE = ("1", "true", "on", "yes", "y", "是", "开", "真")
_FALSE = ("0", "false", "off", "no", "n", "否", "关", "假")


def as_bool(v, default=True):
    """宽松解析布尔值; 认不出来就用 default (配置文件是人手写的, 别报错)。"""
    if isinstance(v, bool):
        return v
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return bool(v)
    s = str(v).strip().lower()
    if s in _TRUE:
        return True
    if s in _FALSE:
        return False
    return default


def as_int(v, default=0):
    try:
        return int(float(str(v).strip()))
    except Exception:
        return default


def as_float(v, default=0.0):
    try:
        return float(str(v).strip())
    except Exception:
        return default


def resolve_bool(key, path=None):
    """按优先级取值并转成 bool。返回 (值, 来源说明)。"""
    v, src = resolve(key, path)
    d = DEFAULTS.get(key)
    return as_bool(v, d if isinstance(d, bool) else True), src


def resolve_int(key, path=None):
    v, src = resolve(key, path)
    return as_int(v, DEFAULTS.get(key) if isinstance(DEFAULTS.get(key), int) else 0), src


def resolve_float(key, path=None):
    v, src = resolve(key, path)
    d = DEFAULTS.get(key)
    return as_float(v, d if isinstance(d, float) else 0.0), src
