#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
文件作用：分析Agent模块，负责解析需求文档、生成测试用例
开发规划：实现PDF读取、大模型生成测试用例、存储到数据库的工作流
"""

import os
import json
import tempfile
from typing import Dict, Any, List, TypedDict, Optional
from datetime import datetime
from loguru import logger
from langgraph.graph import StateGraph
import subprocess

from core.config import get_settings, get_llm_config
from core.database import create_test_task, create_test_case, get_db
from core.utils import generate_unique_id, format_timestamp, ensure_dir, read_file, write_file
from core.logger import get_logger
from core.llm import call_zhipu_api

# 获取带上下文的logger
log = get_logger("analysis_agent")

class AnalysisState(TypedDict):
    """分析Agent状态定义"""
    task_id: str  # 任务ID
    requirement_doc_path: str  # 需求文档路径
    algorithm_image: str  # 算法镜像
    dataset_url: Optional[str]  # 数据集URL
    pdf_content: Optional[str]  # PDF文档内容
    test_cases: Optional[List[Dict[str, Any]]]  # 生成的测试用例
    errors: List[str]  # 错误信息
    status: str  # 任务状态


def read_pdf_content(state: AnalysisState) -> AnalysisState:
    """
    读取PDF文档内容，不进行转换，直接作为prompt的一部分
    
    Args:
        state: 当前状态
        
    Returns:
        更新后的状态
    """
    log.info(f"开始读取PDF内容: {state['task_id']}")
    
    try:
        # 检查文件是否存在
        pdf_path = state['requirement_doc_path']
        if not os.path.exists(pdf_path):
            raise FileNotFoundError(f"需求文档不存在")
        
        # 读取PDF文件内容
        try:
            # 尝试使用PyPDF2读取PDF内容
            import PyPDF2
            pdf_content = ""
            
            with open(pdf_path, 'rb') as file:
                pdf_reader = PyPDF2.PdfReader(file)
                for page_num in range(len(pdf_reader.pages)):
                    page = pdf_reader.pages[page_num]
                    pdf_content += page.extract_text() + "\n\n"
            
            if not pdf_content.strip():
                raise ValueError("PDF内容提取为空")
                
        except (ImportError, Exception) as e:
            # 如果PyPDF2不可用或提取失败，尝试使用pdfminer
            try:
                from pdfminer.high_level import extract_text
                pdf_content = extract_text(pdf_path)
                
                if not pdf_content.strip():
                    raise ValueError("PDF内容提取为空")
                    
            except (ImportError, Exception) as e2:
                # 如果两种方法都失败，尝试使用系统命令
                try:
                    # 尝试使用pdftotext命令行工具
                    temp_txt = tempfile.NamedTemporaryFile(delete=False, suffix='.txt')
                    temp_txt.close()
                    
                    subprocess.run(['pdftotext', pdf_path, temp_txt.name], check=True)
                    
                    with open(temp_txt.name, 'r', encoding='utf-8', errors='ignore') as f:
                        pdf_content = f.read()
                    
                    os.unlink(temp_txt.name)
                    
                    if not pdf_content.strip():
                        raise ValueError("PDF内容提取为空")
                        
                except (subprocess.SubprocessError, Exception) as e3:
                    # 所有方法都失败，直接报错
                    raise ValueError("无法提取PDF内容")
        
        log.success(f"PDF内容读取成功: {state['task_id']}")
        
        # 更新状态
        return {
            **state,
            "pdf_content": pdf_content,
            "status": "pdf_read"
        }
    except Exception as e:
        log.error(f"PDF内容读取失败: {str(e)}")
        return {
            **state,
            "errors": state.get("errors", []) + ["读取失败: " + str(e)],
            "status": "error"
        }


def generate_test_cases(state: AnalysisState) -> AnalysisState:
    """
    使用大模型根据PDF内容直接生成测试用例
    
    Args:
        state: 当前状态
        
    Returns:
        更新后的状态
    """
    log.info("开始生成测试用例: {}".format(state['task_id']))
    
    try:
        # 获取PDF内容
        pdf_content = state.get("pdf_content")
        if not pdf_content:
            raise ValueError("PDF内容为空")
        
        standard_parameters = f"""
        1.draw_roi_area
            false 不画出roi框
            true 区域被框正方下发

        2.polygin
            在roi内进行分割
            在非roi没有分割

        3.roi_color
            颜色为框发生变动

        4.roi_line_thickness
            roi大小发生变动

        5.roi_fill
            true 用颜色填充整个roi
            false 选择填充, 只画roi框

        6.draw_result
            true 画出目标框和目标文本
            false 不画出目标框和目标文本

        7.draw_confidence
            false 不显示置信度
            置信度小于预设值thresh, 不对应可视化, 并且红色
            置信度大于预设值thresh, 进行可视化, 并且绿色

        8.language
            zh 中文字符集采用中文标注

        9.target_rect_color
            修改目标框颜色发生改变

        10.object_rect_line_thickness
            修改目标框大小发生改变

        11.object_text_color
            修改目标文本颜色发生改变

        12.object_text_bg_color
            修改目标文本背景颜色发生改变

        13.object_text_size
            修改目标文本大小发生改变

        14.draw_warning_text
            true 画出警告文本
            false 不画出警告文本

        15.warning_text_en
            修改英文警告字符发生改变

        16.warning_text_zh
            修改中文警告字符发生改变

        17.warning_text_size
            修改警告文本大小发生改变

        18.warning_text_color
            修改警告文本颜色发生改变

        19.warning_text_bg_color
            修改警告文本背景颜色发生改变
        
        """
        
        # 获取LLM配置
        llm_config = get_llm_config()
        
        # 构建提示词
        prompt = f"""
        请根据以下算法需求文档和标准参数说明，生成一系列全面的测试用例，以验证算法的功能和性能。
        
        需求文档：
        {pdf_content}

        标准参数说明：
        {standard_parameters}
        
        请特别注意：
        1. 必须全面验证"算法报警逻辑"部分的正确性，确保算法能够按照文档描述的业务场景和逻辑正确工作
        2. 必须对"二、自定义配置参数 2、自定义参数说明"部分中的每个有编号的参数进行专门的测试，确保每个参数都能正确工作
        3. 每个"二、自定义配置参数 2、自定义参数说明"中的自定义参数至少需要一个专门的测试用例，测试其功能和边界条件
        4. 测试用例应该覆盖参数的默认值、边界值和特殊值情况
        5. 测试用例应该包含所有标准参数的测试，共19个标准参数
        6. 每个测试用例仅对应一条执行命令，不能对应多条执行命令
        
        生成的测试用例必须包含以下信息：
        1. 测试名称：简短描述测试内容，应当明确指出测试的参数或功能
        2. 测试目的：详细说明测试什么功能或参数，以及为什么需要测试它
        3. 测试步骤：如何执行测试，需要详细的步骤，包括具体的参数设置值
        4. 预期结果：测试应该产生什么结果，要具体到数值或状态
        5. 验证方法：如何验证测试结果，包括检查哪些输出字段和值
        
        请按照以下格式输出每个测试用例：
        
        ## 测试用例1：[测试名称]
        - 测试目的：[测试目的]
        - 测试步骤：[测试步骤]
        - 预期结果：[预期结果]
        - 验证方法：[验证方法]
        
        ## 测试用例2：[测试名称]
        - 测试目的：[测试目的]
        - 测试步骤：[测试步骤]
        - 预期结果：[预期结果]
        - 验证方法：[验证方法]
        
        ...以此类推
        
        因为后续的执行测试需要用到的命令为 ./test-ji-api -a 参数名=参数值, 通过-a或-u参数来设置不同的参数进行测试，
        所以在测试步骤中必须明确指出：
        1. 需要设置的具体参数名称
        2. 参数的具体值
        3. 如果有多个参数需要同时设置，请分别列出
        
        例如测试步骤可以这样描述：
        "设置参数 visual_object=false，然后运行算法检测图像"

        预期结果可以根据需求文档中 三、输出json 中的内容来描述
        
        请确保测试用例全面覆盖以下内容：
        1. 算法报警逻辑的正确性验证
        2. 每个自定义配置参数的功能验证
        3. 参数组合使用的场景
        4. 边界条件和异常情况处理
        """
        
        # 调用大模型API
        log.info("调用大模型API生成测试用例: {}".format(state['task_id']))
        test_cases_text = call_zhipu_api(prompt, llm_config)
        
        # 如果API调用失败，返回错误状态
        if test_cases_text.startswith("API调用失败"):
            log.error("大模型API调用失败: {}".format(state['task_id']))
            return {
                **state,
                "errors": state.get("errors", []) + ["大模型API调用失败"],
                "status": "error"
            }
        
        # 解析测试用例文本，转换为结构化数据
        test_cases = []
        
        # 使用更健壮的解析方法
        import re
        
        # 查找所有测试用例
        test_case_pattern = r"##\s*测试用例\d+：(.*?)(?=##|$)"
        test_cases_matches = re.findall(test_case_pattern, test_cases_text, re.DOTALL)
        
        for i, case_text in enumerate(test_cases_matches):
            # 创建测试用例基本结构
            case = {
                "id": generate_unique_id("TC"),
                "name": "",
                "description": "",
                "purpose": "",
                "steps": "",
                "expected_result": "",
                "validation_method": ""
            }
            
            # 提取测试名称
            name_match = re.search(r"测试用例\d+：(.*?)[\r\n]", case_text)
            if name_match:
                case["name"] = name_match.group(1).strip()
            else:
                # 尝试从第一行提取名称
                first_line = case_text.strip().split('\n')[0]
                case["name"] = first_line.strip()
            
            # 提取测试目的
            purpose_match = re.search(r"测试目的：(.*?)(?=-\s*测试步骤|\n-\s*预期结果|\n-\s*验证方法|$)", case_text, re.DOTALL)
            if purpose_match:
                case["purpose"] = purpose_match.group(1).strip()
                case["description"] = "测试目的：{}".format(case['purpose'])
            
            # 提取测试步骤
            steps_match = re.search(r"测试步骤：(.*?)(?=-\s*测试目的|\n-\s*预期结果|\n-\s*验证方法|$)", case_text, re.DOTALL)
            if steps_match:
                case["steps"] = steps_match.group(1).strip()
            
            # 提取预期结果
            expected_match = re.search(r"预期结果：(.*?)(?=-\s*测试目的|\n-\s*测试步骤|\n-\s*验证方法|$)", case_text, re.DOTALL)
            if expected_match:
                case["expected_result"] = expected_match.group(1).strip()
            
            # 提取验证方法
            validation_match = re.search(r"验证方法：(.*?)(?=-\s*测试目的|\n-\s*测试步骤|\n-\s*预期结果|$)", case_text, re.DOTALL)
            if validation_match:
                case["validation_method"] = validation_match.group(1).strip()
            
            # 添加到测试用例列表
            test_cases.append(case)
        
        # 如果没有找到测试用例，尝试使用备用解析方法
        if not test_cases:
            log.warning("使用备用解析方法: {}".format(state['task_id']))
            
            # 按行解析
            lines = test_cases_text.strip().split('\n')
            current_case = None
            current_field = None
            
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                
                # 检测新测试用例的开始
                if line.startswith("## 测试用例") or line.startswith("测试用例"):
                    # 保存前一个测试用例
                    if current_case and current_case.get("name"):
                        test_cases.append(current_case)
                    
                    # 创建新测试用例
                    current_case = {
                        "id": generate_unique_id("TC"),
                        "name": line.split("：", 1)[1].strip() if "：" in line else line,
                        "description": line,
                        "purpose": "",
                        "steps": "",
                        "expected_result": "",
                        "validation_method": ""
                    }
                    current_field = None
                
                # 检测字段
                elif line.startswith("- 测试目的：") or line.startswith("测试目的："):
                    current_field = "purpose"
                    value = line.split("：", 1)[1].strip()
                    if current_case:
                        current_case[current_field] = value
                        if not current_case["description"]:
                            current_case["description"] = "测试目的：{}".format(value)
                
                elif line.startswith("- 测试步骤：") or line.startswith("测试步骤："):
                    current_field = "steps"
                    value = line.split("：", 1)[1].strip()
                    if current_case:
                        current_case[current_field] = value
                
                elif line.startswith("- 预期结果：") or line.startswith("预期结果："):
                    current_field = "expected_result"
                    value = line.split("：", 1)[1].strip()
                    if current_case:
                        current_case[current_field] = value
                
                elif line.startswith("- 验证方法：") or line.startswith("验证方法："):
                    current_field = "validation_method"
                    value = line.split("：", 1)[1].strip()
                    if current_case:
                        current_case[current_field] = value
                
                # 继续当前字段的内容
                elif current_field and current_case:
                    current_case[current_field] += " {}".format(line)
            
            # 添加最后一个测试用例
            if current_case and current_case.get("name"):
                test_cases.append(current_case)
        
        log.success("测试用例生成成功: {}, 共{}个测试用例".format(state['task_id'], len(test_cases)))
        
        # 更新状态
        return {
            **state,
            "test_cases": test_cases,
            "status": "generated"
        }
    except Exception as e:
        log.error("测试用例生成失败: {}".format(str(e)))
        return {
            **state,
            "errors": state.get("errors", []) + [str(e)],
            "status": "error"
        }


def save_to_database(state: AnalysisState) -> AnalysisState:
    """
    将生成的测试用例保存到数据库
    
    Args:
        state: 当前状态
        
    Returns:
        更新后的状态
    """
    log.info(f"开始保存测试用例到数据库: {state['task_id']}")
    
    try:
        # 获取测试用例
        test_cases = state.get("test_cases", [])
        if not test_cases:
            raise ValueError("测试用例为空")
        
        # 创建测试任务
        task_data = {
            "task_id": state["task_id"],
            "requirement_doc": state.get("pdf_content", ""),
            "algorithm_image": state["algorithm_image"],
            "dataset_url": state.get("dataset_url"),
            "status": "created"
        }
        
        # 保存到数据库
        with get_db() as db:
            # 创建测试任务
            create_test_task(task_data)
            
            # 创建测试用例
            for case in test_cases:
                case_data = {
                    "task_id": state["task_id"],
                    "case_id": case["id"],
                    "input_data": {
                        "name": case["name"],
                        "description": case["description"],
                        "purpose": case["purpose"],
                        "steps": case["steps"]
                    },
                    "expected_output": {
                        "expected_result": case["expected_result"],
                        "validation_method": case["validation_method"]
                    }
                }
                create_test_case(case_data)
        
        log.success(f"测试用例保存成功: {state['task_id']}")
        
        # 更新状态
        return {
            **state,
            "status": "saved"
        }
    except Exception as e:
        log.error(f"测试用例保存失败: {str(e)}")
        return {
            **state,
            "errors": state.get("errors", []) + [str(e)],
            "status": "error"
        }


def create_analysis_graph() -> StateGraph:
    """
    创建分析Agent工作流图
    
    Returns:
        工作流图
    """
    # 创建工作流图
    analysis_graph = StateGraph(AnalysisState)
    
    # 添加节点
    analysis_graph.add_node("read_pdf_content", read_pdf_content)
    analysis_graph.add_node("generate_test_cases", generate_test_cases)
    analysis_graph.add_node("save_to_database", save_to_database)
    
    # 添加边
    analysis_graph.add_edge("read_pdf_content", "generate_test_cases")
    analysis_graph.add_edge("generate_test_cases", "save_to_database")
    
    # 设置入口点和结束点
    analysis_graph.set_entry_point("read_pdf_content")
    analysis_graph.set_finish_point("save_to_database")
    
    return analysis_graph


def run_analysis(requirement_doc_path: str, algorithm_image: str, dataset_url: Optional[str] = None) -> Dict[str, Any]:
    """
    运行分析Agent
    
    Args:
        requirement_doc_path: 需求文档路径
        algorithm_image: 算法镜像
        dataset_url: 数据集URL
        
    Returns:
        分析结果
    """
    # 创建工作流图
    analysis_graph = create_analysis_graph()
    
    # 编译工作流
    analysis_app = analysis_graph.compile()
    
    # 创建初始状态
    task_id = generate_unique_id("TASK")
    initial_state = {
        "task_id": task_id,
        "requirement_doc_path": requirement_doc_path,
        "algorithm_image": algorithm_image,
        "dataset_url": dataset_url,
        "pdf_content": None,
        "test_cases": None,
        "errors": [],
        "status": "created"
    }
    
    # 运行工作流
    log.info(f"开始运行分析Agent: {task_id}")
    result = analysis_app.invoke(initial_state)
    log.info(f"分析Agent运行完成: {task_id}, 状态: {result['status']}")
    
    return result