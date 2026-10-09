# -*- coding: utf-8 -*-
"""scene_pilot.py —— 场景调度器 (纯逻辑, 不碰游戏, 便于离线单测)

作用: 判断「当前这个场景还值不值得待」——不值得就告诉 bot 走门换下一层。

为什么需要它 (2026-09-29 实测数据):
  · 一场战斗 ~11.2 秒, 但相邻两场之间平均要等 104 秒 —— 差别全在「找怪」上
  · 场景 157 几何上限 1.0 场/分, 实测 0.50; 场景 229 上限 3.45 场/分
  · => 换场景的收益(3~7 倍) 远大于路线微调(4~16%)
  · 但「门通向哪里」事先不可知 => 不做全局最优路由, 只做「效率驱动的逐段前进」:
      在 A 刷 → 长时间刷不动 → 走最近的门去 B → B 好就留下, B 也差就继续走

判据 (三条, 并联):
  ① 长时间没战斗: now - 上次战斗 > IDLE_LIMIT x 耐心系数
  ② 效率太低:     停留 > EFF_WINDOW 且 胜场/分 < EFF_MIN
  ③ 过路场景:     评分表判 pass(可达怪点<3) => 无战斗超过 PASS_IDLE 就走

防抖:
  · CHANGE_CD 内不再换 (换一次场景要走路, 别来回折腾)
  · REVISIT_CD 内回到刚离开的场景 -> 视为「门在往回走」, 耐心砍半
  · 同一场景被判低效 N 次 -> dud 计数, 下次进入耐心更短(最终变成"纯过路")
"""
import os
import time


def _clamp(v, lo, hi):
    return lo if v < lo else (hi if v > hi else v)


def _env(name, default):
    """允许用环境变量临时调参 (实测/排障用), 不写进命令行。"""
    try:
        return float(os.environ[name])
    except (KeyError, ValueError, TypeError):
        return default


class ScenePilot:
    # ---- 时间参数 (秒) ----
    MIN_STAY = 150.0        # 进场景后至少待这么久才允许换 (别一进门就跑)
    IDLE_LIMIT = 240.0      # 基准: 超过这么久没战斗 -> 换场景
    CHANGE_CD = 180.0       # 两次换场景之间的最小间隔
    EFF_WINDOW = 480.0      # 效率评估窗口(停留时间下限)
    EFF_MIN = 0.15          # 窗口内胜场/分低于此 -> 低效
    PASS_IDLE = 100.0       # 过路场景: 无战斗这么久就走
    REVISIT_CD = 900.0      # 回流判定: 这么快回到刚离开的场景 = 门在回头
    FAIL_CD = 120.0         # 换场景尝试失败后的静默期

    def __init__(self, scores=None, log=None):
        self.scores = scores or {}
        self.log = log or (lambda m: None)
        self.enabled = True
        # 环境变量覆盖 (实测/排障用; 正常跑不设)
        self.MIN_STAY = _env("PAL_PILOT_MIN_STAY", self.MIN_STAY)
        self.IDLE_LIMIT = _env("PAL_PILOT_IDLE", self.IDLE_LIMIT)
        self.CHANGE_CD = _env("PAL_PILOT_CD", self.CHANGE_CD)
        self.EFF_WINDOW = _env("PAL_PILOT_EFF_WIN", self.EFF_WINDOW)
        self.EFF_MIN = _env("PAL_PILOT_EFF_MIN", self.EFF_MIN)
        self.PASS_IDLE = _env("PAL_PILOT_PASS_IDLE", self.PASS_IDLE)
        # 运行时状态
        self.scene = None
        self.t_arrive = 0.0
        self.b0 = 0                 # 进入场景时的全局战斗数
        self.t_last_battle = 0.0
        self.t_last_change = 0.0
        self.hist = []              # [(scene, 离开时刻)]
        self.visits = {}            # scene -> 进入次数
        self.dud = {}               # scene -> 被判低效次数
        self.n_change = 0
        self.n_deny = 0             # 被冷却/停留时间挡下的次数(仅统计)
        self.last_reason = ""

    # ------------------------------------------------------------------
    def on_scene(self, sc, now, battles, reason=""):
        """进入场景时调用。会结算上一段停留。"""
        if self.scene is not None and sc != self.scene:
            self.hist.append((self.scene, now))
            self.hist = self.hist[-12:]
        self.scene = sc
        self.t_arrive = now
        self.b0 = battles
        self.t_last_battle = now
        self.visits[sc] = self.visits.get(sc, 0) + 1
        self.last_reason = ""
        return self._banner(sc, reason)

    def on_battle(self, now, battles=0):
        self.t_last_battle = now

    def note_change(self, now):
        self.t_last_change = now
        self.n_change += 1

    def note_fail(self, now):
        """尝试换场景但没换成 (本场景无门/门不可达/探测失败)。"""
        self.t_last_change = now - self.CHANGE_CD + self.FAIL_CD

    # ------------------------------------------------------------------
    #  评分表
    # ------------------------------------------------------------------
    def info(self, sc=None):
        return self.scores.get(str(sc if sc is not None else self.scene)) or {}

    def _median_expect(self):
        if not hasattr(self, "_med") or self._med is None:
            vals = [v.get("expect", 0.0) for v in self.scores.values()
                    if not v.get("pass")]
            vals = [v for v in vals if v > 0]
            vals.sort()
            self._med = vals[len(vals) // 2] if vals else 0.5
        return self._med

    def patience(self, sc=None, now=None):
        """耐心系数: 越小越容易决定「走」。"""
        v = self.info(sc)
        if not v:
            p = 1.0
        elif v.get("pass"):
            p = 0.45                      # 过路场景
        else:
            med = self._median_expect() or 0.5
            p = _clamp(0.70 + 0.50 * (v.get("expect", 0.0) / med), 0.60, 1.60)
        # 反复低效的场景越来越没耐心
        p *= max(0.35, 1.0 - 0.25 * self.dud.get(sc if sc is not None
                                                 else self.scene, 0))
        # 刚离开又回来(门在回头) -> 砍半
        sc = sc if sc is not None else self.scene
        now = time.time() if now is None else now
        for hsc, t in reversed(self.hist[-4:]):
            if hsc == sc and now - t < self.REVISIT_CD:
                p *= 0.5
                break
        return p

    # ------------------------------------------------------------------
    def decide(self, now, battles, force=False):
        """要不要换场景? 返回 (bool, 原因)。"""
        if not self.enabled or self.scene is None:
            return False, ""
        stay = now - self.t_arrive
        if not force:
            if stay < self.MIN_STAY:
                return False, ""
            if now - self.t_last_change < self.CHANGE_CD:
                self.n_deny += 1
                return False, ""
        p = self.patience(now=now)
        v = self.info()
        idle = now - self.t_last_battle
        wins = battles - self.b0
        eff = 60.0 * wins / max(stay, 1.0)

        # ① 过路场景: 逛一圈没得打就走
        if v.get("pass") and idle >= self.PASS_IDLE * p:
            return True, "过路场景(可达怪点%d个) 已%ds无战斗" % (
                v.get("n_mon", 0), int(idle))
        # ② 长时间没战斗
        if idle >= self.IDLE_LIMIT * p:
            return True, "%ds 无战斗 (场景%d: 怪点%d/巡回%d格)" % (
                int(idle), self.scene, v.get("n_mon", -1), v.get("ring", -1))
        # ③ 效率太低
        if stay >= self.EFF_WINDOW and eff < self.EFF_MIN:
            return True, "停留%.0f分只赢%d场 (%.2f 场/分 < %.2f)" % (
                stay / 60.0, wins, eff, self.EFF_MIN)
        return False, ""

    # ------------------------------------------------------------------
    def _banner(self, sc, reason=""):
        v = self.info(sc)
        if not v:
            return "[调度] 进入场景 %d (%s) — 无评分记录, 按默认耐心" % (sc, reason)
        tag = "过路" if v.get("pass") else "主刷"
        return ("[调度] 进入场景 %d (%s) — %s: 可达怪点 %d 个(共%d), 巡回 %d 格, "
                "上限 %.2f 场/分, 耐心 x%.2f%s"
                % (sc, reason, tag, v.get("n_mon", 0), v.get("n_mon_all", 0),
                   v.get("ring", 0), v.get("ceil", 0.0), self.patience(sc),
                   (" [第%d次来]" % self.visits.get(sc, 1))
                   if self.visits.get(sc, 1) > 1 else ""))

    def note_dud(self, sc=None):
        sc = sc if sc is not None else self.scene
        self.dud[sc] = self.dud.get(sc, 0) + 1
        return self.dud[sc]

    # ------------------------------------------------------------------
    def summary(self):
        return {"换场景次数": self.n_change, "冷却挡下": self.n_deny,
                "低效标记": len(self.dud), "去过的场景": len(self.visits)}
