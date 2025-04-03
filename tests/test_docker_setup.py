#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
测试Docker容器设置功能
"""

import os
import sys
import json
import pytest
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.execution_agent import setup_algorithm_container, execute_container_command
from core.database import TestTask


# 测试任务ID
TEST_TASK_ID = "DEMO_1"
# 测试算法镜像
TEST_ALGORITHM_IMAGE = "ehub.cvmart.net:8443/sdk/hardhatrecognition_9876_gpu:v1.0.5"
# 测试数据集URL
TEST_DATASET_URL = "/data/datasets/test_dataset"


class TestDockerSetup:
    """Docker容器设置功能测试类"""

    @pytest.mark.asyncio
    @patch('agents.execution_agent.get_test_task')
    @patch('agents.execution_agent.sse_client')
    async def test_setup_algorithm_container_mock(self, mock_sse_client, mock_get_test_task):
        """使用Mock测试setup_algorithm_container函数"""
        # 模拟数据库返回的任务
        mock_task = MagicMock()
        mock_task.algorithm_image = TEST_ALGORITHM_IMAGE
        mock_task.dataset_url = TEST_DATASET_URL
        mock_get_test_task.return_value = mock_task

        # 模拟SSE客户端
        mock_read = AsyncMock()
        mock_write = AsyncMock()
        mock_sse_client.return_value.__aenter__.return_value = (mock_read, mock_write)

        # 模拟ClientSession
        mock_session = AsyncMock()
        mock_session.initialize = AsyncMock()
        
        # 模拟执行脚本的返回结果
        mock_result = {
            "stdout": "容器启动成功: algotest_DEMO_1\n算法镜像: ehub.cvmart.net:8443/sdk/hardhatrecognition_9876_gpu:v1.0.5\n数据集URL: /data/datasets/test_dataset 已挂载到容器 /data",
            "stderr": "",
            "exit_code": 0
        }
        mock_session.call_tool = AsyncMock(return_value=mock_result)

        # 返回模拟的session
        mock_client_session = MagicMock()
        mock_client_session.return_value.__aenter__.return_value = mock_session
        
        with patch('agents.execution_agent.ClientSession', mock_client_session):
            # 执行被测试的函数
            result = await setup_algorithm_container(TEST_TASK_ID)

        # 验证函数调用和结果
        mock_get_test_task.assert_called_once_with(TEST_TASK_ID)
        mock_sse_client.assert_called_once()
        mock_session.initialize.assert_called_once()
        mock_session.call_tool.assert_called_once()
        
        # 检查调用execute_script时的脚本参数
        call_args = mock_session.call_tool.call_args[0]
        assert call_args[0] == "execute_script"
        assert "docker pull" in call_args[1]["script"]
        assert TEST_ALGORITHM_IMAGE in call_args[1]["script"]
        assert "docker run" in call_args[1]["script"]
        
        # 验证返回结果
        assert result["success"] is True
        assert result["task_id"] == TEST_TASK_ID
        assert result["container_name"] == f"algotest_{TEST_TASK_ID}"
        assert result["algorithm_image"] == TEST_ALGORITHM_IMAGE
        assert result["dataset_url"] == TEST_DATASET_URL

    @pytest.mark.asyncio
    @patch('agents.execution_agent.get_test_task')
    async def test_setup_algorithm_container_live(self, mock_get_test_task):
        """实际连接MCP服务器测试setup_algorithm_container函数（需要MCP服务器可用）"""
        # 此测试需要实际的MCP服务器
        if os.environ.get("SKIP_LIVE_MCP_TESTS", "true").lower() == "true":
            pytest.skip("跳过实际MCP服务器测试，设置环境变量SKIP_LIVE_MCP_TESTS=false启用")
            
        # 模拟数据库返回的任务
        mock_task = MagicMock()
        mock_task.algorithm_image = TEST_ALGORITHM_IMAGE
        mock_task.dataset_url = TEST_DATASET_URL
        mock_get_test_task.return_value = mock_task
        
        # 执行被测试的函数
        try:
            result = await setup_algorithm_container(TEST_TASK_ID)
            print(f"\n实际执行结果: {json.dumps(result, ensure_ascii=False, indent=2)}")
            
            # 验证返回结果
            assert result["success"] is True
            assert result["task_id"] == TEST_TASK_ID
            assert result["container_name"] == f"algotest_{TEST_TASK_ID}"
            assert result["algorithm_image"] == TEST_ALGORITHM_IMAGE
            assert result["dataset_url"] == TEST_DATASET_URL
        except Exception as e:
            pytest.fail(f"实际MCP测试失败: {str(e)}")

    @pytest.mark.asyncio
    @patch('agents.execution_agent.get_test_task')
    async def test_execute_container_command_live(self, mock_get_test_task):
        """实际连接MCP服务器测试execute_container_command函数（需要MCP服务器可用）"""
        # 此测试需要实际的MCP服务器和已创建的容器
        if os.environ.get("SKIP_LIVE_MCP_TESTS", "true").lower() == "true":
            pytest.skip("跳过实际MCP服务器测试，设置环境变量SKIP_LIVE_MCP_TESTS=false启用")
            
        # 模拟数据库返回的任务
        mock_task = MagicMock()
        mock_task.algorithm_image = TEST_ALGORITHM_IMAGE
        mock_task.dataset_url = TEST_DATASET_URL
        mock_get_test_task.return_value = mock_task
        
        # 首先设置Docker容器
        setup_result = await setup_algorithm_container(TEST_TASK_ID)
        if not setup_result["success"]:
            pytest.skip(f"无法设置Docker容器: {setup_result.get('error', '未知错误')}")
        
        # 测试在容器中执行命令
        command = "echo 'Hello from container'"
        try:
            result = await execute_container_command(TEST_TASK_ID, command)
            print(f"\n命令执行结果: {json.dumps(result, ensure_ascii=False, indent=2)}")
            
            # 验证返回结果
            assert result["success"] is True
            assert result["task_id"] == TEST_TASK_ID
            assert result["container_name"] == f"algotest_{TEST_TASK_ID}"
            assert result["command"] == command
            assert "Hello from container" in str(result["result"]["stdout"])
        except Exception as e:
            pytest.fail(f"在容器中执行命令失败: {str(e)}")


def run_test_direct():
    """直接运行测试（不通过pytest）"""
    print("===== 开始Docker容器设置测试 =====")
    
    from core.database import init_db, get_db
    import sqlite3
    
    # 初始化测试数据
    def init_test_data():
        try:
            # 使用get_db获取数据库连接
            with get_db() as db:
                # 检查测试任务是否存在
                task = db.execute(
                    "SELECT * FROM test_tasks WHERE task_id = ?", 
                    (TEST_TASK_ID,)
                ).fetchone()
                
                # 如果不存在，则创建
                if not task:
                    db.execute(
                        """
                        INSERT INTO test_tasks 
                        (task_id, algorithm_image, dataset_url, status, created_at, updated_at)
                        VALUES (?, ?, ?, 'created', datetime('now'), datetime('now'))
                        """,
                        (TEST_TASK_ID, TEST_ALGORITHM_IMAGE, TEST_DATASET_URL)
                    )
                    db.commit()
                    print(f"创建测试任务: {TEST_TASK_ID}")
                else:
                    print(f"测试任务已存在: {TEST_TASK_ID}")
                
                # 显示任务信息
                task = db.execute(
                    "SELECT * FROM test_tasks WHERE task_id = ?", 
                    (TEST_TASK_ID,)
                ).fetchone()
                
                if task:
                    print(f"测试任务信息: {dict(task)}")
        except sqlite3.Error as e:
            print(f"数据库操作失败: {str(e)}")
    
    async def run_test():
        try:
            # 初始化测试数据
            init_test_data()
            
            # 测试Docker容器设置
            test_docker = TestDockerSetup()
            await test_docker.test_setup_algorithm_container_live(lambda x: None)
            
            # 测试在容器中执行命令
            await test_docker.test_execute_container_command_live(lambda x: None)
            
            print("\n✅ 测试完成")
        except Exception as e:
            print(f"\n❌ 测试失败: {str(e)}")
    
    # 执行测试
    asyncio.run(run_test())


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--direct":
        # 直接运行测试
        run_test_direct()
    else:
        # 使用pytest运行
        pytest.main(["-xvs", __file__]) 