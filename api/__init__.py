#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
文件作用：API包初始化
开发规划：导出API应用实例
"""

from .app import app
from .routes import router as api_router
from .web_routes import router as web_router
from .reports import router as reports_router

# 添加 API 路由
app.include_router(api_router)

# 添加报告路由
app.include_router(reports_router)

# 添加前端页面路由
app.include_router(web_router)

__all__ = ['app']
