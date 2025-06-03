#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
文件作用：报告Agent模块，负责分析测试结果并生成测试报告
开发规划：实现基于大模型的测试结果分析和报告生成功能
"""

import os
import json
from typing import Dict, Any, List, TypedDict, Optional
from datetime import datetime
from loguru import logger
from langgraph.graph import StateGraph
from sqlalchemy.sql import text
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
import re

from core.config import get_settings, get_llm_config
from core.database import (
    get_db, 
    TestCase,
    update_test_case_status,
    update_test_task_status
)
from core.utils import generate_unique_id
from core.logger import get_logger
from core.llm import call_zhipu_api

# 获取带上下文的logger
log = get_logger("report_agent")

class ReportState(TypedDict):
    """报告Agent状态定义"""
    task_id: str  # 任务ID
    test_cases: Optional[List[Dict[str, Any]]]  # 测试用例列表
    analysis_results: Optional[List[Dict[str, Any]]]  # 分析结果列表
    report_data: Optional[Dict[str, Any]]  # Excel报告数据
    report_path: Optional[str]  # 报告文件路径
    errors: List[str]  # 错误信息
    status: str  # 任务状态


def analyze_test_results(state: ReportState) -> ReportState:
    """
    分析测试结果节点 - 从数据库读取测试用例结果并使用大模型分析
    
    Args:
        state: 当前状态
        
    Returns:
        更新后的状态
    """
    task_id = state['task_id']
    log.info(f"开始分析测试结果: {task_id}")
    
    try:
        # 从数据库获取该任务的所有测试用例
        with get_db() as db:
            cases = db.query(TestCase).filter(TestCase.task_id == task_id).all()
            if not cases:
                raise ValueError(f"未找到任务的测试用例: {task_id}")
            
            log.info(f"找到 {len(cases)} 个测试用例")
            
            # 获取LLM配置
            llm_config = get_llm_config()
            
            # 对每个测试用例，先进行结构化分析
            for case in cases:
                # 提取算法输出的关键信息
                algorithm_results = extract_algorithm_results(case.actual_output)
                
                # 进行结构化比较
                structured_comparison = compare_test_case_with_results(
                    {"expected_output": case.expected_output}, 
                    algorithm_results
                )
                
                # 构建丰富的提示词
                case_prompt = construct_enhanced_prompt(
                    case, 
                    algorithm_results,
                    task_type="安全帽检测"  # 根据算法类型调整
                )
                
                # 调用大模型API
                llm_result = call_zhipu_api(case_prompt, llm_config)
                
                # 添加调试日志
                log.info(f"大模型原始回复长度: {len(llm_result) if llm_result else 0}")
                log.info(f"大模型原始回复前500字符: {repr(llm_result[:500]) if llm_result else 'None'}")
                
                # 解析大模型返回结果
                try:
                    llm_analysis = parse_llm_response(llm_result)
                    
                    # 用结构化分析增强大模型结果
                    enhanced_analysis = enhance_llm_results_with_structured_analysis(
                        llm_analysis, 
                        structured_comparison
                    )
                    
                    # 更新测试用例结果
                    case.is_passed = enhanced_analysis["is_passed"]
                    case.result_analysis = enhanced_analysis["analysis"]
                    case.status = "completed"
                    
                except Exception as e:
                    # 如果大模型分析失败，使用结构化分析结果
                    log.warning(f"大模型分析失败，使用结构化分析结果: {str(e)}")
                    case.is_passed = structured_comparison["is_passed"]
                    
                    # 构建更详细的分析说明
                    analysis_parts = []
                    if structured_comparison.get("reasons"):
                        analysis_parts.append(f"分析结果: {', '.join(structured_comparison['reasons'])}")
                    
                    # 添加检测详情
                    if structured_comparison.get("detected_objects"):
                        objects_summary = f"检测到 {len(structured_comparison['detected_objects'])} 个目标"
                        analysis_parts.append(objects_summary)
                    
                    # 添加警报状态
                    if "is_alert" in structured_comparison:
                        alert_status = "有警报" if structured_comparison["is_alert"] else "无警报"
                        analysis_parts.append(f"警报状态: {alert_status}")
                    
                    case.result_analysis = "结构化分析结果: " + "; ".join(analysis_parts) if analysis_parts else "结构化分析完成"
                    case.status = "completed"
            
            # 提交所有更改
            db.commit()
            log.success(f"完成所有测试用例分析: {task_id}")
            
            # 更新状态
            return {
                **state,
                "test_cases": [
                    {
                        "case_id": case.case_id,
                        "input_data": case.input_data,
                        "expected_output": case.expected_output,
                        "actual_output": case.actual_output,
                        "is_passed": case.is_passed,
                        "result_analysis": case.result_analysis
                    }
                    for case in cases
                ],
                "analysis_results": [
                    {
                        "case_id": case.case_id,
                        "is_passed": case.is_passed,
                        "result_analysis": case.result_analysis
                    }
                    for case in cases
                ],
                "status": "analyzed"
            }
            
    except Exception as e:
        log.error(f"分析测试结果失败: {str(e)}")
        return {
            **state,
            "errors": state.get("errors", []) + [str(e)],
            "status": "error"
        }

def generate_excel_report(state: ReportState) -> ReportState:
    """
    生成Excel测试报告节点
    
    Args:
        state: 当前状态
        
    Returns:
        更新后的状态
    """
    task_id = state['task_id']
    log.info(f"开始生成Excel测试报告: {task_id}")
    
    try:
        # 确保report目录存在
        os.makedirs("data/report", exist_ok=True)
        
        # 从数据库获取所有测试用例和任务信息
        with get_db() as db:
            cases = db.query(TestCase).filter(TestCase.task_id == task_id).all()
            if not cases:
                raise ValueError(f"未找到任务的测试用例: {task_id}")
            
            # 获取任务信息
            task = db.execute(text("""
                SELECT algorithm_image, dataset_url 
                FROM test_tasks 
                WHERE task_id = :task_id
            """), {"task_id": task_id}).fetchone()
            
            if not task:
                raise ValueError(f"未找到任务信息: {task_id}")
            
            algorithm_image = task[0] or ""
            dataset_url = task[1] or ""
            
            # 获取LLM配置
            llm_config = get_llm_config()
            
            # 创建新的Excel工作簿
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "测试报告"
            
            # 设置基本信息部分的样式
            header_fill = PatternFill(start_color="CCCCCC", end_color="CCCCCC", fill_type="solid")
            thin_border = Border(
                left=Side(style='thin'),
                right=Side(style='thin'),
                top=Side(style='thin'),
                bottom=Side(style='thin')
            )
            
            # 设置标题
            title = f"算法测试报告-{datetime.now().strftime('%Y_%m_%d')}"
            ws.merge_cells('A1:E1')
            title_cell = ws.cell(row=1, column=1, value=title)
            title_cell.font = Font(bold=True, size=12)
            title_cell.alignment = Alignment(horizontal="center", vertical="center")
            title_cell.border = thin_border
            
            # 添加基本信息
            info_data = [
                ["测试需求", "", "", "SDK版本版本", ""],
                ["中心授权版本", "", "", ""],
                ["测试人员", "", "", ""],
                ["EV_SDK镜像版本", algorithm_image, "", ""],
                ["数据集", dataset_url, "", ""],
                ["服务器配置", "", "", ""],
                ["数据集", "", "", ""],
                ["算法指标说明", "", "", ""]
            ]
            
            current_row = 2
            for info in info_data:
                # 如果是算法指标说明，合并整行
                if info[0] != "测试需求":
                    
                    ws.merge_cells(f'B{current_row}:E{current_row}')
                    cell = ws.cell(row=current_row, column=1, value=info[0])
                    cell.fill = header_fill
                    cell.border = thin_border
                    cell = ws.cell(row=current_row, column=2, value=info[1])
                    cell.border = thin_border
                else:
                    # 合并第3、4列
                    ws.merge_cells(f'B{current_row}:C{current_row}')
                    for col, value in enumerate(info, 1):  # 只取前3个值，因为第3、4列合并了
                        if col != 3:
                            cell = ws.cell(row=current_row, column=col, value=value)
                            cell.border = thin_border
                            if col == 1:  # 第一列使用灰色背景
                                cell.fill = header_fill
                
                current_row += 1
            
            # 添加空行
            current_row += 1
            
            # 设置列宽
            ws.column_dimensions['A'].width = 20  # 分类
            ws.column_dimensions['B'].width = 25  # 子类
            ws.column_dimensions['C'].width = 40  # 标准
            ws.column_dimensions['D'].width = 15  # 测试结果
            ws.column_dimensions['E'].width = 50  # 备注
            
            # 添加精度测试结果标题
            ws.merge_cells(f'A{current_row}:E{current_row}')
            cell = ws.cell(row=current_row, column=1, value="精度测试结果")
            cell.border = thin_border
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center", vertical="center")
            current_row += 1
            
            # 添加模型识别率测试分析标题
            ws.merge_cells(f'A{current_row}:E{current_row}')
            cell = ws.cell(row=current_row, column=1, value="模型识别率测试分析")
            cell.border = thin_border
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center", vertical="center")
            current_row += 1
            
            # 添加性能测试分析标题
            ws.merge_cells(f'A{current_row}:E{current_row}')
            cell = ws.cell(row=current_row, column=1, value="性能测试分析")
            cell.border = thin_border
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center", vertical="center")
            current_row += 1
            
            # 添加兼容性测试分析标题
            ws.merge_cells(f'A{current_row}:E{current_row}')
            cell = ws.cell(row=current_row, column=1, value="兼容性测试分析")
            cell.border = thin_border
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center", vertical="center")
            current_row += 1
            
            # 添加规范测试分析标题
            ws.merge_cells(f'A{current_row}:E{current_row}')
            cell = ws.cell(row=current_row, column=1, value="规范测试分析")
            cell.border = thin_border
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center", vertical="center")
            current_row += 1
            
            # 设置规范测试表头
            headers = ["分类", "子类", "标准", "测试结果", "备注"]
            for col, header in enumerate(headers, 1):
                cell = ws.cell(row=current_row, column=col, value=header)
                cell.font = Font(bold=True)
                cell.fill = header_fill
                cell.alignment = Alignment(horizontal="center", vertical="center")
                cell.border = thin_border
            current_row += 1
            
            # 构建所有测试用例的信息
            test_cases_info = []
            for case in cases:
                input_data = case.input_data or {}
                name = input_data.get("name", "未命名测试用例")
                steps = input_data.get("steps", "")
                
                case_info = f"""
测试用例 {case.case_id}:
- 名称: {name}
- 步骤: {steps}
- 通过状态: {case.is_passed}
- 分析结果: {case.result_analysis or '无分析结果'}
"""
                test_cases_info.append(case_info)
            
            # 构建整体提示词
            prompt = f"""
请分析以下所有测试用例信息，为每个测试用例生成测试报告的一行数据。

{os.linesep.join(test_cases_info)}

对每个测试用例，请生成以下字段：
- category: 测试分类（如：功能测试、性能测试、接口测试等）
- sub_category: 具体测试的参数名称（从测试步骤中提取）
- standard: 该参数的作用和测试标准
- result: 根据is_passed确定（通过/不通过）
- note: 对result_analysis的简要总结

请按以下JSON格式返回，key为测试用例ID：
{{
    "test_case_id_1": {{
        "category": "分类名称",
        "sub_category": "参数名称",
        "standard": "参数作用和测试标准",
        "result": "通过/不通过",
        "note": "分析结果总结"
    }},
    "test_case_id_2": {{
        ...
    }}
}}
"""
            
            try:
                # 调用大模型API
                result = call_zhipu_api(prompt, llm_config)
                
                # 尝试解析JSON响应
                try:
                    # 直接尝试解析
                    report_data_all = json.loads(result)
                except json.JSONDecodeError:
                    # 如果直接解析失败，尝试从文本中提取JSON
                    import re
                    json_match = re.search(r'\{[\s\S]*\}', result)
                    if json_match:
                        report_data_all = json.loads(json_match.group())
                    else:
                        raise ValueError("无法从大模型响应中提取JSON数据")
                
                # 写入每个测试用例的数据
                for case in cases:
                    report_data = report_data_all.get(case.case_id)
                    if report_data:
                        # 写入Excel
                        ws.cell(row=current_row, column=1, value=report_data["category"])
                        ws.cell(row=current_row, column=2, value=report_data["sub_category"])
                        ws.cell(row=current_row, column=3, value=report_data["standard"])
                        ws.cell(row=current_row, column=4, value=report_data["result"])
                        ws.cell(row=current_row, column=5, value=report_data["note"])
                        
                        # 设置单元格样式
                        for col in range(1, 6):
                            cell = ws.cell(row=current_row, column=col)
                            cell.alignment = Alignment(wrap_text=True, vertical="center")
                            cell.border = thin_border
                            if col == 4:  # 测试结果列
                                if report_data["result"] == "通过":
                                    cell.fill = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
                                else:
                                    cell.fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
                        
                        current_row += 1
                    else:
                        log.warning(f"未找到测试用例 {case.case_id} 的报告数据")
                
            except Exception as e:
                log.error(f"处理测试报告数据时出错: {str(e)}")
                return {
                    **state,
                    "errors": state.get("errors", []) + [str(e)],
                    "status": "error"
                }
            
            # 保存Excel文件
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            report_path = f"data/report/test_report_{task_id}_{timestamp}.xlsx"
            wb.save(report_path)
            
            log.success(f"Excel报告生成成功: {report_path}")
            
            return {
                **state,
                "report_path": report_path,
                "status": "report_generated"
            }
            
    except Exception as e:
        log.error(f"生成Excel报告失败: {str(e)}")
        return {
            **state,
            "errors": state.get("errors", []) + [str(e)],
            "status": "error"
        }

def create_report_graph() -> StateGraph:
    """
    创建报告Agent工作流图
    
    Returns:
        工作流图
    """
    # 创建工作流图
    report_graph = StateGraph(ReportState)
    
    # 添加节点
    report_graph.add_node("analyze_test_results", analyze_test_results)
    report_graph.add_node("generate_excel_report", generate_excel_report)
    
    # 添加边
    report_graph.add_edge("analyze_test_results", "generate_excel_report")
    
    # 设置入口点和结束点
    report_graph.set_entry_point("analyze_test_results")
    report_graph.set_finish_point("generate_excel_report")
    
    return report_graph


async def run_report_generation(task_id: str) -> Dict[str, Any]:
    """
    运行报告生成Agent
    
    Args:
        task_id: 任务ID
        
    Returns:
        生成结果
    """
    # 创建工作流图
    report_graph = create_report_graph()
    
    # 编译工作流
    report_app = report_graph.compile()
    
    # 创建初始状态
    initial_state = {
        "task_id": task_id,
        "test_cases": None,
        "analysis_results": None,
        "errors": [],
        "status": "created"
    }
    
    # 运行工作流
    log.info(f"开始运行报告生成Agent: {task_id}")
    result = report_app.invoke(initial_state)
    log.info(f"报告生成Agent运行完成: {task_id}, 状态: {result['status']}")
    
    return result

def extract_algorithm_results(actual_output: str) -> Dict[str, Any]:
    """
    从算法输出中提取关键信息，特别关注安全帽检测和警报状态
    
    Args:
        actual_output: 算法原始输出文本
        
    Returns:
        提取的结构化结果
    """
    result = {
        "detected_objects": [],
        "processing_time": None,
        "config_params": {},
        "is_alert": False,  # 新增字段，关注警报状态
        "alert_reason": None,  # 新增字段，记录警报原因
        "raw_json_result": None
    }
    
    try:
        # 提取处理时间
        processing_time_match = re.search(r'Total processing time: (\d+\.\d+) ms', actual_output)
        if processing_time_match:
            result["processing_time"] = float(processing_time_match.group(1))
        
        # 提取JSON结果部分 - 修复正则表达式
        json_match = re.search(r'json:\s*(\{[\s\S]*?\n\})', actual_output)
        if json_match:
            try:
                # 清理JSON字符串中的转义字符
                json_str = json_match.group(1).replace('\\\\', '\\').replace('\\\t', '\t')
                json_data = json.loads(json_str)
                result["raw_json_result"] = json_data
                
                # 提取检测到的对象
                if "model_data" in json_data and "objects" in json_data["model_data"]:
                    for obj in json_data["model_data"]["objects"]:
                        result["detected_objects"].append({
                            "name": obj.get("name", "unknown"),
                            "confidence": obj.get("confidence", 0),
                            "coordinates": [
                                obj.get("x", 0), 
                                obj.get("y", 0),
                                obj.get("width", 0),
                                obj.get("height", 0)
                            ]
                        })
                
                # 提取配置参数和警报状态
                if "algorithm_data" in json_data:
                    result["config_params"] = json_data["algorithm_data"]
                    
                    # 特别关注is_alert字段
                    if "is_alert" in json_data["algorithm_data"]:
                        result["is_alert"] = json_data["algorithm_data"]["is_alert"]
                    
                    # 提取警报原因
                    if result["is_alert"]:
                        # 如果is_alert为true，查找造成警报的原因
                        alert_reasons = []
                        
                        # 检查是否有没戴安全帽的头部
                        head_count = 0
                        hat_count = 0
                        
                        for obj in result["detected_objects"]:
                            if obj["name"] == "head":
                                head_count += 1
                            elif "hat" in obj["name"]:
                                hat_count += 1
                        
                        if head_count > 0:
                            alert_reasons.append(f"检测到{head_count}个未佩戴安全帽的头部")
                        
                        # 也检查target_info中的is_alert标记
                        if "target_info" in json_data["algorithm_data"]:
                            specific_alerts = []
                            for target in json_data["algorithm_data"]["target_info"]:
                                if target.get("is_alert", False):
                                    specific_alerts.append(target.get("name", "unknown"))
                            if specific_alerts:
                                alert_reasons.append(f"标记警报的对象: {', '.join(specific_alerts)}")
                        
                        if alert_reasons:
                            result["alert_reason"] = "; ".join(alert_reasons)
                        else:
                            result["alert_reason"] = "算法触发警报但未指定具体原因"
            except json.JSONDecodeError:
                pass
    except Exception as e:
        log.error(f"提取算法结果时出错: {str(e)}")
    
    return result

def compare_test_case_with_results(test_case: Dict[str, Any], algorithm_results: Dict[str, Any]) -> Dict[str, Any]:
    """
    比较测试用例预期与算法实际结果，特别关注安全帽检测和面罩警报
    
    Args:
        test_case: 测试用例信息
        algorithm_results: 提取的算法结果
        
    Returns:
        比较结果
    """
    expected_output = test_case.get("expected_output", {})
    
    # 解析测试用例预期输出
    expected_alert = False
    expected_objects = []
    expected_time = None
    
    # 尝试从不同格式的预期输出中提取信息
    if "expected_result" in expected_output and isinstance(expected_output["expected_result"], str):
        # 从文本描述中提取预期
        if "is_alert" in expected_output["expected_result"]:
            if "应为 `true`" in expected_output["expected_result"] or "应为true" in expected_output["expected_result"]:
                expected_alert = True
            elif "应为 `false`" in expected_output["expected_result"] or "应为false" in expected_output["expected_result"]:
                expected_alert = False
        
        # 提取预期检测对象
        objects_match = re.findall(r'检测到(\w+)', expected_output["expected_result"])
        for obj in objects_match:
            expected_objects.append({"name": obj})
    
    # 也可能有结构化的预期
    elif "expected_objects" in expected_output:
        expected_objects = expected_output["expected_objects"]
    
    if "expected_processing_time" in expected_output:
        expected_time = expected_output["expected_processing_time"]
    
    # 开始比较
    comparison = {
        "is_passed": True,
        "object_match": True,
        "alert_match": True,
        "performance_match": True,
        "reasons": []
    }
    
    # 比较检测对象
    if expected_objects:
        # 检查是否检测到了预期数量的对象
        if len(expected_objects) != len(algorithm_results["detected_objects"]):
            comparison["object_match"] = False
            comparison["reasons"].append(
                f"检测到 {len(algorithm_results['detected_objects'])} 个对象，但预期是 {len(expected_objects)} 个"
            )
        
        # 检查每个预期对象是否被检测到
        expected_classes = [obj.get("name") for obj in expected_objects]
        actual_classes = [obj.get("name") for obj in algorithm_results["detected_objects"]]
        
        for expected_class in expected_classes:
            if expected_class not in actual_classes:
                comparison["object_match"] = False
                comparison["reasons"].append(f"未检测到预期的对象类别: {expected_class}")
        
        # 检查置信度是否达到预期阈值
        for expected_obj in expected_objects:
            name = expected_obj.get("name")
            min_confidence = expected_obj.get("min_confidence", 0.5)
            
            matching_objs = [obj for obj in algorithm_results["detected_objects"] if obj.get("name") == name]
            if matching_objs:
                if all(obj["confidence"] < min_confidence for obj in matching_objs):
                    comparison["object_match"] = False
                    max_conf = max(obj["confidence"] for obj in matching_objs)
                    comparison["reasons"].append(
                        f"对象 {name} 的置信度 ({max_conf:.2f}) 低于预期阈值 ({min_confidence:.2f})"
                    )
    
    # 特别检查警报状态
    if "expected_result" in expected_output and "is_alert" in expected_output["expected_result"]:
        if expected_alert != algorithm_results["is_alert"]:
            comparison["alert_match"] = False
            if expected_alert:
                comparison["reasons"].append("预期应发出警报，但算法未发出警报")
            else:
                comparison["reasons"].append("预期不应发出警报，但算法发出了警报")
    
    # 检查处理时间是否符合预期
    if expected_time is not None and algorithm_results["processing_time"] is not None:
        if algorithm_results["processing_time"] > expected_time:
            comparison["performance_match"] = False
            comparison["reasons"].append(
                f"处理时间 ({algorithm_results['processing_time']} ms) 超过预期 ({expected_time} ms)"
            )
    
    # 总体通过判断
    comparison["is_passed"] = comparison["object_match"] and comparison["alert_match"] and comparison["performance_match"]
    
    return comparison

def construct_enhanced_prompt(case, algorithm_results, task_type="安全帽检测"):
    """
    构建增强的提示词，包含专业领域知识和具体任务指导
    
    Args:
        case: 测试用例信息
        algorithm_results: 提取的算法结果
        task_type: 算法类型
        
    Returns:
        构建的提示词
    """
    # 提取测试用例信息
    case_id = case.case_id
    expected_output = case.expected_output or {}
    actual_output = case.actual_output
    test_data = case.test_data or {}
    
    # 从不同格式的预期输出中提取描述
    expected_description = ""
    if isinstance(expected_output, dict):
        if "expected_result" in expected_output:
            expected_description = expected_output["expected_result"]
        elif "description" in expected_output:
            expected_description = expected_output["description"]
    
    # 构建提示词
    detected_objects_summary = []
    for obj in algorithm_results.get('detected_objects', []):
        detected_objects_summary.append(f"{obj['name']}(置信度:{obj['confidence']:.2f})")
    
    prompt = f"""请分析安全帽检测算法的测试结果。

测试用例: {case_id}
测试数据: {test_data}
预期结果: {expected_description}

实际检测结果:
- 检测到{len(algorithm_results.get('detected_objects', []))}个对象: {', '.join(detected_objects_summary)}
- 警报状态: {algorithm_results.get('is_alert', False)}
- 警报原因: {algorithm_results.get('alert_reason', '无警报')}
- 处理时间: {algorithm_results.get('processing_time', '未知')} ms

分析要点:
1. 检查检测到的对象是否符合预期
2. 检查警报状态是否正确(有未戴帽的head时应该为true)
3. 检查处理性能是否合理

请严格按照以下JSON格式回复:
{{"is_passed": true, "analysis": "分析说明"}}"""
    
    return prompt

def parse_llm_response(llm_response: str) -> Dict[str, Any]:
    """
    解析大模型回复，支持多种返回格式
    
    Args:
        llm_response: 大模型返回的文本
        
    Returns:
        解析后的结构化结果
    """
    try:
        # 检查输入是否为空或None
        if not llm_response:
            log.warning("大模型回复为空")
            return {
                "is_passed": False,
                "analysis": "大模型回复为空，无法进行分析",
                "error": "empty_response"
            }
        
        # 检查是否是错误信息
        if llm_response.startswith("API调用失败"):
            log.error(f"大模型API调用失败: {llm_response}")
            return {
                "is_passed": False,
                "analysis": f"大模型API调用失败: {llm_response}",
                "error": "api_call_failed"
            }
        
        log.debug(f"开始解析大模型回复，长度: {len(llm_response)}")
        
        # 清理控制字符
        cleaned_response = ''.join(char for char in llm_response if ord(char) >= 32 or char in '\t\n\r')
        log.debug(f"清理后回复长度: {len(cleaned_response)}")
        
        # 尝试直接解析JSON
        try:
            result = json.loads(cleaned_response)
            log.debug("成功直接解析JSON")
            return result
        except json.JSONDecodeError as json_error:
            log.debug(f"直接JSON解析失败: {str(json_error)}")
            
            # 如果整个响应不是JSON，尝试提取JSON部分
            json_pattern = r'```json\s*([\s\S]*?)\s*```|```\s*([\s\S]*?)\s*```|\{[\s\S]*\}'
            match = re.search(json_pattern, cleaned_response)
            if match:
                json_str = match.group(1) or match.group(2) or match.group(0)
                log.debug(f"提取到JSON候选: {json_str[:200]}...")
                
                # 清理可能的多余字符
                json_str = json_str.strip()
                if json_str.startswith("```") and json_str.endswith("```"):
                    json_str = json_str[3:-3].strip()
                
                # 再次清理控制字符
                json_str = ''.join(char for char in json_str if ord(char) >= 32 or char in '\t\n\r')
                
                try:
                    result = json.loads(json_str)
                    log.debug("成功解析提取的JSON")
                    return result
                except json.JSONDecodeError as extract_error:
                    log.warning(f"提取的JSON解析失败: {str(extract_error)}")
        
        # 如果无法解析JSON，尝试从文本中提取关键信息
        log.debug("无法解析JSON，尝试提取关键信息")
        is_passed = "通过" in cleaned_response or "passed" in cleaned_response.lower()
        if "不通过" in cleaned_response or "failed" in cleaned_response.lower() or "未通过" in cleaned_response:
            is_passed = False
        
        # 简单提取分析内容
        analysis = cleaned_response
        
        return {
            "is_passed": is_passed,
            "analysis": analysis,
            "parse_method": "text_extraction"
        }
            
    except Exception as e:
        log.error(f"解析大模型回复时出错: {str(e)}")
        log.error(f"问题回复内容: {repr(llm_response[:200]) if llm_response else 'None'}")
        # 返回默认结果而不是抛出异常
        return {
            "is_passed": False,
            "analysis": f"解析大模型回复失败: {str(e)}",
            "error": str(e)
        }

def enhance_llm_results_with_structured_analysis(llm_results: Dict, structured_analysis: Dict) -> Dict:
    """
    用结构化分析增强大模型的结果
    
    Args:
        llm_results: 大模型返回的分析结果
        structured_analysis: 结构化分析结果
        
    Returns:
        增强后的分析结果
    """
    # 用结构化分析中的确定性信息增强大模型分析
    enhanced_results = llm_results.copy()
    
    # 如果大模型没有正确识别通过状态，使用结构化分析的结果
    if "is_passed" in structured_analysis and (
            "is_passed" not in enhanced_results or 
            structured_analysis["is_passed"] != enhanced_results.get("is_passed")):
        # 添加说明，表明这是基于结构化分析的判断
        original_passed = enhanced_results.get("is_passed")
        enhanced_results["is_passed"] = structured_analysis["is_passed"]
        
        # 添加结构化分析的理由
        if "analysis" in enhanced_results:
            enhanced_results["analysis"] = f"{enhanced_results['analysis']}\n\n补充分析(基于结构化比较): "
            if structured_analysis.get("reasons"):
                enhanced_results["analysis"] += f"{', '.join(structured_analysis['reasons'])}"
            enhanced_results["analysis"] += f"\n【原大模型通过判定: {original_passed}，结构化分析判定: {structured_analysis['is_passed']}】"
        else:
            enhanced_results["analysis"] = "结构化分析结果: "
            if structured_analysis.get("reasons"):
                enhanced_results["analysis"] += f"{', '.join(structured_analysis['reasons'])}"
    
    # 添加结构化提取的对象信息
    if "detected_objects" not in enhanced_results and structured_analysis.get("detected_objects"):
        enhanced_results["detected_objects"] = structured_analysis["detected_objects"]
    
    # 添加处理时间信息
    if "processing_time" not in enhanced_results and structured_analysis.get("processing_time"):
        enhanced_results["processing_time"] = structured_analysis["processing_time"]
    
    return enhanced_results

if __name__ == "__main__":
    import asyncio
    import sys
    from core.logger import setup_logging
    
    # 设置日志
    setup_logging()
    
    # 使用指定的task_id
    task_id = "TASK1742525623_6049f9a3d00c"
    log.info(f"开始生成测试报告，任务ID: {task_id}")
    
    try:
        # 创建初始状态
        initial_state = {
            "task_id": task_id,
            "test_cases": None,
            "analysis_results": None,
            "errors": [],
            "status": "created"
        }
        
        # 直接调用generate_excel_report函数
        result = generate_excel_report(initial_state)
        
        # 检查结果
        if result["status"] == "report_generated":
            log.success(f"报告生成成功！")
            log.info(f"报告路径: {result.get('report_path')}")
        else:
            log.error(f"报告生成失败: {result.get('errors', ['未知错误'])}")
            sys.exit(1)
            
    except Exception as e:
        log.error(f"执行过程中出错: {str(e)}")
        sys.exit(1)

