# -*- coding: utf-8 -*-
"""自动存盘 (菜单宏引擎) —— pal_bot_heal.py

本模块是**通用的菜单按键宏**: "跑一条固定的菜单路径 + 四道校验",
游戏侧功能由 heal_seq 里那条序列决定。模块名/函数名沿用 2026-10-01
的 heal 历史名 (改名会牵动机器人接线与测试, 收益不大)。

历史: 2026-10-01 做的是"战后自动补血" (每场打完加满血), 序列 = 用户真机实测
的补血菜单路径; 2026-10-13 用户改用**自动存盘** (人物都 99 级, 补血没用了),
只把序列与界面文字换成存档路径, 引擎一行没动:

    ESC 开菜单 -> 系统 -> 存档 -> 选档位 (存完游戏自动回地图, 不用补 esc)

★ 存档路径务必先用 GUI「存盘测试」真按一遍验证: 系统菜单里「退出」「读档」
  是危险项, 序列错位会退出游戏/读回旧档。

本模块把这条路径做成**可配置宏** (键序列写在 bot_config.json / GUI 里),
并且加上三道校验, 坏掉时能自己退出菜单, 而不是把机器人卡在菜单里。

为什么用"宏"而不是"看血量决定":
    血量的内存地址没有用真机样本反查过。按项目硬纪律 (见 MEMORY.md:
    "内存字段索引必须用真实行为样本反查, 不靠推算"), 这一版**不读血量** ——
    只在"打完一场"之后执行一次宏, 场次间隔由 heal_every 控制。
    将来若反查到 HP 数组, 触发条件可以升级成"血不满才加"。

三道校验 (都是"颜色无关"的判据, 熔岩那种红地面场景也照样管用):
  ① 起点校验: 执行前必须"不在战斗、不在菜单"。已经在菜单 -> 先关掉再来,
     免得序列从错误的状态开始按。
  ② 开菜单校验: 发第一下 esc 之后, 画面必须**确实变了** (菜单特征出现,
     或画面差异超过 CHANGE_FRAC)。对话没结束/esc 被吞的情况很常见,
     这时若还硬按完剩下 7 个键, 会把游戏搅进完全无关的子页。
  ③ 收尾校验: 序列跑完必须"画面回到开宏前那张地图"。没回来就补 esc
     (最多 MAX_ESC_RETRY 次), 仍不回就走宿主自己的 escape_menu() 兜底,
     并进入 BAIL_CD 冷却; 连续失败 FAIL_LIMIT 次 -> 本次运行内自动停用。
  ④ 中途战斗校验 (v19.1): **每个中间键发之前**确认没进新战斗 ——
     怪游走贴身无需走路也能触发战斗, 宏跑到一半的 enter 落进战斗
     指令菜单会把"攻击"选上、停在选人。战斗一开始立刻中止宏。

为什么校验要"画面差异 + 菜单特征"双判:
    mem2 的菜单特征 (中央暗红面板 red + 左侧棕黄卷轴 tan) 在熔岩地图上
    会被红地面打穿 —— 菜单明明开着, 特征也可能判成"不是菜单"
    (2026-09-30 的 known issue)。所以只信特征会漏判; 而只用"画面差异"
    又分不清"菜单还开着"和"施法动画在放"。两个一起用:
      菜单开着    = 特征命中 | 画面差异大
      回到地图了  = 特征不命中 & 画面差异小 (允许动画/水波的小抖动)
"""
import os
import time

import numpy as np

import botcfg

DEFAULT_GAP = 0.30        # 相邻两键的间隔 (秒): 太小吃菜单会丢键
CHANGE_FRAC = 0.03        # 画面差异 > 3% 面积 -> 认为"出现了新东西(菜单)"
SETTLE_FRAC = 0.015       # 画面差异 < 1.5% 面积 -> 认为"回到开宏前那张地图"
POLL_TIMES = 6            # 收尾轮询次数 (等施法动画放完)
POLL_GAP = 0.45           # 每次轮询间隔 (秒)
MAX_ESC_RETRY = 2         # 收尾没关掉时最多补几下 esc
BAIL_CD = 180.0           # 宏失败后的冷却 (秒), 其间不再尝试
FAIL_LIMIT = 3            # 连续失败这么多次 -> 本次运行自动停用


def diff_frac(a, b, tol=24):
    """两张截图"明显不同"的像素比例 (0.0~1.0)。形状不一致/缺帧 -> 1.0。"""
    if a is None or b is None:
        return 1.0
    if getattr(a, "shape", None) != getattr(b, "shape", None):
        return 1.0
    try:
        d = np.abs(a.astype("int16") - b.astype("int16")).max(axis=2) > tol
    except Exception:
        return 1.0
    return float(d.mean())


class HealMacro:
    """补血宏本体 (依赖全部由调用方注入, 所以能离线测: 见 test_heal.py)。

    shot()                -> 当前画面 (ndarray)
    log(msg)              -> 写日志
    is_battle(frame)      -> bool      (可省: 省了就跳过战斗校验)
    menu_feat(frame)      -> (red, tan)
    menu_like(red, tan)   -> bool
    escape()              -> 兜底关菜单 (通常是 mem2 的 escape_menu)
    gap                   -> 按键间隔秒
    """

    def __init__(self, shot, key=None, log=print, is_battle=None,
                 menu_feat=None, menu_like=None, escape=None, gap=DEFAULT_GAP):
        self.shot = shot
        self.key = key
        self.log = log
        self.is_battle = is_battle
        self.menu_feat = menu_feat
        self.menu_like = menu_like
        self.escape = escape
        self.gap = max(0.05, float(gap or DEFAULT_GAP))

    # ---------------- 内部 ----------------
    def _is_menu(self, frame):
        """菜单特征命中 (熔岩地面上可能漏判, 所以另配画面差异判据)。"""
        if self.menu_feat is None or self.menu_like is None:
            return False
        try:
            red, tan = self.menu_feat(frame)
            return bool(self.menu_like(red, tan))
        except Exception:
            return False

    def _settled(self, a0, frame):
        """画面是否已经回到"开宏前那张地图"。"""
        return (not self._is_menu(frame)) and \
            diff_frac(a0, frame) <= SETTLE_FRAC

    # ---------------- 主流程 ----------------
    def run(self, keys, why="自动存盘"):
        """执行一次菜单宏。返回结果 dict (ok / reason / sent / opened / closed)。"""
        keys = list(keys or [])
        res = {"ok": False, "reason": "", "sent": [], "opened": False,
               "closed": False, "esc_extra": 0, "elapsed": 0.0,
               "keys": keys, "why": why}
        if not keys:
            res["reason"] = "按键序列是空的"
            return res
        t0 = time.time()

        a0 = self.shot()
        if self.is_battle is not None:
            try:
                if self.is_battle(a0):
                    res["reason"] = "当前在战斗里 -> 不执行 (菜单打不开)"
                    return res
            except Exception:
                pass
        # ① 起点校验: 已经在菜单里就先关掉, 保证序列从"地图"开始按
        if self._is_menu(a0):
            self.log("[存盘] 起始画面像菜单 -> 先关掉再执行宏 (起点必须在地图上)")
            if self.escape is not None:
                self.escape()
                time.sleep(0.4)
            a0 = self.shot()
            if self._is_menu(a0):
                res["reason"] = "已有菜单关不掉 -> 放弃本次"
                res["elapsed"] = time.time() - t0
                return res

        # ② 开菜单 + 校验: 第一下 esc 之后画面必须真的变了
        self._do(keys[0], res)
        a1 = self.shot()
        opened = self._is_menu(a1) or diff_frac(a0, a1) > CHANGE_FRAC
        res["opened"] = opened
        if not opened:
            res["reason"] = ("按 %s 后画面没变 (菜单特征也没出现) —— "
                             "可能对话没结束或按键被吞, 已放弃" % keys[0])
            res["elapsed"] = time.time() - t0
            return res

        # ③ 中间键 —— ★ v19.1 每键前查战斗: 怪游走贴身会在宏跑到一半时
        #   触发新战斗 (无需走路也会发生), 这时再按 enter 就会把战斗指令
        #   菜单打成"选攻击/选人", 机器人从此停在选目标等 180s 超时
        #   (真机 2026-10-10 用户报告)。战斗一开始立刻中止宏, 剩下的
        #   键一个都不发, 交给战斗循环处理 (esc 多按无害, enter 绝不行)。
        for k in keys[1:-1]:
            if self.is_battle is not None:
                try:
                    if self.is_battle(self.shot()):
                        res["reason"] = ("战斗中途开始 -> 中止宏 (已发 %d 键, "
                                         "防 enter 误选攻击)"
                                         % len(res["sent"]))
                        res["elapsed"] = time.time() - t0
                        return res
                except Exception:
                    pass
            self._do(k, res)

        # ④ 收尾: 关菜单 + 校验画面回到地图
        self._do(keys[-1], res)
        closed = False
        for _ in range(POLL_TIMES):
            time.sleep(POLL_GAP)
            if self._settled(a0, self.shot()):
                closed = True
                break
        while not closed and res["esc_extra"] < MAX_ESC_RETRY:
            res["esc_extra"] += 1
            self.log("[存盘] 收尾校验: 画面还没回到地图 -> 补第 %d 次 esc"
                     % res["esc_extra"])
            self._do("esc", res)
            for _ in range(3):
                time.sleep(POLL_GAP)
                if self._settled(a0, self.shot()):
                    closed = True
                    break
        if not closed and self.escape is not None:
            self.log("[存盘] 收尾校验仍不过 -> 交给 escape_menu() 兜底")
            try:
                self.escape()
            except Exception:
                pass
            time.sleep(0.5)
            closed = self._settled(a0, self.shot())
        res["closed"] = closed
        res["ok"] = closed
        if not closed:
            res["reason"] = "序列跑完画面仍不是地图 (可能进错子页/目标不对)"
        res["elapsed"] = time.time() - t0
        return res

    # 真实发键单独一层, 便于测试时注入假按键
    def _do(self, key, res=None):
        if res is not None:
            res["sent"].append(key)
        self._press(key)
        time.sleep(self.gap)

    def _press(self, key):
        if self.key is None:
            raise RuntimeError("HealMacro 没给按键回调 (key=...)")
        self.key(key)


class HealMixin:
    """给 MemBot 系列加"战后自动存盘"。

    宿主 (MemBot4 及其子类) 需要提供:
      self.g.shot() / self.g.key(name) / self.g.flush_keys()
      self.log(msg) / self.band0()
      self._menu_feat / self._menu_like   (mem2 的静态方法, 继承即得)
      self.escape_menu()                  (mem2, 兜底关菜单)
    """

    # ------------------------------------------------------------------
    #  配置: 环境变量 > bot_config.json > 默认值  (走 botcfg, 见其文档)
    # ------------------------------------------------------------------
    def heal_load(self, path=None, force=False):
        """读存盘配置 (只读一次, 之后缓存; force=True 强制重读)。"""
        if getattr(self, "_heal_cfg", None) is not None and not force:
            return self._heal_cfg
        cfg = {"on": False, "keys": [], "every": 1, "gap": DEFAULT_GAP,
               "src": "", "err": ""}
        try:
            on, s1 = botcfg.resolve_bool("heal_after_battle", path)
            seq, s2 = botcfg.resolve("heal_seq", path)
            every, s3 = botcfg.resolve_int("heal_every", path)
            gap, s4 = botcfg.resolve_float("heal_gap", path)
            cfg["keys"] = botcfg.parse_seq(seq)
            cfg["on"] = bool(on) and len(cfg["keys"]) >= 2
            cfg["every"] = max(1, every)
            cfg["gap"] = max(0.05, gap if gap > 0 else DEFAULT_GAP)
            cfg["src"] = "%s / %s / %s / %s" % (s1, s2, s3, s4)
        except ValueError as e:
            # 序列写错了: 不抛异常, 只停用存盘 (机器人照常跑)
            cfg["on"] = False
            cfg["err"] = str(e)
            cfg["src"] = "配置无效"
        self._heal_cfg = cfg
        for k in ("_heal_ok_n", "_heal_fail_n", "_heal_streak", "_heal_bail_t"):
            if not hasattr(self, k):
                setattr(self, k, 0 if k != "_heal_bail_t" else 0.0)
        if not hasattr(self, "_heal_off"):
            self._heal_off = False
        return cfg

    def heal_summary(self):
        """一行配置摘要 (启动时打日志用)。"""
        c = self.heal_load()
        if c["err"]:
            return "关闭 (序列配置无效: %s)" % c["err"]
        if not c["on"]:
            return "关闭"
        return ("开启: 每 %d 场一次, 间隔 %.2fs, %d 个键 [%s]  (%s)"
                % (c["every"], c["gap"], len(c["keys"]),
                   " ".join(c["keys"]), c["src"]))

    # ------------------------------------------------------------------
    #  执行
    # ------------------------------------------------------------------
    def heal_macro(self, why="自动存盘"):
        """跑一次菜单宏, 返回 HealMacro.run 的结果 dict (不判断开关/场次)。"""
        m = HealMacro(shot=self.g.shot, key=self.g.key, log=self.log,
                      is_battle=lambda a: self._heal_is_battle(a),
                      menu_feat=self._menu_feat, menu_like=self._menu_like,
                      escape=getattr(self, "escape_menu", None),
                      gap=self._heal_gap())
        return m.run(self._heal_keys(), why)

    def _heal_is_battle(self, frame):
        import pal_bot as PB
        try:
            return PB.is_battle(frame, self.band0())
        except Exception:
            return False

    def _heal_keys(self):
        return list(self.heal_load()["keys"])

    def _heal_gap(self):
        return self.heal_load()["gap"]

    def after_battle_heal(self, why="自动存盘"):
        """战斗结束后调用。返回 True = 这次真的跑完了宏。

        开关/场次/冷却/连败停用都在这儿判断 —— 关掉时一个键都不发。
        """
        cfg = self.heal_load()
        if cfg["err"]:
            if not getattr(self, "_heal_err_logged", False):
                self._heal_err_logged = True
                self.log("[存盘] 已停用: 键序列配置无效 (%s)" % cfg["err"])
            return False
        if not cfg["on"]:
            return False
        if getattr(self, "_heal_off", False):
            return False
        now = time.time()
        if now < getattr(self, "_heal_bail_t", 0.0):
            self.log("[存盘] 上次失败, 冷却中 (剩 %.0fs) -> 跳过"
                     % (self._heal_bail_t - now))
            return False
        won = int(getattr(self, "battles_won", 0))
        if cfg["every"] > 1 and won % cfg["every"] != 0:
            return False

        res = self.heal_macro(why)
        try:
            self.g.flush_keys()
        except Exception:
            pass
        if res["ok"]:
            self._heal_ok_n += 1
            self._heal_streak = 0
            self.log("[存盘] %s 成功: %d 个键 (%.2fs, 补esc %d 次)"
                     % (why, len(res["sent"]), res["elapsed"], res["esc_extra"]))
        else:
            self._heal_fail_n += 1
            self._heal_streak += 1
            self._heal_bail_t = time.time() + BAIL_CD
            self.log("[存盘] %s 失败: %s (已发 %d 个键: %s) -> 冷却 %.0fs"
                     % (why, res["reason"], len(res["sent"]),
                        " ".join(res["sent"]) or "无", BAIL_CD))
            self._heal_fail_shot(res)
            if self._heal_streak >= FAIL_LIMIT:
                self._heal_off = True
                self.log("[存盘] 连续失败 %d 次 -> 本次运行内停用存盘 "
                         "(可在 GUI 关掉/改序列后重启)"
                         % self._heal_streak)
        return bool(res["ok"])

    def heal_stat(self):
        """(成功, 失败) 计数, 给统计日志用。"""
        self.heal_load()
        return int(getattr(self, "_heal_ok_n", 0)), \
            int(getattr(self, "_heal_fail_n", 0))

    def _heal_fail_shot(self, res):
        """失败留证 (2026-10-04): 存失败瞬间的截图, 看「序列按完后画面停在
        哪一页」, 校准 heal_seq 用 —— 序列对不上光靠日志猜不出来。

        目录由宿主给 (self.heal_shot_dir); 没给就一张不存
        (测试宿主零副作用)。最多留 6 张, 新图顶掉最旧的。
        """
        d = getattr(self, "heal_shot_dir", None)
        if not d:
            return
        try:
            import glob
            from PIL import Image
            old = sorted(glob.glob(os.path.join(d, "heal_fail_*.png")))
            while len(old) >= 6:
                try:
                    os.remove(old.pop(0))
                except OSError:
                    break
            fr = self.g.shot()
            # 文件名带毫秒: 快节奏连续失败也不会同名互覆
            fn = os.path.join(d, "heal_fail_%s_k%02d_%s.png" % (
                time.strftime("%H%M%S") + "_%03d"
                % (int(time.time() * 1000) % 1000),
                len(res.get("sent") or []),
                "open" if res.get("opened") else "noopen"))
            Image.fromarray(fr.astype("uint8")).save(fn)
            self.log("[存盘] 失败现场已存图: %s (按完停在哪一页, 校准序列用)"
                     % os.path.basename(fn))
        except Exception:
            pass
