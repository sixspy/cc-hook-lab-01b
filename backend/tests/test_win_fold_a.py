# -*- coding: utf-8 -*-
"""失败测甲 · 窗内第二张回执仍把尝试加一 —— 归属服务：收据窗服务。

事故命题（骨签：第二张回执在窗内必须折叠）：
    成功回执挤在同一扇幂等窗里时，朴素实现每收一张就 INSERT 一行成功尝试，
    于是窗内第二张回执仍把尝试数 +1，另开一张「成功票」。

正确契约（路径甲：开窗指纹收进收据窗账册，窗内重复拒回刷正式尝试、折入边表）：
    1. 第一张回执开窗并长出唯一一行正式成功尝试（原行）；
    2. 窗内第二张不得再长尝试，只能在边表「回执折叠」留一行影子，
       标记「窗内已折叠」，说明/行末写「窗内重复收据」，原尝试号与原行联查；
    3. 同一回执号重放必须幂等，边表也不得多长；
    4. 窗关上之后，才准另记一笔新尝试并另开一扇窗。

注意防假绿：不能靠入队口的「事件号已存在」、也不能靠投递口
「事件状态为已投递，不可再投」来让本测变绿 —— 本文件只打收据口
``收据窗.收下回执`` / ``POST /api/回执``。

按题目要求：本文件先于主干修改建立，在旧主干上必须先红，禁止无复现就动笔。
"""
from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="win_fold_a_env_")
os.environ.setdefault("数据目录", _TMP)
os.environ["夹具目录"] = str(Path(__file__).resolve().parents[2] / "fixtures")

import pytest  # noqa: E402

import app.db as dbmod  # noqa: E402
from app import seed  # noqa: E402
from app.db import 连接  # noqa: E402
from app.services import 入队, 夹具, 签名目录  # noqa: E402


@pytest.fixture()
def 库(tmp_path, monkeypatch):
    monkeypatch.setattr(dbmod, "数据目录", tmp_path)
    monkeypatch.setattr(dbmod, "库路径", tmp_path / "win_fold_a.db")
    seed.初始化库()
    try:
        from app.services import 收据窗

        收据窗.重置锁()
    except Exception:
        # 见红阶段收据窗服务尚不存在
        pass
    yield tmp_path


@pytest.fixture()
def 假钟(monkeypatch):
    from app.services import 时间

    class 钟:
        def __init__(self):
            self.t = 1_700_100_000.0

        def now(self):
            return self.t

        def 前进(self, s):
            self.t += float(s)

    z = 钟()
    monkeypatch.setattr(时间, "纪元秒", z.now, raising=False)
    return z


def _收据窗():
    """硬导入未来主干；未接线时让用例明确失败（红），不 skip。"""
    from app.services import 收据窗

    assert hasattr(收据窗, "收下回执"), "收据窗服务必须提供 收下回执 入口"
    return 收据窗


def _新事件(摘要: str = "win-fold-a") -> str:
    return 入队.建事件(
        {"事件类型": "invoice.paid", "端点键": "ep-billing", "载荷摘要": 摘要}
    )["事件号"]


def _尝试行数(eid: str, 状态: str | None = None) -> int:
    conn = 连接()
    try:
        if 状态:
            return conn.execute(
                "SELECT COUNT(*) AS c FROM 投递尝试 WHERE 事件号=? AND 状态=?",
                (eid, 状态),
            ).fetchone()["c"]
        return conn.execute(
            "SELECT COUNT(*) AS c FROM 投递尝试 WHERE 事件号=?", (eid,)
        ).fetchone()["c"]
    finally:
        conn.close()


def _折叠行(eid: str) -> list[dict]:
    conn = 连接()
    try:
        rows = conn.execute(
            "SELECT * FROM 回执折叠 WHERE 事件号=? ORDER BY 编号", (eid,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def test_甲0_夹具键_窗内重复回执甲_可读且造出两张回执():
    体 = 夹具.读取("窗内重复回执甲")
    assert 体.get("夹具键") == "窗内重复回执甲"
    assert len(体["事件"]) >= 1
    回执 = 体.get("回执") or []
    assert len(回执) == 2, "甲号夹具必须备妥窗内两张回执"
    assert 回执[0]["回执号"] != 回执[1]["回执号"]


def test_甲1_收据窗服务已接线():
    窗 = _收据窗()
    assert getattr(窗, "骨签", ""), "收据窗服务必须带骨签自证"
    assert "折叠" in 窗.骨签


def test_甲_窗内第二张回执不得再长尝试(库):
    窗 = _收据窗()
    eid = _新事件("甲-第二张")

    r1 = 窗.收下回执(eid, 回执号="RCPT-A-1")
    assert r1.get("ok", True) is not False
    assert r1.get("写出") is True, "第一张回执应长出正式尝试（开窗原行）"
    原尝试号 = r1["尝试号"]
    assert _尝试行数(eid) == 1
    assert _尝试行数(eid, "成功") == 1

    # 窗内第二张：旧主干/朴素实现这里会让尝试数变成 2 —— 必须先红
    r2 = 窗.收下回执(eid, 回执号="RCPT-A-2")

    assert _尝试行数(eid) == 1, "窗内第二张回执仍把尝试加一（win_fold 甲事故）"
    assert _尝试行数(eid, "成功") == 1, "不得另开成功票"
    assert r2.get("折叠") is True or r2.get("已折叠") is True
    assert r2.get("窗内") is True or r2.get("在窗内") is True
    assert r2.get("写出") is False, "折叠票不得回刷正式尝试表"
    assert r2.get("原尝试号") == 原尝试号 or r2.get("尝试号") == 原尝试号

    折 = _折叠行(eid)
    assert len(折) == 1, "窗内第二张必须在边表留一行折叠影子"
    row = 折[0]
    assert row["原尝试号"] == 原尝试号, "边表必须钉住原尝试行以便联查"
    assert "窗内已折叠" in str(row["标记"])
    assert "窗内重复收据" in str(row["说明"])

    # 事件结案状态与死信匣：折叠不是失败，绝不入死信
    detail = 签名目录.事件一条(eid)
    assert detail["状态"] == "已投递"
    assert detail.get("死信") in (None, {})
    conn = 连接()
    try:
        dl = conn.execute("SELECT COUNT(*) c FROM 死信 WHERE 事件号=?", (eid,)).fetchone()["c"]
    finally:
        conn.close()
    assert dl == 0, "窗内重复收据不得进死信匣"


def test_甲_边表折叠标记与尝试行联查_流水带标(库):
    窗 = _收据窗()
    eid = _新事件("甲-联查")
    r1 = 窗.收下回执(eid, 回执号="RCPT-L-1")
    窗.收下回执(eid, 回执号="RCPT-L-2")

    流水 = [r for r in 签名目录.投递列表() if r["事件号"] == eid]
    assert len(流水) == 1, "流水里只能有原尝试一行"
    行 = 流水[0]
    assert 行["尝试号"] == r1["尝试号"]
    assert int(行.get("折叠数") or 行.get("窗内折叠数") or 0) == 1
    联标 = str(行.get("折叠标记") or 行.get("标记") or "")
    assert "窗内已折叠" in 联标
    行末 = str(行.get("行末说明") or 行.get("折叠说明") or 行.get("说明") or "")
    assert "窗内重复收据" in 行末


def test_甲_同一回执号重放幂等_边表不多长(库):
    窗 = _收据窗()
    eid = _新事件("甲-幂等号")
    窗.收下回执(eid, 回执号="RCPT-SAME")
    窗.收下回执(eid, 回执号="RCPT-OTHER")
    # 同一张回执再推一次（重试/双击）
    r3 = 窗.收下回执(eid, 回执号="RCPT-OTHER")

    assert _尝试行数(eid) == 1
    折 = _折叠行(eid)
    assert len(折) == 1, "同一回执号重放必须幂等，不得再长折叠行"
    assert r3.get("幂等重放") is True or r3.get("重复") is True


def test_甲_出窗之后才追加新尝试(库, 假钟):
    窗 = _收据窗()
    签名目录.改签名规({"收据窗秒": "10"})
    eid = _新事件("甲-出窗")

    r1 = 窗.收下回执(eid, 回执号="RCPT-OUT-1", 现在=假钟.t)
    assert r1.get("写出") is True
    窗.收下回执(eid, 回执号="RCPT-OUT-2", 现在=假钟.t)
    assert _尝试行数(eid) == 1

    假钟.前进(11)
    r3 = 窗.收下回执(eid, 回执号="RCPT-OUT-3", 现在=假钟.t)
    assert r3.get("折叠") is False, "出窗后不得再折叠进旧行"
    assert r3.get("写出") is True, "窗关后才准追加一笔新尝试"
    assert _尝试行数(eid) == 2
    assert _尝试行数(eid, "成功") == 2
    # 新窗内的重复折到新行，不折回旧行
    r4 = 窗.收下回执(eid, 回执号="RCPT-OUT-4", 现在=假钟.t)
    assert r4.get("折叠") is True
    折 = _折叠行(eid)
    # 边表是永久联查账：两扇窗各留一道影子；关键是新折必须锚定新尝试，
    # 绝不能折回 r1 的旧行。
    assert len(折) == 2
    新折 = [x for x in 折 if x["原尝试号"] == r3["尝试号"]]
    旧折 = [x for x in 折 if x["原尝试号"] == r1["尝试号"]]
    assert len(新折) == 1 and "窗内重复收据" in str(新折[0]["说明"])
    assert len(旧折) == 1, "出窗后旧折叠影子仍须留档，供流水行永久联标"


def test_甲_甲号夹具直灌_第二张仍被折叠(库):
    """用题目点名的夹具键走一遍完整事故主线。"""
    窗 = _收据窗()
    体 = 夹具.读取("窗内重复回执甲")
    item = dict(体["事件"][0])
    item["事件号"] = f"EVT-JIA-{uuid.uuid4().hex[:8]}"
    eid = 入队.建事件(item)["事件号"]
    回执s = 体["回执"]

    首 = 窗.收下回执(eid, 回执号=回执s[0]["回执号"])
    二 = 窗.收下回执(eid, 回执号=回执s[1]["回执号"])
    assert 首.get("写出") is True
    assert 二.get("折叠") is True
    assert _尝试行数(eid) == 1
    assert len(_折叠行(eid)) == 1
