# -*- coding: utf-8 -*-
"""pytest 全局夹具：在导入 backend 前注入测试环境变量。

config.py 安全硬约束要求显式 JWT_SECRET（>=32 字符），这里提供测试专用值。
"""
import os

os.environ.setdefault(
    "JWT_SECRET",
    "heartmirror-test-secret-key-0123456789abcdef",
)
# 测试用临时 sqlite，避免污染开发库
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
