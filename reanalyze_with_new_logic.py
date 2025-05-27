#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import asyncio
from core.database import get_db
from sqlalchemy.sql import text
from agents.execution_agent import analyze_result

async def reanalyze_existing_cases():
    """使用新的分析逻辑重新分析现有测试用例"""
    
    with get_db() as db:
        # 查询所有需要重新分析的测试用例
        result = db.execute(text("""
            SELECT case_id, actual_output, input_data, expected_output 
            FROM test_cases 
            WHERE actual_output IS NOT NULL 
            AND task_id = 'TASK1748315635_a78e35cc4125'
        """)).fetchall()
        
        print(f"找到 {len(result)} 个测试用例需要重新分析")
        
        for i, row in enumerate(result):
            case_id, actual_output, input_data, expected_output = row
            
            try:
                print(f"\n正在重新分析用例 {i+1}/{len(result)}: {case_id}")
                
                # 解析actual_output中的执行结果
                execution_data = json.loads(actual_output)
                
                # 构造测试用例对象
                current_case = {
                    "case_id": case_id,
                    "input_data": input_data,
                    "expected_output": expected_output
                }
                
                # 模拟execution state
                mock_state = {
                    "case_id": case_id,
                    "test_cases": [current_case],
                    "current_case_index": 0,
                    "execution_result": {
                        "success": execution_data.get("original_success", execution_data.get("success", False)),
                        "all_results": execution_data.get("results", [])
                    }
                }
                
                # 调用新的分析逻辑
                analyzed_state = await analyze_result(mock_state)
                
                # 获取新的分析结果
                new_execution_result = analyzed_state.get("execution_result", {})
                new_success = new_execution_result.get("success", False)
                new_analysis_report = new_execution_result.get("analysis_report", "")
                
                print(f"  原始成功状态: {execution_data.get('original_success', 'N/A')}")
                print(f"  旧的最终状态: {execution_data.get('success', 'N/A')}")
                print(f"  新的最终状态: {new_success}")
                
                # 更新数据库
                if new_analysis_report:
                    # 更新result_analysis字段
                    db.execute(text("""
                        UPDATE test_cases 
                        SET result_analysis = :analysis,
                            status = :status
                        WHERE case_id = :case_id
                    """), {
                        "analysis": new_analysis_report, 
                        "case_id": case_id,
                        "status": "completed" if new_success else "failed"
                    })
                    
                    # 更新actual_output中的分析数据
                    execution_data.update({
                        "success": new_success,
                        "error_detected": new_execution_result.get("error_detected", False),
                        "error_messages": new_execution_result.get("error_messages", []),
                        "ai_analysis": new_execution_result.get("ai_analysis", {}),
                        "analysis_report": new_analysis_report
                    })
                    
                    db.execute(text("""
                        UPDATE test_cases 
                        SET actual_output = :actual_output
                        WHERE case_id = :case_id
                    """), {
                        "actual_output": json.dumps(execution_data, ensure_ascii=False),
                        "case_id": case_id
                    })
                    
                    print(f"  ✓ 已更新分析结果")
                else:
                    print(f"  ✗ 分析失败，未生成新的分析报告")
                
            except Exception as e:
                print(f"  ✗ 重新分析用例 {case_id} 失败: {e}")
        
        # 提交更改
        db.commit()
        print(f"\n重新分析完成!")

if __name__ == "__main__":
    asyncio.run(reanalyze_existing_cases()) 