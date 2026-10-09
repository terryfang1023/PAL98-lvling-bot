# -*- coding: utf-8 -*-
"""chest_log.json —— 开过的宝箱台账 (跨重启记住, 不再白跑一趟)。

为什么单独开一个文件, 而不是塞进 scene_info.json / bot_config.json:
  * `scene_info.json` 是从游戏内存导出的**派生清单**(哪个场景有哪些箱子),
    每次点「刷新怪点/宝箱」都会整个重写 -> 台账写进去会被冲掉。
  * `bot_config.json` 存的是**人的选择**(按键/开关/序列); 台账是**跑出来的
    事实**, 两者生命周期不一样。
  * 机器人一重启, 内存里的 chest_dead 集合就空了 -> 又会对每个开过的箱子
    按 4 次空格白试一遍 (每箱约 3~5 秒 + 冷却)。这个文件就是给那一步兜底的。

文件格式 (人可读, 出问题直接删掉重来):
    {
      "version": 1,
      "chests": {
        "220": {                        # 场景号
          "-53,152": {                  # 箱子格 K = "a,b"
            "t": "2026-10-02 17:52:31", # 最近一次(尝试)的时间
            "n": 1,                     # 累计次数 (正常是 1)
            "item": "傀儡虫",           # 开出来的东西 (物品栏差分, 可能缺)
            "dn": 1,                    # 那一次多了几个
            "ok": true,                 # true=打开过; false=试满次数没反应(空箱)
            "oid": 3799                 # ★ 要素编号里的对象序号 (v19.0)
          }                             #   完整编号 = "场景号-序号" = 220-3799,
        }                               #   由 bot_featlog.code() 拼; 老文件没有
      }                                 #   这个字段 -> 0, 报告会退回按 K 匹配
    }

两类都记的原因: 用户真正在意的是"别再走过去白试一遍"。

★ v18.9 更正 (2026-10-04, 1058 个宝箱全表实测) —— **内存里其实有可靠标记**:
    开箱脚本最后一条是 `0x0002 STOP_ENTRY -> 36509` (36509 = 三条指令的"箱子
    已空"提示), 而 play.c:153 把这个返回值**回写**到该对象的 wTriggerScript
    -> 从此再也走不到 0x001F 加物品。实测"脚本没了加物品"与"触发脚本被改成
    同一个常量"935/935 完全等价。
    当初得出"没有标记", 是因为拿**已开箱 vs 没去过场景的箱**比错了对象
    (跨场景的脚本号本来就不同, 16 个字段当然一样)。
    现在运行时由 pal_bot_mem5.chest_verdict 直接读内存判 (已开空/不可触发的
    箱子一律连走都不走), 台账只留给"开出了什么"以及内存读不到时的兜底;
    反过来, "按空格没反应但内存说还满"时**不再记空箱**(那是走位/丢键失败)。

只读进程内存之外没有任何副作用; 本模块**不 import pal_bot** (GUI 进程要
直接调用它, 见 bot_gui.py 的「开箱记录」)。
"""
import os
import time

BASE = os.path.dirname(os.path.abspath(__file__))
CHESTLOG_PATH = os.path.join(BASE, "chest_log.json")

VERSION = 1
MAX_CHESTS = 4000               # 台账上限护栏: 超了丢最旧的 (正常几百个封顶)


def default_path():
    """默认台账路径 (读模块级 CHESTLOG_PATH, 测试可临时改它)。"""
    return CHESTLOG_PATH


def kk(K):
    """格子坐标 -> 台账里的键 "a,b"。"""
    return "%d,%d" % (int(K[0]), int(K[1]))


def parse_kk(s):
    """台账键 "a,b" -> (a, b); 坏数据 -> None。"""
    try:
        a, b = str(s).split(",")
        return (int(a), int(b))
    except Exception:
        return None


def norm_scene(scene):
    """场景号 -> 台账里的字符串键 (坏值返回 None)。"""
    try:
        return str(int(scene))
    except Exception:
        return None


def _now_str():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _int_or_zero(v):
    """可能是 None/''/坏字符串 -> 0 (台账字段容错)。"""
    try:
        i = int(v)
        return i if i > 0 else 0
    except Exception:
        return 0


class ChestLog:
    """开箱台账的内存视图 + 落盘。构造即可用; 任何异常都不往外抛。

    path 缺省 = default_path() (调用时刻读, 便于测试临时改 CHESTLOG_PATH)。
    """

    def __init__(self, path=None):
        self.path = path or default_path()
        self.data = self._blank()
        self._load()
        self.dirty = False

    # ---------------- 读写 ----------------
    def _blank(self):
        return {"version": VERSION, "chests": {}}

    def reload(self):
        """重读磁盘 (GUI 用来显示机器人子进程刚写进去的记录)。"""
        self._load()
        return self.count()

    def _load(self):
        try:
            import json
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            return                                  # 缺失/损坏 -> 当空台账
        if not isinstance(d, dict) or not isinstance(d.get("chests"), dict):
            return
        clean = {}
        for sc, kv in d["chests"].items():
            if norm_scene(sc) is None or not isinstance(kv, dict):
                continue
            sc2 = norm_scene(sc)
            for k, e in kv.items():
                if parse_kk(k) is None or not isinstance(e, dict):
                    continue
                try:
                    n = max(1, int(e.get("n", 1)))
                except Exception:
                    n = 1
                # 老文件没有 ok 字段 -> 当"已开"(宁可跳过, 也别回去白跑)
                ok = e.get("ok")
                clean.setdefault(sc2, {})[k] = {
                    "t": str(e.get("t") or ""),
                    "n": n,
                    "item": str(e.get("item") or ""),
                    "dn": int(e.get("dn") or 0),
                    "ok": True if ok is None else bool(ok),
                    "oid": _int_or_zero(e.get("oid")),
                }
        self.data = {"version": VERSION, "chests": clean}

    def save(self):
        """原子落盘。失败 -> False (台账是锦上添花, 绝不带崩机器人)。"""
        try:
            import botcfg
            botcfg.save(self.data, self.path)       # 同目录原子写 (tmp+replace)
            self.dirty = False
            return True
        except Exception:
            return False

    # ---------------- 记录 ----------------
    def mark(self, scene, K, item=None, dn=0, ok=True, tries=0, when=None,
             oid=None):
        """记一个箱子。返回写入的条目 dict (失败返回 None)。

        ok=True  = 打开成功 (开出 item; 也可能是空对话)
        ok=False = 试满 CHEST_TRY_N 次都没反应 (空箱/早就开过了) —— 这种也要记,
                   否则每次重启都要把全地图开过的箱子再走一遍 (用户投诉点)。
        tries    = 这一轮总共试了几次 (n 的初值; 便于看"是几次之后放弃的")。
        oid      = 要素对象序号 (v19.0); 完整编号 = "场景号-序号", 见 bot_featlog.code()
        重复记录同一个箱子 -> 累加 n (次数) 并刷新时间; ok 只升不降
        (先"空"后"开出东西"会升级为已开, 反过来不会)。
        """
        sc = norm_scene(scene)
        try:
            k = kk(K)
        except Exception:
            return None
        if sc is None or parse_kk(k) is None:
            return None
        o = _int_or_zero(oid)
        d = self.data.setdefault("chests", {}).setdefault(sc, {})
        e = d.get(k)
        if e is None:
            e = {"t": when or _now_str(), "n": max(1, int(tries or 1)),
                 "item": "", "dn": 0, "ok": bool(ok), "oid": o}
            d[k] = e
        else:
            e["n"] = int(e.get("n", 1)) + 1
            e["t"] = when or _now_str()
            e["ok"] = bool(e.get("ok", True)) or bool(ok)
            if o:
                e["oid"] = o
        if item:
            e["item"] = str(item)
            e["dn"] = int(dn or 0)
        self.dirty = True
        self.prune()
        self.save()
        return e

    def forget(self, scene, K):
        """删掉一条 (误记时用)。返回是否删掉了。"""
        sc = norm_scene(scene)
        try:
            k = kk(K)
        except Exception:
            return False
        d = self.data.get("chests", {}).get(sc or "", {})
        if k in d:
            del d[k]
            if not d:
                self.data["chests"].pop(sc, None)
            self.dirty = True
            self.save()
            return True
        return False

    def forget_empty(self):
        """只删"空箱"记录 (ok=False)。返回删掉条数。

        用武之地: 某次因为被怪挡/转身失败而误记成"空"的箱子, 想让机器人
        再试一次 —— 又不想把"已开出东西"的记录一起清掉。
        """
        n = 0
        for sc, kv in list(self.data.get("chests", {}).items()):
            for k, e in list(kv.items()):
                if not e.get("ok", True):
                    del kv[k]
                    n += 1
            if not kv:
                self.data["chests"].pop(sc, None)
        if n:
            self.dirty = True
            self.save()
        return n

    def clear(self):
        """清空全部记录。返回被清掉的条数。"""
        n = self.count()
        self.data = self._blank()
        self.dirty = True
        self.save()
        return n

    def prune(self, cap=MAX_CHESTS):
        """条数超上限 -> 按时间丢最旧的。返回丢掉条数 (只落盘一次)。"""
        rows = self.rows()
        if len(rows) <= cap:
            return 0
        for r in rows[:len(rows) - cap]:
            kv = self.data.get("chests", {}).get(str(r["scene"]), {})
            kv.pop(kk((r["a"], r["b"])), None)
        for sc in [s for s, kv in self.data.get("chests", {}).items() if not kv]:
            self.data["chests"].pop(sc, None)
        n = len(rows) - cap
        self.dirty = True
        self.save()
        return n

    # ---------------- 查询 ----------------
    def has(self, scene, K):
        sc = norm_scene(scene)
        if sc is None:
            return False
        try:
            return kk(K) in self.data.get("chests", {}).get(sc, {})
        except Exception:                       # 坏坐标当"没记过"
            return False

    def keys(self):
        """{(场景, a, b)} —— 喂给 mem5.chest_dead, 让它一启动就跳过这些箱子。"""
        out = set()
        for sc, kv in self.data.get("chests", {}).items():
            try:
                i = int(sc)
            except Exception:
                continue
            for k in kv:
                p = parse_kk(k)
                if p:
                    out.add((i, p[0], p[1]))
        return out

    def get(self, scene, K):
        sc = norm_scene(scene)
        if sc is None:
            return None
        try:
            return self.data.get("chests", {}).get(sc, {}).get(kk(K))
        except Exception:
            return None

    def oid_at(self, scene, K):
        """该箱子格记下的对象序号 (没有/老文件 -> 0)。"""
        e = self.get(scene, K)
        return _int_or_zero(e.get("oid")) if e else 0

    def count(self):
        """不同箱子的数量 (台账条数)。"""
        return sum(len(kv) for kv in self.data.get("chests", {}).values())

    def total_marks(self):
        """累计打开次数 (含重复)。"""
        return sum(int(e.get("n", 1))
                   for kv in self.data.get("chests", {}).values()
                   for e in kv.values())

    def rows(self):
        """给 GUI 的列表: 按 场景 -> K 排序, 每行带展开字段。"""
        out = []
        for sc, kv in self.data.get("chests", {}).items():
            try:
                si = int(sc)
            except Exception:
                si = 0
            for k, e in kv.items():
                p = parse_kk(k)
                if not p:
                    continue
                ok = bool(e.get("ok", True))
                out.append({
                    "scene": si, "a": p[0], "b": p[1],
                    "K": "(%d,%d)" % (p[0], p[1]),
                    "t": e.get("t", ""), "n": int(e.get("n", 1)),
                    "item": e.get("item", ""), "dn": int(e.get("dn", 0)),
                    "ok": ok, "state": "已开" if ok else "空/打不开",
                    "oid": _int_or_zero(e.get("oid")),
                })
        out.sort(key=lambda r: (r["scene"], r["a"], r["b"]))
        return out

    def stat(self):
        """一句话统计 (日志/GUI 标题用)。"""
        rows = self.rows()
        scenes = len({r["scene"] for r in rows})
        return {"n_chest": len(rows), "n_scene": scenes,
                "n_marks": self.total_marks(),
                "n_ok": sum(1 for r in rows if r["ok"]),
                "n_empty": sum(1 for r in rows if not r["ok"])}

    def summary(self):
        s = self.stat()
        if s["n_empty"]:
            return ("已记 %d 个箱子 (打开 %d / 空 %d, 累计跑 %d 次, %d 个场景)"
                    % (s["n_chest"], s["n_ok"], s["n_empty"], s["n_marks"],
                       s["n_scene"]))
        return "已记 %d 个箱子 (累计开 %d 次, 涉及 %d 个场景)" % (
            s["n_chest"], s["n_marks"], s["n_scene"])
