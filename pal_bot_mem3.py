# -*- coding: utf-8 -*-
"""v14 极速巡航版 (pal_bot_mem3)

用户的洞察: 人物恒在屏幕中心不动、动的是地图 —— 所以"走到哪"完全由内存坐标
决定, 根本不该每步截图。v13 每步干了两件纯浪费的事:
    ① ui_menu() + battle_screen() 各截一张全窗图 (90ms×2) + 特征计算 25ms
    ② 位移后固定再等 0.2s "稳定窗"
  合计 ~0.41s/格 纯开销, 而真正走路只要 0.2s。

v14 快路径 (99% 的步):
    只读内存 (mem.state() 0.019ms) -> 点按 -> 3ms 粒度轮询 K
    ★ 接受信号: K 在按下瞬间就变(实测 0~15ms), 所以"按下即知道成不成"
    ★ 节奏锁: 引擎每 ~0.2s 才收下一次输入, 按早了直接丢弃(不排队) ->
      由 _nxt_press 控制节奏, 没到点不按; 到点还没动就每 55ms 补按一次
      (转身也吃掉一格输入 -> 补按正好覆盖, 不用额外逻辑)

v14 慢路径 (极少数: 预算内完全没动):
    只截 1 张图做分类 -> 菜单(esc) / 战斗(进战斗循环) / 黑屏(等) / 真墙(记绕行)
    => 每次异常只花 1 张图, 而不是每步 2 张。

实测依据 bench_v14*.py: 单按=精确 1 格; 同向间隔 0.25s 接受率 100%;
不同向有一半被"转身"吃掉 (补按机制自动覆盖)。
"""
import os
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import pal_bot as PB                                   # noqa: E402
import pal_bot_mem as PM                               # noqa: E402
import pal_bot_mem2 as V13                             # noqa: E402
from pal_bot_mem2 import MemBot2                       # noqa: E402


class MemBot3(MemBot2):
    # ---- 快路径参数 (可用命令行覆盖, 便于现场标定) ----
    # 实测 (bench_v14i/j, 2026-09-24):
    #   1 点按 = 精确 1 格, K 在按下后 <=16ms 更新;
    #   ★ g.tap() 的"零间隔 keyup+keydown"会被引擎大量丢弃 (ping-pong 2/12!),
    #     v12 时代没暴露是因为只走直线(同向 tap 能收), 一转身就丢 -> 假"被挡"!
    #   ★ g.key() 风格 (keyup,30ms,keydown,70ms,keyup) 间隔 0.20s: 14/14 全收,
    #     4.75 格/秒, 且转身自动被覆盖。
    LOCK_F = 0.200        # 两次按下之间的最小间隔
    BUDGET_F = 0.460      # 基础单格预算 (两次按键机会)
    TURN_EXT = 0.230      # 检测到"引擎接受了转身"后追加的预算
    # ★ 2026-09-30: 单步内最多追加几次。朝向抖动/反复被吃时不至于把 t_end
    #   无限推后 (最坏 = 0.46 + 2×0.43 ≈ 1.32s, 仍然是有限值)。
    TURN_EXT_MAX = 2

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._last_press = 0.0     # 上次按下开始的时刻 (跨步节奏锁)
        self._step_t = []          # 最近若干格的耗时(滑动窗口)
        self._stat_t = time.time()
        self._n_shot = 0           # 累计截图次数(诊断: 越低越好)
        self._n_stall = 0          # 累计异常停机次数
        self._n_retry = 0          # 累计补按次数
        self._n_turn = 0           # 累计转身格数
        self._n_moved = 0
        self._dump_n = 0
        self._t_window = time.time()   # 真实吞吐统计窗
        self._n_window = 0

    # ------------------------------------------------------------------
    #  极速步进: 全程只读内存
    # ------------------------------------------------------------------
    def step(self, key):
        s0 = self.mem.state()
        if s0 is None:
            return ("blocked", (0, 0), (0, 0), self.scene)
        K0, sc0 = s0["K"], s0["scene"]
        dir_seen = self._party_dir()
        # ★ 跨步节奏锁: g.key() 自身耗时 ~0.13s, 若下一步立刻按, 实际按下间隔
        #   只有 0.14s < 0.20s -> 被引擎丢弃 (实测场上传真: 补按 159 次/26s)。
        #   必须按"按下开始的时刻"计时, 而不是按步的边界。
        w = self._last_press + self.LOCK_F - time.perf_counter()
        if w > 0:
            time.sleep(w)
        t0 = time.perf_counter()
        t_end = t0 + self.BUDGET_F
        self.g.key(key)                       # ★ key 风格, 不是 tap!
        self._last_press = t0
        n_retry = 0
        n_ext = 0
        nxt = t0 + self.LOCK_F
        while True:
            s1 = self.mem.state()
            if s1 is not None:
                if s1["scene"] != sc0:
                    self._rate(time.perf_counter() - t0, n_retry, False)
                    return ("door", K0, s1["K"], s1["scene"])
                if s1["K"] != K0:
                    self._rate(time.perf_counter() - t0, n_retry, False)
                    return ("moved", K0, s1["K"], s1["scene"])
            now = time.perf_counter()
            # 引擎接受了"转身"(朝向变了但没走): 多给一个完整动画周期
            # ★ 修好朝向索引 (W[52]) 后这一段才第一次真正生效 —— 换方向时
            #   第一次按键只被吃成转身, 不追加预算就会在 0.46s 处误判成"真墙"。
            d1 = self._party_dir()
            if d1 is not None and d1 != dir_seen:
                dir_seen = d1
                if n_ext < self.TURN_EXT_MAX:
                    n_ext += 1
                    self._n_turn += 1
                    ext = now + self.LOCK_F + self.TURN_EXT
                    if ext > t_end:
                        t_end = ext
            if now >= nxt:
                self.g.key(key)
                self._last_press = time.perf_counter()
                n_retry += 1
                nxt = self._last_press + self.LOCK_F
            if now >= t_end:
                self._rate(now - t0, n_retry, True)
                return self._stall(key, K0, sc0, n_retry)
            time.sleep(0.003)

    def _party_dir(self):
        """wPartyDirection @0x00C3C958 = W[52] (v12 标定 "51" 是错的, 见 IPD 注释)。

        取值 0=下 1=左 2=上 3=右 (SDLPAL 约定)。
        2026-09-30 用真实移动方向 24 样本校验: down->0 up->2 left->1 right->3 全中;
        旧索引 51 的值与朝向无相关性 (随机), 所以 "转身" 计数一直是 0。
        """
        W = self.mem.raw()
        return None if W is None else W[PM.IPD]

    def _rate(self, dt, n_retry, stalled):
        self._n_retry += n_retry
        self._n_moved += 1
        self._n_window += 1
        if not stalled:
            self._step_t.append(dt)
            if len(self._step_t) > 60:
                del self._step_t[:20]
        now = time.time()
        if now - self._stat_t > 25 and len(self._step_t) >= 10:
            self._stat_t = now
            avg = sum(self._step_t) / len(self._step_t)
            el = now - self._t_window
            self.log("[速度] 移动%.3fs/格, 真实吞吐 %.2f 格/秒 (近%.0fs %d步) "
                     "补按%d 转身%d 异常%d 截图%d"
                     % (avg, self._n_window / el if el else 0, el,
                        self._n_window, self._n_retry, self._n_turn,
                        self._n_stall, self._n_shot))
            self._t_window = now
            self._n_window = 0

    # ------------------------------------------------------------------
    #  唯一允许截图的地方: 该动却没动
    # ------------------------------------------------------------------
    MENU_MIS_CD = 120.0          # 菜单误报后跳过菜单判定的冷却 (秒)

    def _esc_if_menu(self, a):
        """停机画面像菜单 -> esc 关掉并验证。返回 True = 确实处理了菜单。

        ★ v2 (2026-09-30 场景185 熔岩图事故) 三处加固:
          1. red 判据带上限 CAP (mem2._menu_like): 熔岩地面 red=0.45 远超真子页面
             0.08~0.10, 旧版把红色场景全判成菜单, esc 每 1s 开合一次死循环;
          2. "已关闭"的判据从"画面变了"改成"菜单特征消失" —— 旧判据在地图上按
             esc 会把"系统菜单被打开"当成"菜单已关闭" (画面确实变了!),
             这正是 开菜单->关菜单 死循环的燃料;
          3. 检测到"esc 反而打开了系统菜单" -> 关回去 + 冷却 120s 跳过菜单判定,
             让 _stall 继续走战斗/被挡分类, 不再空转。
        """
        if time.time() < getattr(self, "_menu_mis_t", 0.0):
            return False
        red, tan = self._menu_feat(a)
        if not self._menu_like(red, tan):
            return False
        self.press_esc()
        a2 = self.g.shot()
        self._n_shot += 1
        red2, tan2 = self._menu_feat(a2)
        # ★ 先判"esc 反而开出了菜单"再判"已关闭": 红场景里 red 超 CAP 时
        #   menu_like 本来就为假, 直接判"已关闭"会把误开的菜单留在屏幕上。
        opened = ((tan2 > self.MENU_TAN_TH and tan <= self.MENU_TAN_TH)
                  or (red2 >= self.MENU_RED_CAP and red < self.MENU_RED_CAP))
        if opened:
            self.press_esc()                       # 把误开的系统菜单关回去
            self._menu_mis_t = time.time() + self.MENU_MIS_CD
            self.log("[快] 菜单误报 (red=%.3f tan=%.3f): esc 反而开了系统菜单 -> "
                     "已关回, %.0fs 内跳过菜单判定" % (red, tan, self.MENU_MIS_CD))
            return False
        if not self._menu_like(red2, tan2):
            self.log("[快] 停机画面=菜单 (red=%.3f tan=%.3f) -> esc 已关闭" % (red, tan))
            return True
        self.press_esc()
        a3 = self.g.shot()
        self._n_shot += 1
        red3, tan3 = self._menu_feat(a3)
        if not self._menu_like(red3, tan3):
            self.log("[快] 停机画面=菜单 (red=%.3f tan=%.3f) -> esc x2 已关闭" % (red, tan))
            return True
        self._menu_mis_t = time.time() + self.MENU_MIS_CD
        self.log("[快] 菜单复核: esc x2 特征未消失 (red=%.3f tan=%.3f) -> 误报, 冷却 %.0fs"
                 % (red, tan, self.MENU_MIS_CD))
        # 战斗刚触发时命令菜单可能还没画出来 (9/27 实测的正是这种状态):
        # 给它 1 秒出现的机会, 出现了就直接进战斗循环
        time.sleep(1.0)
        a4 = self.g.shot()
        self._n_shot += 1
        if PB.is_battle(a4, self.band0()):
            self.log("[快] 菜单误报复核: 1 秒后战斗菜单出现 -> 进战斗循环")
            self.fight(why="菜单误报后战斗复核")
            return True
        self.log("[快] 复核结束: 既非菜单也非战斗 -> 当墙/被挡处理")
        return False

    def _stall(self, key, K0, sc0, n_retry):
        """该动没动: 截图分类。★ 教训: 战斗的抖动转场/攻击动画期间
        is_battle 颜色特征会失效(实测 box 漂移), 被误判成"墙"还会乱按方向键。
        战斗画面永远在动, 真墙绝对静止 -> 用画面差分二次判定。"""
        self._n_stall += 1
        a = self.g.shot()
        self._n_shot += 1
        if self._esc_if_menu(a):
            return ("menu", K0, K0, sc0)
        if PB.is_battle(a, self.band0()):
            self.log("[快] 停机=战斗 -> 进战斗循环")
            self.fight(why="快路径停机判定")
            return ("battle", K0, K0, sc0)
        blk = float((a.sum(axis=2) < 45).mean())
        if blk > 0.95:
            self.log("[快] 停机=黑屏 (%.2f) -> 等恢复" % blk)
            time.sleep(0.8)
            return ("black", K0, K0, sc0)
        # 二次静止性判定 (最多 2 轮, 每轮 0.35s)
        for i in range(2):
            time.sleep(0.35)
            a2 = self.g.shot()
            self._n_shot += 1
            if self._esc_if_menu(a2):
                return ("menu", K0, K0, sc0)
            if PB.is_battle(a2, self.band0()):
                self.log("[快] 停机=战斗(二次判定第%d轮) -> 进战斗循环" % (i + 1))
                self.fight(why="快路径停机二次判定")
                return ("battle", K0, K0, sc0)
            if float((a2.sum(axis=2) < 45).mean()) > 0.95:
                self.log("[快] 停机=黑屏(二次) -> 等恢复")
                time.sleep(0.8)
                continue
            if self._diffpix(a, a2) < self.px_thr(self.F_WALL):      # 画面完全静止 -> 真墙
                if self._dump_n < 8:
                    self._dump_n += 1
                    try:
                        from PIL import Image
                        fn = os.path.join(BASE, "v14wall_%d_K%d_%d_%s.png"
                                          % (self._dump_n, K0[0], K0[1], key))
                        Image.fromarray(a2.astype("uint8")).save(fn)
                        self.log("[快] 停机=真墙 (画面静止) 存图 %s"
                                 % os.path.basename(fn))
                    except Exception:
                        pass
                return ("blocked", K0, K0, sc0)
            a = a2                                # 还在动 -> 多半是战斗, 再看一轮
        self.log("[快] 停机=持续动画(疑似战斗转场) -> 进战斗循环兜底")
        self.fight(why="停机持续动画")
        return ("battle", K0, K0, sc0)

    # ------------------------------------------------------------------
    #  热路径禁止截图: 战斗/菜单一律由 step() 的停机分类捕获
    # ------------------------------------------------------------------
    def maybe_fight(self):
        return False

    def in_battle(self):
        return False


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
                                 "pal_bot_mem3.py")):
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
        MemBot3.LOCK_F = float(sys.argv[3])
    if len(sys.argv) > 4:
        MemBot3.BUDGET_F = float(sys.argv[4])
    bot = MemBot3(max_battles=maxb, max_seconds=secs)
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
        print("[退出] 已清空按键, 共胜利 %d 场 (战斗 %d 次)"
              % (bot.battles_won, bot.MAXB_CNT if hasattr(bot, "MAXB_CNT") else 0),
              flush=True)
