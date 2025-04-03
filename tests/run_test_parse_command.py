#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
运行解析命令测试 - 包括容器名称支持测试
"""

import os
import sys
import unittest

# 添加项目根目录到Python路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if __name__ == "__main__":
    print("开始运行命令解析测试，包括容器名称支持...")
    
    # 导入测试模块
    from tests.test_parse_command import TestParseCommand
    
    # 创建测试加载器
    loader = unittest.TestLoader()
    
    # 加载测试
    suite = loader.loadTestsFromTestCase(TestParseCommand)
    
    # 创建测试运行器
    runner = unittest.TextTestRunner(verbosity=2)
    
    # 运行测试
    result = runner.run(suite)
    
    # 输出结果摘要
    print(f"\n测试结果摘要:")
    print(f"运行测试数: {result.testsRun}")
    print(f"成功: {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f"失败: {len(result.failures)}")
    print(f"错误: {len(result.errors)}")
    
    # 设置退出代码
    sys.exit(not result.wasSuccessful()) 