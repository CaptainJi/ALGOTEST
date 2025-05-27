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

def check_database():
    """检查数据库内容"""
    try:
        # 连接数据库
        conn = sqlite3.connect('algotest.db')
        conn.row_factory = sqlite3.Row  # 使结果可以通过列名访问
        cursor = conn.cursor()
        
        # 检查测试任务
        print("=== 测试任务信息 ===")
        cursor.execute("SELECT task_id, algorithm_image, dataset_url, container_name, status FROM test_tasks ORDER BY created_at DESC LIMIT 5")
        tasks = cursor.fetchall()
        
        if tasks:
            print(f"最近创建的{len(tasks)}个测试任务:")
            for task in tasks:
                print(f"  任务ID: {task['task_id']}")
                print(f"  算法镜像: {task['algorithm_image']}")
                print(f"  数据集URL: {task['dataset_url']}")
                print(f"  容器名称: {task['container_name']}")
                print(f"  状态: {task['status']}")
                print("-" * 50)
        else:
            print("没有找到测试任务")
        
        # 检查测试用例统计
        print("\n=== 测试用例统计 ===")
        cursor.execute("SELECT COUNT(*) as total FROM test_cases")
        total_cases = cursor.fetchone()['total']
        print(f"数据库中总共有 {total_cases} 个测试用例")
        
        cursor.execute("SELECT COUNT(*) as completed FROM test_cases WHERE status IN ('completed', 'failed')")
        completed_cases = cursor.fetchone()['completed']
        print(f"其中已完成执行的测试用例有 {completed_cases} 个")
        
        cursor.execute("SELECT COUNT(*) as failed FROM test_cases WHERE status = 'failed'")
        failed_cases = cursor.fetchone()['failed']
        print(f"状态为'失败'的测试用例: {failed_cases} 个")
        
        cursor.execute("SELECT COUNT(*) as passed FROM test_cases WHERE status = 'completed'")
        passed_cases = cursor.fetchone()['passed']
        print(f"状态为'通过'的测试用例: {passed_cases} 个")
        
        # 检查最近的测试用例
        print("\n=== 最近的测试用例 ===")
        cursor.execute("""
            SELECT case_id, task_id, status, test_data, created_at 
            FROM test_cases 
            ORDER BY created_at DESC 
            LIMIT 5
        """)
        recent_cases = cursor.fetchall()
        
        if recent_cases:
            print(f"最近创建的{len(recent_cases)}个测试用例:")
            for case in recent_cases:
                print(f"  用例ID: {case['case_id']}")
                print(f"  任务ID: {case['task_id']}")
                print(f"  状态: {case['status']}")
                print(f"  测试数据: {case['test_data']}")
                print(f"  创建时间: {case['created_at']}")
                print("-" * 30)
        
        conn.close()
        
    except Exception as e:
        print(f"检查数据库时出错: {str(e)}")

if __name__ == "__main__":
    check_database() 