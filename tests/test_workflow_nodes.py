#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
测试工作流节点功能 - 测试load_test_cases、parse_command、execute_command和save_result四个节点
"""

import os
import sys
import logging
import json
from datetime import datetime
from typing import Dict, Any, List, Optional
from time import time
from sqlalchemy.sql import text
import asyncio
from pathlib import Path

# 将父目录添加到路径，以便导入模块
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
sys.path.append(parent_dir)

# 导入所需模块
from core.database import get_db, TestCase
from agents.execution_agent import load_test_cases, parse_command, execute_command, save_result

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("test_workflow")

# 调试模式开关
DEBUG_MODE = False

def save_debug_info(data: Dict[str, Any], filename: str):
    """保存调试信息到文件（仅在DEBUG_MODE=True时启用）"""
    if not DEBUG_MODE:
        return  # 如果调试模式关闭，则直接返回
        
    try:
        # 创建调试目录
        debug_dir = os.path.join(current_dir, "debug")
        os.makedirs(debug_dir, exist_ok=True)
        
        # 保存数据
        filepath = os.path.join(debug_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"调试信息已保存到: {filepath}")
    except Exception as e:
        logger.error(f"保存调试信息失败: {e}")

def decode_unicode_json(json_str: str) -> str:
    """
    解码包含Unicode编码的中文JSON字符串
    
    Args:
        json_str: 可能包含Unicode编码的字符串
        
    Returns:
        解码后的字符串
    """
    # 如果输入为空，直接返回
    if not json_str:
        return json_str
        
    try:
        # 解码Unicode转义序列
        decoded = json_str.encode().decode('unicode_escape')
        logger.info(f"成功解码Unicode编码的字符串")
        return decoded
    except Exception as e:
        logger.warning(f"解码Unicode编码字符串失败: {e}")
        return json_str

def verify_database_result(case_id: str) -> bool:
    """
    验证测试结果是否成功保存到数据库
    
    Args:
        case_id: 测试用例ID
        
    Returns:
        bool: 是否成功保存到数据库
    """
    with get_db() as db:
        # 查询测试用例表
        test_case = db.query(TestCase).filter(TestCase.case_id == case_id).first()
        if test_case:
            logger.info(f"测试结果已保存到数据库: case_id={case_id}, is_passed={test_case.is_passed}")
            
            # 输出结果分析信息
            if test_case.result_analysis:
                logger.info(f"结果分析: {test_case.result_analysis}")
            
            # 输出actual_output的内容摘要
            if test_case.actual_output:
                try:
                    # 尝试提取输出内容的摘要
                    output_summary = test_case.actual_output[:500] + "..." if len(test_case.actual_output) > 500 else test_case.actual_output
                    logger.info(f"执行结果摘要: {output_summary}")
                except Exception as e:
                    logger.warning(f"提取actual_output摘要失败: {e}")
                    logger.info(f"原始actual_output类型: {type(test_case.actual_output)}")
            
            return True
        else:
            logger.error(f"未在数据库中找到测试用例: case_id={case_id}")
            return False

async def test_load_cases_and_parse_execute_save():
    """测试加载测试用例、解析命令、执行命令和保存结果"""
    # 设置日志记录
    logger.setLevel(logging.INFO)
    
    # 为控制台输出添加处理程序
    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(logging.INFO)
    logger.addHandler(stream_handler)
    
    # 获取任务ID
    task_id = None
    if len(sys.argv) > 1:
        task_id = sys.argv[1]
        logger.info(f"开始测试任务ID: {task_id}")
    else:
        logger.error("未提供任务ID，请通过命令行参数指定")
        return
        
    # 创建一个初始状态
    case_state = {
        "task_id": task_id,
        "current_case_index": 0,
        "test_cases": [],
        "command_strategies": None, 
        "current_strategy_index": 0,
        "status": "created",
        "errors": [],
        "container_ready": True  # 假设容器已准备好
    }
    
    # 第一步：加载测试用例
    try:
        load_result = load_test_cases(case_state)
        if not load_result or load_result.get("status") == "error":
            logger.error(f"加载测试用例失败: {load_result.get('errors', ['未知错误'])}")
            return
            
        test_cases = load_result.get('test_cases', [])
        if not test_cases:
            logger.error("未找到测试用例")
            return
            
        logger.info(f"成功加载测试用例，共 {len(test_cases)} 个")
        case_state = load_result
        
        # 测试解析、执行和保存每个测试用例的命令
        parse_success = 0
        parse_failures = 0
        execute_success = 0
        execute_failures = 0
        save_success = 0
        save_failures = 0
        
        for i, case in enumerate(test_cases):
            logger.info(f"\n处理测试用例 {i+1}/{len(test_cases)}: {case.get('case_id', '未知ID')}")
            
            # 更新状态
            case_state['current_case_index'] = i
            case_id = case.get('case_id')
            if not case_id:
                logger.error("测试用例缺少case_id字段")
                continue
                
            case_state['case_id'] = case_id  # 设置当前用例ID
            
            # 尝试解码input_data中的Unicode编码
            if 'input_data' in case and isinstance(case['input_data'], str):
                try:
                    decoded_input = decode_unicode_json(case['input_data'])
                    logger.info(f"测试用例输入数据:\n{decoded_input[:500]}..." if len(decoded_input) > 500 else f"测试用例输入数据:\n{decoded_input}")
                except Exception as e:
                    logger.warning(f"解码input_data失败: {e}")
            
            # 1. 解析命令
            try:
                logger.info("步骤1：解析命令")
                parse_result = await parse_command(case_state)
                if not parse_result:
                    logger.error(f"命令解析失败: {case_id}")
                    parse_failures += 1
                    continue
                    
                case_state = parse_result
                
                # 获取解析的命令策略
                strategies = case_state.get('command_strategies')
                if strategies and hasattr(strategies, 'strategies'):
                    strategies_list = strategies.strategies
                    logger.info(f"成功解析命令策略，共 {len(strategies_list)} 个")
                    
                    # 打印每个命令策略
                    for j, strategy in enumerate(strategies_list):
                        logger.info(f"  策略 {j+1}: {strategy.tool} - {strategy.description}")
                    
                    parse_success += 1
                else:
                    logger.error(f"未能获取命令策略: {case_id}")
                    parse_failures += 1
                    continue
            except Exception as e:
                logger.error(f"解析命令时出错: {e}")
                parse_failures += 1
                continue
                
            # 2. 执行命令
            try:
                logger.info("步骤2：执行命令")
                execute_result = await execute_command(case_state)
                if not execute_result:
                    logger.error(f"命令执行失败: {case_id}")
                    execute_failures += 1
                    continue
                
                case_state = execute_result
                
                # 获取执行结果
                execution_result = case_state.get('execution_result')
                if execution_result:
                    # 检查执行成功或失败
                    success = execution_result.get('success', False)
                    
                    # 输出MCP工具调用的详细结果
                    logger.info("MCP工具调用结果:")
                    
                    # 详细分析执行结果内容
                    error_found = False
                    result_msg = ""
                    stdout_content = ""
                    stderr_content = ""
                    
                    if 'all_results' in execution_result:
                        # 遍历所有命令执行结果
                        for cmd_idx, cmd_result in enumerate(execution_result['all_results']):
                            result = cmd_result.get('result', {})
                            logger.info(f"命令 {cmd_idx+1} 执行结果:")
                            
                            # 显示完整输出
                            full_output = cmd_result.get('full_output', '')
                            if full_output:
                                logger.info(f"完整输出:\n{full_output}")
                            else:
                                # 如果没有full_output，尝试从result获取stdout和stderr
                                stdout = result.get('stdout', '')
                                stderr = result.get('stderr', '')
                                if stdout:
                                    logger.info(f"标准输出:\n{stdout}")
                                if stderr:
                                    logger.info(f"标准错误:\n{stderr}")
                            
                            # 检查是否有原始输出内容
                            raw_stdout = cmd_result.get('raw_stdout', '')
                            raw_stderr = cmd_result.get('raw_stderr', '')
                            if raw_stdout and raw_stdout != result.get('stdout', ''):
                                logger.info(f"原始标准输出:\n{raw_stdout}")
                            if raw_stderr and raw_stderr != result.get('stderr', ''):
                                logger.info(f"原始标准错误:\n{raw_stderr}")
                            
                            # 检查是否有提取的JSON数据
                            json_data = result.get('extracted_json')
                            if json_data:
                                # 确保使用ensure_ascii=False来正确显示中文
                                json_str = json.dumps(json_data, ensure_ascii=False, indent=2)
                                # 再次应用Unicode解码
                                decoded_json = decode_unicode_json(json_str)
                                logger.info(f"提取的JSON数据:\n{decoded_json}")
                            
                            # 检查是否有错误
                            if not result.get('success', True):
                                error_found = True
                                error_msg = result.get('error', '未知错误')
                                result_msg = f"命令 {cmd_idx+1} 执行失败: {error_msg}"
                                logger.error(result_msg)
                    elif 'result' in execution_result:
                        result_data = execution_result['result']
                        if isinstance(result_data, dict):
                            # 检查stdout中的内容
                            if 'stdout' in result_data:
                                stdout = str(result_data['stdout'])
                                stdout_content = stdout
                                logger.info(f"标准输出:\n{stdout}")
                                
                                # 检查是否在stdout中包含错误信息
                                error_indicators = ["脚本执行失败", "返回码:", "错误:", "Error:", "Failed:"]
                                for indicator in error_indicators:
                                    if indicator in stdout:
                                        error_found = True
                                        result_msg = f"标准输出中检测到错误: {stdout}"
                                        break
                            
                            # 检查stderr中的内容
                            if 'stderr' in result_data and result_data['stderr']:
                                stderr = str(result_data['stderr'])
                                stderr_content = stderr
                                logger.info(f"标准错误:\n{stderr}")
                                if not error_found:  # 如果还没有在stdout中找到错误
                                    error_found = True
                                    result_msg = f"检测到标准错误输出: {stderr}"
                        else:
                            raw_result = str(result_data)
                            logger.info(f"结果内容:\n{raw_result}")
                            
                            # 检查原始结果中的错误信息
                            if "脚本执行失败" in raw_result or "返回码:" in raw_result:
                                error_found = True
                                result_msg = f"原始结果中检测到错误: {raw_result}"
                    
                    # 检查是否有明确的错误字段
                    if 'error' in execution_result and execution_result['error']:
                        error_msg = execution_result['error']
                        logger.error(f"执行错误: {error_msg}")
                        error_found = True
                        result_msg = f"执行返回错误: {error_msg}"
                    
                    # 特殊情况处理: 即使MCP工具返回成功，但输出内容中含有错误信息
                    if not error_found and stdout_content:
                        if isinstance(stdout_content, str):
                            # 再次检查是否包含错误信息的关键词
                            if ("脚本执行失败" in stdout_content or "返回码:" in stdout_content or 
                                "错误:" in stdout_content or "Error:" in stdout_content):
                                error_found = True
                                result_msg = f"标准输出中检测到错误信息: {stdout_content}"
                                logger.warning("检测到标准输出中包含错误信息，但未被前面的逻辑捕获")
                        elif hasattr(stdout_content, 'text') and stdout_content.text:
                            error_text = stdout_content.text
                            if ("脚本执行失败" in error_text or "返回码:" in error_text or 
                                "错误:" in error_text or "Error:" in error_text):
                                error_found = True
                                result_msg = f"TextContent中检测到错误信息: {error_text}"
                                logger.warning("检测到TextContent中包含错误信息，但未被前面的逻辑捕获")
                    
                    # 最终判断命令是否真正成功
                    real_success = success and not error_found
                    
                    # 输出最终执行状态
                    if real_success:
                        logger.info(f"命令执行成功")
                        execute_success += 1
                    else:
                        logger.error(f"命令执行失败: {result_msg if result_msg else '未知原因'}")
                        execute_failures += 1
                else:
                    logger.error("未获取到执行结果")
                    execute_failures += 1
            except Exception as e:
                logger.error(f"执行命令时出错: {e}")
                execute_failures += 1
                continue
                
            # 3. 保存结果
            try:
                logger.info("步骤3：保存结果")
                save_result_state = await save_result(case_state)
                if not save_result_state:
                    logger.error(f"保存结果失败: {case_id}")
                    save_failures += 1
                    continue
                
                case_state = save_result_state
                
                # 验证结果是否已保存到数据库
                if verify_database_result(case_id):
                    logger.info(f"成功验证数据库中的测试结果: {case_id}")
                    save_success += 1
                else:
                    logger.error(f"数据库中未找到测试结果: {case_id}")
                    save_failures += 1
            except Exception as e:
                logger.error(f"保存结果时出错: {e}")
                save_failures += 1
        
        logger.info(f"\n测试完成！总计 {len(test_cases)} 个测试用例")
        logger.info(f"解析命令: 成功 {parse_success} 个，失败 {parse_failures} 个")
        logger.info(f"执行命令: 成功 {execute_success} 个，失败 {execute_failures} 个")
        logger.info(f"保存结果: 成功 {save_success} 个，失败 {save_failures} 个")
        
    except Exception as e:
        logger.error(f"测试过程中发生错误: {e}")
        import traceback
        logger.error(traceback.format_exc())

if __name__ == "__main__":
    asyncio.run(test_load_cases_and_parse_execute_save())