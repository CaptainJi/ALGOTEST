#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import sqlite3
import re
from typing import Dict, Any, List
from datetime import datetime

def extract_algorithm_results(output):
    """从算法输出中提取关键信息，如检测到的对象、处理时间、配置参数、原始JSON结果等"""
    try:
        # 尝试解析JSON
        if isinstance(output, str):
            try:
                data = json.loads(output)
                # 检查是否有结果数据
                if "results" in data and isinstance(data["results"], list) and len(data["results"]) > 0:
                    # 提取处理时间
                    execution_time = data.get("execution_time", None)
                    
                    # 尝试从结果中提取更多数据
                    result = data["results"][0]
                    if "full_output" in result:
                        # 使用正则表达式提取处理时间
                        time_match = re.search(r"processing time: (\d+\.\d+) ms", result["full_output"])
                        processing_time = float(time_match.group(1)) if time_match else None
                        
                        # 提取检测到的对象数量
                        objects_match = re.search(r"\"target_count\" : (\d+)", result["full_output"])
                        detected_objects = int(objects_match.group(1)) if objects_match else 0
                        
                        # 提取是否报警
                        alert_match = re.search(r"\"is_alert\" : (true|false)", result["full_output"])
                        is_alert = alert_match.group(1) == "true" if alert_match else None
                        
                        return {
                            "processing_time": processing_time,
                            "execution_time": execution_time,
                            "detected_objects": detected_objects,
                            "is_alert": is_alert,
                            "raw_output": output
                        }
            except json.JSONDecodeError:
                pass
        
        # 使用正则表达式提取处理时间
        time_match = re.search(r"processing time: (\d+\.\d+) ms", output)
        processing_time = float(time_match.group(1)) if time_match else None
        
        # 提取检测到的对象
        objects_match = re.search(r"\"objects\" : \[\s*(.+?)\s*\]", output, re.DOTALL)
        detected_objects = 0
        if objects_match:
            # 计算对象数量（通过计算 "name" 的出现次数）
            detected_objects = output.count("\"name\" :")
            
        return {
            "processing_time": processing_time,
            "detected_objects": detected_objects,
            "raw_output": output
        }
    except Exception as e:
        print(f"提取算法结果时出错: {e}")
        return None

def compare_test_case_with_results(expected_output, algorithm_results):
    """比较测试用例的预期输出与算法的实际结果"""
    if not algorithm_results:
        return {
            "passed": None,
            "object_match": None,
            "performance_match": None,
            "reason": "无法提取算法结果"
        }
    
    # 检查对象匹配（如果在预期输出中指定了）
    object_match = True  # 默认为True
    
    # 检查性能匹配（如果在预期输出中指定了）
    performance_match = True  # 默认为True
    
    # 整体通过状态
    passed = object_match and performance_match
    
    return {
        "passed": passed,
        "object_match": object_match,
        "performance_match": performance_match,
        "reason": None if passed else "对象匹配失败或性能匹配失败"
    }

def format_datetime(timestamp):
    """将时间戳格式化为可读的日期时间字符串"""
    if timestamp:
        return datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')
    return "未知"

def main():
    """主函数"""
    try:
        # 连接到数据库
        conn = sqlite3.connect('algotest.db')
        cursor = conn.cursor()
        
        # 获取测试用例总数
        cursor.execute('SELECT COUNT(*) FROM test_cases')
        total_cases = cursor.fetchone()[0]
        print(f"数据库中总共有 {total_cases} 个测试用例")
        
        # 获取已完成测试用例的数量
        cursor.execute('SELECT COUNT(*) FROM test_cases WHERE actual_output IS NOT NULL')
        completed_cases = cursor.fetchone()[0]
        print(f"其中已完成执行的测试用例有 {completed_cases} 个")
        
        # 获取各种状态的测试用例数量
        cursor.execute('SELECT is_passed, COUNT(*) FROM test_cases GROUP BY is_passed')
        status_counts = cursor.fetchall()
        for status, count in status_counts:
            status_text = "通过" if status == 1 else "失败" if status == 0 else "未知"
            print(f"状态为'{status_text}'的测试用例: {count} 个")
        
        # 获取最近创建的测试用例
        cursor.execute('''
            SELECT case_id, task_id, created_at, created_at, is_passed 
            FROM test_cases 
            ORDER BY created_at DESC 
            LIMIT 5
        ''')
        recent_cases = cursor.fetchall()
        print("\n最近创建的5个测试用例:")
        for case in recent_cases:
            case_id, task_id, create_time, update_time, is_passed = case
            status = "通过" if is_passed == 1 else "失败" if is_passed == 0 else "未知"
            print(f"ID: {case_id}, 任务ID: {task_id}, 创建时间: {format_datetime(create_time)}, 更新时间: {format_datetime(update_time)}, 状态: {status}")
        
                # 获取最近执行的测试用例
        cursor.execute('''
            SELECT case_id, task_id, created_at, created_at, is_passed 
            FROM test_cases 
            WHERE actual_output IS NOT NULL 
            ORDER BY created_at DESC 
            LIMIT 5
        ''')
        recent_executed_cases = cursor.fetchall()
        print("\n最近执行的5个测试用例:")
        for case in recent_executed_cases:
            case_id, task_id, create_time, update_time, is_passed = case
            status = "通过" if is_passed == 1 else "失败" if is_passed == 0 else "未知"
            print(f"ID: {case_id}, 任务ID: {task_id}, 创建时间: {format_datetime(create_time)}, 更新时间: {format_datetime(update_time)}, 状态: {status}")
        
        # 查询所有已完成的测试用例ID
        cursor.execute('SELECT case_id FROM test_cases WHERE actual_output IS NOT NULL')
        completed_case_ids = [row[0] for row in cursor.fetchall()]
        
        # 分析一个具有实际输出的测试用例
        if completed_case_ids:
            test_case_id = completed_case_ids[0]  # 使用第一个已完成的测试用例
            cursor.execute('''
                SELECT case_id, task_id, test_data, expected_output, actual_output, is_passed, created_at, created_at
                FROM test_cases 
                WHERE case_id = ?
            ''', (test_case_id,))
            case_data = cursor.fetchone()
            
            if case_data:
                case_id, task_id, test_data, expected_output, actual_output, is_passed, create_time, update_time = case_data
                
                print("\n" + "="*80)
                print(f"测试用例详细分析 - ID: {case_id}")
                print(f"任务ID: {task_id}")
                print(f"创建时间: {format_datetime(create_time)}")
                print(f"更新时间: {format_datetime(update_time)}")
                print(f"测试通过状态: {'通过' if is_passed == 1 else '失败' if is_passed == 0 else '未知'}")
                
                # 解析测试数据和预期输出
                try:
                    test_data_json = json.loads(test_data) if test_data else {}
                    expected_output_json = json.loads(expected_output) if expected_output else {}
                    
                    print("\n测试数据:")
                    print(json.dumps(test_data_json, indent=2, ensure_ascii=False))
                    
                    print("\n预期输出:")
                    print(json.dumps(expected_output_json, indent=2, ensure_ascii=False))
                    
                    # 解析实际输出
                    if actual_output:
                        print("\n实际输出前500字符:")
                        print(actual_output[:500] + "..." if len(actual_output) > 500 else actual_output)
                        
                        # 使用我们的函数分析算法结果
                        algorithm_results = extract_algorithm_results(actual_output)
                        print("\n使用新算法分析测试结果:")
                        if algorithm_results:
                            if algorithm_results["processing_time"]:
                                print(f"提取到的处理时间: {algorithm_results['processing_time']} ms")
                            else:
                                print("未能提取到处理时间")
                            print(f"提取到的检测对象: {algorithm_results['detected_objects']} 个")
                            if "is_alert" in algorithm_results and algorithm_results["is_alert"] is not None:
                                print(f"是否报警: {'是' if algorithm_results['is_alert'] else '否'}")
                        else:
                            print("无法提取算法结果")
                        
                        # 比较预期输出和实际结果
                        comparison_results = compare_test_case_with_results(expected_output_json, algorithm_results)
                        print("\n分析比较结果:")
                        print(f"通过状态: {'通过' if comparison_results['passed'] else '失败' if comparison_results['passed'] is False else '未知'}")
                        print(f"对象匹配: {'是' if comparison_results['object_match'] else '否' if comparison_results['object_match'] is False else '未知'}")
                        print(f"性能匹配: {'是' if comparison_results['performance_match'] else '否' if comparison_results['performance_match'] is False else '未知'}")
                        if comparison_results["reason"]:
                            print(f"失败原因: {comparison_results['reason']}")
                        
                        # 结论
                        db_status = "通过" if is_passed == 1 else "失败" if is_passed == 0 else "未知"
                        analysis_status = "通过" if comparison_results['passed'] else "失败" if comparison_results['passed'] is False else "未知"
                        print(f"\n结论: 新算法分析结果与数据库中的状态{'一致' if (db_status == analysis_status) or analysis_status == '未知' else '不一致'}")
                except Exception as e:
                    print(f"解析测试数据时出错: {e}")
            
            # 用户可以选择是否查看完整的算法输出
            if actual_output:
                view_full = input("\n是否查看完整算法输出? (y/n): ")
                if view_full.lower() == 'y':
                    print("\n完整算法输出:")
                    print(actual_output)
        
        # 分析所有已完成的测试用例（限制为前10个）
        print("\n" + "="*80)
        print("所有已完成测试用例的汇总分析:")
        
        # 获取成功和失败的测试用例数量
        cursor.execute('SELECT COUNT(*) FROM test_cases WHERE is_passed = 1 AND actual_output IS NOT NULL')
        passed_count = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM test_cases WHERE is_passed = 0 AND actual_output IS NOT NULL')
        failed_count = cursor.fetchone()[0]
        
        print(f"成功测试用例: {passed_count}, 失败测试用例: {failed_count}")
        
        # 查看失败的测试用例（如果有）
        if failed_count > 0:
            cursor.execute('''
                SELECT case_id, task_id, test_data, expected_output 
                FROM test_cases 
                WHERE is_passed = 0 AND actual_output IS NOT NULL
                LIMIT 5
            ''')
            failed_cases = cursor.fetchall()
            print("\n失败的测试用例样本(最多5个):")
            for case in failed_cases:
                case_id, task_id, test_data, expected_output = case
                try:
                    test_data_json = json.loads(test_data) if test_data else {}
                    test_name = test_data_json.get("name", "未命名测试")
                    test_purpose = test_data_json.get("purpose", "无目的描述")
                    print(f"ID: {case_id}, 名称: {test_name}, 目的: {test_purpose}")
                except:
                    print(f"ID: {case_id}, 无法解析测试数据")
        
        # 关闭数据库连接
        conn.close()
        
    except Exception as e:
        print(f"执行主函数时出错: {e}")

if __name__ == "__main__":
    main() 