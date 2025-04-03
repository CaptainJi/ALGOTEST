#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
文件作用：核心模块初始化文件，导出核心组件供其他模块使用
开发规划：随着项目功能的完善，在此文件中导出更多核心组件
"""

from core.config import get_settings, Settings, get_llm_config
from core.logger import setup_logging, get_logger
from core.database import (
    init_db, 
    ensure_db,
    get_db, 
    TestTask, 
    TestCase,
    create_test_task,
    get_test_task,
    update_test_task,
    create_test_case
)
from core.utils import (
    generate_unique_id, 
    save_json_file, 
    load_json_file, 
    calculate_md5,
    run_docker_container,
    format_timestamp,
    make_request,
    ensure_dir,
    read_file,
    write_file
)
from core.llm import call_zhipu_api

__all__ = [
    # 配置
    'get_settings',
    'Settings',
    'get_llm_config',
    
    # 日志
    'setup_logging',
    'get_logger',
    
    # 数据库
    'init_db',
    'ensure_db',
    'get_db',
    'TestTask',
    'TestCase',
    'create_test_task',
    'get_test_task',
    'update_test_task',
    'create_test_case',
    
    # 工具
    'generate_unique_id',
    'save_json_file',
    'load_json_file',
    'calculate_md5',
    'run_docker_container',
    'format_timestamp',
    'make_request',
    'ensure_dir',
    'read_file',
    'write_file',
    
    # 大模型
    'call_zhipu_api'
]
