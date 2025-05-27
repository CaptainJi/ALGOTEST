#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
from core.database import get_db
from sqlalchemy.sql import text

def fix_existing_analysis():
    """从existing JSON数据重新生成result_analysis"""
    
    with get_db() as db:
        # 查询所有有actual_output但没有result_analysis的测试用例
        result = db.execute(text("""
            SELECT case_id, actual_output 
            FROM test_cases 
            WHERE actual_output IS NOT NULL 
            AND (result_analysis IS NULL OR result_analysis = '')
            AND task_id = 'TASK1748315635_a78e35cc4125'
        """)).fetchall()
        
        print(f"找到 {len(result)} 个需要修复的测试用例")
        
        fixed_count = 0
        for row in result:
            case_id, actual_output = row
            
            try:
                # 解析JSON数据
                data = json.loads(actual_output)
                
                # 提取分析信息
                success = data.get("success", False)
                original_success = data.get("original_success", success)
                error_detected = data.get("error_detected", False)
                error_messages = data.get("error_messages", [])
                execution_time = data.get("execution_time", 0)
                ai_analysis = data.get("ai_analysis", {})
                analysis_report = data.get("analysis_report", "")
                
                # 构建结果分析
                result_analysis = []
                
                if analysis_report:
                    result_analysis.append("=== AI智能分析报告 ===")
                    result_analysis.append(analysis_report)
                else:
                    # 基本执行状态分析
                    result_analysis.append("=== 执行结果分析 ===")
                    if error_detected:
                        result_analysis.append("✗ 执行过程中发现错误")
                        if error_messages:
                            for msg in error_messages:
                                result_analysis.append(f"  - {msg}")
                    else:
                        result_analysis.append("✓ 执行过程中未发现错误")
                    
                    # 成功状态分析
                    if original_success != success:
                        result_analysis.append(f"✗ 智能分析发现问题：原始判断为{'成功' if original_success else '失败'}，但最终判定为{'成功' if success else '失败'}")
                    else:
                        result_analysis.append(f"{'✓' if success else '✗'} 最终执行结果：{'成功' if success else '失败'}")
                
                # 添加算法分析结果
                if ai_analysis and ai_analysis.get("algorithm_result"):
                    algorithm_result = ai_analysis["algorithm_result"]
                    result_analysis.append("\n=== 算法输出分析 ===")
                    
                    if isinstance(algorithm_result, dict):
                        if algorithm_result.get("format") == "extracted":
                            data_inner = algorithm_result.get("data", {})
                            result_analysis.append(f"✓ 算法结果解析状态: {algorithm_result.get('summary', '已解析')}")
                            
                            if "algorithm_status" in data_inner:
                                status = data_inner['algorithm_status']
                                icon = "✓" if status == "success" else "✗"
                                result_analysis.append(f"{icon} 算法执行状态: {status}")
                        else:
                            result_analysis.append(f"📄 算法输出: {str(algorithm_result)[:200]}...")
                    else:
                        result_analysis.append(f"📄 算法输出: {str(algorithm_result)[:200]}...")
                
                # 性能分析
                if execution_time > 0:
                    result_analysis.append(f"\n=== 性能分析 ===")
                    result_analysis.append(f"⏱️ 总执行耗时: {execution_time}毫秒")
                    if execution_time > 5000:
                        result_analysis.append("⚠️ 执行时间较长，可能需要优化")
                    elif execution_time < 1000:
                        result_analysis.append("✓ 执行效率良好")
                
                # 如果result_analysis为空，添加默认信息
                if not result_analysis:
                    result_analysis.append("=== 基础分析报告 ===")
                    result_analysis.append(f"执行状态: {'成功' if success else '失败'}")
                    if execution_time > 0:
                        result_analysis.append(f"执行耗时: {execution_time}毫秒")
                
                # 更新数据库
                analysis_text = "\n".join(result_analysis)
                db.execute(text("""
                    UPDATE test_cases 
                    SET result_analysis = :analysis 
                    WHERE case_id = :case_id
                """), {"analysis": analysis_text, "case_id": case_id})
                
                fixed_count += 1
                print(f"修复用例 {case_id}: {len(analysis_text)} 字符的分析结果")
                
            except Exception as e:
                print(f"修复用例 {case_id} 失败: {e}")
        
        # 提交更改
        db.commit()
        print(f"成功修复 {fixed_count} 个测试用例的分析结果")

if __name__ == "__main__":
    fix_existing_analysis() 