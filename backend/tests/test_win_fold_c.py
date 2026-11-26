# -*- coding: utf-8 -*-
"""争用失败测丙 · 窗内两单同时推同一张回执，不加锁就会各留一行。

事故命题：两名投递/两个浏览器会话同时推进同一张回执，「读开窗状态 + 写尝试/写边表」
不在同一临界区，于是都读到无窗，双双 INSERT 成功票（双成功），幂等票互相覆盖。

正确契约：
    * 同一事件号的收下回执必须打同一把收据锁，并在一个 BEGIN IMMEDIATE
      事务里「读窗 + 写票 + 折边表」同成败；
    * 两路并发推同一张回执：正式投递尝试恰好一行，两路拿到同一个原尝试号；
      同一回执号幂等，不得各留一行；
    * 两路并发推两张不同回执号：仍只许一行正式尝试，另一张折进边表；
    * HTTP 双登录会话并发 POST /api/回执 也必须收敛（200 折叠/幂等，或 409，
      但正式成功行只能有一行）。

按题目要求：第三份建立并亲见三红之后，才准改主干。
"""
from __future__ import annotations

import os
import tempfile
import threading
import uuid
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="win_fold_c_env_")
os.environ.setdefault("数据目录", _TMP)
os.environ["夹具目录"] = str(Path(__file__).resolve().parents[2] / "fixtures")

import pytest  # noqa: E402

import app.db as dbmod  # noqa: E402
from app import seed  # noqa: E402
from app.db import 连接  # noqa: E402
from app.factory import create_app  # noqa: E402
from app.services import 入队  # noqa: E402


@pytest.fixture()
def 库(tmp_path, monkeypatch):
    monkeypatch.setattr(dbmod, "数据目录", tmp_path)
    monkeypatch.setattr(dbmod, "库路径", tmp_path / "win_fold_c.db")
    seed.初始化库()
    try:
        from app.services import 收据窗

        收据窗.重置锁()
    except Exception:
        pass
    yield tmp_path


def _新事件(摘要: str = "win-fold-c") -> str:
    return 入队.建事件(
        {"事件类型": "invoice.paid", "端点键": "ep-billing", "载荷摘要": 摘要}
    )["事件号"]


def _计数(eid: str):
    conn = 连接()
    try:
        尝试 = conn.execute(
            "SELECT COUNT(*) c FROM 投递尝试 WHERE 事件号=?", (eid,)
        ).fetchone()["c"]
        成功 = conn.execute(
            "SELECT COUNT(*) c FROM 投递尝试 WHERE 事件号=? AND 状态='成功'", (eid,)
        ).fetchone()["c"]
        折叠 = conn.execute(
            "SELECT COUNT(*) c FROM 回执折叠 WHERE 事件号=?", (eid,)
        ).fetchone()["c"]
        return int(尝试), int(成功), int(折叠)
    finally:
        conn.close()


def _并发收(eid: str, 回执号s: list[str]):
    from app.services import 收据窗

    闸 = threading.Barrier(2)
    收口: list = []
    锁 = threading.Lock()

    def worker(no: str):
        闸.wait()
        try:
            r = 收据窗.收下回执(eid, 回执号=no)
            with 锁:
                收口.append(("ok", r))
        except Exception as e:  # noqa: BLE001
            with 锁:
                收口.append(("err", e))

    ts = [threading.Thread(target=worker, args=(no,)) for no in 回执号s]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=20)
        assert not t.is_alive(), "并发收回执线程超时/死锁"
    return 收口


def test_丙0_收据锁入口存在():
    from app.services import 收据窗

    assert hasattr(收据窗, "取收据锁") or hasattr(收据窗, "收下回执")


def test_丙_两路同推一张回执_不得各留成功行(库):
    eid = _新事件("丙-同一张")
    no = f"RCPT-C-SAME-{uuid.uuid4().hex[:8]}"
    收口 = _并发收(eid, [no, no])

    assert len(收口) == 2
    assert all(k == "ok" for k, _ in 收口), [
        (k, repr(v) if isinstance(v, Exception) else "") for k, v in 收口
    ]
    尝试, 成功, 折叠 = _计数(eid)
    assert 尝试 == 1, f"同一张回执并发后正式尝试有 {尝试} 行，收据锁缺失"
    assert 成功 == 1
    号组 = {v.get("尝试号") for _, v in 收口}
    assert len(号组) == 1, f"两路必须折到同一个原尝试号：{号组}"


def test_丙_两路推两张不同回执_第二张折叠不双成功(库):
    eid = _新事件("丙-两张不同")
    收口 = _并发收(eid, [
        f"RCPT-C-1-{uuid.uuid4().hex[:8]}",
        f"RCPT-C-2-{uuid.uuid4().hex[:8]}",
    ])
    assert all(k == "ok" for k, _ in 收口)
    尝试, 成功, 折叠 = _计数(eid)
    assert 尝试 == 1, f"窗内两张回执并发长出 {尝试} 行尝试（必须只长一行）"
    assert 成功 == 1
    assert 折叠 == 1, "第二张不同回执应折进边表"
    处置 = sorted(str(v.get("处置") or ("折叠" if v.get("折叠") else "新尝试")) for _, v in 收口)
    assert any("折叠" in x for x in 处置)


def test_丙_HTTP双会话并发顶_api回执_只一行成功(库):
    app = create_app()
    eid = _新事件("丙-http")
    no = f"RCPT-HTTP-{uuid.uuid4().hex[:8]}"
    闸 = threading.Barrier(2)
    收口: list = []
    锁 = threading.Lock()

    def 一路(名: str):
        c = app.test_client()
        lr = c.post("/api/登录", json={"账号": "admin", "密码": "123456"})
        assert lr.status_code == 200
        闸.wait()
        resp = c.post("/api/回执", json={"事件号": eid, "回执号": no})
        with 锁:
            收口.append((名, resp.status_code, resp.get_json()))

    ts = [threading.Thread(target=一路, args=(f"s{i}",)) for i in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=20)
        assert not t.is_alive()

    assert len(收口) == 2
    for 名, 码, body in 收口:
        assert 码 in (200, 409), (名, 码, body)
    尝试, 成功, _ = _计数(eid)
    assert 尝试 == 1, f"HTTP 双会话并发后正式尝试 {尝试} 行"
    assert 成功 == 1
