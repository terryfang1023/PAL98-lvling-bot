# -*- coding: utf-8 -*-
"""pal_bot_mem5.py — v16「要素巡回版」：直奔怪点刷怪 + 顺路开宝箱

════════════════════════════════════════════════════════════════════
为什么不再靠"均匀巡逻"找怪（2026-09-29 内存逆向结论，全部有实测证据）
════════════════════════════════════════════════════════════════════
SDLPAL 把地图上的怪/NPC/宝箱/传送阵都存成**事件对象**(EVENTOBJECT)，
从运行中的游戏内存可以一次性全部读出来（tools/build_scene_info.py）：

  怪   = sprite≠0 且**触发脚本第一句就是 0x0007 START_BATTLE**
         坐标固定；玩家进入其周围 1 格（含斜角）立即开打 (play.c:112)
  宝箱 = 触发脚本含 0x001F ADD_ITEM_TO_INVENTORY
         需走到相邻格、朝它按空格（search 触发）
  传送 = 0x0059 CHANGE_SCENE      石碑 = 0xFFFF TEXT

实测证据链:
  * 场景 157 的 21 个 touch 实体(spr=11/59/470/460/495)脚本链全部是 [0x0007]
  * 日志里 234 场战斗的战斗热点 K(-65,100)/( -11,39)/(14,50) 与
    对象表的 K(-65,99)/(-10,39)/(15,50) 一一对上（差 1 格 = 触发半径）
  * 战斗收尾脚本 = 0x0052 HIDE_FOR_A_WHILE（隐藏 ~800 帧 = 40~50 秒后重生）
    日志里同一怪点重复触发的最小间隔集中在 **96~107 秒** => 与重生周期吻合
  * 部分怪的自动脚本含 0x004C CHASE_PLAYER => 会主动追玩家（所以偶尔
    战斗发生地离出生点较远）

=> 最优策略不是"全图均匀巡逻"，而是**按最近邻顺序巡回已知怪点**：
   走到 => 触发 => 打完 => 换下一个（约 70 秒后原怪已重生，可再回访）。
   这比"走路数恰好撞上随机遇敌"快得多，而且完全不依赖运气。

本版相对 v15(pal_bot_mem4) 只做加法：
  1. pick_target 优先返回「当前场景未在冷却中的怪点」；
  2. 走到怪点/在此开打后，该怪点进入冷却（怪在重生，别原地干等）；
  3. 怪点都在冷却时，顺路去未开的宝箱：走到相邻格 -> 转身 -> 按空格；
  4. 以上全部不可用时，**原样回退 v15 的渐进巡逻**（保底不当机）。
要素清单由 tools/build_scene_info.py 从内存生成 scene_info.json，
启动时缺失会自动尝试生成一次（1~2 秒，不用真的跑遍地图）。
关闭要素模式（回到纯 v15 行为）: 第 8 个参数写 巡逻 或 off
"""
import json
import os
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import pal_bot as PB                                          # noqa: E402
from pal_bot_mem4 import MemBot4                              # noqa: E402


def cheb(p, q):
    return max(abs(p[0] - q[0]), abs(p[1] - q[1]))


class MemBot5(MemBot4):
    # ---- 怪点巡回参数 ----
    MON_CD = 70.0           # 打过之后该怪点的冷却(怪隐藏~800帧≈45s, 留余量)
    MON_SETTLE_MIN = 1.5    # 到了目标点后至少站这么久才判「有没有开打」
    MON_GOAL_TTL = 30.0     # 在途目标的基准时限(实际 = 基准 + 1.5×距离, 见 _goal_ttl)
    MON_HIT_NEAR = 3        # 开打位置距怪点 ≤N 格 => 算这个怪点的战果
    MON_TRY_N = 4           # 走到 1 格内仍不触发 N 次 -> 本局放弃该点
    MON_MISS_N = 3          # 连续 N 次超时都没走到触发圈 -> 判长冷却
    MON_MISS_CD = 600.0     # miss 长冷却(10分钟)。★不永久放弃: miss 真因多半
                            # 是 BFS 假通路/怪点在另一连通块(锁妖塔分层),
                            # 绕行知识更新后可能就通了
    MON_LOG_EVERY = 15      # 每 N 次选点打一条日志
    SLOG_GAP = 20.0         # 同一怪点的日志最小间隔

    # ---- 宝箱参数 ----
    CHEST_TRY_N = 4         # 同一个箱子试 N 次没反应 -> 放弃
    CHEST_CD = 240.0        # 试失败后的冷却
    CHEST_SETTLE = 0.55     # 转身后等待时间(转身动画)
    CHEST_DLG_WAIT = 0.80   # 按空格后等对话框弹出

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.elem_mode = True
        self.scene_info = {}
        self.mon_cd = {}          # (scene,a,b) -> 该怪点可再次前往的时间戳
        self.mon_visit = {}       # (scene,a,b) -> 到了却没开打的次数
        self._mon_miss = {}       # (scene,a,b) -> 没走到触发圈的次数
        self.mon_dead = set()     # 本局放弃的怪点
        self.chest_cd = {}
        self.chest_try = {}
        self.chest_dead = set()
        self._mon_goal = None     # (scene, 走位K, 怪点K, 设定时刻)
        self._chest_goal = None   # (scene, 宝箱K, 站格K, 朝向键)
        self._last_fight_end = 0.0
        self._pick_n5 = 0
        self._elem_t = 0.0
        self._slog_t = {}         # 怪点日志节流
        self._nearest_hint = None
        self.stat = {"mon_hit": 0, "mon_visit": 0, "chest_ok": 0,
                     "chest_try": 0, "fallback": 0}
        self._load_scene_info()

    # ------------------------------------------------------------------
    #  要素清单
    # ------------------------------------------------------------------
    def _load_scene_info(self):
        path = os.path.join(BASE, "scene_info.json")
        if not os.path.exists(path) or os.path.getsize(path) < 100:
            self.log("[要素] scene_info.json 不存在 -> 尝试从内存生成")
            try:
                r = subprocess.run(
                    [sys.executable, os.path.join(BASE, "tools",
                                                  "build_scene_info.py")],
                    cwd=BASE, capture_output=True, timeout=120,
                    env=dict(os.environ, PYTHONIOENCODING="utf-8"))
                if r.returncode != 0:
                    self.log("[要素] 生成失败 -> 退回 v15 巡逻模式")
                    self.elem_mode = False
                    return
            except Exception as e:
                self.log("[要素] 生成异常(%s) -> 退回 v15 巡逻模式" % e)
                self.elem_mode = False
                return
        try:
            with open(path, encoding="utf-8") as f:
                self.scene_info = json.load(f)
        except Exception as e:
            self.log("[要素] 清单读取失败(%s) -> 退回 v15 巡逻模式" % e)
            self.elem_mode = False
            return
        nm = sum(len(v.get("monsters", [])) for v in self.scene_info.values())
        nc = sum(len(v.get("chests", [])) for v in self.scene_info.values())
        self.log("[要素] 清单载入: %d 个场景 / 怪点 %d / 宝箱 %d "
                 "(数据来自内存事件对象表, 精确到格)"
                 % (len(self.scene_info), nm, nc))

    def scene_monsters(self, sc=None):
        sc = self.scene if sc is None else sc
        return (self.scene_info.get(str(sc)) or {}).get("monsters") or []

    def scene_chests(self, sc=None):
        sc = self.scene if sc is None else sc
        return (self.scene_info.get(str(sc)) or {}).get("chests") or []

    # ------------------------------------------------------------------
    #  选目标: 怪点优先 -> 宝箱 -> 回退 v15 巡逻
    # ------------------------------------------------------------------
    def _goal_ttl(self, mk, cur):
        """在途目标的时限: 距离越远给越久(吞吐差的时候 20 格可能要走 30 秒)。"""
        return self.MON_GOAL_TTL + 1.5 * cheb(mk, cur)

    def pick_target(self, exclude=None):
        if not self.elem_mode or self.migrate or self.home_active():
            return MemBot4.pick_target(self, exclude)
        cur = self.mem.state()["K"]
        self._settle_goal(cur)                 # 先结算上一轮目标
        # ★ 目标还在途就直接沿用。两道防线:
        #   ①pick_target 不再每 tick 重设目标(否则驻留计时永远刷新)
        #   ②_settle_goal 只在「超时」时才判 miss(首跑实测: 用 MON_SETTLE_MIN
        #     判 miss 会让远距离目标刚设 1.5 秒就被弃, 8 个怪点全废)
        g = self._mon_goal
        if g is not None and g[0] == self.scene:
            if time.time() - g[3] < self._goal_ttl(g[2], cur):
                return g[1]
            self._mon_goal = None
        gc = self._chest_goal
        if gc is not None and gc[0] == self.scene:
            if time.time() - gc[4] < self._goal_ttl(gc[2], cur):
                return gc[2]
            self._chest_goal = None
        if not (self.scene_monsters() or self.scene_chests()):
            return MemBot4.pick_target(self, exclude)
        ban, reach = self.plan_ctx(cur)
        cand = [c for c in reach if c not in ban and c != cur and c != exclude]
        if not cand:
            return MemBot4.pick_target(self, exclude)
        r = self._pick_monster(cur, cand)
        if r is not None:
            t, mk = r
            self._mon_goal = (self.scene, t, mk, time.time())
            return t
        t = self._pick_chest(cur, cand)
        if t is not None:
            return t
        self.stat["fallback"] += 1
        return MemBot4.pick_target(self, exclude)

    def _pick_monster(self, cur, cand):
        """返回 (要走的格, 该格对应的怪点) —— 全是冷却/已弃则 None。

        ★ 刻意排除"队伍已经站在其 1 格触发圈内"的怪点: 那种情况该触发早就
        触发了, 再把它当目标只会让队伍在相邻两格之间来回摆(首跑实测)。
        这类点交给 _settle_goal 去冷却/弃用。"""
        lst = self.scene_monsters()
        if not lst:
            return None
        now = time.time()
        rset = set(cand)
        best = None
        bd = 10 ** 9
        n_cd = 0
        n_in = 0
        for m in lst:
            k = (m["K"][0], m["K"][1])
            key = (self.scene, k[0], k[1])
            if key in self.mon_dead:
                continue
            if now < self.mon_cd.get(key, 0):
                n_cd += 1
                continue
            if cheb(k, cur) <= 1:
                n_in += 1          # 已经在触发圈里 -> 不是"要去"的目标
                continue
            # 优先站到怪点周围 1 格(触发范围就是 1 格), 这样不会被怪自身的
            # 对象碰撞挡在目标格上 -> 避免误记"实测被挡"把怪点格永久封掉
            alts = [c for c in ((k[0] + da, k[1] + db)
                                for da in (-1, 0, 1) for db in (-1, 0, 1))
                    if c in rset]
            if alts:
                kk = min(alts, key=lambda c: cheb(c, cur))
            elif k in rset:
                kk = k
            else:
                continue
            d = cheb(kk, cur)
            if d < bd:
                bd, best = d, (kk, k)
        self._pick_n5 += 1
        if best is not None and self._pick_n5 % self.MON_LOG_EVERY == 0:
            self.log("[怪点] 目标 K%s -> 怪 K%s (距 %d 格; 本场景 %d 个怪点, "
                     "冷却 %d, 圈内 %d, 已弃 %d)"
                     % (best[0], best[1], bd, len(lst), n_cd, n_in,
                        sum(1 for x in self.mon_dead if x[0] == self.scene)))
        return best

    def _pick_chest(self, cur, cand):
        """未开的宝箱 -> 挑一个可站的相邻格; 同时记住朝向。"""
        lst = self.scene_chests()
        if not lst:
            return None
        now = time.time()
        rset = set(cand)
        best = None
        bd = 10 ** 9
        info = None
        for c in lst:
            k = (c["K"][0], c["K"][1])
            key = (self.scene, k[0], k[1])
            if key in self.chest_dead or now < self.chest_cd.get(key, 0):
                continue
            # 站格 = 箱子 + (da,db); 从站格看向箱子 = 反方向
            # K 空间按键真值: up=+a, down=-a, left=-b, right=+b
            for da, db in ((1, 0), (-1, 0), (0, -1), (0, 1)):
                s = (k[0] + da, k[1] + db)
                if s not in rset:
                    continue
                kd = {(1, 0): "down", (-1, 0): "up",
                      (0, -1): "right", (0, 1): "left"}[(da, db)]
                d = cheb(s, cur)
                if d < bd:
                    bd, best, info = d, s, (k, kd)
        if best is not None:
            self._chest_goal = (self.scene, info[0], best, info[1], time.time())
            self.log("[宝箱] 目标 箱K%s 站位 K%s 朝向 %s (距 %d 格)"
                     % (info[0], best, info[1], bd))
        return best

    # ------------------------------------------------------------------
    #  结算: 上一轮目标是怪点/宝箱, 到了之后该记什么
    # ------------------------------------------------------------------
    def _settle_goal(self, cur):
        """结算上一轮怪点目标。

        ★ 实测教训(2026-09-29 首跑): 原先用 cheb<=1 判「到达」, 队伍站在目标
        身侧 1 格时每 tick 都判一次 -> 174 次空转刷屏。
        ★★ 第二次实测教训: 修完之后改成「过了 MON_SETTLE_MIN(1.5s) 还没走到
        就记 miss」, 结果远距离目标刚设 1.5 秒就被判失败, 8 个怪点 3 秒内全部
        「连续 3 次没走到 -> 放弃」, 一场没赢。
        现在的判据分三段, 互不干扰:
          ① 在途(还没进触发圈) + 未超时      -> 什么都不做, 继续走
          ② 在途 + 超时(时限 = 基准 + 1.5×距离) -> 记 miss, 重选
          ③ 已到触发圈 + 站够 MON_SETTLE_MIN  -> 判「开打 / 走到没打」
        """
        g = self._mon_goal
        if not g:
            return
        sc, t, mk, ts = g
        if sc != self.scene:
            self._mon_goal = None
            return
        age = time.time() - ts
        key = (sc, mk[0], mk[1])
        if cheb(mk, cur) > 1:
            if age < self._goal_ttl(mk, cur):
                return                   # ① 还在路上, 别打扰
            n = self._mon_miss.get(key, 0) + 1      # ② 超时没走到
            self._mon_miss[key] = n
            self._mon_goal = None        # 重选(BFS 会用最新绕行知识重算路径)
            if n >= self.MON_MISS_N:
                self.mon_cd[key] = time.time() + self.MON_MISS_CD
                self._mon_miss[key] = 0  # 重置, 冷却结束后再给一轮机会
                self._slog(key, "[怪点] K%s 连续 %d 次超时(%.0fs/次)都没走到 "
                           "-> 冷却 %d 分钟 (BFS 假通路/可能在别的层)"
                           % (mk, n, age, self.MON_MISS_CD // 60))
            return
        if age < self.MON_SETTLE_MIN:
            return                       # ③ 刚踏进触发圈, 等一会儿看是否开打
        self._mon_goal = None
        if time.time() - self._last_fight_end < 8.0:
            self.mon_visit[key] = 0
            self._mon_miss[key] = 0
            self.mon_cd[key] = time.time() + self.MON_CD
            self.stat["mon_hit"] += 1
            self._slog(key, "[怪点] K%s 已触发战斗 -> 冷却 %.0fs"
                       % (mk, self.MON_CD))
        else:
            # ★ 走进 1 格内仍不触发: 实测多半是这只怪刚被打掉 ——
            #   内存里 sState 从 2 变成 -2, 而 play.c:96 规定 sState<0 的对象
            #   **离开视口(约10格)后自动恢复成 +2** => 走远再回来就是新的一只。
            #   所以这里给冷却而不是永久放弃; 连试 4 次都不行才判该点失效。
            n = self.mon_visit.get(key, 0) + 1
            self.mon_visit[key] = n
            self.mon_cd[key] = time.time() + self.MON_CD
            self.stat["mon_visit"] += 1
            if n >= self.MON_TRY_N:
                self.mon_dead.add(key)
            self._slog(key, "[怪点] K%s 走到 1 格内未触发 (怪在恢复期/不触发) "
                       "第%d次 -> 冷却 %.0fs%s"
                       % (mk, n, self.MON_CD,
                          " -> 本局放弃" if n >= self.MON_TRY_N else ""))

    def _slog(self, key, msg):
        """怪点日志节流: 同一个点最多每 20 秒一条。"""
        now = time.time()
        if now - self._slog_t.get(key, 0) < 20:
            return
        self._slog_t[key] = now
        self.log(msg)

    # ------------------------------------------------------------------
    #  开宝箱: 走到相邻格 -> 朝箱子按方向键(被挡=只转身) -> 按空格搜索
    # ------------------------------------------------------------------
    def tick(self):
        super().tick()
        self._after_arrive()

    def _after_arrive(self):
        g = self._chest_goal
        if not g or g[0] != self.scene:
            return
        ck, stand, kd = g[1], g[2], g[3]
        cur = self.mem.state()["K"]
        if cheb(cur, stand) != 0:
            return
        self._chest_goal = None
        self.open_chest(ck, kd, stand)

    def open_chest(self, ck, kd, stand):
        key = (self.scene, ck[0], ck[1])
        self.stat["chest_try"] += 1
        self.g.flush_keys()
        time.sleep(0.1)
        # ① 转到面向箱子: 箱子是 blocker, 按方向键只会转身不会走
        self.g.key(kd, 0.07)
        time.sleep(self.CHEST_SETTLE)
        self.g.flush_keys()
        cur = self.mem.state()["K"]
        if cur != tuple(stand):
            # 意外走掉了(箱子不挡路?) -> 走回站位再试一次, 失败就放弃
            self.log("[宝箱] 转身时意外移动 K%s -> %s, 回退" % (stand, cur))
            back = {"up": "down", "down": "up",
                    "left": "right", "right": "left"}[kd]
            self.g.key(back, 0.07)
            time.sleep(self.CHEST_SETTLE)
            self.g.flush_keys()
            cur = self.mem.state()["K"]
            if cur != tuple(stand):
                self.log("[宝箱] 回不到位 -> 放弃该箱 (记冷却)")
                self.chest_cd[key] = time.time() + self.CHEST_CD
                self.chest_dead.add(key)
                return
        # ② 按空格搜索（仙剑用空格调查面前的东西）
        self.g.key("space")
        time.sleep(self.CHEST_DLG_WAIT)
        a = self.g.shot()
        got = PB.menu_warm(a) > 0.18
        if not got:
            # 可能没有对话框(空箱/已开) -> 再试一次
            self.g.key("space")
            time.sleep(self.CHEST_DLG_WAIT)
            a = self.g.shot()
            got = PB.menu_warm(a) > 0.18
        if got:
            self.stat["chest_ok"] += 1
            self.log("[宝箱] K%s 打开成功! (累计 %d 个)"
                     % (ck, self.stat["chest_ok"]))
            # 关掉"获得XX"对话框 (最多 5 次空格)
            for _ in range(5):
                time.sleep(0.35)
                if PB.menu_warm(self.g.shot()) < 0.12:
                    break
                self.g.key("space")
        else:
            n = self.chest_try.get(key, 0) + 1
            self.chest_try[key] = n
            self.chest_cd[key] = time.time() + self.CHEST_CD
            if n >= self.CHEST_TRY_N:
                self.chest_dead.add(key)
            self.log("[宝箱] K%s 按空格没反应 (第%d次%s)"
                     % (ck, n, " -> 放弃" if n >= self.CHEST_TRY_N else ""))
        self.g.flush_keys()

    # ------------------------------------------------------------------
    #  战斗挂钩: 记战果 + 让怪点进入冷却
    # ------------------------------------------------------------------
    def fight(self, why=""):
        st = self.mem.state()
        sc, K = st["scene"], st["K"]
        ok = MemBot4.fight(self, why)
        if not ok:
            return ok
        self._last_fight_end = time.time()
        near = [m for m in self.scene_monsters(sc)
                if cheb((m["K"][0], m["K"][1]), K) <= self.MON_HIT_NEAR]
        for m in near:
            key = (sc, m["K"][0], m["K"][1])
            self.mon_cd[key] = time.time() + self.MON_CD
            self.mon_visit[key] = 0
        self.stat["mon_hit"] += 1
        if near:
            self._slog((sc, near[0]["K"][0], near[0]["K"][1]),
                       "[怪点] K%s 战斗命中 -> %d 个怪点冷却 %.0fs"
                       % (K, len(near), self.MON_CD))
        else:
            self._slog((sc, "far"), "[怪点] K%s 战斗命中但附近 %d 格内无记录怪点 "
                       "(怪追过来打的?)" % (K, self.MON_HIT_NEAR))
        return ok

    # ------------------------------------------------------------------
    #  效率日志: 附带要素统计
    # ------------------------------------------------------------------
    def _rate(self, dt, n_retry, stalled):
        super()._rate(dt, n_retry, stalled)
        now = time.time()
        if now - self._elem_t > 120:
            self._elem_t = now
            s = self.stat
            self.log("[要素] 怪点命中 %d / 走到没打 %d / 开箱 %d(试%d) / "
                     "回退巡逻 %d" % (s["mon_hit"], s["mon_visit"],
                                    s["chest_ok"], s["chest_try"],
                                    s["fallback"]))


if __name__ == "__main__":
    _lock = PB._acquire_lock()
    if _lock is None:
        try:
            import psutil as _ps
            _me = os.getpid()
            _pp = _ps.Process(_me).parent().pid
            for _q in _ps.process_iter(["pid", "name"]):
                try:
                    if not _q.info["name"] or "python" not in _q.info["name"].lower():
                        continue
                    if _q.info["pid"] in (_me, _pp):
                        continue
                    _cl = _q.cmdline()
                    if len(_cl) >= 3 and _cl[1] == "-u" and \
                            _cl[2].replace("\\", "/").endswith(
                                ("pal_bot_mem.py", "pal_bot_mem2.py",
                                 "pal_bot_mem3.py", "pal_bot_mem4.py",
                                 "pal_bot_mem5.py")):
                        print("[接管] 终止旧实例 pid=%d" % _q.info["pid"], flush=True)
                        _q.kill()
                except Exception:
                    continue
            time.sleep(1.5)
        except Exception as _e:
            print("[接管] 扫描失败: %s" % _e, flush=True)
        _lock = PB._acquire_lock()
        if _lock is None:
            print("[单实例] 旧实例仍占用游戏, 本进程退出", flush=True)
            sys.exit(0)
    maxb = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1] != "0" else None
    secs = int(sys.argv[2]) if len(sys.argv) > 2 else None
    if len(sys.argv) > 3:
        MemBot4.PATROL_R0 = int(sys.argv[3])
    if len(sys.argv) > 4:
        MemBot4.PATROL_RMAX = int(sys.argv[4])
    stall = 1200
    if len(sys.argv) > 7 and sys.argv[7].strip():
        try:
            stall = int(sys.argv[7])
        except ValueError:
            pass
    if stall <= 0:
        stall = 10 ** 9
    bot = MemBot5(max_battles=maxb, max_seconds=secs, stall_limit=stall)
    if stall >= 10 ** 9:
        bot.log("[配置] 无胜场保险已关闭 (stall=0)")
    try:
        import subprocess as _sp
        _wd = os.path.join(BASE, "bot_watchdog.py")
        _DETACHED = 0x00000008 | 0x00000200
        _sp.Popen([sys.executable, _wd, str(os.getpid()), str(bot.g.hwnd)],
                  creationflags=_DETACHED, cwd=BASE,
                  stdin=_sp.DEVNULL, stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
        bot.log("[看门狗] 已启动 (守护 %d)" % os.getpid())
    except Exception as _e:
        bot.log("[看门狗] 启动失败: %s" % _e)
    if len(sys.argv) > 5 and sys.argv[5]:
        _BIAS = {"上": (1, 0), "下": (-1, 0), "左": (0, -1), "右": (0, 1),
                 "右上": (1, 1), "右下": (-1, 1), "左上": (1, -1),
                 "左下": (-1, -1),
                 "u": (1, 0), "d": (-1, 0), "l": (0, -1), "r": (0, 1),
                 "ru": (1, 1), "rd": (-1, 1), "lu": (1, -1), "ld": (-1, -1)}
        _name = sys.argv[5].strip()
        if _name in _BIAS:
            bot._bias = _BIAS[_name]
            bot._bias_name = _name
            bot.log("[巡逻] 方向偏置开启: %s" % _name)
    if len(sys.argv) > 6 and sys.argv[6].strip() in ("穿门", "开门", "open"):
        bot.open_doors = True
        bot.log("[配置] 开门模式: 踩到传送阵就直接前往下一层")
    # 第 8 参数: 巡逻/off = 关闭要素巡回, 回到 v15 行为
    if len(sys.argv) > 8 and sys.argv[8].strip() in ("巡逻", "off", "关"):
        bot.elem_mode = False
        bot.log("[要素] 已关闭要素巡回 -> 使用 v15 渐进巡逻")
    import atexit
    atexit.register(lambda: bot.g.flush_keys())
    _stop = os.path.join(BASE, "mem_stop.flag")
    if os.path.exists(_stop):
        os.remove(_stop)
    try:
        bot.run()
    except KeyboardInterrupt:
        print("[手动停止] 共胜利 %d 场" % bot.battles_won)
    except Exception:
        import traceback
        traceback.print_exc()
    finally:
        bot.g.flush_keys()
        for _ in range(3):
            time.sleep(0.15)
            bot.g.flush_keys()
        print("[退出] 已清空按键, 共胜利 %d 场, 误抓 %d 次, 要素统计 %s"
              % (bot.battles_won, bot._false_battle, bot.stat), flush=True)
