from __future__ import annotations
import os
from pathlib import Path
from flask import Flask, jsonify, redirect, request, send_from_directory

from app import seed
from app.services import 会话, 入队, 投递, 死信, 重试, 互证, 端点, 夹具, 说明, 签名目录, 收据窗

_STATIC = Path(__file__).resolve().parent / "static"


def create_app() -> Flask:
    app = Flask(__name__, static_folder=str(_STATIC), static_url_path="")
    app.secret_key = os.environ.get("密钥", "hook-lab-dev")

    @app.before_request
    def _gate():
        if request.path.startswith("/api/") and request.path not in ("/api/登录",):
            if not 会话.已登录():
                return jsonify({"错误": "未登录"}), 401
        return None

    @app.route("/login")
    def 登录页():
        return send_from_directory(_STATIC, "login.html")

    @app.route("/")
    def 首页():
        return send_from_directory(_STATIC, "index.html")

    @app.route("/<path:name>")
    def 静态页(name):
        p = _STATIC / name
        if p.is_file():
            return send_from_directory(_STATIC, name)
        return redirect("/")

    @app.post("/api/登录")
    def api_登录():
        body = request.get_json(force=True, silent=True) or {}
        账号 = str(body.get("账号") or body.get("user") or "")
        密码 = str(body.get("密码") or body.get("password") or "")
        if 会话.校验登录(账号, 密码):
            会话.写入会话(账号)
            return jsonify({"ok": True, "用户": 账号})
        return jsonify({"ok": False, "错误": "账号或密码不对"}), 401

    @app.post("/api/登出")
    def api_登出():
        会话.登出()
        return jsonify({"ok": True})

    @app.get("/api/概览")
    def api_概览():
        return jsonify(签名目录.概览())

    @app.get("/api/仓情")
    def api_仓情():
        return jsonify(说明.仓情())

    @app.get("/api/签名规")
    def api_签名规_get():
        return jsonify(签名目录.签名规())

    @app.post("/api/签名规")
    def api_签名规_post():
        body = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(签名目录.改签名规(body))
        except ValueError as e:
            return jsonify({"错误": str(e)}), 400

    @app.get("/api/事件")
    def api_事件():
        return jsonify(签名目录.事件列表())

    @app.get("/api/事件/<eid>")
    def api_事件一条(eid):
        row = 签名目录.事件一条(eid)
        if not row:
            return jsonify({"错误": "无此事件"}), 404
        return jsonify(row)

    @app.post("/api/事件/入队")
    def api_入队():
        body = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(入队.建事件(body))
        except ValueError as e:
            return jsonify({"错误": str(e)}), 400

    @app.get("/api/投递")
    def api_投递列表():
        return jsonify(签名目录.投递列表())

    @app.post("/api/投递")
    def api_投递():
        body = request.get_json(force=True, silent=True) or {}
        try:
            # 若调用方在投递口附带回执号，委托收据窗裁决，避免被
            # 「事件状态为已投递，不可再投」的状态守卫弄成假绿。
            回执号 = body.get("回执号") or body.get("收据号")
            if 回执号:
                return jsonify(收据窗.收下回执(
                    body.get("事件号"),
                    回执号=回执号,
                    强制失败=bool(body.get("强制失败")),
                ))
            return jsonify(投递.投递一次(body["事件号"], bool(body.get("强制失败"))))
        except (KeyError, ValueError) as e:
            return jsonify({"错误": str(e)}), 400

    def _收下回执核心():
        body = request.get_json(force=True, silent=True) or {}
        # 兼容 query/form 传参
        eid = body.get("事件号") or request.args.get("事件号")
        no = (
            body.get("回执号")
            or body.get("收据号")
            or request.args.get("回执号")
            or request.args.get("收据号")
        )
        try:
            out = 收据窗.收下回执(
                eid,
                回执号=no,
                窗秒=body.get("窗秒", body.get("收据窗秒")),
                现在=body.get("现在", body.get("当前纪元")),
            )
            # 默认幂等 200；调用方显式要求严格冲突码时，折叠/重放给 409
            if body.get("严格") and (out.get("折叠") or out.get("幂等重放")):
                return jsonify(out), 409
            return jsonify(out)
        except (KeyError, ValueError) as e:
            msg = str(e)
            # 事件不存在更贴近 REST 语义；其余入参/状态问题仍为 400
            if "无此事件" in msg:
                return jsonify({"ok": False, "错误": msg}), 404
            return jsonify({"ok": False, "错误": msg}), 400

    @app.post("/api/回执")
    @app.post("/api/回执收下")
    @app.post("/api/收据")
    @app.post("/api/收据/收下")
    @app.post("/api/回执/收下")
    @app.post("/api/回执/折叠")
    @app.post("/api/投递/回执")
    @app.post("/api/receipt")
    @app.post("/api/receipts")
    @app.post("/api/win-fold/receipt")
    def api_回执收下():
        return _收下回执核心()

    @app.get("/api/回执")
    def api_回执列表():
        eid = request.args.get("事件号")
        return jsonify(签名目录.回执列表(eid))

    @app.get("/api/折叠")
    def api_折叠列表():
        eid = request.args.get("事件号")
        return jsonify(签名目录.回执列表(eid))

    @app.get("/api/收据窗")
    def api_收据窗状态():
        eid = request.args.get("事件号")
        if not eid:
            return jsonify({"错误": "缺事件号"}), 400
        st = 收据窗.窗状态(eid)
        if st is None:
            return jsonify({"错误": "无此事件"}), 404
        return jsonify(st)

    @app.get("/api/收据对账")
    @app.get("/api/回执对账")
    @app.get("/api/对账")
    @app.get("/api/互证/收据对账")
    def api_收据对账():
        eid = request.args.get("事件号")
        return jsonify(互证.收据对账(eid))

    @app.post("/api/投递/批量重试")
    def api_批量重试():
        return jsonify(重试.批量重试待投())

    @app.get("/api/死信")
    def api_死信():
        return jsonify(签名目录.死信列表())

    @app.post("/api/死信/重投")
    def api_死信重投():
        body = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(死信.重投(body["事件号"]))
        except (KeyError, ValueError) as e:
            return jsonify({"错误": str(e)}), 400

    @app.post("/api/死信/作废")
    def api_死信作废():
        body = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(死信.作废(body["事件号"], body.get("原因", "人工作废")))
        except (KeyError, ValueError) as e:
            return jsonify({"错误": str(e)}), 400

    @app.get("/api/端点")
    def api_端点():
        return jsonify(签名目录.端点列表())

    @app.post("/api/端点")
    def api_端点登记():
        body = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(端点.登记(body))
        except ValueError as e:
            return jsonify({"错误": str(e)}), 400

    @app.post("/api/端点/启用")
    def api_端点启用():
        body = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(端点.改启用(body["端点键"], bool(body.get("启用"))))
        except (KeyError, ValueError) as e:
            return jsonify({"错误": str(e)}), 400

    @app.get("/api/互证")
    def api_互证列表():
        return jsonify(签名目录.互证列表())

    @app.post("/api/互证/跑")
    def api_互证跑():
        return jsonify(互证.跑一次())

    @app.get("/api/夹具")
    def api_夹具():
        return jsonify(夹具.列表())

    @app.post("/api/夹具入队/<name>")
    def api_夹具入队(name):
        try:
            体 = 夹具.读取(name)
            out = [入队.建事件(item) for item in 体.get("事件", [])]
            return jsonify({"入队": out})
        except FileNotFoundError:
            return jsonify({"错误": name}), 404
        except ValueError as e:
            return jsonify({"错误": str(e)}), 400

    @app.get("/api/空桩")
    def api_空桩():
        root = Path(__file__).resolve().parent / "modules"
        out = []
        if root.is_dir():
            for p in sorted(root.iterdir()):
                if p.is_dir() and (p / "__init__.py").is_file():
                    out.append(p.name)
        return jsonify({"空桩": out, "条数": len(out)})

    with app.app_context():
        seed.初始化库()
    return app
