# -*- coding: utf-8 -*-
"""失败测乙 · 流水对账把已折叠的影子回执误标成新的已投。

事故命题：窗内第二张回执已折进原尝试行、只配躺在边表「回执折叠」里当影子，
可对账/流水口径若把影子行也当成一张新成功票，就会把一笔折叠误标成「新的已投」，
账实撕裂。

正确契约：
    * 对账必须跨表联查 正式投递尝试 ↔ 边表回执折叠（按原尝试号/窗号）；
    * 影子回执只计「窗内折叠/影子」，绝不计新增已投递、绝不另开成功票；
    * 混进正式尝试表的窗内第二张成功票（脏双票）必须被点名为异常且总对账不对齐；
    * 互证.跑一次 的总对齐结论必须包含收据窗口径；
    * 窗内重复绝不进死信匣。

按题目顺序：本文件第二份建立，在旧主干上必须再见一次红。
"""
from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="win_fold_b_env_")
os.environ.setdefault("数据目录", _TMP)
os.environ["夹具目录"] = str(Path(__file__).resolve().parents[2] / "fixtures")

import pytest  # noqa: E402

import app.db as dbmod  # noqa: E402
from app import seed  # noqa: E402
from app.db import 连接  # noqa: E402
from app.services import 入队, 签名目录, 互证  # noqa: E402


@pytest.fixture()
def 库(tmp_path, monkeypatch):
    monkeypatch.setattr(dbmod, "数据目录", tmp_path)
    monkeypatch.setattr(dbmod, "库路径", tmp_path / "win_fold_b.db")
    seed.初始化库()
    try:
        from app.services import 收据窗

        收据窗.重置锁()
    except Exception:
        pass
    yield tmp_path


def _收据窗():
    from app.services import 收据窗

    return 收据窗


def _新事件(摘要: str = "win-fold-b") -> str:
    return 入队.建事件(
        {"事件类型": "invoice.paid", "端点键": "ep-billing", "载荷摘要": 摘要}
    )["事件号"]


def _灌一对窗内回执(eid: str) -> tuple[str, str]:
    窗 = _收据窗()
    a = 窗.收下回执(eid, 回执号=f"RCPT-B-{uuid.uuid4().hex[:8]}")
    b = 窗.收下回执(eid, 回执号=f"RCPT-B-{uuid.uuid4().hex[:8]}")
    return a["尝试号"], b.get("折叠号", "")


def test_乙0_对账入口已接线():
    assert hasattr(互证, "收据对账") or hasattr(互证, "回执对账") or hasattr(互证, "投递流水对账")


def test_乙_折叠影子不得误标成新的已投(库):
    eid = _新事件("乙-影子")
    原尝试号, _ = _灌一对窗内回执(eid)

    rep = 互证.收据对账()
    事件报 = [x for x in rep.get("逐事件", []) if x.get("事件号") == eid]
    assert 事件报, "收据对账必须逐事件出报"
    x = 事件报[0]
    assert int(x.get("正式成功票数", x.get("成功票数", 0))) == 1
    assert int(x.get("影子折叠数", x.get("折叠影子数", x.get("窗内折叠数", 0)))) == 1
    assert int(x.get("误标新已投数", x.get("影子误投数", 0))) == 0, "影子回执不得误标为新的已投"
    assert int(x.get("窗内双票异常数", x.get("双成功票数", 0))) == 0
    assert x.get("对齐") is True
    assert rep.get("对齐") is True


def test_乙_互证跑一次_收据口径并入总对齐(库):
    eid = _新事件("乙-总对齐")
    _灌一对窗内回执(eid)

    rep = 互证.跑一次()
    assert rep["是否对齐"] is True
    assert rep["是否全部对齐"] is True, "干净折叠 + 账实同源时总互证必须对齐"
    收据 = rep.get("收据对账") or rep.get("回执对账")
    assert isinstance(收据, dict), "跑一次必须带回收据窗对账块"
    明细 = rep.get("明细", {})
    折叠口径 = int(
        明细.get("窗内折叠数", 明细.get("影子折叠数", 明细.get("折叠影子数", 0))) or 0
    )
    assert 折叠口径 >= 1


def test_乙_脏第二张成功票混进正式表_对账必须点名不对齐(库):
    """事故复刻：朴素实现把窗内第二张回执也 INSERT 成正式成功尝试。

    对账不能把它认成合法的新已投，必须判窗内双票异常并使总对齐转红。
    """
    eid = _新事件("乙-脏双票")
    原尝试号, _ = _灌一对窗内回执(eid)

    # 绕过收据口，直接把“第二张成功票”落进正式尝试表（事故脏数据）
    conn = 连接()
    try:
        row = conn.execute(
            "SELECT * FROM 投递尝试 WHERE 尝试号=?", (原尝试号,)
        ).fetchone()
        conn.execute(
            "INSERT INTO 投递尝试(尝试号,事件号,端点键,签名摘要,状态,响应码,说明,投递时)"
            " VALUES (?,?,?,?,?,?,?,?)",
            ("TRY-DIRTY-B", eid, row["端点键"], row["签名摘要"], "成功", 200,
             "窗内重复收据被误开成成功票", row["投递时"]),
        )
        conn.commit()
    finally:
        conn.close()

    rep = 互证.收据对账()
    x = [q for q in rep["逐事件"] if q["事件号"] == eid][0]
    assert int(x.get("窗内双票异常数", x.get("双成功票数", 0))) >= 1, (
        "窗内两张正式成功票必须被点名为双票异常，不得当成两笔新已投"
    )
    assert x.get("对齐") is False
    assert rep.get("对齐") is False

    总 = 互证.跑一次()
    assert 总["是否全部对齐"] is False, "有双票异常时总互证不得判全部对齐"


def test_乙_窗内重复绝不进死信匣(库):
    eid = _新事件("乙-死信")
    窗 = _收据窗()
    _灌一对窗内回执(eid)
    窗.收下回执(eid, 回执号=f"RCPT-B-{uuid.uuid4().hex[:8]}")

    conn = 连接()
    try:
        n = conn.execute(
            "SELECT COUNT(*) c FROM 死信 WHERE 事件号=?", (eid,)
        ).fetchone()["c"]
    finally:
        conn.close()
    assert n == 0, "窗内重复收据不是投递失败，死信匣不得误收"
    assert all(d["事件号"] != eid for d in 签名目录.死信列表())


def test_乙_流水联查_影子不冒充正式成功行(库):
    eid = _新事件("乙-流水")
    _灌一对窗内回执(eid)

    rows = [r for r in 签名目录.投递列表() if r["事件号"] == eid]
    assert len(rows) == 1, "投递流水里只能有正式原行，影子在边表联标而不是另起一行"
    assert rows[0]["状态"] == "成功"
    assert int(rows[0].get("折叠数") or 0) == 1
