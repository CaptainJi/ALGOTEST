#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
文件作用：MCP服务器配置
开发规划：提供MCP服务器相关的配置项，支持从环境变量获取
"""

import os
from typing import Dict, Any
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# MCP服务器配置
MCP_HOST = os.getenv("MCP_HOST", "172.16.100.108")
MCP_PORT = int(os.getenv("MCP_PORT", "2800"))
SSE_URL = f"http://{MCP_HOST}:{MCP_PORT}/sse"

# 命令格式文档路径
CMD_FORMAT_PATH = os.getenv("CMD_FORMAT_PATH", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "cmd_require.md"))

def get_mcp_config() -> Dict[str, Any]:
    """
    获取MCP服务器配置
    
    Returns:
        MCP服务器配置字典
    """
    return {
        "host": MCP_HOST,
        "port": MCP_PORT,
        "sse_url": SSE_URL,
        "cmd_format_path": CMD_FORMAT_PATH
    }

def get_cmd_format_path() -> str:
    """
    获取命令格式文档路径
    
    Returns:
        命令格式文档路径
    """
    return CMD_FORMAT_PATH 