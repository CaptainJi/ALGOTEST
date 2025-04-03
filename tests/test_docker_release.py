#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
测试Docker容器释放功能
"""

import os
import sys
import asyncio
import json
from loguru import logger
import httpx

# 添加项目根目录到Python路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.logger import setup_logging
from core.utils import generate_unique_id

# 设置API基础URL
API_BASE_URL = "http://localhost:8000/api"

async def test_docker_release():
    """测试Docker容器释放功能"""
    # 设置日志
    setup_logging()
    logger.info("开始测试Docker容器释放功能")
    
    # 使用指定的task_id进行测试
    task_id = "TASK1742443129_45b17f0db380"
    
    try:
        async with httpx.AsyncClient() as client:
            # 调用释放Docker容器的API
            logger.info(f"正在释放任务 {task_id} 的Docker容器...")
            response = await client.post(f"{API_BASE_URL}/tasks/{task_id}/release-docker")
            
            # 检查响应状态码
            if response.status_code != 200:
                logger.error(f"API请求失败，状态码: {response.status_code}")
                logger.error(f"错误信息: {response.text}")
                return
            
            # 解析响应数据
            result = response.json()
            
            if result["success"]:
                logger.success(f"容器释放成功：{result['container_name']}")
                logger.info(f"响应消息：{result['message']}")
            else:
                logger.error(f"容器释放失败：{result['error']}")
            
            # 打印完整的响应数据
            logger.info("完整的响应数据:")
            logger.info(json.dumps(result, indent=2, ensure_ascii=False))
            
    except Exception as e:
        logger.error(f"测试过程中出错: {str(e)}")
        raise

if __name__ == "__main__":
    # 运行测试
    asyncio.run(test_docker_release()) 