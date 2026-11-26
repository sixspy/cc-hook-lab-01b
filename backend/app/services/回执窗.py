# -*- coding: utf-8 -*-
"""兼容别名：回执窗服务，权威实现为 ``app.services.收据窗``。"""
from app.services.收据窗 import *  # noqa: F401,F403
from app.services import 收据窗 as _权威

骨签 = _权威.骨签
收下回执 = _权威.收下回执
窗状态 = _权威.窗状态
重置锁 = _权威.重置锁
取收据锁 = _权威.取收据锁
