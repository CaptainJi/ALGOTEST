#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
直接运行Docker容器设置测试
"""

import os
import sys
import json
import asyncio
from unittest.mock import patch, MagicMock

# 添加项目根目录到Python路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.execution_agent import setup_algorithm_container, execute_container_command

# 任务配置
TASK_ID = "DEMO_1"
ALGORITHM_IMAGE = "ehub.cvmart.net:8443/sdk/hardhatrecognition_9876_gpu:v1.0.5"
DATASET_URL = None  # 设置为None


class MockTask:
    """模拟的任务对象"""
    def __init__(self, task_id, algorithm_image, dataset_url):
        self.task_id = task_id
        self.algorithm_image = algorithm_image
        self.dataset_url = dataset_url


async def test_docker_setup():
    """测试Docker容器设置"""
    print("\n===== 测试Docker容器设置 =====")
    
    try:
        # 创建mock任务，直接使用配置的参数
        mock_task = MockTask(TASK_ID, ALGORITHM_IMAGE, DATASET_URL)
        print(f"使用测试任务: ID={TASK_ID}, 算法镜像={ALGORITHM_IMAGE}, 数据集URL={DATASET_URL}")
        
        # 使用mock替换get_test_task
        with patch('agents.execution_agent.get_test_task', return_value=mock_task):
            # 执行Docker容器设置
            print(f"正在设置Docker容器，任务ID: {TASK_ID}...")
            result = await setup_algorithm_container(TASK_ID)
            
            # 打印结果
            print(f"设置结果: {json.dumps(result, ensure_ascii=False, indent=2)}")
            
            if result.get("success"):
                print("✅ Docker容器设置成功!")
                
                # 测试在容器中执行命令
                print("\n===== 测试在容器中执行命令 =====")
                
                # 执行一个简单的命令
                command = "echo 'Hello from container'"
                print(f"执行命令: {command}")
                cmd_result = await execute_container_command(TASK_ID, command)
                
                # 打印命令执行结果
                print(f"命令执行结果: {json.dumps(cmd_result, ensure_ascii=False, indent=2)}")
                
                if cmd_result.get("success"):
                    print("✅ 命令执行成功!")
                    
                    # 执行更多命令
                    commands = [
                        "ls -la /",
                        "cat /etc/os-release",
                        "python --version"
                    ]
                    
                    for cmd in commands:
                        print(f"\n执行命令: {cmd}")
                        cmd_result = await execute_container_command(TASK_ID, cmd)
                        stdout = cmd_result.get("result", {}).get("stdout", "")
                        stderr = cmd_result.get("result", {}).get("stderr", "")
                        print(f"输出: {stdout}")
                        if stderr:
                            print(f"错误: {stderr}")
                    
                    return True
                else:
                    print(f"❌ 命令执行失败: {cmd_result.get('error', '未知错误')}")
                    return False
            else:
                print(f"❌ Docker容器设置失败: {result.get('error', '未知错误')}")
                return False
    except Exception as e:
        print(f"❌ 测试过程中发生异常: {str(e)}")
        return False


if __name__ == "__main__":
    asyncio.run(test_docker_setup()) 