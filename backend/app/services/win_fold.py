# -*- coding: utf-8 -*-
"""Compatibility alias: the win-fold service canonical module is
``app.services.收据窗`` (骨签：第二张回执在窗内必须折叠)."""
from app.services.收据窗 import *  # noqa: F401,F403
from app.services import 收据窗 as _权威

BONE = _权威.骨签
收下回执 = _权威.收下回执
accept_receipt = _权威.accept_receipt
record_receipt = _权威.record_receipt
reset_locks = _权威.重置锁
