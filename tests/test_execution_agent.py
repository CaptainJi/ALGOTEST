#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
测试执行Agent模块的功能
"""

import os
import sys
import unittest
import json
from unittest.mock import patch, MagicMock, ANY
from typing import Dict, Any, List

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.execution_agent import load_test_cases, ExecutionState
from core.database import get_db


class TestLoadTestCases(unittest.TestCase):
    """测试加载测试用例功能"""

    def setUp(self):
        """测试前设置"""
        # 模拟数据库返回的测试用例数据
        self.mock_test_case1 = {
            "id": 1,
            "task_id": "TASK12345",
            "case_id": "TC12345",
            "document_id": "DOC12345",
            "input_data": {
                "name": "测试用例1",
                "purpose": "测试算法基本功能",
                "steps": "执行python main.py --test"
            },
            "expected_output": {
                "expected_result": "程序正常退出，返回码为0",
                "validation_method": "检查返回码"
            }
        }
        
        self.mock_test_case2 = {
            "id": 2,
            "task_id": "TASK12345",
            "case_id": "TC67890",
            "document_id": "DOC12345",
            "input_data": {
                "name": "测试用例2",
                "purpose": "测试错误处理",
                "steps": "执行python main.py --invalid-arg"
            },
            "expected_output": {
                "expected_result": "程序应输出错误信息",
                "validation_method": "检查标准错误输出"
            }
        }
        
        # 初始状态
        self.base_state = {
            "task_id": "TASK12345",
            "case_id": None,
            "algorithm_image": "test_image:latest",
            "dataset_url": None,
            "test_cases": [],
            "current_case_index": 0,
            "command_strategy": None,
            "execution_result": None,
            "errors": [],
            "status": "created",
            "container_ready": False
        }

    @patch('agents.execution_agent.get_db')
    def test_load_all_test_cases(self, mock_get_db):
        """测试加载任务的所有测试用例"""
        # 模拟数据库连接和查询结果
        mock_db = MagicMock()
        mock_query = MagicMock()
        mock_query.fetchall.return_value = [self.mock_test_case1, self.mock_test_case2]
        mock_db.query.return_value = mock_query
        mock_get_db.return_value.__enter__.return_value = mock_db
        
        # 执行函数
        state = load_test_cases(self.base_state)
        
        # 验证结果
        self.assertEqual(state["status"], "loaded")
        self.assertEqual(len(state["test_cases"]), 2)
        self.assertEqual(state["current_case_index"], 0)
        self.assertEqual(state["test_cases"][0]["case_id"], "TC12345")
        self.assertEqual(state["test_cases"][1]["case_id"], "TC67890")
        
        # 验证调用
        mock_db.query.assert_called_once_with(
            "SELECT * FROM test_cases WHERE task_id = ?", 
            ["TASK12345"]
        )

    @patch('agents.execution_agent.get_db')
    def test_load_specific_test_case(self, mock_get_db):
        """测试加载特定的测试用例"""
        # 设置状态包含case_id
        state_with_case_id = {**self.base_state, "case_id": "TC12345"}
        
        # 模拟数据库连接和查询结果
        mock_db = MagicMock()
        mock_query = MagicMock()
        mock_query.fetchone.return_value = self.mock_test_case1
        mock_db.query.return_value = mock_query
        mock_get_db.return_value.__enter__.return_value = mock_db
        
        # 执行函数
        state = load_test_cases(state_with_case_id)
        
        # 验证结果
        self.assertEqual(state["status"], "loaded")
        self.assertEqual(len(state["test_cases"]), 1)
        self.assertEqual(state["test_cases"][0]["case_id"], "TC12345")
        
        # 验证调用
        mock_db.query.assert_called_once_with(
            "SELECT * FROM test_cases WHERE case_id = ? AND task_id = ?", 
            ["TC12345", "TASK12345"]
        )

    @patch('agents.execution_agent.get_db')
    def test_no_test_cases_found(self, mock_get_db):
        """测试没有找到测试用例的情况"""
        # 模拟数据库连接和空的查询结果
        mock_db = MagicMock()
        mock_query = MagicMock()
        mock_query.fetchall.return_value = []
        mock_db.query.return_value = mock_query
        mock_get_db.return_value.__enter__.return_value = mock_db
        
        # 执行函数
        state = load_test_cases(self.base_state)
        
        # 验证结果
        self.assertEqual(state["status"], "error")
        self.assertTrue(any("没有测试用例" in error for error in state["errors"]))

    @patch('agents.execution_agent.get_db')
    def test_specific_test_case_not_found(self, mock_get_db):
        """测试指定的测试用例未找到的情况"""
        # 设置状态包含不存在的case_id
        state_with_invalid_case_id = {**self.base_state, "case_id": "TC_NONEXISTENT"}
        
        # 模拟数据库连接和空的查询结果
        mock_db = MagicMock()
        mock_query = MagicMock()
        mock_query.fetchone.return_value = None
        mock_db.query.return_value = mock_query
        mock_get_db.return_value.__enter__.return_value = mock_db
        
        # 执行函数
        state = load_test_cases(state_with_invalid_case_id)
        
        # 验证结果
        self.assertEqual(state["status"], "error")
        self.assertTrue(any("找不到指定的测试用例" in error for error in state["errors"]))

    @patch('agents.execution_agent.get_db')
    def test_database_error(self, mock_get_db):
        """测试数据库错误的情况"""
        # 模拟数据库连接抛出异常
        mock_get_db.return_value.__enter__.side_effect = Exception("数据库连接失败")
        
        # 执行函数
        state = load_test_cases(self.base_state)
        
        # 验证结果
        self.assertEqual(state["status"], "error")
        self.assertTrue(any("数据库连接失败" in error for error in state["errors"]))


if __name__ == "__main__":
    unittest.main()
