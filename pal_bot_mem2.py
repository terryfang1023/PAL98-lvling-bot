# -*- coding: utf-8 -*-
"""pal_bot_mem2.py — v13: 战斗状态机 + 可达域寻路 (在 v12 之上最小覆写)

【本版两大修复】

1) 战斗期间绝不发方向键, 且有明确的 开始/结束 标志:
     ==================================================
     [战斗开始] #3  场景77 K(8,83)  t=09:41:02
     ==================================================
     [战斗结束] #3  时长=18.4s  按键 22 次  累计胜场=3  t=09:41:20
   战斗判定用「双条件」:  PB.is_battle(画面) 且 画面在持续动画。
   —— 实测教训: 系统菜单(红色面板+底部头像栏)会让 is_battle 误判为战斗,
      但菜单画面是完全静止的(1.2 秒 0 像素变化), 而战斗里敌人有蠕动空闲帧,
      画面持续变化。用这一条就能把两者干净地区分开。

2) 可达域寻路 (修掉历次"无路可规划"死循环):
     v12 把门坐标 ±2 圈设为门禁, 实测会把 14409 格的可走域切成 2576 格,
     pick_target 又只在几何半径里挑"最深"格(不看可达性) → 挑到不可达格 →
     bfs 失败 → 还把好格误记成"被挡" → 地图被污染 → 80 次后 RESET → 循环。
   修复: 门禁只禁门格本身(pad=0, 实测零损失); pick_target 只在
     "从当前位置 BFS 可达"的格子里挑目标; bfs_path 直接沿可达域父指针回溯。
   实测: 20 秒走 50 步(2.5 格/秒), 队伍真正在探索。

【菜单自救配方 (实测有效)】
   循环 [按一个方向键 -> 看内存 K 有没有变; 没变 -> 按 esc]。
   v12 的 esc,esc,space 不可靠: space 在菜单里会误触菜单项, 越陷越深。
"""
import ctypes, json, os, struct, sys, time
from collections import deque
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pal_bot as PB
import pal_bot_mem as V12

BASE = V12.BASE
BAR = "=" * 62
BATTLE_MAX = 180.0          # 单场战斗最长等待(秒); 一场坏仗不该吃掉5分钟
ANIM_MIN_PIX = 300          # (旧)判定"画面在动"的像素数; 现用 MemBot.F_ANIM_MIN 按面积折算


class AgedSet(set):
    """带时间戳的集合: 每条记录 TTL 后自动失效。

    v12 的 `blocked`("实测被挡") 是只增不减的, 而它的唯一信号是
    "按了方向键 0.55 秒内坐标没变" —— 这个信号无法区分:
      ① 真的是墙/NPC 挡住   ② 系统菜单打开(方向键全废)
      ③ 对话进行中          ④ 按键被消息队列吞掉
    一旦出现 ②③④, 好格子会被误标成墙, 而且**永久不可逆**, 越积越多,
    最终把队伍所在格围成孤岛 -> "半径内无处可去" -> 死锁(实测 125 次)。
    加上 TTL 后, 假信号会自己过期复原; 真的墙只是多试一次(代价 0.55 秒)。
    """

    def __init__(self, *a):
        super().__init__(*a)
        self.ts = {}

    def add(self, x):
        self.ts[x] = time.time()
        super().add(x)

    def discard(self, x):
        self.ts.pop(x, None)
        super().discard(x)

    def clear(self):
        self.ts.clear()
        super().clear()

    def prune(self, ttl):
        now = time.time()
        dead = [c for c, t in list(self.ts.items()) if now - t > ttl]
        for c in dead:
            self.ts.pop(c, None)
            set.discard(self, c)
        return len(dead)


class MemBot2(V12.MemBot):
    BATTLE_END_CONFIRM = 8       # 连续 N 次确认"非战斗"才算结束
                                 # ★ 实测: 施法/攻击动画会遮住底部状态栏, 令 is_battle
                                 #   假性失效约 1~3 秒。原来 3 次(2.4s) 太短 -> 真战斗
                                 #   被误判成 3.4 秒结束。改成 8 次(~6.4s) 留足容错。
    BATTLE_GAP = 1.0             # 两次确认之间的间隔(秒)
    ANIM_WINDOW = 0.9            # 动画对比的时间窗(秒)
    STILL_LIMIT = 8.0            # 战斗画面静止超过此秒数 -> 卡在子菜单, 升级自救
    LONG_BATTLE = 40.0           # 真战斗~9秒; 超过此秒数未结束 -> 周期性esc兜底
    BLOCK_TTL = 70.0             # 「实测被挡」记录存活时间(秒), 超时自动失效
    ISLAND_MIN = 60              # 半径内可达格低于此值 -> 判定被围成孤岛
    REACH_MIN = 2000             # 门禁后可达域低于此值才放宽门禁
                                 # ★ 实测: 场景77 pad=0 门禁后可达 2586 格(含刷怪区
                                 #   K(-40,104)~(-2,111)), 够用且能防穿门乒乓; 若阈值
                                 #   设 5000 会放宽成"无门禁"(14409), 路径就会穿过门格
    HOME_TTL = 100.0             # 意外穿门后, 尝试走回入口穿回去的最长时间(秒)
    MIGRATE_AFTER = 400          # 提高: 120 步太激进, 刚打几场就想跑(实测 131 步就迁移)
    # 实测无怪的地图 (走很久也不遇敌) -> 到了就赶紧走
    #   场景70/地图62: 实测 167 步 0 场战斗, 而同期的场景77 是 243 秒 5 场
    NO_MONSTER = {70}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        # 换用重新解码且已验证的完整地图数据 (223 张)
        self.grids = np.load(os.path.join(BASE, "map_walk2.npy"))
        self._kwalk_cache = {}
        self.n_battle = 0
        self._bf_ref = None       # (时间, 帧) 用于动画对比
        self._bf_last = False
        self._still_since = None  # 战斗画面开始"完全静止"的时刻
        self._stuck_n = 0
        # ★ 关键: 把只增不减的 blocked 换成带时效的版本
        self.blocked = AgedSet(self.blocked)
        self._island_n = 0
        self.home = None          # {"scene":原场景, "anchor":落点K, "t_end":截止}
        self.scene_battles = {}   # 场景 -> 战斗场次 (用于判断哪里是刷怪区)
        self.hotK = None          # 本场景最近发生战斗的位置
        self.MIGRATE_AFTER = 400  # 覆盖 v12 的 120: 刚打几场就想跑太激进
        self.log("[v13] 战斗判定 = is_battle 且画面在动; 方向键硬门禁已开启")
        self.log("[v13] 「实测被挡」带 %.0fs 时效, 孤岛自动自愈已开启" % self.BLOCK_TTL)
        self.log("[v13] 意外穿门会走回入口尝试穿回 (上限 %.0fs); "
                 "迁移阈值: 打过仗 400 步 / 没打过 150 步" % self.HOME_TTL)

    # ---------- 日志: 抑制短时间内的完全重复行 ----------
    def log(self, msg):
        now = time.time()
        d = getattr(self, "_duplog", None)
        if d and d[0] == msg and now - d[1] < 5.0:
            return
        self._duplog = (msg, now)
        super().log(msg)

    # ---------- 场景切换: 保证 blocked 始终是带时效的 AgedSet ----------
    def enter_scene(self, sc, why=""):
        st = self.scene_know.get(sc)
        if st is not None and not isinstance(st.get("blocked"), AgedSet):
            st["blocked"] = AgedSet(st.get("blocked") or ())
        super().enter_scene(sc, why)
        if not isinstance(self.blocked, AgedSet):
            self.blocked = AgedSet(self.blocked)
        if self.home and sc == self.home["scene"]:
            self.log("[回家] 已回到原场景 %d, 回家模式结束" % sc)
            # 落点常紧邻门格(实测: 落点 K(-87,147) 旁就是门 K(-89,147)),
            # 立刻把已知门格临时禁入(70s 后自动失效), 迫使队伍先走开, 防乒乓
            for d in self.known_doors.get(sc, ()):
                self.blocked.add(d)
            self._pc_key = None
            self.home = None

    # ---------- 回家模式: 意外穿门离开刷怪区时, 走回入口穿回去 ----------
    def home_active(self):
        h = self.home
        if not h:
            return False
        if time.time() > h["t_end"]:
            self.log("[回家] 超过 %.0f 秒没能穿回场景 %d, 放弃并就地探索"
                     % (self.HOME_TTL, h["scene"]))
            self.home = None
            return False
        return True

    # ---------- 困区逃逸 (2026-09-29 熔岩走廊实测) ----------
    TRAP_WIN = 360.0          # 观察窗口 (秒)
    TRAP_SPREAD = 9.0         # 窗口内采样点离质心的最大切比雪夫距离 <= 此值 = 被困
    TRAP_MIN_SAMPLES = 60     # 窗口内最少采样步数 (太少 = 刚进场/大多在战斗)
    TRAP_BLOCKED = 25         # 窗口内「实测被挡」累计 >= 此值才算受困
    TRAP_CHECK_EVERY = 30.0   # 检查间隔
    TRAP_CD = 600.0           # 两次逃逸的最小间隔 (防刷屏)
    TRAP_ESC_R = 14           # 逃逸时清除绕行/长期禁入/怪拉黑的半径
    TRAP_MIN_REACH = 60       # 清完后可达域低于此 -> 判真地形困死, 直接换场景
    TRAP_GOAL_TTL = 240.0     # 突围目标有效期

    def _trap_check(self):
        """困区判定: 位置长时间困在小圈 + 被挡高频 -> 逃逸。30s 一次。"""
        now = time.time()
        if now < self._trap_last + self.TRAP_CHECK_EVERY:
            return
        self._trap_last = now
        if now < self._trap_cd_until or self.migrate or self.home_active():
            return
        win = [(t, k) for t, k in self._trace if t >= now - self.TRAP_WIN]
        if len(win) < self.TRAP_MIN_SAMPLES:
            return
        ca = sum(k[0] for _, k in win) / len(win)
        cb = sum(k[1] for _, k in win) / len(win)
        spread = max(max(abs(k[0] - ca), abs(k[1] - cb)) for _, k in win)
        if spread > self.TRAP_SPREAD:
            return
        bsum = sum(cnt for cnt, last in self.blocked_hits.values()
                   if now - last <= self.TRAP_WIN)
        if bsum < self.TRAP_BLOCKED:
            return
        self.log("[困区] %.0f 秒内活动半径仅 %.0f 格、被挡 %d 次 -> 启动逃逸"
                 % (self.TRAP_WIN, spread, bsum))
        self._trap_escape(self.mem.state()["K"], now)

    def _trap_escape(self, cur, now):
        """清空身边全部绕行知识; 有路就突围到最远可达格, 没路就换场景。"""
        self._trap_cd_until = now + self.TRAP_CD
        R = self.TRAP_ESC_R
        cleared = 0
        for c in list(self.blocked):
            if max(abs(c[0] - cur[0]), abs(c[1] - cur[1])) <= R:
                self.blocked.discard(c)
                cleared += 1
        hits = getattr(self, "blocked_hits", None)
        if hits:
            for c in [c for c in hits
                      if max(abs(c[0] - cur[0]), abs(c[1] - cur[1])) <= R]:
                hits.pop(c, None)
        anim = getattr(self, "_animK", None)
        if anim:
            a = anim.get(self.scene)
            if a:
                for c in [c for c in a
                          if max(abs(c[0] - cur[0]), abs(c[1] - cur[1])) <= R]:
                    a.pop(c, None)
        self._pc_key = None
        self._mon_goal = None
        self._chest_goal = None
        self._trap_goal = None
        ban = set(self.blocked) - {cur}
        r = self.reach(cur, ban)
        if len(r) >= self.TRAP_MIN_REACH:
            goal = max(r, key=lambda c: (abs(c[0] - cur[0])
                                         + abs(c[1] - cur[1])))
            self._trap_goal = (self.scene, goal, now + self.TRAP_GOAL_TTL)
            self.log("[困区] 清身边 %d 格内 %d 条绕行记录 -> 突围目标 K%s"
                     % (R, cleared, goal))
        else:
            self.log("[困区] 清完可达域仍只有 %d 格 -> 真地形困死, 启动换场景"
                     % len(r))
            self.migrate = True

    def handle_door(self, K_door, new_scene, key):
        """覆写: 意外穿门且退不回时, 不立刻接受新场景乱跑, 而是记住入口去穿回来。"""
        was_mig = self.migrate
        old = self.scene
        super().handle_door(K_door, new_scene, key)
        if was_mig:
            return                       # 迁移穿门是主动行为, 不算跑丢
        if self.scene == new_scene and self.scene != old:
            # ★ 只有当"跑到了更没怪的地方"才值得回家, 否则会反向乒乓
            if (self.scene_battles.get(old, 0) > self.scene_battles.get(new_scene, 0)
                    or new_scene in self.NO_MONSTER):
                self.home = {"scene": old, "anchor": self.mem.state()["K"],
                             "t_end": time.time() + self.HOME_TTL}
                self.log("[回家] 意外从场景 %d 进入更没怪的 %d -> %.0f 秒内"
                         "在入口 K%s 附近来回踏, 尝试穿回"
                         % (old, new_scene, self.HOME_TTL, self.home["anchor"]))
            else:
                self.log("[回家] 场景 %d -> %d, 新场景不更差, 就地探索"
                         % (old, new_scene))

    # ---------- 战斗判定 ----------
    @staticmethod
    def _diffpix(a, b):
        d = np.abs(a.astype(np.int32) - b.astype(np.int32)).sum(axis=2)
        return int((d > 12).sum())

    def battle_screen(self, frame=None):
        """True = 正在战斗 (或战斗菜单)。

        ★ 教训: 真战斗在"等待指令"阶段画面是**静止**的, 所以不能要求"画面在动" ——
          否则一场仗会在 3.3 秒后被误判结束(实测日志)。正确规则:
            is_battle 为真            -> 就算战斗 (方向键硬门禁生效, 安全);
            但长时间完全静止(>25 秒)    -> 更像卡在系统菜单 -> 自救。
        """
        a = frame if frame is not None else self.g.shot()
        band0 = self.band0()
        if not PB.is_battle(a, band0):
            # 诊断: 战斗刚转非战斗时, 打出底部特征值, 用于查"3.3 秒假结束"
            if self._bf_last:
                band = a[band0:, :, :] if band0 else a[int(a.shape[0] * 0.70):]
                R, G, B = band[..., 0], band[..., 1], band[..., 2]
                box = float(((R >= 38) & (R <= 95) & (G >= 8) & (G <= 60) &
                             (B >= 0) & (B <= 40) & (R > G + 15) &
                             (G >= B)).mean())
                teal = float(((G >= 130) & (G > R + 55) & (B >= 100) &
                              (B <= 190) & (G > B + 15)).mean())
                self.log("[战斗判据] is_battle 真转假: box=%.4f teal=%.5f "
                         "black=%s" % (box, teal, PB.is_black(a)))
            self._bf_ref = None
            self._bf_last = False
            self._still_since = None
            return False
        t = time.time()
        ref = self._bf_ref
        if ref is None:
            self._bf_ref = (t, a)
            self._still_since = t
            self._bf_last = True
            return True
        if t - ref[0] >= self.ANIM_WINDOW:
            moving = self._diffpix(a, ref[1]) >= self.px_thr(self.F_ANIM_MIN)
            self._bf_ref = (t, a)
            self._bf_last = moving
            if moving:
                self._still_since = t
        if self._still_since is not None and \
                time.time() - self._still_since > self.STILL_LIMIT:
            self.log("[战斗] 战斗画面静止 %.0f 秒 -> 疑似卡在法术/道具子菜单, "
                     "esc退出子菜单后选攻击"
                     % (time.time() - self._still_since))
            self._still_since = None
            self._bf_ref = None
            self._bf_last = False
            # ★ 实测配方 (2026-09-24 12:15, exp1_esc.png 铁证):
            #   卡住的是【地图系统菜单的"法术"页】(想放凝神归元回血但 MP 只有
            #   18/281, 页面永远停着), is_battle 把它误判成战斗。
            #   **单按 esc 就关掉** (变化 28 万像素, is_battle 立刻转假)。
            #   ⚠️ 绝不能盲按两次 esc: 在地图上第二次 esc 会【打开】系统菜单,
            #      于是 bot 自己造出新的假战斗画面, 死循环 (v13l 就是这么卡的)。
            #   也绝不按 f: SDLPAL 里 f=kKeyForce(法术), 正是它把法术页打开的。
            #   (旧注释写 "f=重复上次动作" —— 错的; 重复是 r=kKeyRepeat,
            #    见 botcfg.ATTACK_KEY_CHOICES 的官方键表。)
            okt = self.close_menu_like()
            self._still_since = None
            self._bf_ref = None
            self._bf_last = False
            if okt:
                return False                  # 菜单已关掉, 不在战斗中
            self.g.key("a")                   # 真战斗且放不出法术 -> 自动战斗兜底
            time.sleep(0.6)
            self.g.flush_keys()
            return True
        return True

    def in_battle(self):
        return self.battle_screen()

    # ---------- 菜单画面检测器 (2026-09-24 实测标定; 2026-09-30 加红上限) ----------
    # is_battle 在"法术页"上会误报 True, 而菜单根上是 False —— 都拦不住方向键。
    # 这里用两个颜色特征把它们变成可判定信号:
    #   法术/子页: 中央大块"暗红面板"占比 > 0.04 (实测 页面0.08~0.10, 战斗/地图<0.01)
    #   菜单根:   左侧"棕黄卷轴"占比 > 0.15 (实测 根0.25, 其它<=0.07)
    #   ★ 上限 CAP (2026-09-30 场景185 熔岩图事故): 熔岩地图整屏红地面 red=0.45~0.53,
    #     比真子页面高 5 倍 —— 红色占比超过 CAP 的是"场景本身红", 不是菜单面板。
    #     没有 CAP 时 ui_menu 在熔岩图恒真: 每步按 esc (开合死循环),
    #     真战斗也被"进场即菜单 -> esc 判为误抓", 一场都打不了。
    MENU_RED_TH = 0.04
    MENU_TAN_TH = 0.15
    MENU_RED_CAP = 0.30

    @staticmethod
    def _menu_feat(a):
        h, w = a.shape[:2]
        band = a[int(h * 0.30):int(h * 0.72), int(w * 0.08):int(w * 0.92)]
        R, G, B = band[..., 0], band[..., 1], band[..., 2]
        red = float(((R >= 90) & (R <= 190) & (G >= 15) & (G <= 75) &
                     (B >= 15) & (B <= 75) & (R > G + 55) & (R > B + 55)).mean())
        p = a[int(h * 0.26):int(h * 0.60), int(w * 0.02):int(w * 0.19)]
        R, G, B = p[..., 0], p[..., 1], p[..., 2]
        tan = float(((R >= 120) & (R <= 215) & (G >= 95) & (G <= 180) &
                     (B >= 55) & (B <= 140) & (R > B + 40) & (G > B + 15)).mean())
        return red, tan

    @staticmethod
    def _menu_like(red, tan):
        """菜单类画面的判据 (red 带上限, 见 MENU_RED_CAP 注释)。"""
        return (MemBot2.MENU_RED_TH < red < MemBot2.MENU_RED_CAP) \
            or tan > MemBot2.MENU_TAN_TH

    def spell_page(self, frame=None):
        a = frame if frame is not None else self.g.shot()
        red, tan = self._menu_feat(a)
        return self.MENU_RED_TH < red < self.MENU_RED_CAP

    def menu_root(self, frame=None):
        a = frame if frame is not None else self.g.shot()
        return self._menu_feat(a)[1] > self.MENU_TAN_TH

    def ui_menu(self, frame=None):
        """True = 屏幕上有任何菜单类画面 -> 绝对禁止方向键。"""
        a = frame if frame is not None else self.g.shot()
        red, tan = self._menu_feat(a)
        return self._menu_like(red, tan)

    def press_esc(self):
        """关掉当前菜单页 (法术页1次esc直达地图; 菜单根1次esc回地图)。"""
        self.g.key("esc")
        time.sleep(0.7)
        self.g.flush_keys()

    # ---------- 硬门禁: 战斗中绝不发方向键 ----------
    def step(self, key):
        st = self.mem.state()
        K0 = st["K"]
        # ★ 总闸 (用户指示): 方向键只准在确认"没有任何菜单"时发。
        #   菜单根 is_battle=False, 光靠战斗门禁拦不住 -> 必须单独检测。
        if self.ui_menu():
            self.press_esc()
            return ("menu", K0, K0, st["scene"])
        if self.battle_screen():
            return ("battle", K0, K0, st["scene"])
        return super().step(key)

    # ---------- 战斗状态机 ----------
    def fight(self, why="is_battle+动画"):
        self.n_battle += 1
        n = self.n_battle
        st = self.mem.state()
        # 记录"这里能打怪" -> 用于判断刷怪区 / 动态迁移阈值
        self.scene_battles[st["scene"]] = \
            self.scene_battles.get(st["scene"], 0) + 1
        self.hotK = st["K"]
        self.g.flush_keys()          # 开战瞬间丢掉可能按住的方向键
        try:                          # 诊断: 开战瞬间的画面留档
            from PIL import Image
            Image.fromarray(self.g.shot().astype('uint8')) \
                .resize((843, 647)).save(os.path.join(
                    BASE, "battle_start_%02d.png" % (n % 20)))
        except Exception:
            pass
        t0 = time.time()
        self.log(BAR)
        self.log("[战斗开始] #%d  场景%s K%s  判据=%s  t=%s"
                 % (n, st["scene"], st["K"], why, time.strftime("%H:%M:%S")))
        self.log(BAR)
        seq = [PB.attack_key()]       # ★ 战斗里只用这一个键 (默认 R; GUI 可调)
        i = 0
        presses = 0
        lost = 0
        reason = "未知"
        last_esc = 0.0
        while True:
            over = time.time() - t0
            if over > BATTLE_MAX:
                reason = "超时%.0fs" % BATTLE_MAX
                break
            if os.path.exists(os.path.join(BASE, "mem_stop.flag")):
                reason = "停止标志"
                break
            inb = self.battle_screen()
            if not inb:
                # ★★ 关键修复 (2026-09-24 12:40 实测):
                #   战斗其实已经结束, 但本循环过去仍每 0.8 秒按攻击键 —— 而【在地图上
                #   按 F 会打开"角色名单/法术"系统菜单】(实测: f 后画面变化 40 万像素、
                #   is_battle 变 True; esc 才关得掉。f=kKeyForce 法术, 与现在默认的
                #   r=kKeyRepeat 不同, 但"地图上乱按会开菜单"这条风险对所有键都成立)。
                #   菜单里的角色有待机动画, 画面永不"静止", 于是所有静止类自救全失效
                #   -> 一场仗白卡 180 秒。
                #   所以: 一旦判定非战斗, 绝不再按攻击键, 只按 space 推进战后结算。
                lost += 1
                if lost >= self.BATTLE_END_CONFIRM:
                    reason = "连续 %d 次确认非战斗" % lost
                    break
                self.g.key("space")
                presses += 1
                time.sleep(0.6)
                continue
            lost = 0
            fr = self.g.shot()
            if self.ui_menu(fr):
                # 法术页/菜单页出现 -> 只按 esc
                self.press_esc()
                presses += 1
                continue
            # ★ 长战斗兜底: 真战斗只需 ~9 秒, 超过 40 秒仍未结束
            #   (且画面可能因角色待机动画而"不静止") -> 周期性 esc 关掉误开的菜单
            if over > self.LONG_BATTLE and over - last_esc > 8.0:
                last_esc = over
                self.press_esc()
                presses += 1
                continue
            self.g.attack()
            i += 1
            presses += 1
            time.sleep(0.8)
        dur = time.time() - t0
        self.g.flush_keys()
        for _ in range(2):           # 结算/对话只按 space (不是方向键, 安全)
            if self.battle_screen():
                break
            time.sleep(0.5)
            self.g.key("space")
        self.g.flush_keys()
        if presses >= 2:
            self.battles_won += 1
            self.last_win = time.time()
            self.steps_since_battle = 0
            self._stuck_n = 0
        else:
            self.log("[战斗] 未实际操作, 疑似误判, 不计胜场")
        self.log(BAR)
        self.log("[战斗结束] #%d  判据=%s  时长=%.1fs  按键 %d 次  "
                 "累计胜场=%d  t=%s"
                 % (n, reason, dur, presses, self.battles_won,
                    time.strftime("%H:%M:%S")))
        self.log(BAR)

    def maybe_fight(self):
        if self.battle_screen():
            self.fight()
            return True
        a = self.g.shot()
        if PB.is_black(a):
            self._black_n = getattr(self, "_black_n", 0) + 1
            if self._black_n % 10 == 1:
                self.log("[MEM] 黑屏中 (%d 次), 等待恢复" % self._black_n)
            if self._black_n >= 20:
                self.g.key("space")
                self.log("[MEM] 黑屏过久, 发 space 尝试恢复")
                self._black_n = 0
            time.sleep(0.5)
            return True
        self._black_n = 0
        return False

    # ---------- 菜单自救 (实测配方) ----------
    def close_menu_like(self, tries=2):
        """按 esc 直到脱离 is_battle 画面 (最多 tries 次)。

        ⚠️ 不能盲按 N 次 esc: 在地图上 esc 会【打开】系统菜单, 多按反而自造假战斗。
        """
        for i in range(tries):
            if not PB.is_battle(self.g.shot(), self.band0()):
                if i:
                    self.log("[MENU] %d 次 esc 后脱离菜单画面" % i)
                return True
            self.g.key("esc")
            time.sleep(0.8)
        self.g.flush_keys()
        return not PB.is_battle(self.g.shot(), self.band0())

    def escape_menu(self, tries=8):
        """脱困 = 只用 esc/space, 绝不发方向键 (用户指示)。

        ★ 历史教训: 旧版"方向键试探->没动就按 esc"是自伤 —— esc 打开菜单根后,
          方向键在菜单里移动光标, space/f 把"仙术"子页点开 -> 二、三级菜单就是这么来的。
        现在只做两件事: ①检测到任何菜单画面 -> esc 关掉; ②否则按 space 推进对话。
        移动是否恢复由调用方的正常 walk step 验证。
        """
        for i in range(tries):
            fr = self.g.shot()
            if self.ui_menu(fr):
                self.press_esc()
                continue
            if self.battle_screen(fr):
                self.log("[MEM] 自救中断: 疑似真战斗, 交回战斗处理")
                return False
            # 无菜单无战斗: 可能是对话框挡路 -> space 推进 (地图上 space 无害)
            self.g.key("space")
            time.sleep(0.7)
        self.g.flush_keys()
        return True

    def menu_close(self):
        """v12 的 esc,esc,space 不可靠(space 会误触菜单项) -> 换成实测配方。"""
        self.escape_menu()

    def startup_selfcheck(self):
        fr = self.g.shot()
        if self.ui_menu(fr):
            self.log("[MEM] 启动自检: 检测到菜单画面 -> 先 esc 关闭")
            self.press_esc()
            return
        if self.battle_screen(fr):
            self.log("[MEM] 启动自检跳过: 当前在战斗")
            return
        k0 = self.mem.state()["K"]
        self.g.key("down")
        time.sleep(0.6)
        if self.mem.state()["K"] != k0:
            return
        self.log("[MEM] 启动自检: 无法移动 -> 执行菜单自救")
        self.escape_menu()

    # ---------- 可达域寻路 ----------
    def door_ban(self, pad):
        s = set()
        for src in (self.report_doors.get(self.scene, ()),
                    self.known_doors.get(self.scene, ())):
            for (da, db) in src:
                for ua in range(-pad, pad + 1):
                    for ub in range(-pad, pad + 1):
                        s.add((da + ua, db + ub))
        return s

    def reach(self, src, bad):
        ks = self.kwalk(self.map)
        prev = {src: None}
        q = deque([src])
        head = 0
        while head < len(q):
            p = q[head]
            head += 1
            for d in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                t = (p[0] + d[0], p[1] + d[1])
                if t in ks and t not in bad and t not in prev:
                    prev[t] = p
                    q.append(t)
        return prev

    def near_count(self, reach, cur):
        R = self.RADIUS
        return sum(1 for c in reach
                   if max(abs(c[0] - cur[0]), abs(c[1] - cur[1])) <= R)

    def graded_reach(self, cur):
        """门禁分级。门格常锁在走廊咽喉上, 禁几个就能把可达域砍到 1/5,
        所以必须做连通性校验, 逐级放宽直到可达域够大。返回 (ban, tag, reach)。"""
        blk = set(self.blocked) - {cur}
        best = None
        for tag_i, d_ban in (
                ("pad=0", self.door_ban(0)),
                ("仅实测门", set(self.known_doors.get(self.scene, ()))),
                ("无门禁", set())):
            b = (d_ban | blk) - {cur}
            rr = self.reach(cur, b)
            if best is None or len(rr) > len(best[2]):
                best = (b, tag_i, rr)
            if len(rr) >= self.REACH_MIN:
                break
        return best

    def plan_ctx(self, cur):
        # ① 过期清理: 临时状态(对话/菜单)造成的假"被挡"在这里自动复原
        n = self.blocked.prune(self.BLOCK_TTL)
        if n:
            self.log("[自愈] %d 条「实测被挡」超时失效, 地图已复原" % n)
            self._pc_key = None
        key = (cur, len(self.blocked), len(self.doors))
        if getattr(self, "_pc_key", None) == key:
            return self._pc_ban, self._pc_reach
        if self.home_active():
            # 回家时必须能踩到入口格 -> 不禁门
            ban = set(self.blocked) - {cur}
            r = self.reach(cur, ban)
            tag = "回家(不禁门)"
        else:
            ban, tag, r = self.graded_reach(cur)
        # ② 孤岛检测: 只有当可达域明显变小才细算半径, 平时零开销
        if len(r) < 5000:
            nn = self.near_count(r, cur)
            if nn < self.ISLAND_MIN and self.blocked:
                cleared = 0
                for c in list(self.blocked):
                    if max(abs(c[0] - cur[0]), abs(c[1] - cur[1])) <= 20:
                        if self._is_perm(c):
                            continue          # 长期禁入格 (反复实测被挡) 不参与自愈
                        self.blocked.discard(c)
                        cleared += 1
                if cleared:
                    self._island_n += 1
                    ban, tag2, r = self.graded_reach(cur)
                    tag = "自愈/" + tag2
                    self.log("[自愈] 半径内可达域被压到 %d 格 -> 清除身边 %d 条"
                             "「被挡」记录 (第 %d 次, 复原后 %d 格)"
                             % (nn, cleared, self._island_n, len(r)))
                    if self._island_n >= 3:
                        self._island_n = 0
                        self.log("[自愈] 连续 3 次孤岛 -> 判定输入被吞(菜单/对话),"
                                 " 执行菜单自救")
                        self.escape_menu()
            else:
                self._island_n = 0
        self._pc_key = key
        self._pc_ban, self._pc_reach = ban, r
        if tag != getattr(self, "_pc_tag", None):
            self._pc_tag = tag
            self.log("[PT] 可达域 %d 格 (禁用 %d 格, %s)" % (len(r), len(ban), tag))
        return ban, r

    def pick_target(self, exclude=None):
        cur = self.mem.state()["K"]
        # ★ 动态迁移阈值: 本场景打过仗就彻底别走(留在这里刷), 一场没打才考虑换地方
        self.MIGRATE_AFTER = 99999 if self.scene_battles.get(self.scene, 0) else 150
        # ★ 已知无怪场景: 别耗着, 立刻走 (受迁移冷却保护, 防刷屏)
        if (self.scene in self.NO_MONSTER and not self.home_active()
                and not self.migrate and time.time() > self.migrate_cd):
            self.log("[迁移] 场景 %d 是实测无怪区 -> 立即启动迁移" % self.scene)
            self.migrate = True
            return None
        # ★ 回家模式: 在入口落点 ±3 范围来回横穿
        #   (实测: 门格与穿门落点常差 1~2 格, 只盯着落点踩不到)
        if self.home_active():
            a = self.home["anchor"]
            ks = self.kwalk(self.map) if self.map is not None else set()
            ring = []
            for ua in range(-3, 4):
                for ub in range(-3, 4):
                    t = (a[0] + ua, a[1] + ub)
                    if t in ks and t != exclude:
                        ring.append(t)
            if not ring:
                return None
            ring.sort(key=lambda c: -max(abs(c[0] - cur[0]), abs(c[1] - cur[1])))
            return ring[0]
        # ★ 探索锚点: 有"打怪点"就围着它转(用户要求: 留在战斗区域), 否则用入场点
        anchor = self.hotK if self.hotK else self.startK
        ban, reach = self.plan_ctx(cur)
        cands = [c for c in reach if c not in ban
                 and max(abs(c[0] - anchor[0]), abs(c[1] - anchor[1])) <= self.RADIUS]
        if not cands:
            # 兜底①: 绕行知识把当前格糊死 -> 清掉临时记录重挑一次
            #   (长期禁入先保留: 那是反复实测确认的阻挡)
            n1 = self._clear_blocked(keep_perm=True)
            if n1:
                self.log("[自愈] 候选集为空 -> 清空 %d 条「被挡」记录重挑" % n1)
                ban, reach = self.plan_ctx(cur)
                cands = [c for c in reach if c not in ban
                         and max(abs(c[0] - anchor[0]),
                                 abs(c[1] - anchor[1])) <= self.RADIUS]
            # 兜底②: 长期禁入把当前位置困死 (窄路上出口被全升级) ->
            #   解除身边的长期禁入。移动能力优先于避让, 否则彻底死锁。
            if not cands:
                n2 = self._release_perm_near(cur, 5)
                if n2:
                    self.log("[自愈] 长期禁入把当前位置困死 -> 解除身边 %d 条重挑" % n2)
                    ban, reach = self.plan_ctx(cur)
                    cands = [c for c in reach if c not in ban
                             and max(abs(c[0] - anchor[0]),
                                     abs(c[1] - anchor[1])) <= self.RADIUS]
            # 兜底③: 还是无路 -> 绕行知识整体不可信, 全部清空重来
            if not cands:
                n3 = self._clear_blocked(keep_perm=False)
                if n3:
                    self.log("[自愈] 仍无路可走 -> 清空全部 %d 条绕行记录重挑" % n3)
                    ban, reach = self.plan_ctx(cur)
                    cands = [c for c in reach if c not in ban
                             and max(abs(c[0] - anchor[0]),
                                     abs(c[1] - anchor[1])) <= self.RADIUS]
            if not cands:
                self._dead_n = getattr(self, "_dead_n", 0) + 1
                if self._dead_n >= 6:
                    self._dead_n = 0
                    self.log("[自愈] 连续 6 轮无候选 -> 判定输入被吞, 执行菜单自救")
                    self.escape_menu()
                return None
        self._dead_n = 0
        unvis = [c for c in cands if c not in self.visited]
        pool = unvis or cands
        if exclude is not None:
            pool = [c for c in pool if c != exclude] or \
                   [c for c in cands if c != exclude]
        if not pool:
            return None
        pool.sort(key=lambda c: (-(c[0] + c[1]),
                                 max(abs(c[0] - cur[0]), abs(c[1] - cur[1]))))
        top = pool[:max(1, len(pool) * 3 // 10)]
        top.sort(key=lambda c: max(abs(c[0] - cur[0]), abs(c[1] - cur[1])))
        return top[0]

    def bfs_path(self, src, dsts, extra_blocked=frozenset(), allow_doors=False,
                 relax_blocked=False):
        """relax_blocked=True 时不信「绕行」标记 (只信真地图+walk_path核实)。
        2026-10-02: 绳子旁走廊被怪挡几次后绳位全进 blocked, 探测永远
        走不过去 -> rope_probe 对绳位放宽一次。只影响 allow_doors 分支。

        ★ 2026-10-04 生路偏好: 目标仍按 dsts 顺序 + 可达性挑 (语义不变),
        但**路径**改由 _weighted_path 算 —— 同长度时优先走 visit_cnt 小的格。
        REVISIT_LAMBDA=0 或 visit_cnt 全空 -> 与历史版本逐位一致。
        """
        extra = set(extra_blocked)
        blk = set() if relax_blocked else self.blocked
        if allow_doors:
            bad = blk | extra
            reach = self.reach(src, bad)
        else:
            ban, reach = self.plan_ctx(src)
            bad = ban | extra
        best = None
        for d in dsts:
            if d in reach and d not in bad:
                best = d
                break
        if best is None:
            return None
        lam = getattr(self, "REVISIT_LAMBDA", 0.0)
        if lam > 0.0:
            wp = self._weighted_path(src, best, bad, lam)
            if wp is not None:
                return wp
            # 理论上到不了这儿 (reach 已证明可达): 万一算法有分歧,
            # 退回老 BFS 的 prev 树, 绝不因为"想走生路"而把路走丢。
        path = []
        p = best
        while p is not None:
            path.append(p)
            p = reach[p]
        return path[::-1]


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
                            _cl[2].replace("\\", "/").endswith(("pal_bot_mem.py",
                                                                "pal_bot_mem2.py")):
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
    bot = MemBot2(max_battles=maxb, max_seconds=secs)
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
              % (bot.battles_won, bot.n_battle), flush=True)
