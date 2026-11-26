from __future__ import annotations
from app.db import 连接
from app.services import 校验


def 签名规() -> dict:
    conn = 连接()
    try:
        rows = conn.execute("SELECT 键,值 FROM 签名规").fetchall()
        return {r["键"]: r["值"] for r in rows}
    finally:
        conn.close()


def 改签名规(body: dict) -> dict:
    conn = 连接()
    try:
        for k, v in body.items():
            键 = 校验.非空("键", k)
            值 = 校验.非空("值", v)
            conn.execute(
                "INSERT INTO 签名规(键,值) VALUES (?,?) ON CONFLICT(键) DO UPDATE SET 值=excluded.值",
                (键, 值),
            )
        conn.commit()
        return 签名规()
    finally:
        conn.close()


def 概览() -> dict:
    conn = 连接()
    try:
        def c(sql):
            return conn.execute(sql).fetchone()[0]
        折表在 = _表存在(conn, "回执折叠")
        return {
            "端点数": c("SELECT COUNT(*) FROM 投递端点"),
            "启用端点": c("SELECT COUNT(*) FROM 投递端点 WHERE 启用=1"),
            "事件总数": c("SELECT COUNT(*) FROM 事件"),
            "待投": c("SELECT COUNT(*) FROM 事件 WHERE 状态='待投'"),
            "投递中": c("SELECT COUNT(*) FROM 事件 WHERE 状态='投递中'"),
            "已投递": c("SELECT COUNT(*) FROM 事件 WHERE 状态='已投递'"),
            "死信": c("SELECT COUNT(*) FROM 事件 WHERE 状态='死信'"),
            "尝试数": c("SELECT COUNT(*) FROM 投递尝试"),
            "死信匣": c("SELECT COUNT(*) FROM 死信 WHERE 状态='待处置'"),
            "窗内折叠数": c("SELECT COUNT(*) FROM 回执折叠 WHERE 是否折叠=1") if 折表在 else 0,
            "收据折叠总数": c("SELECT COUNT(*) FROM 回执折叠 WHERE 是否折叠=1") if 折表在 else 0,
            "签名规": 签名规(),
        }
    finally:
        conn.close()


def 事件列表() -> list:
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM 事件 ORDER BY 入队时 DESC").fetchall()]
    finally:
        conn.close()


def 事件一条(事件号: str) -> dict | None:
    conn = 连接()
    try:
        row = conn.execute("SELECT * FROM 事件 WHERE 事件号=?", (事件号,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["尝试"] = [dict(x) for x in conn.execute(
            "SELECT * FROM 投递尝试 WHERE 事件号=? ORDER BY 投递时 DESC, rowid DESC", (事件号,)
        ).fetchall()]
        dl = conn.execute("SELECT * FROM 死信 WHERE 事件号=?", (事件号,)).fetchone()
        d["死信"] = dict(dl) if dl else None

        # —— 收据窗折叠面：正式尝试与边表联查，影子回执单独成列 ——
        折行 = [dict(x) for x in conn.execute(
            "SELECT * FROM 回执折叠 WHERE 事件号=? ORDER BY 编号 ASC", (事件号,)
        ).fetchall()] if _表存在(conn, "回执折叠") else []
        窗行 = [dict(x) for x in conn.execute(
            "SELECT * FROM 收据窗 WHERE 事件号=? ORDER BY 窗号 DESC", (事件号,)
        ).fetchall()] if _表存在(conn, "收据窗") else []
        影子 = [x for x in 折行 if int(x.get("是否折叠") or 0) == 1]
        d["回执折叠"] = 折行
        d["折叠"] = 影子
        d["影子回执"] = 影子
        d["折叠数"] = len(影子)
        d["窗内折叠数"] = len(影子)
        d["收据窗"] = 窗行
        d["窗账册"] = 窗行
        try:
            from app.services import 收据窗
            d["窗状态"] = 收据窗.窗状态(事件号)
        except Exception:
            d["窗状态"] = None
        if 影子:
            d["折叠标记"] = "窗内已折叠"
            d["标记"] = "窗内已折叠"
            d["行末说明"] = "窗内重复收据"
        else:
            d["折叠标记"] = ""
            d["行末说明"] = ""
        return d
    finally:
        conn.close()


def _表存在(conn, 表名: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (表名,)
    ).fetchone() is not None


def 投递列表() -> list:
    """正式投递流水：只出正式尝试行；窗内影子不另起行，以折叠计数/标记联在原行旁。"""
    conn = 连接()
    try:
        rows = [dict(r) for r in conn.execute(
            """
            SELECT t.*,
                   COALESCE(f.折叠数, 0) AS 折叠数,
                   COALESCE(f.折叠数, 0) AS 窗内折叠数,
                   COALESCE(f.折叠数, 0) AS 回执折叠数,
                   COALESCE(f.影子回执号, '') AS 影子回执号
              FROM 投递尝试 t
              LEFT JOIN (
                    SELECT 原尝试号,
                           COUNT(*) AS 折叠数,
                           GROUP_CONCAT(回执号, '、') AS 影子回执号
                      FROM 回执折叠
                     WHERE 是否折叠 = 1
                     GROUP BY 原尝试号
              ) f ON f.原尝试号 = t.尝试号
             ORDER BY t.投递时 DESC, t.rowid DESC
             LIMIT 200
            """
        ).fetchall()] if _表存在(conn, "回执折叠") else [
            dict(r) for r in conn.execute(
                "SELECT * FROM 投递尝试 ORDER BY 投递时 DESC, rowid DESC LIMIT 200"
            ).fetchall()
        ]
        for d in rows:
            n = int(d.get("折叠数") or 0)
            if n > 0:
                d["折叠标记"] = "窗内已折叠"
                d["标记"] = "窗内已折叠"
                d["行末说明"] = "窗内重复收据"
                d["折叠说明"] = "窗内重复收据"
            else:
                d["折叠标记"] = ""
                d.setdefault("行末说明", "")
                d.setdefault("折叠说明", "")
        return rows
    finally:
        conn.close()


def 死信列表() -> list:
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM 死信 ORDER BY 入匣时 DESC").fetchall()]
    finally:
        conn.close()


def 端点列表() -> list:
    conn = 连接()
    try:
        if _表存在(conn, "回执折叠"):
            rows = [dict(r) for r in conn.execute(
                """
                SELECT e.*,
                       COALESCE(f.折叠数, 0) AS 折叠数,
                       COALESCE(f.折叠数, 0) AS 窗内折叠数,
                       COALESCE(f.折叠数, 0) AS 回执折叠数
                  FROM 投递端点 e
                  LEFT JOIN (
                        SELECT 端点键, COUNT(*) AS 折叠数
                          FROM 回执折叠
                         WHERE 是否折叠 = 1
                         GROUP BY 端点键
                  ) f ON f.端点键 = e.端点键
                 ORDER BY e.端点键
                """
            ).fetchall()]
        else:
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM 投递端点 ORDER BY 端点键"
            ).fetchall()]
            for d in rows:
                d["折叠数"] = 0
                d["窗内折叠数"] = 0
                d["回执折叠数"] = 0
        for d in rows:
            n = int(d.get("折叠数") or 0)
            d["折叠标记"] = "窗内已折叠" if n > 0 else ""
        return rows
    finally:
        conn.close()


def 回执列表(事件号: str | None = None) -> list:
    """边表回执折叠流水（含开窗原票 是否折叠=0 与窗内影子 是否折叠=1）。"""
    conn = 连接()
    try:
        if not _表存在(conn, "回执折叠"):
            return []
        if 事件号:
            rows = conn.execute(
                "SELECT * FROM 回执折叠 WHERE 事件号=? ORDER BY 编号 DESC LIMIT 200",
                (事件号,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM 回执折叠 ORDER BY 编号 DESC LIMIT 200"
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


折叠列表 = 回执列表


def 互证列表() -> list:
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM 互证批次 ORDER BY 批次号 DESC LIMIT 50").fetchall()]
    finally:
        conn.close()
