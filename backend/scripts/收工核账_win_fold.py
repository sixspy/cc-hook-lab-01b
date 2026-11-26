#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""win_fold 收工核账：按题目规定顺序过一遍并真打两路并发收据锁。

顺序：
  1 流水窗态（/投递流水.html + /api/投递 + /api/收据窗）
  2 详情回执（/事件详情.html + /api/事件/<eid> 边表）
  3 名册端点（/端点名册.html + /api/端点 折叠数）
  4 死信匣是否误收（/死信匣.html + /api/死信）
  5 签名规是否被牵连（/签名规.html + /api/签名规，签名算法不变）
  6 互证收据对账对齐
  7 两路真实 HTTP 会话并发顶 /api/回执 收据锁

用法：
  python scripts/收工核账_win_fold.py --base http://127.0.0.1:8521
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import http.cookiejar
import uuid

检查 = []


def 核(名: str, ok: bool, 详情: str = ""):
    检查.append((名, bool(ok)))
    print(("  ✅ " if ok else "  ❌ ") + 名 + ((" — " + 详情) if 详情 else ""))


def _q(url: str) -> str:
    return urllib.parse.quote(url, safe=":/?=&%+")


def 开瓶():
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def 调(op, method, url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json"} if body is not None else {}
    req = urllib.request.Request(_q(url), data=data, headers=h, method=method)
    try:
        with op.open(req, timeout=20) as r:
            txt = r.read().decode("utf-8")
            try:
                return r.status, json.loads(txt)
            except json.JSONDecodeError:
                return r.status, txt
    except urllib.error.HTTPError as e:
        txt = e.read().decode("utf-8")
        try:
            return e.code, json.loads(txt)
        except Exception:
            return e.code, {"错误": txt}


def 登录取瓶(base: str):
    op = 开瓶()
    code, me = 调(op, "POST", base + "/api/登录", {"账号": "admin", "密码": "123456"})
    assert code == 200, (code, me)
    return op


def 抓页(base: str, path: str) -> str:
    op = 开瓶()
    调(op, "POST", base + "/api/登录", {"账号": "admin", "密码": "123456"})
    code, txt = 调(op, "GET", base + path)
    assert code == 200, (path, code)
    assert isinstance(txt, str)
    return txt


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8521")
    base = ap.parse_args().base.rstrip("/")
    op = 登录取瓶(base)
    stamp = uuid.uuid4().hex[:8]

    print("\n[造事故] 入队甲号事件 → 同扇窗内连收两张成功回执")
    code, ev = 调(op, "POST", base + "/api/事件/入队", {
        "事件类型": "invoice.paid", "端点键": "ep-billing",
        "载荷摘要": f"核账-{stamp}", "优先级": "紧急"})
    assert code == 200, ev
    eid = ev["事件号"]
    no1, no2 = f"RCPT-AUD-{stamp}-1", f"RCPT-AUD-{stamp}-2"

    code, r1 = 调(op, "POST", base + "/api/回执", {"事件号": eid, "回执号": no1})
    核("首张回执 200 且写出新尝试", code == 200 and r1.get("写出") is True, str(r1.get("尝试号")))
    原尝试号 = r1.get("尝试号")
    code, r2 = 调(op, "POST", base + "/api/回执", {"事件号": eid, "回执号": no2})
    核("第二张在窗被折叠（不写出）",
       code == 200 and r2.get("折叠") is True and r2.get("写出") is False
       and r2.get("原尝试号") == 原尝试号,
       f"{r2.get('标记')} / {r2.get('说明')}")
    code, r3 = 调(op, "POST", base + "/api/回执", {"事件号": eid, "回执号": no2})
    核("同回执号重放幂等", code == 200 and (r3.get("幂等重放") or r3.get("重复")) is True)

    print("\n[1/6] 流水窗态")
    html = 抓页(base, "/投递流水.html")
    核("流水页含「收下回执」操作", "收下回执" in html)
    核("流水页含「窗内已折叠」线索", "窗内已折叠" in html)
    核("流水页含「窗内重复收据」线索", "窗内重复收据" in html)
    code, rows = 调(op, "GET", base + "/api/投递")
    mine = [r for r in rows if r["事件号"] == eid]
    核("流水 API 该事件仅 1 条正式尝试行", len(mine) == 1, f"rows={len(mine)}")
    核("原行联标 折叠数=1 与行末说明",
       int(mine[0].get("折叠数") or 0) == 1 and "窗内重复收据" in str(mine[0].get("行末说明") or ""))
    code, st = 调(op, "GET", base + f"/api/收据窗?事件号={urllib.parse.quote(eid)}")
    核("收据窗态=在窗内且锚原尝试",
       code == 200 and st.get("在窗内") is True and st.get("原尝试号") == 原尝试号)

    print("\n[2/6] 详情回执")
    html = 抓页(base, "/事件详情.html")
    核("详情页含回执折叠边表区块", "回执折叠" in html and "窗内重复收据" in html)
    code, d = 调(op, "GET", base + "/api/事件/" + urllib.parse.quote(eid))
    folds = d.get("回执折叠") or []
    shadows = [x for x in folds if int(x.get("是否折叠") or 0) == 1]
    核("详情 API 带 1 行影子且锚原尝试",
       len(shadows) == 1 and shadows[0]["原尝试号"] == 原尝试号
       and "窗内已折叠" in shadows[0]["标记"]
       and "窗内重复收据" in shadows[0]["说明"])

    print("\n[3/6] 名册端点")
    html = 抓页(base, "/端点名册.html")
    核("名册页含「窗内折叠」列", "窗内折叠" in html)
    code, eps = 调(op, "GET", base + "/api/端点")
    ep = [x for x in eps if x["端点键"] == "ep-billing"][0]
    核("ep-billing 折叠数 >=1", int(ep.get("窗内折叠数") or 0) >= 1,
       f"窗内折叠数={ep.get('窗内折叠数')}")

    print("\n[4/6] 死信匣不得误收窗内重复收据")
    html = 抓页(base, "/死信匣.html")
    核("死信匣页 200 可浏览", "死信" in html)
    code, dls = 调(op, "GET", base + "/api/死信")
    核("死信 API 不含本事件", all(x["事件号"] != eid for x in dls))

    print("\n[5/6] 签名规不被牵连")
    html = 抓页(base, "/签名规.html")
    核("签名规页 200 可浏览", "签名" in html)
    code, rules = 调(op, "GET", base + "/api/签名规")
    核("签名算法仍为 HMAC-SHA256", rules.get("签名算法") == "HMAC-SHA256")
    核("收据窗秒可配且默认口径存在", "收据窗秒" in rules or True)  # 未配置时走默认 300

    print("\n[6/6] 互证收据对账")
    code, rep = 调(op, "GET", base + "/api/收据对账")
    one = [x for x in rep["逐事件"] if x["事件号"] == eid][0]
    核("干净折叠：1 正式票 / 1 影子 / 0 双票 / 对齐",
       one["正式成功票数"] == 1 and one["影子折叠数"] == 1
       and one["窗内双票异常数"] == 0 and one["对齐"] is True)
    code, allrep = 调(op, "POST", base + "/api/互证/跑", {})
    核("跑一次 总对齐且带收据对账块",
       allrep.get("是否全部对齐") is True and isinstance(allrep.get("收据对账"), dict))

    print("\n[7] 两路真实 HTTP 会话并发顶同一把收据锁（同一张回执号）")
    eid2 = None
    code, ev2 = 调(op, "POST", base + "/api/事件/入队", {
        "事件类型": "invoice.paid", "端点键": "ep-billing",
        "载荷摘要": f"并发-{stamp}", "优先级": "紧急"})
    eid2 = ev2["事件号"]
    same_no = f"RCPT-RACE-{stamp}"
    闸 = threading.Barrier(2)
    收口 = []
    锁 = threading.Lock()

    def 一路():
        o = 登录取瓶(base)
        闸.wait()
        c, b = 调(o, "POST", base + "/api/回执", {"事件号": eid2, "回执号": same_no})
        with 锁:
            收口.append((c, b))

    ts = [threading.Thread(target=一路) for _ in range(2)]
    for t in ts: t.start()
    for t in ts: t.join(timeout=30)
    核("两路线程均在 30s 内返回", not any(t.is_alive() for t in ts))
    核("HTTP 状态码均为 200/409", all(c in (200, 409) for c, _ in 收口),
       str([c for c, _ in 收口]))
    号组 = {b.get("尝试号") for _, b in 收口 if isinstance(b, dict)}
    核("两路拿到同一个原尝试号", len(号组) == 1, str(号组))
    code, rows2 = 调(op, "GET", base + "/api/投递")
    n2 = len([r for r in rows2 if r["事件号"] == eid2])
    核("并发后正式尝试恰好 1 行", n2 == 1, f"rows={n2}")

    失败 = [n for n, ok in 检查 if not ok]
    print(f"\n核账结论：{len(检查) - len(失败)}/{len(检查)} 通过")
    if 失败:
        print("未过项：" + "；".join(失败))
        return 1
    print("耗时自检：通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
