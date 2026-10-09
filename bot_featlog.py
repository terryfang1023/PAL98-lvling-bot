# -*- coding: utf-8 -*-
"""feat_log.json —— 按「要素编号」记的要素台账 (战斗 / 以后可扩展别的)。

为什么需要它
------------
用户要能回答三个问题:
    · 这张地图上我一共打了哪些怪点?
    · 每个怪点打了几次?
    · 哪些怪点从来没打到过?
原来看不出这些 —— `hotspot.json` 只记"每场景最近一个锚点 K + 场景累计战斗数",
按 K 记账又太脆 (怪会游走, 同格可能叠东西)。本模块按 **要素编号** 记账。

编号从哪来 (游戏本身没有任何编号, 见 tools/build_scene_info.py 的说明)
--------------------------------------------------------------------
    id   = 事件对象的全局 1-based 序号 (lprgEventObject 下标+1, 1..5077),
           由 SSS.MKF 里的 rgScene[] 区间唯一确定 -> 重进场景/读档都不变。
    编号 = `场景号-序号` (如 `221-3813`), 见本模块的 code()。
    台账同时存 K 坐标与内容名 (敌人名/物品名), 既能按编号查也能按地图对。

文件格式 (人可读, 出问题直接删掉重来):
    {
      "version": 1,
      "feats": {
        "221": {                        # 场景号
          "3813": {                     # 要素编号(对象序号)
            "k": "mon",                 # 类型: mon=怪点 (预留 pick/gather)
            "n": 12,                    # 累计命中次数
            "t": "2026-10-08 12:40:01", # 最近一次时间
            "K": [-33, 94],             # 格子坐标 (可读性)
            "nm": "小毒蝎×3 / 五毒巨蝎"  # 内容名 (敌人名/物品名)
          }
        }
      }
    }

台账是"跑出来的事实": 不 import pal_bot (GUI 要直接读它), 任何异常都不外抛。
"""
import os
import time

BASE = os.path.dirname(os.path.abspath(__file__))
FEATLOG_PATH = os.path.join(BASE, "feat_log.json")

VERSION = 1
MAX_FEATS = 8000            # 台账上限护栏: 超了丢最旧的 (全游戏怪点 ~2000)
KINDS = ("mon", "pick", "gather")


def default_path():
    """默认台账路径 (读模块级 FEATLOG_PATH, 测试可临时改它)。"""
    return FEATLOG_PATH


def code(scene, oid):
    """(场景号, 对象序号) -> 要素编号字符串, 如 "221-3813"。

    序号 4 位补零, 便于排序与肉眼对齐; 场景/序号非法时返回 ""。
    """
    try:
        return "%d-%04d" % (int(scene), int(oid))
    except Exception:
        return ""


def parse_code(s):
    """"221-3813" -> (221, 3813); 坏数据 -> None。"""
    try:
        a, b = str(s).split("-")
        return (int(a), int(b))
    except Exception:
        return None


def norm_scene(scene):
    """场景号 -> 台账里的字符串键 (坏值返回 None)。"""
    try:
        return str(int(scene))
    except Exception:
        return None


def norm_oid(oid):
    """对象序号 -> 台账里的字符串键 (坏值返回 None)。"""
    try:
        i = int(oid)
        return str(i) if i > 0 else None
    except Exception:
        return None


def _now_str():
    return time.strftime("%Y-%m-%d %H:%M:%S")


class FeatLog:
    """要素台账的内存视图 + 落盘。

    path 缺省 = default_path() (调用时刻读, 便于测试临时改 FEATLOG_PATH)。
    """

    def __init__(self, path=None):
        self.path = path or default_path()
        self.data = self._blank()
        self._load()
        self.dirty = False

    # ---------------- 读写 ----------------
    def _blank(self):
        return {"version": VERSION, "feats": {}}

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
        if not isinstance(d, dict) or not isinstance(d.get("feats"), dict):
            return
        clean = {}
        for sc, kv in d["feats"].items():
            sc2 = norm_scene(sc)
            if sc2 is None or not isinstance(kv, dict):
                continue
            for o, e in kv.items():
                o2 = norm_oid(o)
                if o2 is None or not isinstance(e, dict):
                    continue
                try:
                    n = max(1, int(e.get("n", 1)))
                except Exception:
                    n = 1
                kk_ = e.get("K")
                K = None
                if isinstance(kk_, (list, tuple)) and len(kk_) == 2:
                    try:
                        K = [int(kk_[0]), int(kk_[1])]
                    except Exception:
                        K = None
                kind = str(e.get("k") or "mon")
                if kind not in KINDS:
                    kind = "mon"
                clean.setdefault(sc2, {})[o2] = {
                    "k": kind, "n": n, "t": str(e.get("t") or ""),
                    "K": K, "nm": str(e.get("nm") or ""),
                }
        self.data = {"version": VERSION, "feats": clean}

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
    def mark(self, scene, oid, kind="mon", K=None, name="", when=None):
        """记一次要素命中。返回写入的条目 dict (参数非法返回 None)。

        同一个编号重复命中 -> 累加 n 并刷新时间 (K/名字跟着更新)。
        """
        sc = norm_scene(scene)
        o = norm_oid(oid)
        if sc is None or o is None:
            return None
        k = str(kind or "mon")
        if k not in KINDS:
            k = "mon"
        try:
            K2 = [int(K[0]), int(K[1])] if K is not None else None
        except Exception:
            K2 = None
        d = self.data.setdefault("feats", {}).setdefault(sc, {})
        e = d.get(o)
        if e is None:
            e = {"k": k, "n": 1, "t": when or _now_str(), "K": K2,
                 "nm": str(name or "")}
            d[o] = e
        else:
            e["n"] = int(e.get("n", 1)) + 1
            e["t"] = when or _now_str()
            if K2 is not None:
                e["K"] = K2
            if name:
                e["nm"] = str(name)
        self.dirty = True
        self.prune()
        self.save()
        return e

    def forget(self, scene, oid):
        """删掉一条 (误记时用)。返回是否删掉了。"""
        sc, o = norm_scene(scene), norm_oid(oid)
        if sc is None or o is None:
            return False
        d = self.data.get("feats", {}).get(sc, {})
        if o in d:
            del d[o]
            if not d:
                self.data["feats"].pop(sc, None)
            self.dirty = True
            self.save()
            return True
        return False

    def clear(self):
        """清空全部记录。返回被清掉的条数。"""
        n = self.count()
        self.data = self._blank()
        self.dirty = True
        self.save()
        return n

    def prune(self, cap=MAX_FEATS):
        """条数超上限 -> 按时间丢最旧的。返回丢掉条数 (只落盘一次)。"""
        rows = self.rows()
        if len(rows) <= cap:
            return 0
        for r in rows[:len(rows) - cap]:
            kv = self.data.get("feats", {}).get(str(r["scene"]), {})
            kv.pop(str(r["oid"]), None)
        for sc in [s for s, kv in self.data.get("feats", {}).items() if not kv]:
            self.data["feats"].pop(sc, None)
        n = len(rows) - cap
        self.dirty = True
        self.save()
        return n

    # ---------------- 查询 ----------------
    def has(self, scene, oid):
        sc, o = norm_scene(scene), norm_oid(oid)
        if sc is None or o is None:
            return False
        return o in self.data.get("feats", {}).get(sc, {})

    def get(self, scene, oid):
        sc, o = norm_scene(scene), norm_oid(oid)
        if sc is None or o is None:
            return None
        return self.data.get("feats", {}).get(sc, {}).get(o)

    def hits(self, scene, oid):
        """某编号的累计命中次数 (没记过返回 0)。"""
        e = self.get(scene, oid)
        return int(e.get("n", 0)) if e else 0

    def keys(self):
        """{(场景, 序号)} —— 供报告/统计使用。"""
        out = set()
        for sc, kv in self.data.get("feats", {}).items():
            try:
                i = int(sc)
            except Exception:
                continue
            for o in kv:
                try:
                    out.add((i, int(o)))
                except Exception:
                    continue
        return out

    def count(self):
        """不同要素编号的数量 (台账条数)。"""
        return sum(len(kv) for kv in self.data.get("feats", {}).values())

    def total_marks(self):
        """累计命中次数 (含重复)。"""
        return sum(int(e.get("n", 1))
                   for kv in self.data.get("feats", {}).values()
                   for e in kv.values())

    def rows(self):
        """给 GUI/报告的列表: 按 场景 -> 序号 排序, 每行带展开字段。"""
        out = []
        for sc, kv in self.data.get("feats", {}).items():
            try:
                si = int(sc)
            except Exception:
                si = 0
            for o, e in kv.items():
                try:
                    oi = int(o)
                except Exception:
                    continue
                K = e.get("K")
                out.append({
                    "scene": si, "oid": oi, "code": code(si, oi),
                    "kind": e.get("k", "mon"), "n": int(e.get("n", 1)),
                    "t": e.get("t", ""), "K": K,
                    "Ktxt": "(%d,%d)" % (K[0], K[1]) if K else "",
                    "name": e.get("nm", ""),
                    "kind_txt": {"mon": "怪点", "pick": "拾取",
                                 "gather": "采集"}.get(e.get("k", "mon"),
                                                       "要素"),
                })
        out.sort(key=lambda r: (r["scene"], r["oid"]))
        return out

    def stat(self):
        """一句话统计 (日志/GUI 标题用)。"""
        rows = self.rows()
        return {"n_feat": len(rows),
                "n_scene": len({r["scene"] for r in rows}),
                "n_marks": self.total_marks(),
                "n_mon": sum(1 for r in rows if r["kind"] == "mon")}

    def summary(self):
        s = self.stat()
        return ("已记 %d 个要素 (怪点 %d, 累计命中 %d 次, %d 个场景)"
                % (s["n_feat"], s["n_mon"], s["n_marks"], s["n_scene"]))
