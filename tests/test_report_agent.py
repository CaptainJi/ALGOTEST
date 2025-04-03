#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
测试report_agent功能
"""

import os
import sys
import asyncio
import logging
from pathlib import Path

# 将父目录添加到路径，以便导入模块
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
sys.path.append(parent_dir)

from agents.report_agent import run_report_generation
from core.logger import get_logger

# 配置日志
logger = get_logger("test_report")

async def test_report_generation():
    """测试报告生成功能"""
    # 设置测试任务ID
    task_id = "TASK1742525623_6049f9a3d00c"
    
    try:
        logger.info(f"开始测试报告生成: {task_id}")
        
        # 运行报告生成
        result = await run_report_generation(task_id)
        
        # 检查结果
        logger.info(f"报告生成状态: {result['status']}")
        
        if result['status'] == 'error':
            logger.error(f"报告生成失败: {result.get('errors', [])}")
            return
            
        # 输出分析结果统计
        test_cases = result.get('test_cases', [])
        analysis_results = result.get('analysis_results', [])
        
        if test_cases:
            passed_count = sum(1 for case in test_cases if case.get('is_passed', False))
            total_count = len(test_cases)
            
            logger.info(f"测试用例统计:")
            logger.info(f"- 总数: {total_count}")
            logger.info(f"- 通过: {passed_count}")
            logger.info(f"- 失败: {total_count - passed_count}")
            
            # 输出每个测试用例的分析结果
            logger.info("\n详细分析结果:")
            for case in test_cases:
                logger.info(f"\n测试用例: {case['case_id']}")
                logger.info(f"状态: {'通过' if case.get('is_passed') else '失败'}")
                logger.info(f"分析结果:\n{case.get('result_analysis', '无分析结果')}")
        
        logger.success("报告生成测试完成")
        
    except Exception as e:
        logger.error(f"测试过程出错: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())

if __name__ == "__main__":
    asyncio.run(test_report_generation())
