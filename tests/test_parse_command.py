#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
测试解析命令功能
"""

import os
import sys
import unittest
import asyncio
import json
from unittest.mock import patch, MagicMock, AsyncMock
from typing import Dict, Any, List

# 添加项目根目录到Python路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.execution_agent import ZhipuAIClient, parse_command, CommandStrategy, ExecutionState
from core.logger import get_logger

class MockResponse:
    """模拟智谱AI响应对象"""
    def __init__(self, content):
        self.choices = [MagicMock(message=MagicMock(content=content))]

class TestParseCommand(unittest.IsolatedAsyncioTestCase):
    """测试解析命令功能"""
    
    def setUp(self):
        """测试前的准备工作"""
        # 确保存在命令格式要求文档
        self.cmd_format_path = "/Users/stella/Desktop/cmd_require.md"
        # 如果文档不存在，创建一个测试用的格式文档
        if not os.path.exists(self.cmd_format_path):
            with open(self.cmd_format_path, 'w', encoding='utf-8') as f:
                f.write("# 测试命令格式\n```shell\n./ev_sdk/bin/test-ji-api -f 1 -i /data/000000.jpg -o ./output.jpg -a '{\"draw_confidence\": true}'\n```")
    
    @patch('zhipuai.ZhipuAI')
    async def test_parse_command_with_container(self, mock_zhipuai):
        """测试带容器名称的命令解析"""
        # 模拟ZhipuAI客户端和响应
        mock_client = MagicMock()
        mock_zhipuai.return_value = mock_client
        
        # 模拟容器名称
        container_name = "algotest_TASK123"
        
        # 模拟响应内容
        json_response = json.dumps({
            "tool": "execute_command",
            "parameters": {
                "command": f"docker exec {container_name} ./ev_sdk/bin/test-ji-api -f 1 -i /data/000000.jpg -o ./output.jpg -a '{{\"draw_confidence\": true}}'",
                "working_dir": "/app"
            },
            "description": "在容器中执行目标检测算法的测试命令"
        })
        
        # 设置模拟响应
        mock_client.chat.completions.create.return_value = MockResponse(json_response)
        
        # 创建ZhipuAIClient实例
        client = ZhipuAIClient("fake_api_key", "fake_model")
        
        # 测试解析命令，传入容器名称
        result = await client.parse_command("执行目标检测算法", [], container_name)
        
        # 验证结果
        self.assertEqual(result.tool, "execute_command")
        self.assertEqual(result.parameters["command"], f"docker exec {container_name} ./ev_sdk/bin/test-ji-api -f 1 -i /data/000000.jpg -o ./output.jpg -a '{{\"draw_confidence\": true}}'")
        self.assertEqual(result.parameters["working_dir"], "/app")
        self.assertEqual(result.description, "在容器中执行目标检测算法的测试命令")
    
    @patch('agents.execution_agent.ZhipuAIClient.parse_command')
    @patch('agents.execution_agent.get_db')
    async def test_parse_command_workflow_with_db(self, mock_get_db, mock_parse_command):
        """测试工作流中的parse_command函数获取容器名称"""
        # 模拟数据库连接和查询结果
        mock_db = MagicMock()
        mock_db.query.return_value.fetchone.return_value = {
            "task_id": "TASK123",
            "container_name": "algotest_TASK123_custom"
        }
        mock_get_db.return_value.__enter__.return_value = mock_db
        
        # 模拟测试用例和状态
        test_case = {
            "case_id": "TC123",
            "task_id": "TASK123",
            "input_data": {
                "name": "测试目标检测",
                "purpose": "验证目标检测算法的准确性",
                "steps": "执行目标检测算法"
            }
        }
        
        state = {
            "task_id": "TASK123",
            "test_cases": [test_case],
            "current_case_index": 0,
            "errors": []
        }
        
        # 模拟解析结果
        mock_strategy = CommandStrategy(
            tool="execute_command",
            parameters={"command": "docker exec algotest_TASK123_custom ./ev_sdk/bin/test-ji-api -f 1 -i /data/000000.jpg -o ./output.jpg -a '{\"draw_confidence\": true}'"},
            description="在容器中执行目标检测命令"
        )
        mock_parse_command.return_value = mock_strategy
        
        # 调用工作流中的parse_command函数
        result_state = await parse_command(state)
        
        # 验证结果
        self.assertEqual(result_state["status"], "parsed")
        self.assertEqual(result_state["case_id"], "TC123")
        self.assertEqual(result_state["command_strategy"], mock_strategy)
        
        # 验证数据库查询
        mock_db.query.assert_called_once_with("SELECT * FROM test_tasks WHERE task_id = ?", ["TASK123"])
        # 验证容器名称传递
        mock_parse_command.assert_called_once_with("执行目标检测算法", [], "algotest_TASK123_custom")
    
    @patch('agents.execution_agent.get_db')
    @patch('agents.execution_agent.ZhipuAIClient.parse_command')
    async def test_parse_command_workflow_fallback_container(self, mock_parse_command, mock_get_db):
        """测试工作流中的parse_command函数在容器名称不存在时使用默认格式"""
        # 模拟数据库连接和查询结果 - 无container_name字段
        mock_db = MagicMock()
        mock_db.query.return_value.fetchone.return_value = {
            "task_id": "TASK123"
            # 没有container_name字段
        }
        mock_get_db.return_value.__enter__.return_value = mock_db
        
        # 模拟测试用例和状态
        test_case = {
            "case_id": "TC123",
            "task_id": "TASK123",
            "input_data": {
                "name": "测试目标检测",
                "purpose": "验证目标检测算法的准确性",
                "steps": "执行目标检测算法"
            }
        }
        
        state = {
            "task_id": "TASK123",
            "test_cases": [test_case],
            "current_case_index": 0,
            "errors": []
        }
        
        # 模拟解析结果
        mock_strategy = CommandStrategy(
            tool="execute_command",
            parameters={"command": "docker exec algotest_TASK123 ./ev_sdk/bin/test-ji-api -f 1 -i /data/000000.jpg -o ./output.jpg -a '{\"draw_confidence\": true}'"},
            description="在容器中执行目标检测命令"
        )
        mock_parse_command.return_value = mock_strategy
        
        # 调用工作流中的parse_command函数
        result_state = await parse_command(state)
        
        # 验证结果
        self.assertEqual(result_state["status"], "parsed")
        self.assertEqual(result_state["case_id"], "TC123")
        self.assertEqual(result_state["command_strategy"], mock_strategy)
        
        # 验证容器名称传递 - 应使用默认格式
        mock_parse_command.assert_called_once_with("执行目标检测算法", [], "algotest_TASK123")


if __name__ == "__main__":
    unittest.main() 