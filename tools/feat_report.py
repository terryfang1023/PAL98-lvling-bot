# -*- coding: utf-8 -*-
"""feat_report.py — 要素覆盖率报告: 每张地图的怪点/拾取点打了没有、打了几次。

把三份东西合起来看:
    scene_info.json   来自游戏数据的**全量清单**(每个要素的编号/名字/坐标)
    feat_log.json     机器人跑出来的**战斗台账**(按编号记: 打了几次/最近时间)
    chest_log.json    机器人跑出来的**拾取台账**(按格子记: 开出什么/几次)

于是能回答:
    · 这张地图上一共有几个怪点? 打过几个? 还差哪几个没打过?
    · 每个怪点叫什么(土蜘蛛/小毒蝎×3…)、坐标在哪、打过几次?
    · 拾取点开出了什么(傀儡虫/吸金鼎/捆仙绳…)?

编号 = `场景号-对象序号` (如 221-3813):
    游戏引擎本身**没有**任何点位编号/名字, 这个编号是
    tools/build_scene_info.py 从**游戏原始数据 (SSS.MKF)** 派生的稳定主键:
    id = 事件对象的全局序号 (1..5077, 由 rgScene[] 区间唯一确定, 重进场景/读档不变)。

本工具**只读**运行目录里的台账/清单, 不连游戏进程、不按键、不碰任何运行标志
(mem_stop.flag / bot_config.json 一律不碰)。只写出报告文件:
    feat_report.txt    人看的报告
    feat_report.json   机读的明细

用法:
    python tools/feat_report.py                # 全图汇总 + 未打过的怪点清单
    python tools/feat_report.py --scene 221    # 只细看某个场景
    python tools/feat_report.py --miss         # 只列没打过的(全图)
    python tools/feat_report.py --out D:/tmp   # 报告写到别的目录
"""
import argparse
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

import bot_featlog                                             # noqa: E402
import bot_chestlog                                            # noqa: E402


def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def kkey(K):
    try:
        return (int(K[0]), int(K[1]))
    except Exception:
        return None


def build_index(feat_rows, chest_rows):
    """台账 -> 索引。

    by_oid  {(场景,序号): 次数}          —— 要素台账(战斗/拾取都按编号)
    by_coid {(场景,序号): (次数, 名字)}  —— 拾取台账里带 oid 的记录
    by_Kmon {(场景,a,b): 次数}           —— 老要素台账只有坐标时的兜底
    by_K    {(场景,a,b): (次数, 名字)}   —— 拾取台账按坐标兜底
    """
    by_oid, by_K, by_coid, by_Kmon = {}, {}, {}, {}
    for r in feat_rows:
        by_oid[(r["scene"], r["oid"])] = r["n"]
        k = r.get("K")
        if isinstance(k, (list, tuple)) and len(k) == 2:
            key = (r["scene"], int(k[0]), int(k[1]))
            by_Kmon[key] = max(by_Kmon.get(key, 0), int(r["n"]))
    for r in chest_rows:
        by_K[(r["scene"], r["a"], r["b"])] = (r["n"], r.get("item") or "")
        o = int(r.get("oid") or 0)
        if o:
            s, t = by_coid.get((r["scene"], o), (0, ""))
            by_coid[(r["scene"], o)] = (s + int(r["n"]), r.get("item") or t)
    return by_oid, by_K, by_coid, by_Kmon


def scene_rows(sc, info, by_oid, by_K, by_coid=None, by_Kmon=None):
    """把一个场景的要素 + 台账合并成报告行。"""
    by_coid = by_coid or {}
    by_Kmon = by_Kmon or {}
    out = []
    for m in info.get("monsters", []):
        oid = m.get("id")
        kk = kkey(m.get("K"))
        n = by_oid.get((sc, oid), 0) if oid else 0
        src = "编号" if n else ""
        if not n and kk:                      # 老台账按 K 记的 -> 退回坐标匹配
            n = by_Kmon.get((sc, kk[0], kk[1]), 0)
            src = "坐标" if n else ""
        out.append({"kind": "mon", "scene": sc, "oid": oid,
                    "code": bot_featlog.code(sc, oid) if oid else "",
                    "K": list(kk) if kk else None, "n": int(n), "src": src,
                    "name": m.get("ename") or "?",
                    "team": m.get("team"), "spr": m.get("spr")})
    for c in info.get("chests", []):
        oid = c.get("id")
        kk = kkey(c.get("K"))
        # ★ 优先按编号对账 (拾取台账 v19.0 起带 oid), 再退回坐标
        n, item, src = 0, "", ""
        if oid and (sc, oid) in by_coid:
            n, item = by_coid[(sc, oid)]
            src = "编号"
        if not n and kk:
            n2, item2 = by_K.get((sc, kk[0], kk[1]), (0, ""))
            if n2:
                n, item, src = n2, item2, "坐标"
        if not n:
            n = by_oid.get((sc, oid), 0) if oid else 0
            src = "编号" if n else ""
        out.append({"kind": "pick", "scene": sc, "oid": oid,
                    "code": bot_featlog.code(sc, oid) if oid else "",
                    "K": list(kk) if kk else None, "n": int(n), "src": src,
                    "name": (item or c.get("iname") or "?"),
                    "item": c.get("item"), "spr": c.get("spr")})
    return out


def fmt_K(K):
    return "(%d,%d)" % (K[0], K[1]) if K else "(?,?)"


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--scene", type=int, default=0, help="只细看某个场景")
    ap.add_argument("--miss", action="store_true", help="只列没打过的怪点")
    ap.add_argument("--out", default=BASE, help="报告输出目录 (默认运行目录)")
    ap.add_argument("--quiet", action="store_true", help="不打印报告正文")
    a = ap.parse_args()

    sc_info = load_json(os.path.join(BASE, "scene_info.json"), {})
    if not sc_info:
        print("★ 读不到 scene_info.json —— 跑一次 "
              "`python tools/build_scene_info.py` 生成 (离线读 SSS.MKF, "
              "不需要游戏开着)")
        return 2
    fl = bot_featlog.FeatLog()
    cl = bot_chestlog.ChestLog()
    by_oid, by_K, by_coid, by_Kmon = build_index(fl.rows(), cl.rows())

    n_old = sum(1 for v in sc_info.values()
                for m in v.get("monsters", []) if not m.get("id"))
    lines = []
    lines.append("要素覆盖率报告  (生成时间 %s)"
                 % __import__("time").strftime("%Y-%m-%d %H:%M:%S"))
    lines.append("清单: %d 个场景 | 台账: 要素 %d 条 / 拾取 %d 条"
                 % (len(sc_info), fl.count(), cl.count()))
    if n_old:
        lines.append("⚠ 清单里有 %d 个怪点没有编号 -> 请重新生成 scene_info.json"
                     " (python tools/build_scene_info.py)" % n_old)
    lines.append("")

    all_rows, miss_rows = [], []
    scenes = sorted(int(s) for s in sc_info if str(s).isdigit())
    if a.scene:
        scenes = [s for s in scenes if s == a.scene]
        if not scenes:
            print("★ 清单里没有场景 %d" % a.scene)
            return 2
    tot_m = tot_m_hit = tot_p = tot_p_hit = 0
    for sc in scenes:
        rows = scene_rows(sc, sc_info[str(sc)], by_oid, by_K, by_coid, by_Kmon)
        mons = [r for r in rows if r["kind"] == "mon"]
        picks = [r for r in rows if r["kind"] == "pick"]
        hit_m = [r for r in mons if r["n"] > 0]
        hit_p = [r for r in picks if r["n"] > 0]
        tot_m += len(mons); tot_m_hit += len(hit_m)
        tot_p += len(picks); tot_p_hit += len(hit_p)
        all_rows += rows
        miss_rows += [r for r in mons if r["n"] == 0]
        if not rows:
            continue
        if not a.miss:
            pct = (100.0 * len(hit_m) / len(mons)) if mons else 0.0
            lines.append("场景 %-3d (地图%-3s) 怪点 %2d/%2d 打过 (%.0f%%)"
                         " | 拾取 %2d/%2d 开过"
                         % (sc, sc_info[str(sc)].get("map"), len(hit_m),
                            len(mons), pct, len(hit_p), len(picks)))
            for r in mons:
                mark = "✔" if r["n"] else "·"
                lines.append("   %s %-10s K%-11s 打了 %-4d 次  %s"
                             % (mark, r["code"] or "(无编号)", fmt_K(r["K"]),
                                r["n"], r["name"]))
            for r in picks:
                mark = "✔" if r["n"] else "·"
                lines.append("   %s %-10s K%-11s 开过 %-4d 次  %s"
                             % (mark, r["code"] or "(无编号)", fmt_K(r["K"]),
                                r["n"], r["name"]))
            lines.append("")

    lines.append("=" * 62)
    lines.append("全图合计: 怪点打过 %d/%d (%.1f%%) | 拾取点开过 %d/%d (%.1f%%)"
                 % (tot_m_hit, tot_m,
                    100.0 * tot_m_hit / tot_m if tot_m else 0.0,
                    tot_p_hit, tot_p,
                    100.0 * tot_p_hit / tot_p if tot_p else 0.0))
    lines.append("从未打过的怪点: %d 个" % len(miss_rows))
    from collections import Counter
    per_scene_miss = Counter(r["scene"] for r in miss_rows)
    if per_scene_miss:
        top = " ".join("场景%d:%d个" % (s, n)
                       for s, n in per_scene_miss.most_common(12))
        lines.append("   缺口最多的场景: %s" % top)
    lines.append("")
    lines.append("-- 没打过的怪点清单 --")
    for r in miss_rows:
        lines.append("   场景%-3d %-10s K%-11s %s"
                     % (r["scene"], r["code"] or "(无编号)", fmt_K(r["K"]),
                        r["name"]))

    txt = "\n".join(lines)
    if not a.quiet:
        print(txt)
    out_dir = a.out
    try:
        with open(os.path.join(out_dir, "feat_report.txt"), "w",
                  encoding="utf-8") as f:
            f.write(txt + "\n")
        with open(os.path.join(out_dir, "feat_report.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"totals": {"mon": tot_m, "mon_hit": tot_m_hit,
                                  "pick": tot_p, "pick_hit": tot_p_hit},
                       "miss_mon": miss_rows, "rows": all_rows},
                      f, ensure_ascii=False, separators=(",", ":"))
        print("\n报告已写出: %s" % os.path.join(out_dir, "feat_report.txt"))
    except Exception as e:
        print("★ 报告写盘失败: %s" % e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
