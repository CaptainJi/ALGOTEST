#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from core.database import get_db
from sqlalchemy.sql import text

def check_analysis_results():
    """检查测试用例的分析结果字段"""
    
    with get_db() as db:
        # 查询最近的测试用例及其分析结果
        result = db.execute(text("""
            SELECT case_id, status, result_analysis, actual_output, created_at
            FROM test_cases 
            WHERE task_id = 'TASK1748315635_a78e35cc4125'
            ORDER BY created_at DESC 
            LIMIT 5
        """)).fetchall()
        
        print("=== 测试用例分析结果检查 ===")
        for row in result:
            case_id, status, result_analysis, actual_output, created_at = row
            print(f"用例ID: {case_id}")
            print(f"状态: {status}")
            print(f"创建时间: {created_at}")
            
            if result_analysis:
                print(f"分析结果 (前300字符): {result_analysis[:300]}...")
            else:
                print("分析结果: None")
            
            if actual_output:
                print(f"实际输出 (前200字符): {actual_output[:200]}...")
            else:
                print("实际输出: None")
            
            print("-" * 60)

if __name__ == "__main__":
    check_analysis_results() 