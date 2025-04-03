#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
文件作用：项目入口文件，负责启动FastAPI应用程序
开发规划：初始化各个组件，配置日志系统，启动Web服务
"""

import uvicorn
import os
from dotenv import load_dotenv
from fastapi import FastAPI
from core import setup_logging, ensure_db, get_settings
from api import app

def main():
    """
    主函数，启动FastAPI应用
    """
    # 加载环境变量
    load_dotenv()
    
    # 设置日志
    setup_logging()
    
    # 确保数据库表存在（不会删除现有数据）
    ensure_db()
    
    # 获取配置
    settings = get_settings()
    
    # 获取端口，确保使用最新设置
    port = int(os.getenv("API_PORT", 8000))
    print(f"环境变量中的API_PORT: {os.getenv('API_PORT')}")
    
    # 打印启动信息
    print("=" * 80)
    print("ALGOTEST 系统启动中...")
    print(f"API服务地址: http://{settings.api_host}:{port}")
    print(f"Web界面地址: http://{settings.api_host}:{port}")
    print(f"API文档地址: http://{settings.api_host}:{port}/api/docs")
    print("=" * 80)
    
    # 启动Web服务
    uvicorn.run(
        "api:app",
        host=settings.api_host,
        port=port,
        workers=settings.api_workers,
        reload=settings.api_reload,
        timeout_keep_alive=settings.api_timeout
    )

if __name__ == "__main__":
    main()
