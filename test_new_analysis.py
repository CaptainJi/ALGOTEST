#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import json
from core.database import get_db
from sqlalchemy.sql import text
from agents.execution_agent import analyze_result

async def test_single_case():
    """测试单个用例的新分析逻辑"""
    with get_db() as db:
        # 获取一个测试用例
        result = db.execute(text("""
            SELECT case_id, actual_output, input_data, expected_output 
            FROM test_cases 
            WHERE actual_output IS NOT NULL 
            AND task_id = 'TASK1748315635_a78e35cc4125'
            LIMIT 1
        """)).fetchone()
        
        if result:
            case_id, actual_output, input_data, expected_output = result
            print(f"测试用例: {case_id}")
            
            execution_data = json.loads(actual_output)
            current_case = {
                "case_id": case_id,
                "input_data": input_data,
                "expected_output": expected_output
            }
            
            mock_state = {
                "case_id": case_id,
                "test_cases": [current_case],
                "current_case_index": 0,
                "execution_result": {
                    "success": execution_data.get("original_success", execution_data.get("success", False)),
                    "all_results": execution_data.get("results", [])
                }
            }
            
            print("开始新的AI分析...")
            analyzed_state = await analyze_result(mock_state)
            new_execution_result = analyzed_state.get("execution_result", {})
            
            print(f"原始状态: {execution_data.get('original_success', 'N/A')}")
            print(f"新分析状态: {new_execution_result.get('success', 'N/A')}")
            
            ai_analysis = new_execution_result.get("ai_analysis", {})
            ai_content = ai_analysis.get("ai_analysis_content", "无AI内容")
            print(f"AI分析内容前300字符: {ai_content[:300]}...")
            
            if "AI分析失败" in ai_content:
                print("⚠️ AI分析失败，可能是API配置问题")
            elif len(ai_content) > 50:
                print("✓ AI分析成功，生成了详细分析内容")
            else:
                print("⚠️ AI分析内容较短，可能有问题")

if __name__ == "__main__":
    asyncio.run(test_single_case()) 