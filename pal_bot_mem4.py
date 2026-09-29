# -*- coding: utf-8 -*-
"""v15 = 全图巡逻 + 快速战斗判定 (pal_bot_mem4)

用户反馈: ① 想放开整张地图都能去 (但不能出门) ② 一直往复循环刷怪
          ③ 效率太低, 感觉只在两个地方往返

一、巡逻范围 (用户诉求 ①②)
    v13/v14 是"锚点 + 半径 28 格": 锚点 = hotK(最近开战点) 或 startK(入场点)。
    后果: 半径内走完就退化成"在最深 30% 里挑最近的" -> 两点来回摆。
    v15 改为 **全可达域覆盖**:
      - 不用锚点、不用 RADIUS; 目标从 plan_ctx 返回的**整个可达域**里挑
      - 门禁分级照旧生效 (这是"不能出门"的保障: 门格禁入 + 可达域校验)
      - 每格维护 visit_cnt, 目标 = **访问次数最少的一档里挑最远的**
        => 天然往复循环: 全图扫一轮 -> 计数拉平 -> 再扫一轮
      - FAR_MIN 保证不会在脚边两点来回, FAR_MAX 防止一路狂奔不回头

二、效率 (用户诉求 ③) —— 日志证据
    `[战斗结束] #21 判据=连续 8 次确认非战斗 时长=6.3s 按键 7 次`
      7 次按键全是 space (8 次确认里最后一次不按键) => **这场根本不是战斗**,
      是被 _stall 的"持续动画 -> 进战斗循环兜底"误抓, 白烧 4.8 秒 + 白计胜场。
      整段日志几乎每场都是这个指纹 => 机器人长期卡在
      「走一步被吞 -> 判战斗 -> 白烧 6 秒 -> 回来再走一步」的循环里。
    真战斗也被同一段代码拖累: 结束时 8 次确认 x 0.6s = 4.8 秒, 而战斗本身
    按 F 两下就结束了 (约 1.5 秒) => 有效时间只占 20%。

    v15 的 fight():
      ① 进场先花 <=1.2 秒确认 (4 x 0.3s): 一次都没看到战斗画面就立刻退出,
         **一个键都不按** (旧版要按 7 次 space 等 4.8 秒)
      ② 真战斗: 按 F (用户铁律: 只用 F) -> 结束判定 = "is_battle 假 且 画面静止"
         连续 2 次 (约 0.5 秒), 而"攻击/施法动画"期间画面剧烈变化 -> 不算结束
      ③ 胜场只在真的发出过 F 时才计
      ④ 误抓时把当前格记入绕行 (AgedSet 会自愈), 并限流自救 (连续 3 次 -> space)
"""
import os
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import numpy as np                                        # noqa: E402
import pal_bot as PB                                       # noqa: E402
import pal_bot_mem2 as V13                                 # noqa: E402
from pal_bot_mem2 import MemBot2, BAR, BATTLE_MAX           # noqa: E402
from pal_bot_mem3 import MemBot3                            # noqa: E402


class MemBot4(MemBot3):
    # ---- 渐进巡逻参数 (v15b) ----
    # 实测教训 (v15 首版): 纯"全图均匀巡逻"会把大量步数花在无怪区,
    # 刷怪效率反而与 v14 持平 (0.8 场/分)。改为: 锚点(怪窝)周围从 28 格起,
    # 走空一圈自动 +12 直到全图; 每开一场战斗重新聚焦 -> 优先在有怪区域刷,
    # 同时最终仍能覆盖整张可达域 (用户要求"全图都能去但不能出门")。
    PATROL_R0 = 28            # 初始巡逻半径 (锚点周围)
    PATROL_STEP = 12          # 半径内走空后扩大的步长
    PATROL_RMAX = 90          # 巡逻半径上限 (覆盖整个可达域)
    FAR_MIN = 10              # 目标至少这么远 (格), 避免脚边两点来回

    # ---- 追怪参数 (2026-09-28, 明雷怪主动追击) ----
    # 原理: 两帧差分找"会动的东西"(怪物待机/游走动画), 排除队伍(画面中心)
    # 和已学习的静态动画(火把/水波), 剩下的动屑换算成 K 坐标,
    # BFS 可达就优先追过去 -- 踩到怪身上自动触发战斗。
    SCAN_DT = 1.5             # 两次扫描的最小间隔 (秒)
    SCAN_PAIR_DT = 0.28       # 差分双帧间隔 (秒)
    SCAN_DIFF_TH = 40         # 像素差分阈值 (RGB 通道和)
    SCAN_MIN_CELLS = 5        # 动屑块最小尺寸 (8x8 像素块数)
    CHASE_MAX_TRIES = 3       # 同一格追 3 次没开打 -> 判定是静态动画, 学习拉黑
    CHASE_MAX_DIST = 25       # 动屑换算的 K 离队伍超过此格数 -> 视为换算误差, 忽略
    GAME_Y0, GAME_Y1 = 139, 1189     # 游戏视口在 1687x1294 窗口截屏中的 y 范围
    SCALE_X = 1687 / 320.0           # 窗口截屏 -> 游戏 320x200 原生坐标
    SCALE_Y = (GAME_Y1 - GAME_Y0) / 200.0

    # ---- 战斗判定参数 (v15 提速核心) ----
    PROBE_N = 4               # 进场确认次数
    PROBE_DT = 0.30           # 每次间隔 => 最多 1.2 秒判定"是不是真战斗"
    ANIM_BIG = 8000           # 画面剧烈变化的像素阈值 (攻击/施法动画)
    END_CONFIRM = 2           # 连续 N 次「非战斗 + 画面静止」= 真结束
    KEY_F_GAP = 0.60          # 按 F 的间隔 (等一击动画走完)

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.visit_cnt = {}       # 格 -> 访问次数 (巡逻的"往复盘")
        self._patrol_r = self.PATROL_R0
        self._pick_n = 0
        self._false_battle = 0    # 累计误抓次数
        self._false_streak = 0    # 连续误抓
        self._t_walk = 0.0        # 累计走路耗时
        self._t_fight = 0.0       # 累计战斗耗时
        self._t_false = 0.0       # 累计误抓耗时
        self._hot_file = os.path.join(BASE, "hotspot.json")
        self._bias = None            # 方向偏置 (K空间向量), None=距锚最近优先
        self._bias_name = ""
        self.open_doors = False      # 开门模式: 不避传送阵/楼梯, 踩到就去下一层
        self._scan_t = 0.0           # 追怪: 上次扫描时刻
        self._animK = {}             # 追怪: 场景 -> {K: 无战斗次数} (火把等学习拉黑)
        self._chase_tries = {}       # 追怪: (scene,K) -> 已追次数
        self._hot_restored = False
        self.log("[v15b] 渐进巡逻: 锚点(怪窝)半径 %d 起, 走空 +%d, 上限 %d; "
                 "每场战斗重新聚焦" % (self.PATROL_R0, self.PATROL_STEP,
                                     self.PATROL_RMAX))
        self.log("[v15b] 战斗判定: 进场 %.1fs 确认, 误抓不按键直接退; "
                 "结束 = 非战斗 + 画面静止 x%d" % (self.PROBE_N * self.PROBE_DT,
                                                 self.END_CONFIRM))

    # ------------------------------------------------------------------
    #  ★ 怪窝持久化: 重启后直奔上次刷怪的地点, 不再从落点瞎摸索
    #    (实测: 从无怪区启动 -> 151 步没打仗 -> 误迁移 -> 被送进场景70,
    #     300 秒 0 胜。把怪窝和战斗计数存盘, 启动时恢复。)
    # ------------------------------------------------------------------
    def _save_hotspot(self):
        # 2026-09-26 修复: 改为"每场景各自记录锚点", 旧版单一 hotK 会被
        # 偶尔闯入的新场景(哪怕只打 1 场)覆盖掉刷了几百场的老怪窝。
        try:
            import json
            if self.hotK is None:
                return
            d = {}
            if os.path.exists(self._hot_file):
                try:
                    with open(self._hot_file, encoding="utf-8") as f:
                        d = json.load(f)
                except Exception:
                    d = {}
            hs = d.get("hotspots") or {}
            hs[str(self.scene)] = {
                "K": list(self.hotK),
                "battles": self.scene_battles.get(self.scene, 0),
            }
            with open(self._hot_file, "w", encoding="utf-8") as f:
                json.dump({"hotspots": hs, "battles": self.scene_battles}, f)
        except Exception:
            pass

    def _restore_hotspot(self):
        if self._hot_restored:
            return
        self._hot_restored = True
        try:
            import json
            with open(self._hot_file, encoding="utf-8") as f:
                d = json.load(f)
            for sc, n in (d.get("battles") or {}).items():
                self.scene_battles[int(sc)] = int(n)
            hs = d.get("hotspots") or {}
            if str(self.scene) in hs and hs[str(self.scene)].get("K"):
                K = (int(hs[str(self.scene)]["K"][0]),
                     int(hs[str(self.scene)]["K"][1]))
                self.hotK = K
                self.log("[v15] 恢复怪窝: 场景%d K%s (历史战斗%s 场)"
                         % (self.scene, K, self.scene_battles.get(self.scene, 0)))
            elif hs:
                self.log("[v15] 怪窝档案无场景%d的锚点(有: %s) -> 照常探索"
                         % (self.scene, ",".join(hs.keys())))
            elif d.get("scene") is not None and int(d.get("scene", -1)) == self.scene:
                # 旧版单锚点格式兼容
                K = (int(d["K"][0]), int(d["K"][1]))
                self.hotK = K
                self.log("[v15] 恢复怪窝(旧格式): 场景%d K%s" % (self.scene, K))
            else:
                self.log("[v15] 无怪窝档案 -> 照常探索")
        except Exception:
            self.log("[v15] 无怪窝档案 -> 照常探索")

    def enter_scene(self, sc, why=""):
        super().enter_scene(sc, why)
        self._restore_hotspot()

    # ------------------------------------------------------------------
    #  ★ bug 修复: 迁移阈值必须每步更新
    #    原来只在 pick_target 里更新, 而长路径行走期间 pick_target 不被调用,
    #    导致"打过仗的场景"也会在 150 步时被误判为无怪区 -> 强制迁移 ->
    #    被送进无怪的场景 70, 然后卡在门口反复试探 (v15 首版实测)。
    # ------------------------------------------------------------------
    def step(self, key):
        r = super().step(key)
        self.MIGRATE_AFTER = 99999 if self.scene_battles.get(self.scene, 0) else 150
        return r

    # ------------------------------------------------------------------
    #  ★ 开门模式 (2026-09-26, 锁妖塔等多层地图用):
    #    不再避开传送阵/楼梯 —— 门禁全撤, 踩到就直接前往下一层继续刷。
    #    默认关闭 (第 6 个启动参数 "穿门" 打开), 老行为完全不变。
    # ------------------------------------------------------------------
    def door_ban(self, pad):
        if self.open_doors:
            return set()                     # 门禁全撤: 门格就是普通格子
        return MemBot2.door_ban(self, pad)

    def handle_door(self, K_door, new_scene, key):
        if not self.open_doors:
            return MemBot2.handle_door(self, K_door, new_scene, key)
        old = self.scene
        self.log("[穿门] 开门模式: 场景%d -> %d (K%s, 来向%s) -> 直接前往"
                 % (old, new_scene, K_door, key))
        # 门位档案照常积累 (回头想关模式也有数据)
        st = self.scene_know.setdefault(old, {"doors": set()})
        for ua in (-1, 0, 1):
            for ub in (-1, 0, 1):
                st["doors"].add((K_door[0] + ua, K_door[1] + ub))
        self.remember_door(old, K_door)
        self.enter_scene(new_scene, "(开门穿门)")
        # 防乒乓: 落点 ±1 临时禁入 (BLOCK_TTL 秒后自动失效),
        # 逼队伍先往新层里走, 不要落地就踩回头楼梯
        K = self.mem.state()["K"]
        for ua in (-1, 0, 1):
            for ub in (-1, 0, 1):
                self.blocked.add((K[0] + ua, K[1] + ub))
        self._pc_key = None
        self.log("[穿门] 落点 K%s ±1 临时禁入 %.0fs (防落地踩回头楼梯)"
                 % (K, self.BLOCK_TTL))

    # ------------------------------------------------------------------
    #  渐进巡逻: 覆写目标选择 (v15b)
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    #  ★ 追怪 (2026-09-28): 画面动屑找明雷怪, 可达就优先追过去
    # ------------------------------------------------------------------
    def _find_blobs(self, mask):
        """8x8 降采样 + 8 邻域连通域。返回 [(屏x, 屏y, 块数), ...]"""
        gh, gw = mask.shape[0] // 8, mask.shape[1] // 8
        grid = mask[:gh * 8, :gw * 8].reshape(gh, 8, gw, 8) \
            .sum(axis=(1, 3)) > 20
        seen = np.zeros_like(grid, dtype=bool)
        out = []
        for i in range(gh):
            for j in range(gw):
                if not grid[i, j] or seen[i, j]:
                    continue
                q = [(i, j)]
                seen[i, j] = True
                cells = []
                while q:
                    y, x = q.pop()
                    cells.append((y, x))
                    for dy in (-1, 0, 1):
                        for dx in (-1, 0, 1):
                            ny, nx = y + dy, x + dx
                            if (0 <= ny < gh and 0 <= nx < gw
                                    and grid[ny, nx] and not seen[ny, nx]):
                                seen[ny, nx] = True
                                q.append((ny, nx))
                if len(cells) >= self.SCAN_MIN_CELLS:
                    ys = sum(c[0] for c in cells) / len(cells)
                    xs = sum(c[1] for c in cells) / len(cells)
                    out.append((xs * 8 + 4, ys * 8 + 4, len(cells)))
        return out

    def _screen_to_K(self, sx, sy, st):
        """截屏像素 -> 地图 K 坐标。
        队伍恒在视口中心 (partyoffset 160,112 恒定), 其世界像素 = st[px,py];
        动屑相对队伍的屏幕偏移除以缩放 = 原生像素偏移, 加到队伍世界像素上。"""
        pcx = 1687 / 2.0
        pcy = (self.GAME_Y0 + self.GAME_Y1) / 2.0
        wx = st["px"] + (sx - pcx) / self.SCALE_X
        wy = st["py"] + (sy - pcy) / self.SCALE_Y
        return (int(np.floor((wx - 2 * wy) / 32.0)),
                int(np.floor((wx + 2 * wy - 32) / 32.0)))

    def scan_monsters(self, cur):
        """扫描画面动屑找怪。发现可达目标返回其 K, 否则 None。"""
        now = time.time()
        if now - self._scan_t < self.SCAN_DT:
            return None
        self._scan_t = now
        try:
            st = self.mem.state()
        except Exception:
            return None
        if st is None:
            return None
        a1 = self.g.shot()
        self._n_shot = getattr(self, "_n_shot", 0) + 1
        time.sleep(self.SCAN_PAIR_DT)
        a2 = self.g.shot()
        self._n_shot += 1
        d = np.abs(a1.astype(np.int16) - a2.astype(np.int16)).sum(axis=2)
        mask = d > self.SCAN_DIFF_TH
        h, w = mask.shape
        pcy = (self.GAME_Y0 + self.GAME_Y1) // 2
        mask[:self.GAME_Y0 - 4, :] = False             # 排除黑边
        mask[self.GAME_Y1 + 4:, :] = False
        mask[pcy - 110:pcy + 110, w // 2 - 100:w // 2 + 100] = False  # 队伍
        anim = self._animK.setdefault(self.scene, {})
        tries = self._chase_tries
        ban, reach = self.plan_ctx(cur)
        best = None
        for (cx, cy, ncell) in self._find_blobs(mask):
            K = self._screen_to_K(cx, cy, st)
            if K == cur:
                continue
            if max(abs(K[0] - cur[0]), abs(K[1] - cur[1])) > self.CHASE_MAX_DIST:
                continue                                # 换算误差保护
            if anim.get(K, 0) >= self.CHASE_MAX_TRIES:
                continue                                # 已学习: 火把等
            if tries.get((self.scene, K), 0) >= self.CHASE_MAX_TRIES:
                continue
            if K in ban or K in self.blocked or K not in reach:
                continue                                # 不可达 / 门禁
            if best is None or ncell > best[2]:
                best = (K, (cx, cy), ncell)
        if best is None:
            return None
        K = best[0]
        dist = max(abs(K[0] - cur[0]), abs(K[1] - cur[1]))
        if dist <= 1:
            # 就在脚边却没开打 -> 不是怪 (动态图块), 学习拉黑
            anim[K] = anim.get(K, 0) + 1
            return None
        tries[(self.scene, K)] = tries.get((self.scene, K), 0) + 1
        self.log("[追怪] 发现动屑目标 场景%d K%s (屏(%d,%d) %d块, 距我%d格,"
                 " 第%d次) -> 优先追击"
                 % (self.scene, K, best[1][0], best[1][1], best[2], dist,
                    tries[(self.scene, K)]))
        return K

    def pick_target(self, exclude=None):
        cur = self.mem.state()["K"]
        # 无怪区 / 回家模式沿用父类逻辑
        # (MIGRATE_AFTER 已改在 step() 里每步更新, 修掉"长路径期间阈值过期"bug)
        if (self.scene in self.NO_MONSTER and not self.home_active()
                and not self.migrate and time.time() > self.migrate_cd):
            self.log("[迁移] 场景 %d 是实测无怪区 -> 立即启动迁移" % self.scene)
            self.migrate = True
            return None
        if self.home_active():
            return MemBot2.pick_target(self, exclude)     # 回家分支: 复用父类

        # ★ 追怪: 场景里有可见怪且路径可达 -> 优先于巡逻去触发战斗
        if not self.migrate:
            mk = self.scan_monsters(cur)
            if mk is not None:
                return mk

        self.visit_cnt[cur] = self.visit_cnt.get(cur, 0) + 1
        ban, reach = self.plan_ctx(cur)                   # ★ 门禁分级 = 防出门
        cands = [c for c in reach if c not in ban and c != cur and c != exclude]
        if not cands:
            if self.blocked:
                n = len(self.blocked)
                self.blocked.clear()
                self._pc_key = None
                self.log("[自愈] 全图候选为空 -> 清空 %d 条「被挡」重挑" % n)
                ban, reach = self.plan_ctx(cur)
                cands = [c for c in reach
                         if c not in ban and c != cur and c != exclude]
            if not cands:
                return None

        anchor = self.hotK if self.hotK else self.startK

        def dist(c):
            return max(abs(c[0] - cur[0]), abs(c[1] - cur[1]))

        def dist_a(c):
            return max(abs(c[0] - anchor[0]), abs(c[1] - anchor[1]))

        vc = self.visit_cnt
        # ★ 渐进半径: 锚点周围从 PATROL_R0 起, 该圈内全访问过就扩一圈
        ring = unvis = None
        R = self._patrol_r
        while R <= self.PATROL_RMAX:
            inr = [c for c in cands if dist_a(c) <= R]
            unv = [c for c in inr if vc.get(c, 0) == 0]
            if unv:
                ring, unvis = inr, unv
                break
            R += self.PATROL_STEP
        if ring is None:                       # 全图都访问过 -> 重头再扫
            R = self.PATROL_RMAX
            ring = [c for c in cands if dist_a(c) <= R]
            unvis = [c for c in ring if vc.get(c, 0) == 0] or ring
        if R != self._patrol_r:
            self._patrol_r = R
            self.log("[巡逻] 半径内已走空 -> 扩大到 %d 格 (锚点 K%s)"
                     % (R, anchor))
        pool = unvis
        # ★ 往复循环: 只在"访问次数最少"的一档里挑
        mn = min(vc.get(c, 0) for c in pool)
        tier = [c for c in pool if vc.get(c, 0) <= mn]
        # 别挑脚边格 (避免两点摆动); 全都在脚边就只好就近
        tier = [c for c in tier if dist(c) >= self.FAR_MIN] or tier
        # ★ 方向偏置 (2026-09-26 新增): 用户把队伍带到新地方启动时,
        #   原逻辑"距锚最近优先"会先扫完来时走廊才往深处去。
        #   指定方向后, 优先挑锚点在该方向一侧的格子 (投影大的在前)。
        if self._bias is not None:
            bx, by = self._bias

            def proj(c):
                return (c[0] - anchor[0]) * bx + (c[1] - anchor[1]) * by
            fwd = [c for c in tier if proj(c) > 0]
            if fwd:
                tier = fwd
            tier.sort(key=lambda c: (-proj(c), dist_a(c)))
            if self._pick_n % 40 == 1:
                self.log("[巡逻] 方向偏置 (%s): 前向候选 %d/%d"
                         % (self._bias_name, len(fwd), len(tier)))
        else:
            # 靠近锚点(怪窝)优先: 遇敌率高; 取最近的 1/3 里伪随机一个
            tier.sort(key=lambda c: dist_a(c))
            tier = tier[:max(1, len(tier) // 3)]
        self._pick_n += 1
        pick = tier[(self._pick_n * 7) % len(tier)] if tier else None
        if self._pick_n % 40 == 1:
            self.log("[巡逻] 目标 K%s (距锚%d/距我%d, 半径%d, 候选%d, 未访问%d)"
                     % (pick, dist_a(pick), dist(pick), R, len(cands),
                        len(unvis)))
        return pick

    # ------------------------------------------------------------------
    #  停机分类 (覆写 v14): ★ 误抓两大来源实测 (false_*.png 铁证)
    #    ① 场景动态图块 (火把火焰闪烁): 画面小幅持续变化, 不是战斗
    #    ② 穿门换图的淡入: 画面整体压暗渐亮, 也不是战斗
    #    两者都曾被送进 fight() 白烧 1.7s。现在:
    #    - 平均亮度 <95 -> 淡入/黑屏, 等待 (0.6s)
    #    - diff>150000 (约7%像素) 才算战斗转场; 鬼阴坛火把/水波动画实测 diff~13000
    #    - 其余一律当墙处理 (省 0.8s); 旧阈值 15000 与环境动画同量级 -> 误抓泛滥
    # ------------------------------------------------------------------
    def _stall(self, key, K0, sc0, n_retry):
        self._n_stall += 1
        a = self.g.shot()
        self._n_shot += 1
        if self._esc_if_menu(a):
            return ("menu", K0, K0, sc0)
        if PB.is_battle(a):
            self.log("[快] 停机=战斗 -> 进战斗循环")
            self.fight(why="快路径停机判定")
            return ("battle", K0, K0, sc0)
        if float((a.sum(axis=2) < 45).mean()) > 0.95:
            self.log("[快] 停机=黑屏 -> 等恢复")
            time.sleep(0.8)
            return ("black", K0, K0, sc0)
        if float(a.mean()) < 25:               # ★ 真黑才可能是淡入
                                               #   (鬼阴坛洞穴正常画面均值~44, 旧阈值95全误判)
            self.log("[快] 停机=换图淡入(亮度%.0f) -> 等 0.6s" % float(a.mean()))
            time.sleep(0.6)
            return ("black", K0, K0, sc0)
        for i in range(2):                     # 二次静止性判定
            time.sleep(0.35)
            a2 = self.g.shot()
            self._n_shot += 1
            if self._esc_if_menu(a2):
                return ("menu", K0, K0, sc0)
            if PB.is_battle(a2):
                self.log("[快] 停机=战斗(二次判定第%d轮) -> 进战斗循环" % (i + 1))
                self.fight(why="快路径停机二次判定")
                return ("battle", K0, K0, sc0)
            if float((a2.sum(axis=2) < 45).mean()) > 0.95:
                time.sleep(0.8)
                continue
            d = self._diffpix(a, a2)
            if d < 2500:                       # 完全静止 -> 真墙
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
            if float(a2.mean()) < 25:          # ★ 真黑 = 淡入中 (洞穴正常画面~44)
                time.sleep(0.6)
                return ("black", K0, K0, sc0)
            if d > 150000:                     # 全屏级变化 -> 疑似战斗转场
                a = a2
                continue
            # ★ 小幅持续变化 = 火把/水波等动态图块, 不是战斗 -> 当墙处理
            self.log("[快] 停机=动态图块(diff=%d, 亮度%.0f) -> 当墙处理"
                     % (d, float(a2.mean())))
            return ("blocked", K0, K0, sc0)
        self.log("[快] 停机=持续动画(疑似战斗转场) -> 进战斗循环兜底")
        self.fight(why="停机持续动画")
        return ("battle", K0, K0, sc0)

    # ------------------------------------------------------------------
    #  快速战斗判定: 覆写 fight
    # ------------------------------------------------------------------
    def fight(self, why=""):
        self._chase_tries = {}      # 开打了: 追怪计数清零 (怪是真的)
        st = self.mem.state()
        sc, K = st["scene"], st["K"]
        # ① 进场确认: 真战斗不可能一进来就是结束画面
        conf = False
        t_probe = time.time()
        for _ in range(self.PROBE_N):
            a = self.g.shot()
            if self.ui_menu(a):
                self.log("[战斗] 进场即菜单 -> esc, 判为误抓")
                self.press_esc()
                return False
            if PB.is_battle(a):
                conf = True
                break
            time.sleep(self.PROBE_DT)
        if not conf:
            dt = time.time() - t_probe
            self._false_battle += 1
            self._false_streak += 1
            self._t_false += dt
            self.log("[战斗] 误抓: %.1fs 内未见战斗画面 (累计%d, 连续%d)"
                     " -> 不按键直接退" % (dt, self._false_battle,
                                        self._false_streak))
            if self._false_battle <= 3:      # 诊断: 误抓画面留档
                try:
                    from PIL import Image
                    Image.fromarray(self.g.shot().astype("uint8")).save(
                        os.path.join(BASE, "false_%d_K%d_%d.png"
                                     % (self._false_battle, K[0], K[1])))
                except Exception:
                    pass
            self.blocked.add(K)          # 确实走不动 -> 记绕行 (AgedSet 会自愈)
            if self._false_streak >= 3:
                self._false_streak = 0
                self.log("[战斗] 连续误抓 3 次 -> space 试探 (推进对话)")
                self.g.key("space")
                time.sleep(0.3)
                self.g.flush_keys()
            return False
        self._false_streak = 0

        # ② 真战斗
        self.n_battle += 1
        n = self.n_battle
        self.scene_battles[sc] = self.scene_battles.get(sc, 0) + 1
        self.hotK = K
        self._save_hotspot()         # ★ 怪窝落盘: 重启可直奔这里
        self.g.flush_keys()              # 开战瞬间丢掉可能按住的方向键
        if n <= 3:                        # 只在前 3 场留档 (省 IO)
            try:
                from PIL import Image
                Image.fromarray(self.g.shot().astype("uint8")) \
                    .resize((843, 647)).save(os.path.join(
                        BASE, "battle_start_%02d.png" % n))
            except Exception:
                pass
        t0 = time.time()
        self.log(BAR)
        self.log("[战斗开始] #%d  场景%s K%s  判据=%s  t=%s"
                 % (n, sc, K, why, time.strftime("%H:%M:%S")))
        self.log(BAR)
        n_f = 0
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
            a = self.g.shot()
            if self.ui_menu(a):
                # 菜单类画面 (含"法术页", is_battle 会误报 True) -> 只按 esc
                self.press_esc()
                continue
            if over > self.LONG_BATTLE and over - last_esc > 8.0:
                last_esc = over
                self.press_esc()
                continue
            if PB.is_battle(a):
                lost = 0
                self.g.key("f")          # ★ 用户铁律: 战斗里只用 F
                n_f += 1
                time.sleep(self.KEY_F_GAP)
                continue
            # is_battle 假: 是"攻击/施法动画"还是"真结束"?
            time.sleep(0.18)
            a2 = self.g.shot()
            if PB.is_battle(a2):
                lost = 0
                continue
            if self._diffpix(a, a2) > self.ANIM_BIG:
                continue                  # 画面剧烈变化 -> 动画中, 继续打
            lost += 1
            if lost >= self.END_CONFIRM:
                reason = "连续%d次非战斗且画面静止" % lost
                break
            time.sleep(0.15)
        dur = time.time() - t0
        self._t_fight += dur
        self._patrol_r = self.PATROL_R0        # ★ 重新聚焦新怪窝
        self.g.flush_keys()
        # ③ 结算: 只按 space (地图上 space 是"确认/对话", 安全)
        for _ in range(2):
            if self.battle_screen():
                break
            time.sleep(0.35)
            self.g.key("space")
        self.g.flush_keys()
        if n_f >= 1:
            self.battles_won += 1
            self.last_win = time.time()
            self.steps_since_battle = 0
            self._stuck_n = 0
        else:
            self.log("[战斗] 一次 F 都没发出 -> 不计胜场 (疑似误判)")
        self.log(BAR)
        self.log("[战斗结束] #%d  判据=%s  时长=%.1fs  F键%d次  "
                 "累计胜场=%d  t=%s"
                 % (n, reason, dur, n_f, self.battles_won,
                    time.strftime("%H:%M:%S")))
        self.log(BAR)
        return True

    # ------------------------------------------------------------------
    #  覆写: 统计走路/战斗时间占比 (诊断效率用)
    # ------------------------------------------------------------------
    def _rate(self, dt, n_retry, stalled):
        if not stalled:
            self._t_walk += dt
        super()._rate(dt, n_retry, stalled)
        now = time.time()
        if now - getattr(self, "_eff_t", 0) > 60:
            self._eff_t = now
            tot = self._t_walk + self._t_fight + self._t_false
            if tot > 1:
                self.log("[效率] 走路 %.0f%% / 战斗 %.0f%% / 误抓 %.0f%% "
                         "(累计 走%.0fs 战%.0fs 误%.0fs, 误抓%d次)"
                         % (self._t_walk / tot * 100, self._t_fight / tot * 100,
                            self._t_false / tot * 100, self._t_walk,
                            self._t_fight, self._t_false, self._false_battle))


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
                                 "pal_bot_mem3.py", "pal_bot_mem4.py")):
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
    # 第7参数: 无胜场保险秒数 (0=关闭该保险; 默认1200=20分钟没赢就停机)
    stall = 1200
    if len(sys.argv) > 7 and sys.argv[7].strip():
        try:
            stall = int(sys.argv[7])
        except ValueError:
            pass
    if stall <= 0:
        stall = 10 ** 9            # 事实上的"永不触发"
    bot = MemBot4(max_battles=maxb, max_seconds=secs, stall_limit=stall)
    if stall >= 10 ** 9:
        bot.log("[配置] 无胜场保险已关闭 (stall=0, 漂到无怪区也不会自动停, 注意观察)")
    # ★ 看门狗 (2026-09-26): 独立分离进程守护本进程,
    #   命令行窗口被关/崩溃时自动对游戏窗口 flush_keys, 防卡键走丢
    try:
        import subprocess
        _wd = os.path.join(BASE, "bot_watchdog.py")
        _DETACHED = 0x00000008 | 0x00000200   # DETACHED_PROCESS | NEW_PGRP
        subprocess.Popen([sys.executable, _wd, str(os.getpid()),
                          str(bot.g.hwnd)],
                         creationflags=_DETACHED, cwd=BASE,
                         stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
        bot.log("[看门狗] 已启动: bot 异常退出时自动清键 (守护 %d)"
                % os.getpid())
    except Exception as _e:
        bot.log("[看门狗] 启动失败: %s (不影响正常运行)" % _e)
    if len(sys.argv) > 5 and sys.argv[5]:
        # 方向偏置: K 空间映射 up=+a, right=+b (按键真值 ΔK 实测标定)
        _BIAS = {"上": (1, 0), "下": (-1, 0), "左": (0, -1), "右": (0, 1),
                 "右上": (1, 1), "右下": (-1, 1), "左上": (1, -1),
                 "左下": (-1, -1),
                 "u": (1, 0), "d": (-1, 0), "l": (0, -1), "r": (0, 1),
                 "ru": (1, 1), "rd": (-1, 1), "lu": (1, -1), "ld": (-1, -1)}
        _name = sys.argv[5].strip()
        if _name in _BIAS:
            bot._bias = _BIAS[_name]
            bot._bias_name = _name
            bot.log("[巡逻] 方向偏置开启: %s (优先往 %s 探索, 不先扫来路)"
                    % (_name, _name))
        else:
            bot.log("[巡逻] 未知方向 '%s' (可选: 上/下/左/右/右上/右下/左上/左下)"
                    " -> 忽略" % _name)
    if len(sys.argv) > 6 and sys.argv[6].strip() in ("穿门", "开门", "open"):
        bot.open_doors = True
        bot.log("[配置] 开门模式: 不避开传送阵/楼梯, 踩到就直接前往下一层"
                " (落点±1 临时禁入防乒乓)")
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
        print("[退出] 已清空按键, 共胜利 %d 场, 误抓 %d 次"
              % (bot.battles_won, bot._false_battle), flush=True)
