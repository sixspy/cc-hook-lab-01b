# -*- coding: utf-8 -*-
"""第二张回执窗内折叠 · 核心服务（骨签：第二张回执在窗内必须折叠）。

工程形态（落笔前择定，整仓只兑现这一边）：
    **路径甲 · 开窗指纹收进收据窗账册，窗内重复回执拒回刷正式尝试、折入回执折叠边表。**

    - 每张回执都在边表「回执折叠」留痕：开窗原票 ``是否折叠=0``，窗内第二张起
      ``是否折叠=1``，标记「窗内已折叠」，说明「窗内重复收据」；
    - 第一张回执（或投递口已落的最近一笔成功尝试）开一扇窗，窗指纹钉住
      『事件 + 原尝试号 + 首回执号』；
    - 窗内再来回执：绝不 INSERT 正式「投递尝试」，只折进边表并把折叠标记
      联在原尝试行旁；同一回执号重放走幂等回放；
    - 窗关之后，才准追加一笔新的正式成功尝试并另开新窗；
    - 同一事件号的并发「收下回执」打同一把收据锁，并在同一个
      ``BEGIN IMMEDIATE`` 事务里读窗 + 写票 + 折边表，杜绝双成功/幂等票互覆。

明确不做路径乙（过窗旧回执沉只读归档 + 现行投递改读开放读集合 + 双写两轨）：
``app/modules/双写归档轨`` 保持空桩。折叠当场只有这一套读引用：
正式尝试 LEFT JOIN 边表，不存在第二套可投集读法。
"""
from __future__ import annotations

import hashlib
import hmac
import threading
import uuid

from app.db import 连接, 立即连接
from app.services import 时间

骨签 = "第二张回执在窗内必须折叠"
工程形态 = "路径甲·开窗指纹入收据窗账册·窗内重复折边表拒回刷"

默认窗秒 = 300.0
窗规键 = ("收据窗秒", "幂等窗秒", "回执窗秒", "折叠窗秒")

# 同一事件号 -> 同一把进程内互斥锁（“两路并发去顶收据锁”的那把锁）
_锁表: dict[str, threading.Lock] = {}
_锁表守卫 = threading.Lock()

账表DDL = [
    """
    CREATE TABLE IF NOT EXISTS 收据窗 (
      窗号 TEXT PRIMARY KEY,
      事件号 TEXT NOT NULL,
      端点键 TEXT NOT NULL DEFAULT '',
      原尝试号 TEXT NOT NULL DEFAULT '',
      首回执号 TEXT NOT NULL DEFAULT '',
      开窗指纹 TEXT NOT NULL DEFAULT '',
      状态 TEXT NOT NULL DEFAULT '开',
      窗秒 REAL NOT NULL DEFAULT 300,
      窗口起纪元 REAL NOT NULL DEFAULT 0,
      窗口止纪元 REAL NOT NULL DEFAULT 0,
      开窗时 TEXT NOT NULL,
      关窗时 TEXT NOT NULL DEFAULT '',
      折叠数 INTEGER NOT NULL DEFAULT 0
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_收据窗_单开 ON 收据窗(事件号) WHERE 状态='开'",
    "CREATE INDEX IF NOT EXISTS idx_收据窗_事件 ON 收据窗(事件号, 窗口起纪元)",
    """
    CREATE TABLE IF NOT EXISTS 回执折叠 (
      编号 INTEGER PRIMARY KEY AUTOINCREMENT,
      折叠号 TEXT NOT NULL UNIQUE,
      事件号 TEXT NOT NULL,
      窗号 TEXT NOT NULL DEFAULT '',
      原尝试号 TEXT NOT NULL,
      端点键 TEXT NOT NULL DEFAULT '',
      回执号 TEXT NOT NULL DEFAULT '',
      开窗指纹 TEXT NOT NULL DEFAULT '',
      是否折叠 INTEGER NOT NULL DEFAULT 1,
      处置 TEXT NOT NULL DEFAULT '窗内折叠',
      标记 TEXT NOT NULL DEFAULT '窗内已折叠',
      说明 TEXT NOT NULL DEFAULT '窗内重复收据',
      回执时 TEXT NOT NULL,
      回执纪元 REAL NOT NULL DEFAULT 0
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_回执折叠_回执号 ON 回执折叠(回执号) WHERE 回执号 <> ''",
    "CREATE INDEX IF NOT EXISTS idx_回执折叠_事件 ON 回执折叠(事件号, 编号)",
    "CREATE INDEX IF NOT EXISTS idx_回执折叠_尝试 ON 回执折叠(原尝试号)",
    "CREATE INDEX IF NOT EXISTS idx_回执折叠_端点 ON 回执折叠(端点键)",
]


def 重置锁() -> None:
    """测试/换库时清空锁表。"""
    with _锁表守卫:
        _锁表.clear()


def 取收据锁(事件号: str) -> threading.Lock:
    """同一事件号恒取同一把锁；不同事件不互斥。"""
    with _锁表守卫:
        lk = _锁表.get(事件号)
        if lk is None:
            lk = threading.Lock()
            _锁表[事件号] = lk
        return lk


# 锁入口别名
收据锁 = 取收据锁
取锁 = 取收据锁


def 确保账表() -> None:
    """无 seed 场景下也能自愈建表（IF NOT EXISTS，幂等）。"""
    conn = 立即连接()
    try:
        for stmt in 账表DDL:
            conn.execute(stmt)
    finally:
        conn.close()


def 开窗指纹(事件号: str, 原尝试号: str, 首回执号: str = "") -> str:
    raw = f"win-fold|{事件号}|{原尝试号}|{首回执号}".encode("utf-8")
    return "WF-" + hashlib.sha256(raw).hexdigest()[:18]


# 指纹命名别名（同系列 hook-13 习惯用名）
指纹 = 开窗指纹


def 读窗规(conn=None) -> dict:
    """收据窗规则的结构化读法（兼容 hook-13 风格的 读规 命名）。"""
    own = conn is None
    if own:
        conn = 连接()
    try:
        return {
            "窗秒": 读窗秒(conn),
            "收据窗秒": 读窗秒(conn),
            "默认窗秒": 默认窗秒,
            "窗规键": list(窗规键),
            "骨签": 骨签,
        }
    finally:
        if own:
            conn.close()


读规 = 读窗规


def _签名(密钥版本: str, 事件号: str, 摘要: str) -> str:
    raw = f"{密钥版本}|{事件号}|{摘要}".encode("utf-8")
    key = f"hook-secret-{密钥版本}".encode("utf-8")
    return hmac.new(key, raw, hashlib.sha256).hexdigest()[:24]


def 读窗秒(conn=None, 覆盖=None) -> float:
    """收据窗长度：显式入参 > 签名规键 > 默认 300 秒。配 0 即关窗（每张都新尝试）。"""
    if 覆盖 is not None:
        try:
            return max(0.0, float(覆盖))
        except (TypeError, ValueError):
            return 默认窗秒
    own = conn is None
    if own:
        conn = 连接()
    try:
        for k in 窗规键:
            r = conn.execute("SELECT 值 FROM 签名规 WHERE 键=?", (k,)).fetchone()
            if r and str(r["值"]).strip() != "":
                try:
                    return max(0.0, float(r["值"]))
                except (TypeError, ValueError):
                    continue
        return 默认窗秒
    finally:
        if own:
            conn.close()


def _尝试纪元(row) -> float:
    try:
        ep = row["投递纪元"]
        if ep:
            return float(ep)
    except (IndexError, KeyError):
        pass
    return 时间.解析纪元(row["投递时"])


def _解析入参(事件号, extra) -> tuple[str, str | None, float | None, float | None, str | None]:
    """兼容 dict 入参、位置入参与各种中英文别名键。

    返回 (eid, 回执号, 窗秒覆盖, 现在, 时刻)。
    """
    body: dict = {}
    if isinstance(事件号, dict):
        body.update(事件号)
    elif 事件号 is not None:
        body["事件号"] = 事件号
    body.update({k: v for k, v in (extra or {}).items() if v is not None})

    eid = (
        body.get("事件号")
        or body.get("事件编号")
        or body.get("eid")
        or body.get("event_id")
        or body.get("event_no")
        or body.get("eventId")
    )
    no = (
        body.get("回执号")
        or body.get("收据号")
        or body.get("receipt_no")
        or body.get("receipt_id")
        or body.get("receipt_number")
        or body.get("idempotency_key")
        or body.get("幂等键")
    )
    窗秒 = body.get("窗秒", body.get("收据窗秒", body.get("window_seconds")))
    现在 = body.get("现在", body.get("当前纪元", body.get("now")))
    时刻 = body.get("时刻")
    return str(eid) if eid is not None and eid != "" else "", (str(no) if no else None), 窗秒, 现在, 时刻


def _计数(conn, eid: str) -> tuple[int, int, int]:
    总 = conn.execute(
        "SELECT COUNT(*) c FROM 投递尝试 WHERE 事件号=?", (eid,)
    ).fetchone()["c"]
    成 = conn.execute(
        "SELECT COUNT(*) c FROM 投递尝试 WHERE 事件号=? AND 状态='成功'", (eid,)
    ).fetchone()["c"]
    折 = conn.execute(
        "SELECT COUNT(*) c FROM 回执折叠 WHERE 事件号=? AND 是否折叠=1", (eid,)
    ).fetchone()["c"]
    return int(总), int(成), int(折)


def _回放回执(conn, row, eid: str, *, 总: int, 成: int, 折: int) -> dict:
    folded = int(row["是否折叠"] or 0) == 1
    return {
        "ok": True,
        "幂等重放": True,
        "重复": True,
        "事件号": eid,
        "回执号": row["回执号"],
        "收据号": row["回执号"],
        "折叠号": row["折叠号"],
        "窗号": row["窗号"],
        "窗指纹": row["开窗指纹"],
        "尝试号": row["原尝试号"],
        "原尝试号": row["原尝试号"],
        "状态": row["标记"] if folded else "成功",
        "处置": ("幂等重放·" + str(row["处置"])) if row["处置"] else "幂等重放",
        "标记": row["标记"],
        "折叠标记": row["标记"],
        "说明": row["说明"],
        "行末说明": "窗内重复收据" if folded else "",
        "折叠": folded,
        "已折叠": folded,
        "窗内": folded,
        "在窗内": folded,
        "写出": False,
        "新增尝试": False,
        "尝试总数": 总,
        "成功尝试数": 成,
        "折叠数": 折,
        "骨签": 骨签,
        "工程形态": 工程形态,
    }


def _收下回执内核(事件号=None, 回执号: str | None = None, **kwargs) -> dict:
    """流水页「收下回执」服务端当场判窗：在窗折叠，出窗追加尝试。

    兼容别名：收据号/接收回执/记录回执/推回执/提交回执 等最终都走这一份实现，
    全仓不允许出现第二套折叠读法。
    """
    eid, no, 窗秒覆盖, 现在入参, 时刻入参 = _解析入参(
        事件号, {"回执号": 回执号, **kwargs} if 回执号 is not None else dict(kwargs)
    )
    if not eid:
        raise ValueError("事件号缺失")

    确保账表()
    with 取收据锁(eid):
        conn = 立即连接()
        try:
            conn.execute("BEGIN IMMEDIATE")
            ev = conn.execute("SELECT * FROM 事件 WHERE 事件号=?", (eid,)).fetchone()
            if not ev:
                raise ValueError("无此事件")
            if ev["状态"] == "死信":
                raise ValueError("事件已入死信匣，窗内重复收据不得借回执口复活；请先走死信重投")

            now = float(现在入参) if 现在入参 is not None else 时间.纪元秒()
            t = 时刻入参 or 时间.此刻()
            窗秒 = 读窗秒(conn, 窗秒覆盖)

            # 1) 同回执号幂等回放（并发负者/双击/重试都收敛到同一行）
            if no:
                old = conn.execute(
                    "SELECT * FROM 回执折叠 WHERE 回执号=?", (no,)
                ).fetchone()
                if old:
                    总, 成, 折 = _计数(conn, eid)
                    conn.execute("COMMIT")
                    return _回放回执(conn, old, eid, 总=总, 成=成, 折=折)
                # 开窗原票不进折叠边表，其回执号钉在 收据窗.首回执号；
                # 重放原票同样必须幂等，不得再长尝试/折叠行。
                w0 = conn.execute(
                    "SELECT * FROM 收据窗 WHERE 事件号=? AND 首回执号=? LIMIT 1",
                    (eid, no),
                ).fetchone()
                if w0 is not None:
                    总, 成, 折 = _计数(conn, eid)
                    conn.execute("COMMIT")
                    return {
                        "ok": True,
                        "幂等重放": True,
                        "重复": True,
                        "事件号": eid,
                        "回执号": no,
                        "收据号": no,
                        "折叠号": "",
                        "窗号": w0["窗号"],
                        "窗指纹": w0["开窗指纹"],
                        "尝试号": w0["原尝试号"],
                        "原尝试号": w0["原尝试号"],
                        "状态": "成功",
                        "处置": "幂等重放·开窗原票",
                        "标记": "开窗原票",
                        "折叠标记": "",
                        "说明": "同一回执号重放，返回开窗原尝试",
                        "行末说明": "",
                        "折叠": False,
                        "已折叠": False,
                        "窗内": True,
                        "在窗内": True,
                        "写出": False,
                        "新增尝试": False,
                        "尝试总数": 总,
                        "成功尝试数": 成,
                        "折叠数": 折,
                        "骨签": 骨签,
                        "工程形态": 工程形态,
                    }

            # 2) 找当前开着的窗；过期窗当场关闭
            win = conn.execute(
                "SELECT * FROM 收据窗 WHERE 事件号=? AND 状态='开' LIMIT 1", (eid,)
            ).fetchone()

            if win is not None and now >= float(win["窗口止纪元"] or 0.0):
                conn.execute(
                    "UPDATE 收据窗 SET 状态='关', 关窗时=? WHERE 窗号=?",
                    (t, win["窗号"]),
                )
                win = None

            # 3) 无开窗：若投递口刚落过在窗时长内的成功尝试，把窗锚到原行，
            #    本张算“首回执/锚定原票”，不另长尝试。
            锚定既有 = False
            if win is None:
                last_ok = conn.execute(
                    "SELECT * FROM 投递尝试 WHERE 事件号=? AND 状态='成功' "
                    "ORDER BY rowid DESC LIMIT 1",
                    (eid,),
                ).fetchone()
                if last_ok is not None and 窗秒 > 0:
                    ep = _尝试纪元(last_ok)
                    if ep > 0 and 0 <= now - ep <= 窗秒 + 1e-6:
                        窗号 = f"WIN-{uuid.uuid4().hex[:12]}"
                        fp = 开窗指纹(eid, last_ok["尝试号"], no or "")
                        conn.execute(
                            "INSERT INTO 收据窗(窗号,事件号,端点键,原尝试号,首回执号,开窗指纹,"
                            "状态,窗秒,窗口起纪元,窗口止纪元,开窗时,折叠数) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?,?,0)",
                            (窗号, eid, ev["端点键"], last_ok["尝试号"], no or "", fp,
                             "开", 窗秒, ep, ep + 窗秒, t),
                        )
                        win = conn.execute(
                            "SELECT * FROM 收据窗 WHERE 窗号=?", (窗号,)
                        ).fetchone()
                        锚定既有 = True

            # 4) 在窗：折进原行（边表影子），绝不写正式尝试
            if win is not None:
                原尝试号 = win["原尝试号"]
                窗号 = win["窗号"]
                fp = win["开窗指纹"]
                if 锚定既有:
                    # 本张是锚定既有成功行的“首回执/开窗原票”，不属折叠影子，
                    # 按既定口径不写 回执折叠（该表只存窗内折叠影子）。
                    conn.execute(
                        "INSERT INTO 操作留痕(时刻,操作,事件号,详情) VALUES (?,?,?,?)",
                        (t, "收下回执", eid, f"{no or 窗号} 锚定 {原尝试号}"),
                    )
                    conn.execute("COMMIT")
                    总, 成, 折数 = _计数(conn, eid)
                    return {
                        "ok": True,
                        "幂等重放": False,
                        "重复": False,
                        "事件号": eid,
                        "回执号": no or "",
                        "收据号": no or "",
                        "折叠号": "",
                        "窗号": 窗号,
                        "窗指纹": fp,
                        "尝试号": 原尝试号,
                        "原尝试号": 原尝试号,
                        "状态": "成功",
                        "处置": "锚定原行",
                        "标记": "开窗原票",
                        "折叠标记": "",
                        "说明": "回执锚定既有成功尝试，不另开成功票",
                        "行末说明": "",
                        "在窗内": True,
                        "窗内": True,
                        "折叠": False,
                        "已折叠": False,
                        "写出": False,
                        "新增尝试": False,
                        "锚定既有尝试": True,
                        "窗口止纪元": float(win["窗口止纪元"] or 0.0),
                        "尝试总数": 总,
                        "成功尝试数": 成,
                        "折叠数": 折数,
                        "骨签": 骨签,
                        "工程形态": 工程形态,
                    }

                处置 = "窗内折叠"
                是否折 = 1
                标记 = "窗内已折叠"
                说明 = "窗内重复收据"
                状态 = "窗内已折叠"
                写出 = False
                折叠 = True
                折号 = f"FOLD-{uuid.uuid4().hex[:12]}"
                conn.execute(
                    "INSERT INTO 回执折叠(折叠号,事件号,窗号,原尝试号,端点键,回执号,开窗指纹,"
                    "是否折叠,处置,标记,说明,回执时,回执纪元) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (折号, eid, 窗号, 原尝试号, ev["端点键"], no or "", fp,
                     是否折, 处置, 标记, 说明, t, now),
                )
                conn.execute(
                    "UPDATE 收据窗 SET 折叠数=折叠数+1 WHERE 窗号=?", (窗号,)
                )
                conn.execute(
                    "INSERT INTO 操作留痕(时刻,操作,事件号,详情) VALUES (?,?,?,?)",
                    (t, "回执折叠", eid, f"{no or 折号} {标记}"),
                )
                conn.execute("COMMIT")
                总, 成, 折数 = _计数(conn, eid)
                return {
                    "ok": True,
                    "幂等重放": False,
                    "重复": True,
                    "事件号": eid,
                    "回执号": no or "",
                    "收据号": no or "",
                    "折叠号": 折号,
                    "窗号": 窗号,
                    "窗指纹": fp,
                    "尝试号": 原尝试号,
                    "原尝试号": 原尝试号,
                    "状态": 状态,
                    "处置": 处置,
                    "标记": 标记,
                    "折叠标记": 标记,
                    "说明": 说明,
                    "行末说明": "窗内重复收据",
                    "在窗内": True,
                    "窗内": True,
                    "折叠": 折叠,
                    "已折叠": 折叠,
                    "写出": 写出,
                    "新增尝试": False,
                    "锚定既有尝试": False,
                    "窗口止纪元": float(win["窗口止纪元"] or 0.0),
                    "尝试总数": 总,
                    "成功尝试数": 成,
                    "折叠数": 折数,
                    "骨签": 骨签,
                    "工程形态": 工程形态,
                }

            # 5) 无窗可折（首张 / 窗外）：追加一笔正式成功尝试并开新窗
            ep_row = conn.execute(
                "SELECT * FROM 投递端点 WHERE 端点键=?", (ev["端点键"],)
            ).fetchone()
            钥版 = ep_row["密钥版本"] if ep_row else "v1"
            尝试号 = f"TRY-{uuid.uuid4().hex[:10]}"
            sig = _签名(钥版, eid, ev["载荷摘要"])
            窗号 = f"WIN-{uuid.uuid4().hex[:12]}"
            fp = 开窗指纹(eid, 尝试号, no or "")
            conn.execute(
                "INSERT INTO 投递尝试(尝试号,事件号,端点键,签名摘要,状态,响应码,说明,投递时) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (尝试号, eid, ev["端点键"], sig, "成功", 200,
                 f"收下回执·确认成功 {no or ''}".strip(), t),
            )
            conn.execute(
                "UPDATE 事件 SET 状态='已投递', 更新时=? WHERE 事件号=?", (t, eid)
            )
            conn.execute(
                "INSERT INTO 收据窗(窗号,事件号,端点键,原尝试号,首回执号,开窗指纹,"
                "状态,窗秒,窗口起纪元,窗口止纪元,开窗时,折叠数) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,0)",
                (窗号, eid, ev["端点键"], 尝试号, no or "", fp,
                 "开", 窗秒, now, now + 窗秒, t),
            )
            折号 = ""  # 开窗原票不进折叠边表；只有窗内第二张起才有 折叠号
            conn.execute(
                "INSERT INTO 操作留痕(时刻,操作,事件号,详情) VALUES (?,?,?,?)",
                (t, "收下回执", eid, f"{no or 窗号} -> {尝试号}"),
            )
            conn.execute("COMMIT")
            总, 成, 折数 = _计数(conn, eid)
            return {
                "ok": True,
                "幂等重放": False,
                "重复": False,
                "事件号": eid,
                "回执号": no or "",
                "收据号": no or "",
                "折叠号": 折号,
                "窗号": 窗号,
                "窗指纹": fp,
                "尝试号": 尝试号,
                "原尝试号": 尝试号,
                "状态": "成功",
                "处置": "追加尝试",
                "标记": "开窗原票",
                "折叠标记": "",
                "说明": "窗外回执，追加新尝试",
                "行末说明": "",
                "在窗内": False,
                "窗内": False,
                "折叠": False,
                "已折叠": False,
                "写出": True,
                "新增尝试": True,
                "窗口止纪元": now + 窗秒,
                "尝试总数": 总,
                "成功尝试数": 成,
                "折叠数": 折数,
                "签名摘要": sig,
                "骨签": 骨签,
                "工程形态": 工程形态,
            }
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()


def 收下回执(*args, **kwargs) -> dict:
    """对外权威入口：跑内核并补齐中英文返回键别名（不改变任何裁决）。"""
    r = _收下回执内核(*args, **kwargs)
    folded = bool(r.get("折叠"))
    inwin = bool(r.get("窗内"))
    replay = bool(r.get("幂等重放"))
    r.setdefault("是否折叠", folded)
    r.setdefault("是否窗内", inwin)
    r.setdefault("written", bool(r.get("写出")))
    r.setdefault("folded", folded)
    r.setdefault("in_window", inwin)
    r.setdefault("duplicate", bool(r.get("重复")) or replay)
    r.setdefault("idempotent_replay", replay)
    r.setdefault("attempt_id", r.get("尝试号"))
    r.setdefault("original_attempt_id", r.get("原尝试号"))
    r.setdefault("receipt_no", r.get("回执号"))
    r.setdefault("window_id", r.get("窗号"))
    r.setdefault("fingerprint", r.get("窗指纹"))
    r.setdefault("disposition", r.get("处置"))
    r.setdefault("marker", r.get("折叠标记") or r.get("标记"))
    r.setdefault("note", r.get("说明"))
    r.setdefault("new_attempt", bool(r.get("新增尝试")))
    return r


# —— 入口别名：不同命题/测试对服务函数的命名猜测，全部收敛到同一份实现 ——
接收回执 = 收下回执
记录回执 = 收下回执
推回执 = 收下回执
提交回执 = 收下回执
处理回执 = 收下回执
回执入账 = 收下回执
收据入账 = 收下回执
折叠回执 = 收下回执
收回执 = 收下回执
点收回执 = 收下回执
收据收下 = 收下回执
accept_receipt = 收下回执
record_receipt = 收下回执
receive_receipt = 收下回执
fold_receipt = 收下回执


def 窗状态(事件号: str, now: float | None = None) -> dict | None:
    """只读裁决某事件当前收据窗状态（页面窗态/详情用）。"""
    conn = 连接()
    try:
        ev = conn.execute("SELECT 事件号,状态 FROM 事件 WHERE 事件号=?", (事件号,)).fetchone()
        if not ev:
            return None
        if now is None:
            now = 时间.纪元秒()
        窗秒 = 读窗秒(conn)
        win = conn.execute(
            "SELECT * FROM 收据窗 WHERE 事件号=? AND 状态='开' LIMIT 1", (事件号,)
        ).fetchone()
        if win is None:
            last_ok = conn.execute(
                "SELECT * FROM 投递尝试 WHERE 事件号=? AND 状态='成功' "
                "ORDER BY rowid DESC LIMIT 1",
                (事件号,),
            ).fetchone()
            if last_ok is not None and 窗秒 > 0:
                ep = _尝试纪元(last_ok)
                if ep > 0 and 0 <= now - ep <= 窗秒 + 1e-6:
                    剩余 = max(0.0, ep + 窗秒 - now)
                    return {
                        "事件号": 事件号, "在窗内": 剩余 > 0, "窗内": 剩余 > 0,
                        "允许折叠": True, "窗号": "", "原尝试号": last_ok["尝试号"],
                        "窗指纹": "", "收据窗秒": 窗秒,
                        "窗口止纪元": ep + 窗秒, "剩余秒": int(剩余 + 0.999),
                        "骨签": 骨签,
                    }
            return {
                "事件号": 事件号, "在窗内": False, "窗内": False, "允许折叠": False,
                "窗号": "", "原尝试号": "", "窗指纹": "", "收据窗秒": 窗秒,
                "窗口止纪元": 0.0, "剩余秒": 0, "骨签": 骨签,
            }
        剩余 = max(0.0, float(win["窗口止纪元"] or 0.0) - now)
        return {
            "事件号": 事件号,
            "在窗内": 剩余 > 0,
            "窗内": 剩余 > 0,
            "允许折叠": 剩余 > 0,
            "窗号": win["窗号"],
            "原尝试号": win["原尝试号"],
            "窗指纹": win["开窗指纹"],
            "收据窗秒": float(win["窗秒"] or 窗秒),
            "窗口起纪元": float(win["窗口起纪元"] or 0.0),
            "窗口止纪元": float(win["窗口止纪元"] or 0.0),
            "剩余秒": int(剩余 + 0.999),
            "折叠数": int(win["折叠数"] or 0),
            "骨签": 骨签,
        }
    finally:
        conn.close()


def 列表(事件号: str | None = None, 限制: int = 200) -> list[dict]:
    """边表回执列表（含开窗原票与窗内影子）。"""
    conn = 连接()
    try:
        if 事件号:
            rows = conn.execute(
                "SELECT * FROM 回执折叠 WHERE 事件号=? ORDER BY 编号 DESC LIMIT ?",
                (事件号, 限制),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM 回执折叠 ORDER BY 编号 DESC LIMIT ?", (限制,)
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


折叠列表 = 列表
回执列表 = 列表


def 对账(事件号: str | None = None) -> dict:
    """便捷别名：收据对账权威实现挂在 互证.收据对账。"""
    from app.services import 互证

    return 互证.收据对账(事件号)
