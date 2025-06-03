#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import json
from core.database import get_db
from sqlalchemy.sql import text
from agents.execution_agent import analyze_result

async def show_full_ai_analysis():
    """显示完整的AI分析内容"""
    with get_db() as db:
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
            print("="*80)
            
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
            
            analyzed_state = await analyze_result(mock_state)
            new_execution_result = analyzed_state.get("execution_result", {})
            ai_analysis = new_execution_result.get("ai_analysis", {})
            
            print("完整的AI分析内容:")
            print("-"*80)
            print(ai_analysis.get("ai_analysis_content", "无AI内容"))
            print("-"*80)
            
            print(f"\n最终分析报告:")
            print(new_execution_result.get("analysis_report", "无分析报告"))

if __name__ == "__main__":
    asyncio.run(show_full_ai_analysis()) 