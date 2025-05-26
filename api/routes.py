#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
文件作用：API路由定义，包括文档上传、测试用例生成和管理接口
开发规划：实现低耦合的API接口，便于后期工程化维护
"""

import os
import json
import shutil
import tempfile
import time
import logging
import hashlib
import asyncio
import traceback
from typing import Dict, Any, List, Optional, Union
from fastapi import APIRouter, HTTPException, UploadFile, File, Path, Query, Body, Depends, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from datetime import datetime, timedelta

from core.logger import get_logger
from core.utils import generate_unique_id
from core.database import (
    get_db,
    create_test_task,
    create_test_case,
    TestCase as DBTestCase,
    TestTask as DBTestTask,
    Session,
    update_test_task,
    get_all_test_tasks,
    get_test_task_by_document_id,
    get_test_task,
    update_task_algorithm_image as db_update_algorithm_image,
    update_task_dataset_url as db_update_dataset_url,
    update_task_container_name,
    close_task_session,
    update_test_case_status,
    update_test_task_status
)
from agents.analysis_agent import read_pdf_content, generate_test_cases as agent_generate_test_cases
from agents.execution_agent import (
    setup_algorithm_container, 
    load_test_cases, 
    parse_command, 
    execute_command, 
    save_result,
    release_algorithm_container
)
from agents.report_agent import run_report_generation
from agents.select_agent import select_test_images
from api.models import (
    TestCase, 
    TestCasesResponse, 
    DocumentUploadResponse, 
    DocumentAnalysisRequest,
    AlgorithmImageRequest,
    DatasetUrlRequest,
    TestCaseCreateRequest,
    TestCaseUpdateRequest,
    MessageResponse,
    TestTaskItem,
    TestTasksResponse,
    DockerSetupResponse,
    TestExecutionResponse,
    TestAnalysisResult,
    TestAnalysisResponse,
    TestDataUpdateRequest,
    TestCaseWithData,
    TestCasesDataResponse,
    ReportGenerationResponse,
    TestDataBatchUpdateRequest,
    DockerReleaseResponse,
    TaskTestCasesResponse,
    BatchTestDataSetRequest,
    ImageSelectionResponse
)

# 添加MCP相关导入
from mcp.client.session import ClientSession
from mcp.client.sse import sse_client
from core.mcp_config import get_mcp_config

# 创建路由器
router = APIRouter(prefix="/api", tags=["tests"])

# 获取日志记录器
log = get_logger("api")

# 添加WebSocket连接管理
class ConnectionManager:
    def __init__(self):
        # 存储所有活跃连接，格式: {case_id: [websocket1, websocket2, ...]}
        self.active_connections: Dict[str, List[WebSocket]] = {}
    
    async def connect(self, websocket: WebSocket, case_id: str):
        """添加新的WebSocket连接"""
        await websocket.accept()
        if case_id not in self.active_connections:
            self.active_connections[case_id] = []
        self.active_connections[case_id].append(websocket)
        log.info(f"WebSocket连接已建立: case_id={case_id}")
    
    def disconnect(self, websocket: WebSocket, case_id: str):
        """移除WebSocket连接"""
        if case_id in self.active_connections:
            if websocket in self.active_connections[case_id]:
                self.active_connections[case_id].remove(websocket)
            if not self.active_connections[case_id]:
                del self.active_connections[case_id]
        log.info(f"WebSocket连接已关闭: case_id={case_id}")
    
    async def send_json(self, case_id: str, message: dict):
        """向特定case_id的所有连接发送JSON消息"""
        if case_id in self.active_connections:
            disconnected_ws = []
            for websocket in self.active_connections[case_id]:
                try:
                    await websocket.send_json(message)
                except Exception as e:
                    log.error(f"发送消息失败: {str(e)}")
                    disconnected_ws.append(websocket)
            
            # 移除已断开的连接
            for ws in disconnected_ws:
                self.disconnect(ws, case_id)
    
    async def broadcast(self, message: dict):
        """向所有连接发送消息"""
        for case_id in list(self.active_connections.keys()):
            await self.send_json(case_id, message)

# 创建连接管理器实例
manager = ConnectionManager()

# WebSocket测试用例执行状态端点
@router.websocket("/ws/testcases/{case_id}")
async def websocket_endpoint(websocket: WebSocket, case_id: str):
    """
    WebSocket端点，用于获取测试用例执行的实时状态更新
    
    - **case_id**: 测试用例ID
    """
    await manager.connect(websocket, case_id)
    
    # 发送初始连接成功消息
    await websocket.send_json({
        "event": "connected",
        "message": f"WebSocket连接已建立，将接收测试用例 {case_id} 的实时更新",
        "case_id": case_id,
        "timestamp": datetime.now().isoformat()
    })
    
    try:
        # 保持连接，直到客户端断开
        while True:
            # 等待客户端消息，但不处理（仅用于保持连接）
            data = await websocket.receive_text()
            # 发送简单的确认消息
            await websocket.send_json({
                "event": "pong",
                "message": "连接正常",
                "timestamp": datetime.now().isoformat()
            })
    except WebSocketDisconnect:
        manager.disconnect(websocket, case_id)

# 临时存储上传的文档
# 注意：在实际生产环境中，应该使用数据库存储
DOCUMENTS = {}

def format_test_case(case: DBTestCase) -> TestCase:
    """将数据库测试用例对象转换为API响应模型"""
    input_data = case.input_data or {}
    expected_output = case.expected_output or {}
    return TestCase(
        id=case.case_id,
        name=input_data.get("name", ""),
        purpose=input_data.get("purpose", ""),
        steps=input_data.get("steps", ""),
        expected_result=expected_output.get("expected_result", ""),
        validation_method=expected_output.get("validation_method", ""),
        document_id=case.document_id,
        actual_output=case.actual_output,
        result_analysis=case.result_analysis,
        is_passed=case.is_passed,
        status=case.status or "pending"
    )

# 1. 文档上传接口
@router.post("/documents", response_model=DocumentUploadResponse)
async def upload_document(
    file: UploadFile = File(..., description="算法需求文档文件（PDF格式）"),
    db: Session = Depends(get_db)
):
    """
    上传算法需求文档
    
    - **file**: 上传的PDF格式需求文档
    
    返回文档ID和存储路径
    """
    # 检查文件类型
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="只支持PDF格式的需求文档")
    
    log.info(f"开始上传文档: {file.filename}")
    
    try:
        # 确保目标目录存在
        os.makedirs("data/pdfs", exist_ok=True)
        
        # 生成唯一文档ID
        document_id = generate_unique_id("DOC")
        
        # 构建文件保存路径
        file_path = f"data/pdfs/{document_id}_{file.filename}"
        
        # 读取上传的文件内容并保存
        content = await file.read()
        
        # 检查文件内容是否已存在（通过文件哈希值比较）
        import hashlib
        file_hash = hashlib.md5(content).hexdigest()
        
        # 检查数据库中是否已存在相同内容的文档
        existing_task = db.query(DBTestTask).filter(
            DBTestTask.document_hash == file_hash
        ).first()
        
        if existing_task:
            log.info(f"文件已存在: {file.filename}, 关联任务ID: {existing_task.task_id}")
            # 返回已存在文档的信息
            return {
                "message": "文档已存在",
                "document_id": existing_task.document_id,
                "filename": file.filename,
                "file_path": f"data/pdfs/{existing_task.document_id}_{file.filename}" 
            }
            
        with open(file_path, "wb") as f:
            f.write(content)
        
        # 为该文档创建一个任务
        task_id = generate_unique_id("TASK")
        task_data = {
            "task_id": task_id,
            "document_id": document_id,  # 添加文档ID
            "requirement_doc": "",  # 暂时不保存文档内容，后续分析时会更新
            "algorithm_image": None,
            "dataset_url": None,
            "document_hash": file_hash,  # 保存文件哈希值
            "status": "created"
        }
        
        # 保存到数据库
        task = DBTestTask(**task_data)
        db.add(task)
        db.commit()
        
        # 存储文档信息
        DOCUMENTS[document_id] = {
            "id": document_id,
            "filename": file.filename,
            "file_path": file_path,
            "file_hash": file_hash,
            "task_id": task_id  # 保存任务ID
        }
        
        log.success(f"文档上传成功: {file.filename}, ID: {document_id}, 关联任务ID: {task_id}")
        
        return {
            "message": "文档上传成功",
            "document_id": document_id,
            "filename": file.filename,
            "file_path": file_path
        }
    except Exception as e:
        if 'db' in locals() and db:
            db.rollback()
        log.error(f"文档上传异常: {str(e)}")
        raise HTTPException(status_code=500, detail=f"文档上传异常: {str(e)}")


# 3. 获取测试用例列表
@router.get("/testcases", response_model=TestCasesResponse)
async def get_test_cases(
    document_id: Optional[str] = Query(None, description="文档ID，可选"),
    db: Session = Depends(get_db)
):
    """
    获取测试用例列表
    
    - **document_id**: 可选的文档ID，如果提供则只返回该文档的测试用例
    
    返回测试用例列表
    """
    query = db.query(DBTestCase)
    if document_id:
        query = query.filter(DBTestCase.document_id == document_id)
    
    cases = query.all()
    formatted_cases = [format_test_case(case) for case in cases]
    return {
        "message": f"成功获取{len(formatted_cases)}个测试用例",
        "test_cases": formatted_cases
    }


# 批量设置测试数据页面的任务列表API - 确保这个路由在通配符路由前定义
@router.get("/testcases/batch-data", response_model=TaskTestCasesResponse)
async def get_tasks_for_batch_data(
    db: Session = Depends(get_db)
):
    """
    获取所有任务及其测试用例，用于批量设置测试数据页面
    
    返回所有任务列表，包含任务信息和测试用例
    """
    log.info("获取所有任务及其测试用例，用于批量设置测试数据")
    
    try:
        # 获取所有任务
        tasks = db.query(DBTestTask).all()
        
        # 构建结果
        result = []
        for task in tasks:
            # 获取任务的测试用例
            test_cases = db.query(DBTestCase).filter(DBTestCase.task_id == task.task_id).all()
            
            # 格式化测试用例
            formatted_cases = []
            for case in test_cases:
                input_data = case.input_data or {}
                formatted_cases.append({
                    "case_id": case.case_id,
                    "name": input_data.get("name", "未命名测试用例"),
                    "purpose": input_data.get("purpose", ""),
                    "test_data": case.test_data,
                    "status": case.status or "pending"
                })
            
            # 添加到结果
            result.append({
                "task_id": task.task_id,
                "algorithm_image": task.algorithm_image,
                "dataset_url": task.dataset_url,
                "created_at": task.created_at.isoformat() if task.created_at else None,
                "status": task.status,
                "test_cases_count": len(formatted_cases),
                "test_cases": formatted_cases
            })
        
        return TaskTestCasesResponse(
            message=f"成功获取{len(result)}个任务及其测试用例",
            tasks=result
        )
        
    except Exception as e:
        log.error(f"获取任务及测试用例失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"获取任务及测试用例失败: {str(e)}")


# 任务测试用例列表API - 确保这个路由在通配符路由前定义
@router.get("/testcases/tasks", response_model=TaskTestCasesResponse)
async def get_tasks_with_testcases(
    db: Session = Depends(get_db)
):
    """
    获取所有任务及其测试用例，用于批量设置测试数据
    
    返回所有任务列表，包含任务信息和测试用例
    """
    log.info("获取所有任务及其测试用例")
    
    try:
        # 获取所有任务
        tasks = db.query(DBTestTask).all()
        
        # 构建结果
        result = []
        for task in tasks:
            # 获取任务的测试用例
            test_cases = db.query(DBTestCase).filter(DBTestCase.task_id == task.task_id).all()
            
            # 格式化测试用例
            formatted_cases = []
            for case in test_cases:
                input_data = case.input_data or {}
                formatted_cases.append({
                    "case_id": case.case_id,
                    "name": input_data.get("name", "未命名测试用例"),
                    "purpose": input_data.get("purpose", ""),
                    "test_data": case.test_data,
                    "status": case.status or "pending"
                })
            
            # 添加到结果
            result.append({
                "task_id": task.task_id,
                "algorithm_image": task.algorithm_image,
                "dataset_url": task.dataset_url,
                "created_at": task.created_at.isoformat() if task.created_at else None,
                "status": task.status,
                "test_cases_count": len(formatted_cases),
                "test_cases": formatted_cases
            })
        
        return TaskTestCasesResponse(
            message=f"成功获取{len(result)}个任务及其测试用例",
            tasks=result
        )
        
    except Exception as e:
        log.error(f"获取任务及测试用例失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"获取任务及测试用例失败: {str(e)}")


# 批量设置测试数据API - 确保这个路由在通配符路由前定义
@router.post("/testcases/batch-set-data", response_model=MessageResponse)
async def batch_set_test_data(
    request: BatchTestDataSetRequest = Body(..., description="批量设置测试数据请求"),
    db: Session = Depends(get_db)
):
    """
    批量设置测试用例的测试数据
    
    - **request**: 包含测试用例ID列表和测试数据路径的请求
    
    返回更新结果
    """
    log.info(f"批量设置测试数据，共{len(request.case_ids)}个测试用例")
    
    try:
        # 获取所有指定ID的测试用例
        cases = db.query(DBTestCase).filter(DBTestCase.case_id.in_(request.case_ids)).all()
        
        if not cases:
            raise HTTPException(status_code=404, detail="未找到指定的测试用例")
            
        found_case_ids = {case.case_id for case in cases}
        missing_case_ids = set(request.case_ids) - found_case_ids
        
        if missing_case_ids:
            raise HTTPException(
                status_code=400, 
                detail=f"以下测试用例ID不存在: {', '.join(missing_case_ids)}"
            )
        
        # 验证测试数据路径
        if not request.test_data:
            raise HTTPException(
                status_code=400,
                detail="测试数据路径不能为空"
            )
        
        # 更新测试数据
        updated_count = 0
        for case in cases:
            case.test_data = request.test_data
            updated_count += 1
        
        # 提交更新
        db.commit()
        
        return MessageResponse(
            message=f"成功更新{updated_count}个测试用例的测试数据",
            success=True
        )
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        log.error(f"批量设置测试数据失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"批量设置测试数据失败: {str(e)}")


# 获取单个测试用例 - 通配符路由放在具体路由之后
@router.get("/testcases/{case_id}", response_model=TestCase)
async def get_test_case(
    case_id: str = Path(..., description="测试用例ID"),
    db: Session = Depends(get_db)
):
    """
    获取单个测试用例详情
    
    - **case_id**: 测试用例ID
    
    返回测试用例详情
    """
    # 跳过特殊路径
    if case_id in ["tasks", "batch-data", "batch-set-data"]:
        raise HTTPException(status_code=404, detail=f"未找到路径: /testcases/{case_id}")
        
    case = db.query(DBTestCase).filter(DBTestCase.case_id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail=f"测试用例不存在: {case_id}")
    
    return format_test_case(case)


# 2. 文档分析接口
@router.post("/documents/{document_id}/analyze", response_model=TestCasesResponse)
async def analyze_document(
    document_id: str = Path(..., description="文档ID"),
    db: Session = Depends(get_db)
):
    """
    分析文档并生成测试用例
    
    - **document_id**: 文档ID，通过上传接口获取
    
    返回生成的测试用例列表
    """
    # 直接检查文件是否存在
    pdf_dir = "data/pdfs"
    # 在pdf_dir目录下查找以document_id开头的文件
    matching_files = [f for f in os.listdir(pdf_dir) if f.startswith(document_id)]
    
    if not matching_files:
        raise HTTPException(status_code=404, detail=f"文档不存在: {document_id}")
    
    # 使用找到的第一个匹配文件
    file_path = os.path.join(pdf_dir, matching_files[0])
    log.info(f"开始分析文档: {matching_files[0]}, ID: {document_id}")
    
    try:
        # 查找与文档关联的任务
        task = None
        doc_info = DOCUMENTS.get(document_id)
        
        if doc_info and 'task_id' in doc_info:
            # 如果文档信息中有任务ID，获取该任务
            task_id = doc_info['task_id']
            task = db.query(DBTestTask).filter(DBTestTask.task_id == task_id).first()
            log.info(f"找到文档关联的任务: {task_id}")
        
        if not task:
            # 如果没有找到任务，创建一个新任务
            task_id = generate_unique_id("TASK")
            task_data = {
                "task_id": task_id,
                "document_id": document_id,  # 添加文档ID
                "requirement_doc": "",  # 暂时不保存文档内容
                "algorithm_image": "auto_generated",
                "status": "created"
            }
            
            # 创建测试任务
            task = DBTestTask(**task_data)
            db.add(task)
            db.commit()
            db.refresh(task)
            
            # 如果文档信息存在，更新任务ID
            if doc_info:
                doc_info['task_id'] = task_id
            
            log.info(f"为文档创建新任务: {task_id}")
        
        # 创建初始状态
        state = {
            "task_id": task.task_id,
            "requirement_doc_path": file_path,
            "algorithm_image": task.algorithm_image or "temp_image",  # 使用任务中的镜像地址，如果没有则使用临时值
            "dataset_url": task.dataset_url,  # 使用任务中的数据集地址
            "pdf_content": None,
            "test_cases": None,
            "errors": [],
            "status": "created"
        }
        
        # 读取PDF内容
        state = read_pdf_content(state)
        if state["status"] == "error":
            raise HTTPException(status_code=500, detail=f"读取PDF内容失败: {state['errors']}")
        
        # 更新任务的需求文档
        task.requirement_doc = state["pdf_content"]
        db.commit()
        
        # 生成测试用例
        state = agent_generate_test_cases(state)
        
        if state["status"] == "error":
            raise HTTPException(status_code=500, detail=f"生成测试用例失败: {state['errors']}")
        
        # 获取测试用例
        test_cases = state.get("test_cases", [])
        if not test_cases:
            return {"message": "未生成测试用例", "test_cases": []}
        
        # 保存测试用例到数据库并格式化返回数据
        formatted_test_cases = []
        for case in test_cases:
            case_data = {
                "task_id": task.task_id,
                "case_id": case["id"],
                "document_id": document_id,
                "input_data": {
                    "name": case["name"],
                    "purpose": case["purpose"],
                    "steps": case["steps"]
                },
                "expected_output": {
                    "expected_result": case["expected_result"],
                    "validation_method": case["validation_method"]
                }
            }
            # 创建测试用例
            db_case = DBTestCase(**case_data)
            db.add(db_case)
            db.commit()
            db.refresh(db_case)
            formatted_test_cases.append(format_test_case(db_case))
        
        log.success(f"测试用例生成成功，共{len(formatted_test_cases)}个测试用例")
        
        return {
            "message": f"成功从文档生成{len(formatted_test_cases)}个测试用例",
            "test_cases": formatted_test_cases
        }
    except Exception as e:
        db.rollback()  # 发生异常时回滚事务
        log.error(f"分析文档异常: {str(e)}")
        raise HTTPException(status_code=500, detail=f"分析文档异常: {str(e)}")


# 4. 创建测试用例
@router.post("/testcases", response_model=TestCase)
async def create_test_case_endpoint(
    test_case: TestCaseCreateRequest = Body(..., description="测试用例信息"),
    db: Session = Depends(get_db)
):
    """
    创建新的测试用例
    
    - **test_case**: 测试用例信息
    
    返回创建的测试用例
    """
    case_id = generate_unique_id("TC")
    
    # 创建测试任务（如果不存在）
    task_id = generate_unique_id("TASK")
    task_data = {
        "task_id": task_id,
        "requirement_doc": "",
        "algorithm_image": "manual_created",
        "status": "completed"
    }
    
    # 直接创建测试任务
    task = DBTestTask(**task_data)
    db.add(task)
    db.commit()
    db.refresh(task)
    
    # 创建测试用例
    case_data = {
        "task_id": task_id,
        "case_id": case_id,
        "document_id": test_case.document_id,
        "input_data": {
            "name": test_case.name,
            "purpose": test_case.purpose,
            "steps": test_case.steps
        },
        "expected_output": {
            "expected_result": test_case.expected_result,
            "validation_method": test_case.validation_method
        }
    }
    
    # 直接创建测试用例
    case = DBTestCase(**case_data)
    db.add(case)
    db.commit()
    db.refresh(case)
    
    log.success(f"测试用例创建成功: {case_id}")
    
    return format_test_case(case)


# 5. 更新测试用例
@router.put("/testcases/{case_id}", response_model=TestCase)
async def update_test_case(
    case_id: str = Path(..., description="测试用例ID"),
    test_case: TestCaseUpdateRequest = Body(..., description="测试用例更新信息"),
    db: Session = Depends(get_db)
):
    """
    更新测试用例
    
    - **case_id**: 测试用例ID
    - **test_case**: 测试用例更新信息
    
    返回更新后的测试用例
    """
    case = db.query(DBTestCase).filter(DBTestCase.case_id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail=f"测试用例不存在: {case_id}")
    
    # 更新非空字段
    update_data = test_case.dict(exclude_unset=True)
    if update_data:
        # 更新document_id
        if "document_id" in update_data:
            case.document_id = update_data["document_id"]
            
        input_data = dict(case.input_data)
        expected_output = dict(case.expected_output)
        
        # 更新input_data
        for key in ["name", "purpose", "steps"]:
            if key in update_data:
                input_data[key] = update_data[key]
        
        # 更新expected_output
        for key in ["expected_result", "validation_method"]:
            if key in update_data:
                expected_output[key] = update_data[key]
        
        case.input_data = input_data
        case.expected_output = expected_output
        db.commit()
        db.refresh(case)
    
    log.success(f"测试用例更新成功: {case_id}")
    return format_test_case(case)


# 6. 删除测试用例
@router.delete("/testcases/{case_id}", response_model=MessageResponse)
async def delete_test_case(
    case_id: str = Path(..., description="测试用例ID"),
    db: Session = Depends(get_db)
):
    """
    删除测试用例
    
    - **case_id**: 测试用例ID
    
    返回删除结果
    """
    case = db.query(DBTestCase).filter(DBTestCase.case_id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail=f"测试用例不存在: {case_id}")
    
    db.delete(case)
    db.commit()
    
    log.success(f"测试用例删除成功: {case_id}")
    return {"message": f"测试用例删除成功: {case_id}"}


# 7. 兼容旧接口 - 直接从上传文档生成测试用例
@router.post("/generate-testcases", response_model=TestCasesResponse)
async def generate_testcases_from_doc(
    file: UploadFile = File(..., description="算法需求文档文件（PDF格式）"),
    db: Session = Depends(get_db)
):
    """
    直接从上传的需求文档生成测试用例，不创建测试任务
    
    - **file**: 上传的PDF格式需求文档
    
    返回生成的测试用例列表
    """
    # 检查文件类型
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="只支持PDF格式的需求文档")
    
    log.info(f"开始从上传文档生成测试用例: {file.filename}")
    
    try:
        # 创建临时文件保存上传的PDF
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as temp_file:
            # 读取上传的文件内容并写入临时文件
            content = await file.read()
            temp_file.write(content)
            temp_file_path = temp_file.name
        
        # 生成唯一文档ID
        document_id = generate_unique_id("DOC")
        
        # 创建测试任务
        task_id = generate_unique_id("TASK")
        task_data = {
            "task_id": task_id,
            "document_id": document_id,  # 添加文档ID
            "requirement_doc": "",  # 暂时不保存文档内容
            "algorithm_image": "auto_generated",
            "status": "created"
        }
        
        # 创建测试任务
        task = DBTestTask(**task_data)
        db.add(task)
        db.commit()
        db.refresh(task)
        
        # 创建初始状态
        state = {
            "task_id": task_id,
            "requirement_doc_path": temp_file_path,
            "algorithm_image": "temp_image",  # 临时值，不会实际使用
            "dataset_url": None,
            "pdf_content": None,
            "test_cases": None,
            "errors": [],
            "status": "created"
        }
        
        # 读取PDF内容
        state = read_pdf_content(state)
        if state["status"] == "error":
            # 删除临时文件
            os.unlink(temp_file_path)
            raise HTTPException(status_code=500, detail=f"读取PDF内容失败: {state['errors']}")
        
        # 更新任务的需求文档
        task.requirement_doc = state["pdf_content"]
        db.commit()
        
        # 生成测试用例
        state = agent_generate_test_cases(state)
        
        # 删除临时文件
        os.unlink(temp_file_path)
        
        if state["status"] == "error":
            raise HTTPException(status_code=500, detail=f"生成测试用例失败: {state['errors']}")
        
        # 获取测试用例
        test_cases = state.get("test_cases", [])
        if not test_cases:
            return {"message": "未生成测试用例", "test_cases": []}
        
        # 保存测试用例到数据库并格式化返回数据
        formatted_test_cases = []
        for case in test_cases:
            case_data = {
                "task_id": task_id,
                "case_id": case["id"],
                "document_id": document_id,
                "input_data": {
                    "name": case["name"],
                    "purpose": case["purpose"],
                    "steps": case["steps"]
                },
                "expected_output": {
                    "expected_result": case["expected_result"],
                    "validation_method": case["validation_method"]
                }
            }
            db_case = DBTestCase(**case_data)
            db.add(db_case)
            db.commit()
            db.refresh(db_case)
            formatted_test_cases.append(format_test_case(db_case))
        
        log.success(f"测试用例生成成功，共{len(formatted_test_cases)}个测试用例")
        
        return {
            "message": f"成功从文档生成{len(formatted_test_cases)}个测试用例",
            "test_cases": formatted_test_cases
        }
    except Exception as e:
        log.error(f"生成测试用例异常: {str(e)}")
        # 确保临时文件被删除
        try:
            if 'temp_file_path' in locals():
                os.unlink(temp_file_path)
        except:
            pass
        raise HTTPException(status_code=500, detail=f"生成测试用例异常: {str(e)}")


# 添加算法镜像地址
@router.post("/tasks/{task_id}/algorithm-image", response_model=MessageResponse)
async def update_task_algorithm_image(
    task_id: str = Path(..., description="任务ID"),
    request: AlgorithmImageRequest = Body(..., description="算法镜像信息"),
    db: Session = Depends(get_db)
):
    """
    通过任务ID更新算法镜像地址
    
    - **task_id**: 任务ID
    - **request**: 包含算法镜像地址的请求
    
    返回成功消息
    """
    log.info(f"开始更新任务的算法镜像地址: {request.algorithm_image}, 任务ID: {task_id}")
    
    try:
        # 使用专用函数更新算法镜像地址
        task = db_update_algorithm_image(task_id, request.algorithm_image)
        
        if task:
            log.info(f"更新任务 {task_id} 的算法镜像地址")
            return {"message": f"算法镜像地址已成功更新: {request.algorithm_image}"}
        else:
            raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
    except Exception as e:
        log.error(f"更新算法镜像地址异常: {str(e)}")
        raise HTTPException(status_code=500, detail=f"更新算法镜像地址异常: {str(e)}")


# 添加数据集地址 - PUT方法
@router.put("/tasks/{task_id}/algorithm_image", response_model=MessageResponse)
async def update_algorithm_image_put(
    task_id: str = Path(..., description="任务ID"),
    request: AlgorithmImageRequest = Body(..., description="算法镜像信息"),
    db: Session = Depends(get_db)
):
    """
    通过任务ID更新算法镜像地址 (PUT方法)
    
    - **task_id**: 任务ID
    - **request**: 包含算法镜像地址的请求
    
    返回成功消息
    """
    log.info(f"开始更新任务的算法镜像地址 (PUT): {request.algorithm_image}, 任务ID: {task_id}")
    
    try:
        # 使用专用函数更新算法镜像地址
        task = db_update_algorithm_image(task_id, request.algorithm_image)
        
        if task:
            log.success(f"更新任务 {task_id} 的算法镜像地址成功: {request.algorithm_image}")
            return {"message": f"算法镜像地址已成功更新: {request.algorithm_image}"}
        else:
            raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
    except Exception as e:
        log.error(f"更新算法镜像地址异常: {str(e)}")
        raise HTTPException(status_code=500, detail=f"更新算法镜像地址异常: {str(e)}")


# 添加数据集地址 - PUT方法
@router.put("/tasks/{task_id}/dataset_url", response_model=MessageResponse)
async def update_dataset_url_put(
    task_id: str = Path(..., description="任务ID"),
    request: DatasetUrlRequest = Body(..., description="数据集地址信息"),
    db: Session = Depends(get_db)
):
    """
    通过任务ID更新数据集地址 (PUT方法)
    
    - **task_id**: 任务ID
    - **request**: 包含数据集地址的请求
    
    返回成功消息
    """
    log.info(f"开始更新任务的数据集地址 (PUT): {request.dataset_url}, 任务ID: {task_id}")
    
    try:
        # 使用专用函数更新数据集地址
        task = db_update_dataset_url(task_id, request.dataset_url)
        
        if task:
            log.success(f"更新任务 {task_id} 的数据集地址成功: {request.dataset_url}")
            return {"message": f"数据集地址已成功更新: {request.dataset_url}"}
        else:
            raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
    except Exception as e:
        log.error(f"更新数据集地址异常: {str(e)}")
        raise HTTPException(status_code=500, detail=f"更新数据集地址异常: {str(e)}")


# 添加数据集地址
@router.post("/tasks/{task_id}/dataset-url", response_model=MessageResponse)
async def update_task_dataset_url(
    task_id: str = Path(..., description="任务ID"),
    request: DatasetUrlRequest = Body(..., description="数据集信息"),
    db: Session = Depends(get_db)
):
    """
    通过任务ID更新数据集地址
    
    - **task_id**: 任务ID
    - **request**: 包含数据集地址的请求
    
    返回成功消息
    """
    log.info(f"开始更新任务的数据集地址: {request.dataset_url}, 任务ID: {task_id}")
    
    try:
        # 使用专用函数更新数据集URL
        task = db_update_dataset_url(task_id, request.dataset_url)
        
        if task:
            log.info(f"更新任务 {task_id} 的数据集地址")
            return {"message": f"数据集地址已成功更新: {request.dataset_url}"}
        else:
            raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
    except Exception as e:
        log.error(f"更新数据集地址异常: {str(e)}")
        raise HTTPException(status_code=500, detail=f"更新数据集地址异常: {str(e)}")


# 添加文档任务信息查询接口
@router.get("/documents/{document_id}/task-info", response_model=Dict[str, Any])
async def get_document_task_info(
    document_id: str = Path(..., description="文档ID"),
    db: Session = Depends(get_db)
):
    """
    获取文档关联的任务信息
    
    - **document_id**: 文档ID
    
    返回文档关联的任务信息，包括算法镜像地址和数据集URL
    """
    # 检查文档是否存在
    pdf_dir = "data/pdfs"
    matching_files = [f for f in os.listdir(pdf_dir) if f.startswith(document_id)]
    
    if not matching_files:
        raise HTTPException(status_code=404, detail=f"文档不存在: {document_id}")
    
    log.info(f"查询文档关联的任务信息: 文档ID={document_id}")
    
    try:
        # 查找与文档关联的任务
        task = None
        task_id = None
        doc_info = DOCUMENTS.get(document_id)
        
        if doc_info and 'task_id' in doc_info:
            # 如果文档信息中有任务ID，获取该任务ID
            task_id = doc_info['task_id']
            # 查询任务
            task = db.query(DBTestTask).filter(DBTestTask.task_id == task_id).first()
        
        if not task:
            # 如果没有找到直接关联的任务，尝试通过测试用例找到关联的任务
            test_cases = db.query(DBTestCase).filter(DBTestCase.document_id == document_id).all()
            if test_cases:
                # 获取第一个测试用例的任务ID
                task_id = test_cases[0].task_id
                # 查询任务
                task = db.query(DBTestTask).filter(DBTestTask.task_id == task_id).first()
        
        if task:
            # 如果找到任务，返回任务信息
            result = {
                "document_id": document_id,
                "task_id": task.task_id,
                "algorithm_image": task.algorithm_image,
                "dataset_url": task.dataset_url,
                "status": task.status,
                "created_at": task.created_at.isoformat() if task.created_at else None,
                "updated_at": task.updated_at.isoformat() if task.updated_at else None
            }
            
            # 如果有文档信息，添加到结果中
            if doc_info:
                result["filename"] = doc_info.get("filename")
                result["file_path"] = doc_info.get("file_path")
            
            return result
        else:
            # 如果仍未找到任务，返回一个基本的文档信息
            result = {
                "document_id": document_id,
                "task_id": None,
                "algorithm_image": None,
                "dataset_url": None,
                "status": "unknown"
            }
            
            # 如果有文档信息，添加到结果中
            if doc_info:
                result["filename"] = doc_info.get("filename")
                result["file_path"] = doc_info.get("file_path")
            
            return result
            
    except Exception as e:
        log.error(f"查询文档任务信息异常: {str(e)}")
        raise HTTPException(status_code=500, detail=f"查询文档任务信息异常: {str(e)}")

@router.get("/tasks", response_model=TestTasksResponse)
async def get_all_tasks(
    db: Session = Depends(get_db)
):
    """
    获取所有测试任务
    
    返回数据库中所有测试任务的列表。
    
    Returns:
        TestTasksResponse: 包含测试任务列表的响应
    """
    log.info("获取所有测试任务")
    
    try:
        # 直接从数据库查询所有任务
        tasks = db.query(DBTestTask).all()
        
        # 格式化任务数据
        formatted_tasks = []
        for task in tasks:
            # 获取任务关联的测试用例数量
            test_cases_count = db.query(DBTestCase).filter(DBTestCase.task_id == task.task_id).count()
            
            formatted_tasks.append(TestTaskItem(
                id=task.id,
                task_id=task.task_id,
                document_id=task.document_id,
                requirement_doc=task.requirement_doc,
                algorithm_image=task.algorithm_image,
                dataset_url=task.dataset_url,
                container_name=task.container_name,
                status=task.status or "unknown",
                created_at=task.created_at.isoformat() if task.created_at else None,
                updated_at=task.updated_at.isoformat() if task.updated_at else None,
                test_cases_count=test_cases_count
            ))
        
        return TestTasksResponse(
            message=f"成功获取{len(formatted_tasks)}个测试任务",
            tasks=formatted_tasks
        )
    except Exception as e:
        log.error(f"获取所有测试任务失败: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"获取测试任务失败: {str(e)}"
        )

# 开始任务执行前准备Docker容器
@router.post("/tasks/{task_id}/prepare", response_model=DockerSetupResponse)
async def prepare_task_execution(
    task_id: str = Path(..., description="任务ID")
):
    """
    为测试任务执行准备Docker容器环境
    
    该接口会基于任务信息设置Docker容器，为后续的测试执行做准备。
    它会从数据库获取algorithm_image和dataset_url，然后通过MCP在远程服务器上设置Docker容器。
    
    - **task_id**: 任务ID
    
    返回Docker容器设置结果
    """
    log.info(f"开始为任务 {task_id} 准备Docker环境")
    
    try:
        # 获取任务信息验证
        task = get_test_task(task_id)
        if not task:
            log.error(f"任务不存在: {task_id}")
            raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
            
        if not task.algorithm_image:
            log.error(f"任务 {task_id} 未配置算法镜像")
            raise HTTPException(status_code=400, detail=f"未配置算法镜像，请先设置算法镜像")
        
        # 检查任务是否已有容器名称
        if task.container_name:
            log.info(f"任务 {task_id} 已有容器名称: {task.container_name}，跳过设置")
            return {
                "message": "Docker容器已存在",
                "success": True,
                "task_id": task_id,
                "container_name": task.container_name,
                "error": None
            }
        
        # 调用执行函数设置Docker容器
        log.info(f"开始为任务 {task_id} 设置Docker容器，算法镜像: {task.algorithm_image}")
        result = await setup_algorithm_container(task_id)
        
        log.info(f"Docker容器设置结果: success={result.get('success')}")
        
        # 返回响应
        if result.get("success"):
            container_name = result.get("container_name")
            log.success(f"Docker容器设置成功: {container_name}")
            
            # 更新任务的容器名称
            log.info(f"更新任务 {task_id} 的容器名称: {container_name}")
            update_task_container_name(task_id, container_name)
            
            return {
                "message": "Docker容器设置成功",
                "success": True,
                "task_id": task_id,
                "container_name": container_name,
                "error": None
            }
        else:
            error_message = result.get("error", "未知错误")
            log.error(f"Docker容器设置失败: {error_message}")
            raise HTTPException(
                status_code=500, 
                detail=f"Docker容器设置失败: {error_message}"
            )
    except HTTPException:
        # 直接重新抛出HTTP异常
        raise
    except Exception as e:
        log.error(f"准备Docker环境失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"准备Docker环境失败: {str(e)}")


# 执行测试任务
@router.post("/tasks/{task_id}/execute", response_model=TestExecutionResponse)
async def execute_task_tests(
    task_id: str = Path(..., description="任务ID")
):
    """
    执行任务中的所有测试用例
    
    该接口会执行指定任务中的所有测试用例，并返回执行结果。
    在执行之前会检查：
    1. Docker容器是否已设置
    2. 测试用例是否已设置测试数据
    
    - **task_id**: 任务ID
    
    返回测试执行结果
    """
    log.info(f"开始执行任务测试: {task_id}")
    
    try:
        # 记录开始时间
        start_time = time.time()
        
        # 获取任务信息
        task = get_test_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"测试任务不存在: {task_id}")
            
        # 确保容器已准备就绪 
        log.info(f"确保容器已准备就绪: {task_id}")
        container_result = await ensure_container_ready(task_id)
        if not container_result["success"]:
            error_msg = container_result.get('error', '未知错误')
            log.error(f"容器准备失败: {error_msg}")
            return {
                "message": f"容器准备失败: {error_msg}",
                "success": False,
                "task_id": task_id,
                "cases_total": 0,
                "cases_executed": 0,
                "cases_passed": 0,
                "cases_failed": 0,
                "execution_time": time.time() - start_time,
                "error": error_msg
            }
            
        log.info(f"容器已准备就绪: {container_result.get('container_name')}")
        
        # 初始化状态
        state = {
            "task_id": task_id,
            "current_case_index": 0,
            "current_strategy_index": 0,
            "test_cases": [],
            "command_strategies": None,
            "status": "created",
            "errors": [],
            "container_ready": True  # 容器已准备好
        }
        
        # 统计变量
        cases_total = 0
        cases_executed = 0
        cases_passed = 0
        cases_failed = 0
        error_messages = []
        
        # 加载测试用例
        log.info(f"加载测试用例: {task_id}")
        load_result = load_test_cases(state)
        if not load_result or load_result.get("status") == "error":
            error_msg = f"加载测试用例失败: {load_result.get('errors', ['未知错误'])}"
            log.error(error_msg)
            return {
                "message": error_msg,
                "success": False,
                "task_id": task_id,
                "cases_total": 0,
                "cases_executed": 0,
                "cases_passed": 0,
                "cases_failed": 0,
                "execution_time": time.time() - start_time,
                "error": error_msg
            }
            
        test_cases = load_result.get('test_cases', [])
        if not test_cases:
            log.error("未找到测试用例")
            return {
                "message": "未找到测试用例",
                "success": False,
                "task_id": task_id,
                "cases_total": 0,
                "cases_executed": 0,
                "cases_passed": 0,
                "cases_failed": 0,
                "execution_time": time.time() - start_time,
                "error": "未找到测试用例"
            }
            
        log.info(f"成功加载测试用例，共 {len(test_cases)} 个")
        cases_total = len(test_cases)
        state = load_result
        
        # 逐个执行测试用例
        for i, case in enumerate(test_cases):
            case_id = case.get('case_id')
            if not case_id:
                log.error("测试用例缺少case_id字段")
                error_messages.append("测试用例缺少case_id字段")
                continue
                
            log.info(f"正在处理测试用例 {i+1}/{len(test_cases)}: {case_id}")
            
            # 更新当前用例索引
            state['current_case_index'] = i
            state['case_id'] = case_id
            
            try:
                # 解析命令
                log.info(f"解析测试用例命令: {case_id}")
                parse_result = await parse_command(state)
                if not parse_result or parse_result.get("status") != "parsed":
                    log.error(f"命令解析失败: {case_id}")
                    cases_failed += 1
                    continue
                    
                state = parse_result
                
                # 执行命令
                log.info(f"执行测试用例命令: {case_id}")
                execute_result = await execute_command(state)
                if not execute_result or execute_result.get("status") != "executed":
                    log.error(f"命令执行失败: {case_id}")
                    cases_failed += 1
                    continue
                
                state = execute_result
                
                # 获取执行结果
                execution_result = state.get('execution_result', {})
                success = execution_result.get('success', False)
                
                if success:
                    cases_passed += 1
                else:
                    cases_failed += 1
                
                # 保存结果
                log.info(f"保存测试用例结果: {case_id}")
                save_result_state = await save_result(state)
                if not save_result_state:
                    log.error(f"保存结果失败: {case_id}")
                    error_messages.append(f"保存结果失败: {case_id}")
                else:
                    cases_executed += 1
                    state = save_result_state
                
            except Exception as e:
                log.error(f"处理测试用例 {case_id} 时出错: {str(e)}")
                error_messages.append(f"处理测试用例 {case_id} 时出错: {str(e)}")
                cases_failed += 1
        
        # 计算总执行时间
        execution_time = time.time() - start_time
        
        # 更新任务状态为已完成
        with get_db() as db:
            # 更新测试任务状态
            update_test_task_status(
                task_id=task_id,
                status="completed"
            )
        
        # 组装最终响应
        message = f"测试执行完成，共 {cases_total} 个测试用例，成功 {cases_passed} 个，失败 {cases_failed} 个"
        log.success(message)
        
        return {
            "message": message,
            "success": cases_failed == 0 and cases_executed > 0,
            "task_id": task_id,
            "cases_total": cases_total,
            "cases_executed": cases_executed,
            "cases_passed": cases_passed,
            "cases_failed": cases_failed,
            "execution_time": execution_time,
            "error": "; ".join(error_messages) if error_messages else None
        }
    except Exception as e:
        log.error(f"执行测试任务时出错: {str(e)}")
        import traceback
        log.error(traceback.format_exc())
        
        # 确保start_time已定义
        if 'start_time' not in locals():
            start_time = time.time()
        
        return {
            "message": f"执行测试任务时出错: {str(e)}",
            "success": False,
            "task_id": task_id,
            "cases_total": 0,
            "cases_executed": 0,
            "cases_passed": 0,
            "cases_failed": 0,
            "execution_time": time.time() - start_time,
            "error": str(e)
        }

# 执行单个测试用例
@router.post("/testcases/{case_id}/execute", response_model=TestExecutionResponse)
async def execute_single_test_case(
    case_id: str = Path(..., description="测试用例ID"),
    db: Session = Depends(get_db)
):
    """
    执行单个测试用例
    
    该接口会执行指定的单个测试用例，并自动完成命令解析、命令执行和结果保存等步骤。
    在执行之前会检查：
    1. Docker容器是否已设置
    2. 测试用例是否已设置测试数据
    
    - **case_id**: 测试用例ID
    
    返回测试执行结果
    """
    log.info(f"开始执行单个测试用例: {case_id}")
    
    try:
        # 记录开始时间
        start_time = time.time()
        
        # 获取测试用例信息
        case = db.query(DBTestCase).filter(DBTestCase.case_id == case_id).first()
        if not case:
            raise HTTPException(status_code=404, detail=f"测试用例不存在: {case_id}")
            
        # 获取任务ID
        task_id = case.task_id
        
        # 获取任务信息
        task = get_test_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"关联的任务不存在: {task_id}")
        
        # 通过WebSocket发送测试开始消息
        await manager.send_json(case_id, {
            "event": "test_started",
            "message": "测试用例执行开始",
            "case_id": case_id,
            "task_id": task_id,
            "timestamp": datetime.now().isoformat(),
            "status": "executing"
        })
        
        # 更新测试用例状态为执行中
        update_test_case_status(case_id, "executing")
            
        # 检查测试数据是否已设置
        if not case.test_data:
            log.error(f"测试用例 {case_id} 未设置测试数据")
            error_msg = "请先设置测试数据后再执行测试"
            
            # 发送错误消息
            await manager.send_json(case_id, {
                "event": "test_error",
                "message": error_msg,
                "case_id": case_id,
                "timestamp": datetime.now().isoformat(),
                "status": "failed"
            })
            
            raise HTTPException(
                status_code=400,
                detail=error_msg
            )
        
        # 确保容器已准备就绪
        log.info(f"确保容器已准备就绪: {task_id}")
        
        # 发送容器准备消息
        await manager.send_json(case_id, {
            "event": "preparing_container",
            "message": "正在准备Docker容器...",
            "case_id": case_id,
            "timestamp": datetime.now().isoformat(),
            "status": "executing"
        })
        
        container_result = await ensure_container_ready(task_id)
        if not container_result["success"]:
            error_msg = container_result.get('error', '未知错误')
            log.error(f"容器准备失败: {error_msg}")
            
            # 发送容器准备失败消息
            await manager.send_json(case_id, {
                "event": "container_failed",
                "message": f"容器准备失败: {error_msg}",
                "case_id": case_id,
                "timestamp": datetime.now().isoformat(),
                "status": "failed"
            })
            
            raise HTTPException(
                status_code=500,
                detail=f"容器准备失败: {error_msg}"
            )
        
        log.info(f"容器已准备就绪: {container_result.get('container_name')}")
        
        # 发送容器准备就绪消息
        await manager.send_json(case_id, {
            "event": "container_ready",
            "message": "Docker容器已准备就绪",
            "case_id": case_id,
            "container_name": container_result.get('container_name', ''),
            "timestamp": datetime.now().isoformat(),
            "status": "executing"
        })
        
        # 初始化状态
        state = {
            "task_id": task_id,
            "case_id": case_id,  # 指定测试用例ID
            "current_case_index": 0,
            "test_cases": [],
            "command_strategies": None, 
            "current_strategy_index": 0,
            "status": "created",
            "errors": [],
            "container_ready": True  # 容器已准备好
        }
        
        cases_passed = 0
        cases_failed = 0
        error_message = None
        
        # 加载指定的测试用例
        log.info(f"加载测试用例: {case_id}")
        
        # 发送加载测试用例消息
        await manager.send_json(case_id, {
            "event": "loading_testcase",
            "message": "正在加载测试用例...",
            "case_id": case_id,
            "timestamp": datetime.now().isoformat(),
            "status": "executing"
        })
        
        load_result = load_test_cases(state)
        if not load_result or load_result.get("status") == "error":
            error_msg = f"加载测试用例失败: {load_result.get('errors', ['未知错误'])}"
            log.error(error_msg)
            
            # 发送测试用例加载失败消息
            await manager.send_json(case_id, {
                "event": "loading_failed",
                "message": error_msg,
                "case_id": case_id,
                "timestamp": datetime.now().isoformat(),
                "status": "failed"
            })
            
            return {
                "message": error_msg,
                "success": False,
                "task_id": task_id,
                "cases_total": 1,
                "cases_executed": 0,
                "cases_passed": 0,
                "cases_failed": 1,
                "execution_time": time.time() - start_time,
                "error": error_msg
            }
            
        test_cases = load_result.get('test_cases', [])
        if not test_cases:
            log.error(f"未找到测试用例: {case_id}")
            error_msg = f"未找到测试用例: {case_id}"
            
            # 发送测试用例未找到消息
            await manager.send_json(case_id, {
                "event": "testcase_not_found",
                "message": error_msg,
                "case_id": case_id,
                "timestamp": datetime.now().isoformat(),
                "status": "failed"
            })
            
            return {
                "message": error_msg,
                "success": False,
                "task_id": task_id,
                "cases_total": 1,
                "cases_executed": 0,
                "cases_passed": 0,
                "cases_failed": 1,
                "execution_time": time.time() - start_time,
                "error": error_msg
            }
            
        log.info(f"成功加载测试用例: {case_id}")
        
        # 发送测试用例加载成功消息
        await manager.send_json(case_id, {
            "event": "testcase_loaded",
            "message": "测试用例加载成功",
            "case_id": case_id,
            "timestamp": datetime.now().isoformat(),
            "status": "executing"
        })
        
        # 使用字典合并更新state，保留原始字段
        state = {
            **state,
            **load_result
        }
        
        try:
            # 解析命令
            log.info(f"解析测试用例命令: {case_id}")
            
            # 发送命令解析消息
            await manager.send_json(case_id, {
                "event": "parsing_command",
                "message": "正在解析测试命令...",
                "case_id": case_id,
                "timestamp": datetime.now().isoformat(),
                "status": "executing"
            })
            
            parse_result = await parse_command(state)
            if not parse_result or parse_result.get("status") != "parsed":
                error_msg = f"命令解析失败: {case_id}"
                log.error(error_msg)
                cases_failed = 1
                error_message = error_msg
                
                # 发送命令解析失败消息
                await manager.send_json(case_id, {
                    "event": "parsing_failed",
                    "message": error_msg,
                    "case_id": case_id,
                    "timestamp": datetime.now().isoformat(),
                    "status": "failed"
                })
            else:
                state = parse_result
                
                # 发送命令解析成功消息
                await manager.send_json(case_id, {
                    "event": "parsing_succeeded",
                    "message": "命令解析成功，准备执行",
                    "case_id": case_id,
                    "timestamp": datetime.now().isoformat(),
                    "status": "executing"
                })
                
                # 执行命令
                log.info(f"执行测试用例命令: {case_id}")
                
                # 发送命令执行开始消息
                await manager.send_json(case_id, {
                    "event": "executing_command",
                    "message": "正在执行测试命令...",
                    "case_id": case_id,
                    "timestamp": datetime.now().isoformat(),
                    "status": "executing"
                })
                
                execute_result = await execute_command(state)
                if not execute_result or execute_result.get("status") != "executed":
                    error_msg = f"命令执行失败: {case_id}"
                    log.error(error_msg)
                    cases_failed = 1
                    error_message = error_msg
                    
                    # 发送命令执行失败消息
                    await manager.send_json(case_id, {
                        "event": "execution_failed",
                        "message": error_msg,
                        "case_id": case_id,
                        "timestamp": datetime.now().isoformat(),
                        "status": "failed"
                    })
                else:
                    state = execute_result
                    
                    # 获取执行结果
                    execution_result = state.get('execution_result', {})
                    success = execution_result.get('success', False)
                    raw_stdout = execution_result.get('raw_stdout', '')
                    raw_stderr = execution_result.get('raw_stderr', '')
                    
                    # 发送命令执行结果消息
                    await manager.send_json(case_id, {
                        "event": "execution_completed",
                        "message": "命令执行完成",
                        "case_id": case_id,
                        "success": success,
                        "stdout": raw_stdout[:500] + "..." if len(raw_stdout) > 500 else raw_stdout,
                        "stderr": raw_stderr[:500] + "..." if len(raw_stderr) > 500 else raw_stderr,
                        "timestamp": datetime.now().isoformat(),
                        "status": "executing"
                    })
                    
                    if success:
                        cases_passed = 1
                    else:
                        cases_failed = 1
                        error_message = execution_result.get('error', '未知错误')
                    
                    # 保存结果
                    log.info(f"保存测试用例结果: {case_id}")
                    
                    # 发送保存结果消息
                    await manager.send_json(case_id, {
                        "event": "saving_results",
                        "message": "正在保存测试结果...",
                        "case_id": case_id,
                        "timestamp": datetime.now().isoformat(),
                        "status": "executing"
                    })
                    
                    save_result_state = await save_result(state)
                    if not save_result_state:
                        error_msg = f"保存结果失败: {case_id}"
                        log.error(error_msg)
                        if not error_message:
                            error_message = error_msg
                        
                        # 发送保存结果失败消息
                        await manager.send_json(case_id, {
                            "event": "saving_failed",
                            "message": error_msg,
                            "case_id": case_id,
                            "timestamp": datetime.now().isoformat(),
                            "status": "executing"
                        })
                    else:
                        # 发送保存结果成功消息
                        await manager.send_json(case_id, {
                            "event": "saving_succeeded",
                            "message": "测试结果已保存",
                            "case_id": case_id,
                            "timestamp": datetime.now().isoformat(),
                            "status": "executing"
                        })
        except Exception as e:
            log.error(f"处理测试用例 {case_id} 时出错: {str(e)}")
            cases_failed = 1
            error_message = str(e)
            
            # 发送执行异常消息
            await manager.send_json(case_id, {
                "event": "execution_exception",
                "message": f"执行过程中发生异常: {str(e)}",
                "case_id": case_id,
                "timestamp": datetime.now().isoformat(),
                "status": "failed"
            })
        
        # 计算总执行时间
        execution_time = time.time() - start_time
        
        # 组装最终响应
        cases_executed = cases_passed + cases_failed
        message = f"测试用例 {case_id} 执行{'成功' if cases_passed == 1 else '失败'}"
        log.info(message)
        
        return {
            "message": message,
            "success": cases_passed == 1,
            "task_id": task_id,
            "cases_total": 1,
            "cases_executed": cases_executed,
            "cases_passed": cases_passed,
            "cases_failed": cases_failed,
            "execution_time": execution_time,
            "error": error_message
        }
    except Exception as e:
        log.error(f"执行测试用例时出错: {str(e)}")
        import traceback
        log.error(traceback.format_exc())
        
        # 确保start_time已定义
        if 'start_time' not in locals():
            start_time = time.time()
        
        # 发送测试失败消息
        try:
            await manager.send_json(case_id, {
                "event": "test_failed",
                "message": f"执行测试用例时出错: {str(e)}",
                "case_id": case_id,
                "task_id": task_id if 'task_id' in locals() else "unknown",
                "error": str(e),
                "timestamp": datetime.now().isoformat(),
                "status": "failed"
            })
        except Exception as ws_error:
            log.error(f"发送WebSocket消息失败: {str(ws_error)}")
        
        return {
            "message": f"执行测试用例时出错: {str(e)}",
            "success": False,
            "task_id": task_id if 'task_id' in locals() else "unknown",
            "cases_total": 1,
            "cases_executed": 0,
            "cases_passed": 0,
            "cases_failed": 1,
            "execution_time": time.time() - start_time,
            "error": str(e)
        }

# 查询任务测试状态
@router.get("/tasks/{task_id}/status", response_model=Dict[str, Any])
async def get_task_test_status(
    task_id: str = Path(..., description="任务ID"),
    db: Session = Depends(get_db)
):
    """
    查询任务的测试状态
    
    该接口返回指定任务的测试状态，包括总体进度和各测试用例的详细状态。
    
    - **task_id**: 任务ID
    
    返回任务测试状态信息
    """
    log.info(f"查询任务测试状态: {task_id}")
    
    try:
        # 获取任务信息
        task = get_test_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
            
        # 获取任务的所有测试用例
        cases = db.query(DBTestCase).filter(DBTestCase.task_id == task_id).all()
        
        # 统计测试用例状态
        total_cases = len(cases)
        completed_cases = 0
        passed_cases = 0
        failed_cases = 0
        pending_cases = 0
        
        case_details = []
        
        for case in cases:
            # 从input_data中获取测试用例名称
            input_data = case.input_data or {}
            name = input_data.get("name", "未命名测试用例")
            
            case_info = {
                "case_id": case.case_id,
                "name": name,  # 添加测试用例名称
                "status": case.status or "pending",
                "is_passed": case.is_passed,
                "result_analysis": case.result_analysis,
                "has_output": bool(case.actual_output)
            }
            
            case_details.append(case_info)
            
            # 统计不同状态的用例数量
            if case.status == "completed":
                completed_cases += 1
                if case.is_passed:
                    passed_cases += 1
                else:
                    failed_cases += 1
            elif case.status == "failed":
                completed_cases += 1
                failed_cases += 1
            else:
                pending_cases += 1
        
        # 计算整体进度百分比
        progress = (completed_cases / total_cases * 100) if total_cases > 0 else 0
        
        # 组装响应
        response = {
            "task_id": task_id,
            "status": task.status,
            "total_cases": total_cases,
            "completed_cases": completed_cases,
            "passed_cases": passed_cases,
            "failed_cases": failed_cases,
            "pending_cases": pending_cases,
            "progress_percent": round(progress, 2),
            "case_details": case_details,
            "updated_at": task.updated_at.isoformat() if task.updated_at else None
        }
        
        return response
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"查询任务测试状态失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"查询任务测试状态失败: {str(e)}")

# 获取测试分析结果
@router.get("/tasks/{task_id}/analysis", response_model=TestAnalysisResponse)
async def get_task_analysis(
    task_id: str = Path(..., description="任务ID"),
    db: Session = Depends(get_db)
):
    """
    获取任务的测试分析结果，以结构化的格式返回详细的分析信息
    
    该接口返回指定任务的所有测试用例的分析结果，包括：
    - 整体测试总结
    - 每个测试用例的详细分析
    - 执行信息和输出概要
    
    - **task_id**: 任务ID
    
    返回测试分析结果信息
    """
    log.info(f"获取任务测试分析结果: {task_id}")
    
    try:
        # 获取任务信息
        task = get_test_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
            
        # 获取任务的所有测试用例
        cases = db.query(DBTestCase).filter(DBTestCase.task_id == task_id).all()
        
        # 统计数据
        total_cases = len(cases)
        passed_cases = 0
        failed_cases = 0
        pending_cases = 0
        
        # 处理每个测试用例的分析结果
        analysis_results = []
        
        for case in cases:
            # 从input_data中获取测试用例信息
            input_data = case.input_data or {}
            name = input_data.get("name", "未命名测试用例")
            purpose = input_data.get("purpose", "")
            steps = input_data.get("steps", "")
            
            # 统计通过/失败数量
            if case.status == "completed":
                if case.is_passed:
                    passed_cases += 1
                else:
                    failed_cases += 1
            elif case.status == "pending":
                pending_cases += 1
            
            # 解析实际输出
            actual_output = case.actual_output or ""
            output_summary = ""
            if actual_output:
                try:
                    # 提取关键信息
                    if "algorithm_data" in actual_output:
                        output_data = json.loads(actual_output)
                        algo_data = output_data.get("algorithm_data", {})
                        output_summary = f"检测到 {algo_data.get('target_count', 0)} 个目标，" \
                                      f"报警状态: {'是' if algo_data.get('is_alert') else '否'}"
                except:
                    output_summary = "输出解析失败"
            
            # 构建分析结果
            result = TestAnalysisResult(
                case_id=case.case_id,
                name=name,
                is_passed=case.is_passed,
                summary="测试通过" if case.is_passed else "测试失败",
                details={
                    "测试目的": purpose,
                    "测试步骤": steps,
                    "预期结果": case.expected_output.get("expected_result", "") if case.expected_output else "",
                    "验证方法": case.expected_output.get("validation_method", "") if case.expected_output else "",
                    "分析结果": case.result_analysis or "暂无分析结果"
                },
                execution_info={
                    "状态": case.status or "pending",
                    "执行时间": None  # 移除对updated_at的引用
                },
                output_summary=output_summary
            )
            
            analysis_results.append(result)
        
        # 计算成功率
        success_rate = (passed_cases / total_cases * 100) if total_cases > 0 else 0
        
        # 组装响应
        return TestAnalysisResponse(
            message="成功获取测试分析结果",
            task_id=task_id,
            summary={
                "total_cases": total_cases,
                "passed_cases": passed_cases,
                "failed_cases": failed_cases,
                "pending_cases": pending_cases,
                "success_rate": round(success_rate, 2),
                "status": task.status,
                "execution_time": task.updated_at.isoformat() if task.updated_at else None
            },
            analysis_results=analysis_results
        )
        
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"获取任务测试分析结果失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"获取任务测试分析结果失败: {str(e)}")

# 获取任务的所有测试用例及其test_data
@router.get("/tasks/{task_id}/test-data", response_model=TestCasesDataResponse)
async def get_test_cases_data(
    task_id: str = Path(..., description="任务ID"),
    db: Session = Depends(get_db)
):
    """
    获取指定任务的所有测试用例及其测试数据路径
    
    - **task_id**: 任务ID
    
    返回测试用例列表及其测试数据路径
    """
    log.info(f"获取任务测试用例数据: {task_id}")
    
    try:
        # 检查任务是否存在
        task = get_test_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
        
        # 获取所有测试用例
        cases = db.query(DBTestCase).filter(DBTestCase.task_id == task_id).all()
        
        # 转换为响应格式
        test_cases = []
        for case in cases:
            input_data = case.input_data or {}
            test_cases.append(
                TestCaseWithData(
                    case_id=case.case_id,
                    name=input_data.get("name", "未命名测试用例"),
                    test_data=case.test_data,
                    purpose=input_data.get("purpose", ""),
                    steps=input_data.get("steps", ""),
                    status=case.status or "pending",  # 添加状态字段
                    is_passed=case.is_passed  # 添加是否通过字段
                )
            )
        
        return TestCasesDataResponse(
            message=f"成功获取{len(test_cases)}个测试用例数据",
            task_id=task_id,
            test_cases=test_cases
        )
        
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"获取测试用例数据失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"获取测试用例数据失败: {str(e)}")


# 更新测试用例的test_data
@router.put("/tasks/{task_id}/test-data", response_model=TestCasesDataResponse)
async def update_test_cases_data(
    task_id: str = Path(..., description="任务ID"),
    request: TestDataBatchUpdateRequest = Body(..., description="测试数据更新请求"),
    db: Session = Depends(get_db)
):
    """
    批量更新测试用例的测试数据路径
    
    - **task_id**: 任务ID
    - **request**: 包含要更新的测试用例ID和对应的测试数据路径
    
    返回更新后的测试用例列表
    """
    log.info(f"更新任务测试用例数据: {task_id}")
    
    try:
        # 检查任务是否存在
        task = get_test_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
        
        # 获取所有要更新的case_id
        case_ids = [update.case_id for update in request.updates]
        
        # 获取这些测试用例
        cases = db.query(DBTestCase).filter(
            DBTestCase.task_id == task_id,
            DBTestCase.case_id.in_(case_ids)
        ).all()
        
        # 创建case_id到test_data的映射
        updates_map = {update.case_id: update.test_data for update in request.updates}
        
        # 更新测试用例
        updated_cases = []
        for case in cases:
            if case.case_id in updates_map:
                case.test_data = updates_map[case.case_id]
                input_data = case.input_data or {}
                updated_cases.append(
                    TestCaseWithData(
                        case_id=case.case_id,
                        name=input_data.get("name", "未命名测试用例"),
                        test_data=case.test_data,
                        purpose=input_data.get("purpose", ""),
                        steps=input_data.get("steps", "")
                    )
                )
        
        # 提交更改
        db.commit()
        
        # 获取所有测试用例（包括未更新的）
        all_cases = db.query(DBTestCase).filter(DBTestCase.task_id == task_id).all()
        test_cases = []
        for case in all_cases:
            input_data = case.input_data or {}
            test_cases.append(
                TestCaseWithData(
                    case_id=case.case_id,
                    name=input_data.get("name", "未命名测试用例"),
                    test_data=case.test_data,
                    purpose=input_data.get("purpose", ""),
                    steps=input_data.get("steps", "")
                )
            )
        
        return TestCasesDataResponse(
            message=f"成功更新{len(updated_cases)}个测试用例数据",
            task_id=task_id,
            test_cases=test_cases
        )
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        log.error(f"更新测试用例数据失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"更新测试用例数据失败: {str(e)}")

@router.post("/tasks/{task_id}/report", response_model=ReportGenerationResponse)
async def generate_task_report(
    task_id: str = Path(..., description="任务ID"),
    db: Session = Depends(get_db)
):
    """
    生成测试任务的Excel报告
    
    该接口会根据任务ID生成一个包含所有测试用例结果的Excel报告。
    
    - **task_id**: 任务ID
    
    返回报告生成结果和报告文件路径
    """
    log.info(f"开始生成任务报告: {task_id}")
    
    try:
        # 检查任务是否存在
        task = get_test_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
        
        # 调用报告生成函数
        result = await run_report_generation(task_id)
        
        if result.get("status") == "error":
            error_msg = "; ".join(result.get("errors", ["未知错误"]))
            log.error(f"生成报告失败: {error_msg}")
            return ReportGenerationResponse(
                message="报告生成失败",
                task_id=task_id,
                success=False,
                error=error_msg
            )
        
        report_path = result.get("report_path")
        if not report_path:
            log.error("报告生成成功但未返回文件路径")
            return ReportGenerationResponse(
                message="报告生成成功但未返回文件路径",
                task_id=task_id,
                success=False,
                error="未获取到报告文件路径"
            )
        
        log.success(f"报告生成成功: {report_path}")
        return ReportGenerationResponse(
            message="报告生成成功",
            task_id=task_id,
            report_path=report_path,
            success=True
        )
        
    except Exception as e:
        log.error(f"生成报告时出错: {str(e)}")
        return ReportGenerationResponse(
            message="报告生成失败",
            task_id=task_id,
            success=False,
            error=str(e)
        )

@router.post("/tasks/{task_id}/release-docker", response_model=DockerReleaseResponse)
async def release_task_docker(
    task_id: str = Path(..., description="任务ID"),
    db: Session = Depends(get_db)
):
    """
    释放指定任务的Docker容器
    
    该接口会停止并删除与指定任务关联的Docker容器。
    
    - **task_id**: 任务ID
    
    返回容器释放结果
    """
    log.info(f"开始释放任务Docker容器: {task_id}")
    
    try:
        # 检查任务是否存在
        task = get_test_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
        
        # 检查是否有关联的Docker容器
        if not task.container_name:
            return DockerReleaseResponse(
                message="任务没有关联的Docker容器",
                success=True,
                task_id=task_id
            )
        
        # 调用释放函数
        result = await release_algorithm_container(task_id)
        
        if not result["success"]:
            error_msg = result.get("error", "未知错误")
            log.error(f"释放Docker容器失败: {error_msg}")
            return DockerReleaseResponse(
                message="Docker容器释放失败",
                success=False,
                task_id=task_id,
                container_name=task.container_name,
                error=error_msg
            )
        
        log.success(f"Docker容器释放成功: {task.container_name}")
        return DockerReleaseResponse(
            message="Docker容器释放成功",
            success=True,
            task_id=task_id,
            container_name=task.container_name
        )
        
    except Exception as e:
        log.error(f"释放Docker容器时出错: {str(e)}")
        return DockerReleaseResponse(
            message="Docker容器释放失败",
            success=False,
            task_id=task_id,
            error=str(e)
        )

# 7.1 获取测试用例的任务ID
@router.get("/testcases/{case_id}/task", response_model=Dict[str, str])
async def get_testcase_task(
    case_id: str = Path(..., description="测试用例ID"),
    db: Session = Depends(get_db)
):
    """
    获取测试用例所属的任务ID
    
    - **case_id**: 测试用例ID
    
    返回包含任务ID的字典
    """
    log.info(f"获取测试用例的任务ID: {case_id}")
    
    # 查询数据库
    case = db.query(DBTestCase).filter(DBTestCase.case_id == case_id).first()
    if not case:
        log.error(f"测试用例不存在: {case_id}")
        raise HTTPException(status_code=404, detail=f"测试用例不存在: {case_id}")
    
    log.info(f"查询到测试用例对应的任务ID: {case.task_id}")
    return {"task_id": case.task_id}

# 添加新的API端点
@router.post("/tasks/{task_id}/select-images", response_model=ImageSelectionResponse)
async def execute_select_images(
    task_id: str = Path(..., description="任务ID"),
    db: Session = Depends(get_db)
):
    """
    为任务中的测试用例自动选择合适的测试图片
    
    - **task_id**: 任务ID
    
    返回选择结果，包括成功状态、任务ID、更新的测试用例数量等信息
    """
    log.info(f"开始为任务 {task_id} 执行图片选择")
    
    try:
        # 检查任务是否存在
        task = get_test_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"未找到任务: {task_id}")
        
        # 检查任务是否有数据集URL
        if not task.dataset_url:
            raise HTTPException(status_code=400, detail=f"任务 {task_id} 未设置数据集URL")
        
        # 检查任务是否有测试用例
        with db as session:
            test_cases_count = session.query(DBTestCase).filter(DBTestCase.task_id == task_id).count()
            if test_cases_count == 0:
                raise HTTPException(status_code=400, detail=f"任务 {task_id} 没有测试用例")
        
        # 调用select_test_images函数
        result = await select_test_images(task_id)
        
        # 根据结果返回响应
        if result.get("success"):
            return {
                "success": True,
                "message": f"成功为任务 {task_id} 选择测试图片",
                "task_id": task_id,
                "updated_count": result.get("updated_count", 0),
                "image_examples": list(result.get("image_mapping", {}).items())[:5] if result.get("image_mapping") else []
            }
        else:
            # 如果执行失败，返回错误信息
            return {
                "success": False,
                "message": f"为任务 {task_id} 选择测试图片失败",
                "task_id": task_id,
                "errors": result.get("errors", ["未知错误"]),
                "status": result.get("status", "未知状态")
            }
        
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"执行图片选择时出错: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"执行图片选择时出错: {str(e)}")

# 添加新的响应模型
class DashboardStatsResponse(BaseModel):
    """仪表盘统计数据响应模型"""
    document_count: int = Field(..., description="已上传的需求文档数量")
    test_case_count: int = Field(..., description="自动生成的测试用例数量")
    task_stats: Dict[str, int] = Field(..., description="任务状态统计")
    waiting_tasks: int = Field(..., description="等待执行的任务数")
    completed_tasks: int = Field(..., description="成功执行的任务数")
    test_result_stats: Dict[str, Union[int, float]] = Field(..., description="测试结果统计")

@router.get("/dashboard/stats", response_model=DashboardStatsResponse)
async def get_dashboard_stats(
    db: Session = Depends(get_db)
):
    """
    获取仪表盘统计数据
    
    返回系统整体运行状态的统计数据，包括：
    - 文档数量
    - 测试用例数量
    - 任务状态分布
    - 等待/完成的任务数
    - 测试结果统计
    - 近7天的测试执行数据
    """
    log.info("获取仪表盘统计数据")
    
    try:
        # 1. 统计文档数量（通过已上传的PDF文件）
        pdf_dir = "data/pdfs"
        try:
            document_count = len([f for f in os.listdir(pdf_dir) if f.endswith('.pdf')])
        except:
            document_count = 0
            
        # 2. 统计测试用例总数
        test_case_count = db.query(DBTestCase).count()
        
        # 3. 统计任务状态分布
        task_stats = {}
        tasks = db.query(DBTestTask).all()
        for task in tasks:
            status = task.status or "unknown"
            task_stats[status] = task_stats.get(status, 0) + 1
            
        # 4. 统计等待和完成的任务
        waiting_tasks = db.query(DBTestTask).filter(
            DBTestTask.status.in_(["created", "pending"])
        ).count()
        
        completed_tasks = db.query(DBTestTask).filter(
            DBTestTask.status == "completed"
        ).count()
        
        # 5. 统计测试结果
        total_cases = test_case_count
        passed_cases = db.query(DBTestCase).filter(DBTestCase.is_passed == True).count()
        failed_cases = db.query(DBTestCase).filter(DBTestCase.is_passed == False).count()
        pending_cases = total_cases - passed_cases - failed_cases
        
        success_rate = (passed_cases / total_cases * 100) if total_cases > 0 else 0
        
        test_result_stats = {
            "total": total_cases,
            "passed": passed_cases,
            "failed": failed_cases,
            "pending": pending_cases,
            "success_rate": round(success_rate, 2)
        }

        # 6. 统计近7天的测试执行数据
        today = datetime.now().date()
        daily_stats = []
        
        for i in range(6, -1, -1):
            target_date = today - timedelta(days=i)
            next_date = target_date + timedelta(days=1)
            
            # 查询当天执行的测试用例数量
            daily_count = db.query(DBTestCase).filter(
                DBTestCase.created_at >= target_date,
                DBTestCase.created_at < next_date
            ).count()
            
            daily_stats.append({
                "date": target_date.strftime("%Y-%m-%d"),
                "count": daily_count
            })
        
        return {
            "document_count": document_count,
            "test_case_count": test_case_count,
            "task_stats": task_stats,
            "waiting_tasks": waiting_tasks,
            "completed_tasks": completed_tasks,
            "test_result_stats": test_result_stats,
            "daily_stats": daily_stats
        }
        
    except Exception as e:
        log.error(f"获取仪表盘统计数据失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"获取仪表盘统计数据失败: {str(e)}")

# 添加MCP状态检查函数
async def check_mcp_connection() -> bool:
    """
    检查MCP服务连接状态
    
    Returns:
        连接成功返回True，失败返回False
    """
    # 获取MCP配置
    mcp_config = get_mcp_config()
    host = mcp_config["host"]
    port = mcp_config["port"]
    sse_url = mcp_config["sse_url"]
    
    log.info(f"正在检查MCP服务连接状态: {sse_url}")
    
    try:
        # 尝试建立SSE连接
        connection_task = asyncio.create_task(
            asyncio.wait_for(
                establish_sse_connection(sse_url),
                timeout=5.0  # 设置5秒超时
            )
        )
        
        # 等待连接结果
        result = await connection_task
        return result
    except asyncio.TimeoutError:
        log.error("连接MCP服务超时")
        return False
    except Exception as e:
        log.error(f"连接MCP服务异常: {str(e)}")
        return False

async def establish_sse_connection(sse_url: str) -> bool:
    """
    尝试建立SSE连接
    
    Args:
        sse_url: SSE连接URL
        
    Returns:
        连接成功返回True，失败返回False
    """
    try:
        # 建立SSE连接
        async with sse_client(sse_url) as (read, write):
            # 创建客户端会话
            async with ClientSession(read, write) as session:
                # 初始化连接
                await session.initialize()
                # 检查可用工具
                tools = await session.list_tools()
                log.info(f"MCP服务连接成功，可用工具数量: {len(tools.tools) if hasattr(tools, 'tools') else 0}")
                return True
    except Exception as e:
        log.error(f"尝试建立SSE连接时出错: {str(e)}")
        return False

# MCP状态检查API路由
@router.get("/mcp/status", response_model=Dict[str, Any])
async def mcp_status():
    """
    检查MCP服务状态
    
    返回MCP服务状态信息
    """
    try:
        # 检查MCP连接状态
        status = await check_mcp_connection()
        return {
            "status": "running" if status else "error",
            "message": "服务正常运行" if status else "服务连接异常",
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        log.error(f"检查MCP服务状态时出错: {str(e)}")
        return {
            "status": "error",
            "message": f"检查服务状态时出错: {str(e)}",
            "timestamp": datetime.now().isoformat()
        }

# 仪表盘统计数据API
@router.get("/dashboard/stats", response_model=Dict[str, Any])
async def dashboard_stats(
    db: Session = Depends(get_db)
):
    """
    获取仪表盘统计数据
    
    返回任务和测试用例的统计信息
    """
    try:
        # 获取总任务数
        total_tasks = db.query(DBTestTask).count()
        
        # 获取总用例数
        total_cases = db.query(DBTestCase).count()
        
        # 计算成功率
        passed_cases = db.query(DBTestCase).filter(DBTestCase.is_passed == True).count()
        success_rate = round((passed_cases / total_cases * 100)) if total_cases > 0 else 0
        
        # 获取今日执行数
        today = datetime.now().date()
        today_start = datetime.combine(today, datetime.min.time())
        today_end = datetime.combine(today, datetime.max.time())
        
        today_executions = db.query(DBTestCase).filter(
            DBTestCase.updated_at >= today_start,
            DBTestCase.updated_at <= today_end,
            DBTestCase.status == 'completed'
        ).count()
        
        return {
            "total_tasks": total_tasks,
            "total_cases": total_cases,
            "success_rate": success_rate,
            "today_executions": today_executions
        }
    except Exception as e:
        log.error(f"获取仪表盘统计数据失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"获取仪表盘统计数据失败: {str(e)}")

# 仪表盘趋势数据API
@router.get("/dashboard/trend", response_model=Dict[str, Any])
async def dashboard_trend(
    days: int = Query(7, description="查询近几天的数据"),
    db: Session = Depends(get_db)
):
    """
    获取执行趋势数据
    
    - **days**: 查询近几天的数据，默认为7天
    
    返回近几天的执行数据趋势
    """
    try:
        # 获取当前日期并计算时间范围
        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=days-1)
        
        # 准备日期标签和数据容器
        date_labels = []
        executed_counts = []
        passed_counts = []
        
        # 按日期查询数据
        current_date = start_date
        while current_date <= end_date:
            date_str = current_date.strftime("%m-%d")
            date_labels.append(date_str)
            
            day_start = datetime.combine(current_date, datetime.min.time())
            day_end = datetime.combine(current_date, datetime.max.time())
            
            # 获取当天执行的测试用例数
            executed_count = db.query(DBTestCase).filter(
                DBTestCase.created_at >= day_start,
                DBTestCase.created_at <= day_end,
                DBTestCase.status == 'completed'
            ).count()
            
            # 获取当天通过的测试用例数
            passed_count = db.query(DBTestCase).filter(
                DBTestCase.created_at >= day_start,
                DBTestCase.created_at <= day_end,
                DBTestCase.status == 'completed',
                DBTestCase.is_passed == True
            ).count()
            
            executed_counts.append(executed_count)
            passed_counts.append(passed_count)
            
            current_date += timedelta(days=1)
        
        return {
            "dates": date_labels,
            "executed": executed_counts,
            "passed": passed_counts
        }
    except Exception as e:
        log.error(f"获取仪表盘趋势数据失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"获取仪表盘趋势数据失败: {str(e)}")

# 仪表盘分布数据API
@router.get("/dashboard/distribution", response_model=Dict[str, Any])
async def dashboard_distribution(
    db: Session = Depends(get_db)
):
    """
    获取测试用例状态分布数据
    
    返回测试用例的状态分布统计
    """
    try:
        # 获取通过的测试用例数
        passed = db.query(DBTestCase).filter(
            DBTestCase.status == 'completed',
            DBTestCase.is_passed == True
        ).count()
        
        # 获取失败的测试用例数
        failed = db.query(DBTestCase).filter(
            DBTestCase.status == 'completed',
            DBTestCase.is_passed == False
        ).count()
        
        # 获取待执行的测试用例数
        pending = db.query(DBTestCase).filter(
            (DBTestCase.status == 'pending') | 
            (DBTestCase.status == 'executing') |
            (DBTestCase.status == None)
        ).count()
        
        return {
            "passed": passed,
            "failed": failed,
            "pending": pending,
            "status": "success"
        }
    except Exception as e:
        log.error(f"获取仪表盘分布数据失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"获取仪表盘分布数据失败: {str(e)}")

# 添加一个执行用例前的容器检查和准备函数
async def ensure_container_ready(task_id: str) -> Dict[str, Any]:
    """
    确保测试任务的容器已经准备好
    
    如果容器不存在或未运行，则尝试创建新容器
    
    Args:
        task_id: 测试任务ID
        
    Returns:
        容器准备结果，包含success标志
    """
    log.info(f"检查任务 {task_id} 的容器是否准备就绪")
    
    try:
        # 从数据库获取任务信息
        task = get_test_task(task_id)
        if not task:
            log.error(f"任务不存在: {task_id}")
            return {"success": False, "error": f"任务不存在: {task_id}"}
        
        container_name = task.container_name
        
        # 如果数据库中没有容器名称记录，直接创建新容器
        if not container_name:
            log.info(f"数据库中没有容器名称记录，创建新容器")
            result = await setup_algorithm_container(task_id)
            return result
        
        # 如果有容器名称记录，检查容器是否存在且运行中
        # 构建检查脚本
        script = f"""
container_status=$(docker inspect -f '{{{{.State.Running}}}}' {container_name} 2>/dev/null || echo "no_such_container")
if [ "$container_status" = "no_such_container" ]; then
    echo "容器不存在: {container_name}"
    exit 1
elif [ "$container_status" != "true" ]; then
    echo "容器存在但未运行: {container_name}"
    exit 2
else
    echo "容器运行中: {container_name}"
    exit 0
fi
"""
        
        log.info(f"检查容器状态: {container_name}")
        
        try:
            # 从配置获取SSE URL
            mcp_config = get_mcp_config()
            sse_url = mcp_config["sse_url"]
            
            # 连接到MCP服务器并执行检查脚本
            try:
                async with sse_client(sse_url) as (read, write):
                    try:
                        async with ClientSession(read, write) as session:
                            # 初始化连接
                            await session.initialize()
                            
                            # 执行检查脚本
                            result = await session.call_tool("execute_script", {"script": script})
                            
                            # 检查脚本退出码
                            exit_code = -1
                            if hasattr(result, 'exit_code'):
                                exit_code = result.exit_code
                            
                            # 退出码: 0=容器运行中, 1=容器不存在, 2=容器未运行
                            if exit_code == 0:
                                log.info(f"容器已运行: {container_name}")
                                return {"success": True, "container_name": container_name}
                            
                            log.warning(f"容器状态异常，需要重新创建: exit_code={exit_code}")
                            
                            # 尝试强制释放旧容器（如果存在）
                            release_script = f"""
docker stop {container_name} 2>/dev/null || true
docker rm -f {container_name} 2>/dev/null || true
echo "容器已释放"
"""
                            await session.call_tool("execute_script", {"script": release_script})
                    except Exception as session_error:
                        log.error(f"MCP会话错误: {str(session_error)}")
                        raise Exception(f"MCP会话错误: {str(session_error)}")
            except Exception as sse_error:
                log.error(f"SSE客户端连接错误: {str(sse_error)}")
                raise Exception(f"SSE客户端连接错误: {str(sse_error)}")
                
        except Exception as mcp_error:
            log.error(f"MCP操作失败: {str(mcp_error)}")
            return {"success": False, "error": f"MCP操作失败: {str(mcp_error)}"}
        
        # 创建新容器
        log.info(f"创建新容器")
        return await setup_algorithm_container(task_id)
        
    except Exception as e:
        log.error(f"检查容器状态时出错: {str(e)}")
        return {"success": False, "error": f"检查容器状态时出错: {str(e)}"}


@router.post("/tasks/{task_id}/execute-case/{case_id}", response_model=TestExecutionResponse)
async def execute_test_case(
    task_id: str = Path(..., description="任务ID"),
    case_id: str = Path(..., description="测试用例ID"),
    db: Session = Depends(get_db)
):
    """
    执行单个测试用例
    
    - **task_id**: 任务ID
    - **case_id**: 测试用例ID
    
    返回测试执行结果
    """
    log.info(f"执行单个测试用例: 任务ID={task_id}, 用例ID={case_id}")
    
    try:
        # 记录开始时间
        start_time = time.time()
        
        # 检查测试用例是否存在
        case = db.query(DBTestCase).filter(DBTestCase.case_id == case_id).first()
        if not case:
            raise HTTPException(status_code=404, detail=f"测试用例不存在: {case_id}")
        
        # 检查测试用例是否属于指定任务
        if case.task_id != task_id:
            raise HTTPException(status_code=400, detail=f"测试用例 {case_id} 不属于任务 {task_id}")
        
        # 检查测试数据是否已设置
        if not case.test_data:
            raise HTTPException(status_code=400, detail=f"测试用例 {case_id} 未设置测试数据")
        
        # 检查算法容器是否准备就绪
        container_result = await ensure_container_ready(task_id)
        if not container_result["success"]:
            raise HTTPException(
                status_code=500, 
                detail=f"容器准备失败: {container_result.get('error', '未知错误')}"
            )
            
        log.info(f"容器已准备就绪: {container_result.get('container_name')}")
        
        # 初始化状态
        state = {
            "task_id": task_id,
            "case_id": case_id,  # 指定测试用例ID
            "current_case_index": 0,
            "test_cases": [],
            "command_strategies": None, 
            "current_strategy_index": 0,
            "status": "created",
            "errors": [],
            "container_ready": True  # 假设容器已准备好
        }
        
        cases_passed = 0
        cases_failed = 0
        error_message = None
        
        # 加载指定的测试用例
        log.info(f"加载测试用例: {case_id}")
        load_result = load_test_cases(state)
        if not load_result or load_result.get("status") == "error":
            error_msg = f"加载测试用例失败: {load_result.get('errors', ['未知错误'])}"
            log.error(error_msg)
            return {
                "message": error_msg,
                "success": False,
                "task_id": task_id,
                "cases_total": 1,
                "cases_executed": 0,
                "cases_passed": 0,
                "cases_failed": 1,
                "execution_time": time.time() - start_time,
                "error": error_msg
            }
            
        test_cases = load_result.get('test_cases', [])
        if not test_cases:
            log.error(f"未找到测试用例: {case_id}")
            return {
                "message": f"未找到测试用例: {case_id}",
                "success": False,
                "task_id": task_id,
                "cases_total": 1,
                "cases_executed": 0,
                "cases_passed": 0,
                "cases_failed": 1,
                "execution_time": time.time() - start_time,
                "error": f"未找到测试用例: {case_id}"
            }
            
        log.info(f"成功加载测试用例: {case_id}")
        # 使用字典合并更新state，保留原始字段
        state = {
            **state,
            **load_result
        }
        
        try:
            # 解析命令
            log.info(f"解析测试用例命令: {case_id}")
            parse_result = await parse_command(state)
            if not parse_result or parse_result.get("status") != "parsed":
                error_msg = f"命令解析失败: {case_id}"
                log.error(error_msg)
                cases_failed = 1
                error_message = error_msg
            else:
                state = parse_result
                
                # 执行命令
                log.info(f"执行测试用例命令: {case_id}")
                execute_result = await execute_command(state)
                if not execute_result or execute_result.get("status") != "executed":
                    error_msg = f"命令执行失败: {case_id}"
                    log.error(error_msg)
                    cases_failed = 1
                    error_message = error_msg
                else:
                    state = execute_result
                    
                    # 获取执行结果
                    execution_result = state.get('execution_result', {})
                    success = execution_result.get('success', False)
                    
                    if success:
                        cases_passed = 1
                    else:
                        cases_failed = 1
                        error_message = execution_result.get('error', '未知错误')
                    
                    # 保存结果
                    log.info(f"保存测试用例结果: {case_id}")
                    save_result_state = await save_result(state)
                    if not save_result_state:
                        error_msg = f"保存结果失败: {case_id}"
                        log.error(error_msg)
                        if not error_message:
                            error_message = error_msg
                        
                        # 发送保存结果失败消息
                        await manager.send_json(case_id, {
                            "event": "saving_failed",
                            "message": error_msg,
                            "case_id": case_id,
                            "timestamp": datetime.now().isoformat(),
                            "status": "executing"
                        })
                    else:
                        # 发送保存结果成功消息
                        await manager.send_json(case_id, {
                            "event": "saving_succeeded",
                            "message": "测试结果已保存",
                            "case_id": case_id,
                            "timestamp": datetime.now().isoformat(),
                            "status": "executing"
                        })
        except Exception as e:
            log.error(f"处理测试用例 {case_id} 时出错: {str(e)}")
            cases_failed = 1
            error_message = str(e)
            
            # 发送执行异常消息
            await manager.send_json(case_id, {
                "event": "execution_exception",
                "message": f"执行过程中发生异常: {str(e)}",
                "case_id": case_id,
                "timestamp": datetime.now().isoformat(),
                "status": "failed"
            })
        
        # 计算总执行时间
        execution_time = time.time() - start_time
        
        # 组装最终响应
        cases_executed = cases_passed + cases_failed
        message = f"测试用例 {case_id} 执行{'成功' if cases_passed == 1 else '失败'}"
        log.info(message)
        
        return {
            "message": message,
            "success": cases_passed == 1,
            "task_id": task_id,
            "cases_total": 1,
            "cases_executed": cases_executed,
            "cases_passed": cases_passed,
            "cases_failed": cases_failed,
            "execution_time": execution_time,
            "error": error_message
        }
    except Exception as e:
        log.error(f"执行测试用例时出错: {str(e)}")
        import traceback
        log.error(traceback.format_exc())
        
        # 确保start_time已定义
        if 'start_time' not in locals():
            start_time = time.time()
        
        return {
            "message": f"执行测试用例时出错: {str(e)}",
            "success": False,
            "task_id": task_id if 'task_id' in locals() else "unknown",
            "cases_total": 1,
            "cases_executed": 0,
            "cases_passed": 0,
            "cases_failed": 1,
            "execution_time": time.time() - start_time,
            "error": str(e)
        }

# 添加删除任务的API接口
@router.delete("/tasks/{task_id}", response_model=MessageResponse)
async def delete_task(
    task_id: str = Path(..., description="任务ID"),
    db: Session = Depends(get_db)
):
    """
    删除指定任务及其相关的所有测试用例
    
    在删除任务前，会先尝试释放任务关联的Docker容器。
    
    - **task_id**: 任务ID
    
    返回操作结果
    """
    log.info(f"开始删除任务: {task_id}")
    
    try:
        # 1. 检查任务是否存在
        task = get_test_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
        
        # 2. 如果任务有关联的Docker容器，先尝试释放容器
        if task.container_name:
            log.info(f"任务 {task_id} 有关联的Docker容器，尝试释放: {task.container_name}")
            try:
                result = await release_algorithm_container(task_id)
                if not result["success"]:
                    log.warning(f"释放Docker容器失败: {result.get('error', '未知错误')}")
                    # 继续执行删除，不因为容器释放失败而中断整个删除流程
                else:
                    log.success(f"Docker容器释放成功: {task.container_name}")
            except Exception as e:
                log.error(f"释放Docker容器时出错: {str(e)}")
                # 继续执行删除，不因为容器释放失败而中断整个删除流程
        
        # 3. 删除与任务关联的所有测试用例
        test_cases = db.query(DBTestCase).filter(DBTestCase.task_id == task_id).all()
        test_cases_count = len(test_cases)
        
        if test_cases_count > 0:
            log.info(f"删除任务 {task_id} 关联的 {test_cases_count} 个测试用例")
            for case in test_cases:
                db.delete(case)
        
        # 4. 删除任务记录
        db.query(DBTestTask).filter(DBTestTask.task_id == task_id).delete()
        
        # 5. 提交事务
        db.commit()
        
        log.success(f"任务 {task_id} 删除成功，共删除 {test_cases_count} 个测试用例")
        
        return {
            "message": f"任务 {task_id} 删除成功，共删除 {test_cases_count} 个测试用例",
            "success": True
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        log.error(f"删除任务 {task_id} 时出错: {str(e)}")
        raise HTTPException(status_code=500, detail=f"删除任务时出错: {str(e)}")

@router.get("/tasks/{task_id}/report", response_model=Dict[str, Any])
async def get_task_report(
    task_id: str = Path(..., description="任务ID"),
    db: Session = Depends(get_db)
):
    """
    获取任务的测试报告数据
    
    该接口返回指定任务的测试报告数据，包括任务基本信息和测试用例执行结果。
    
    - **task_id**: 任务ID
    
    返回报告数据，包括任务信息和测试用例结果
    """
    log.info(f"获取任务报告数据: {task_id}")
    
    try:
        # 检查任务是否存在
        task = get_test_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
        
        # 获取任务基本信息
        task_info = {
            "task_id": task.task_id,
            "status": task.status,
            "algorithm_image": task.algorithm_image,
            "dataset_url": task.dataset_url,
            "container_name": task.container_name,
            "created_at": task.created_at,
            "updated_at": task.updated_at
        }
        
        # 添加可选字段（如果存在）
        if hasattr(task, 'start_time'):
            task_info["start_time"] = task.start_time
        if hasattr(task, 'end_time'):
            task_info["end_time"] = task.end_time
        if hasattr(task, 'document_id'):
            task_info["document_id"] = task.document_id
        
        # 计算任务执行总时长（秒）
        duration = None
        if hasattr(task, 'start_time') and hasattr(task, 'end_time') and task.start_time and task.end_time:
            start = datetime.fromisoformat(task.start_time.replace('Z', '+00:00'))
            end = datetime.fromisoformat(task.end_time.replace('Z', '+00:00'))
            duration = (end - start).total_seconds()
        
        # 查询任务下的所有测试用例及其结果
        with db as session:
            query = session.query(DBTestCase).filter(DBTestCase.task_id == task_id)
            cases = query.all()
            
            # 转换测试用例列表为字典列表
            test_cases = []
            for case in cases:
                # 解析result字段（如果是JSON字符串）
                result_data = {}
                
                # 直接跳过不存在的result字段检查
                
                # 确定用例是否通过
                is_passed = False
                if hasattr(case, 'is_passed') and case.is_passed:
                    is_passed = case.is_passed
                elif hasattr(case, 'status') and case.status == "completed":
                    is_passed = True
                elif hasattr(case, 'status') and case.status == "failed":
                    is_passed = False
                
                # 获取执行时间
                execution_time = None
                if hasattr(case, 'execution_time') and case.execution_time:
                    execution_time = case.execution_time
                
                # 提取执行日志
                execution_log = ""
                if hasattr(case, 'actual_output') and case.actual_output:
                    execution_log = case.actual_output
                
                # 构建基本测试用例信息
                case_info = {
                    "case_id": case.case_id,
                    "status": case.status if hasattr(case, 'status') else "unknown",
                    "is_passed": is_passed,
                    "result_data": result_data,
                    "result_analysis": case.result_analysis if hasattr(case, 'result_analysis') else None,
                    "execution_log": execution_log,
                    "duration": execution_time  # 以毫秒为单位的执行时间
                }
                
                # 添加可选字段（如果存在）
                if hasattr(case, 'name'):
                    case_info["name"] = case.name
                else:
                    # 尝试从input_data中提取名称
                    case_info["name"] = case.input_data.get("name", f"测试用例 {case.case_id}") if hasattr(case, 'input_data') and case.input_data else f"测试用例 {case.case_id}"
                
                if hasattr(case, 'purpose'):
                    case_info["purpose"] = case.purpose
                elif hasattr(case, 'input_data') and case.input_data and "purpose" in case.input_data:
                    case_info["purpose"] = case.input_data["purpose"]
                else:
                    case_info["purpose"] = "未指定目的"
                
                if hasattr(case, 'test_data'):
                    case_info["test_data"] = case.test_data
                
                test_cases.append(case_info)
        
        # 统计测试结果
        total_cases = len(test_cases)
        passed_cases = sum(1 for case in test_cases if case["is_passed"])
        failed_cases = total_cases - passed_cases
        pass_rate = (passed_cases / total_cases * 100) if total_cases > 0 else 0
        
        # 生成简单的分析报告
        analysis = {
            "conclusion": f"总计{total_cases}个测试用例，通过{passed_cases}个，失败{failed_cases}个，通过率{pass_rate:.2f}%。",
            "failed_cases": [
                {
                    "case_id": case["case_id"],
                    "name": case.get("name", f"测试用例 {case['case_id']}"),
                    "reason": case.get("result_analysis", "未提供失败原因")
                }
                for case in test_cases if not case["is_passed"]
            ],
            "recommendations": []
        }
        
        # 根据失败情况提供简单建议
        if failed_cases > 0:
            analysis["recommendations"] = ["检查算法实现是否符合接口规范", "查看失败用例的输入数据格式是否正确"]
        
        # 返回完整报告数据
        report_data = {
            **task_info,
            "duration": duration,
            "test_cases": test_cases,
            "statistics": {
                "total_cases": total_cases,
                "passed_cases": passed_cases,
                "failed_cases": failed_cases,
                "pass_rate": pass_rate
            },
            "analysis": analysis
        }
        
        log.info(f"成功获取任务报告数据: {task_id}, 包含{total_cases}个测试用例")
        return report_data
        
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"获取任务报告数据失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"获取任务报告数据失败: {str(e)}")

# 释放Docker容器
@router.post("/tasks/{task_id}/release-docker", response_model=DockerReleaseResponse)
async def release_task_docker(
    task_id: str = Path(..., description="任务ID"),
    db: Session = Depends(get_db)
):
    """
    释放任务的Docker容器
    
    该接口会释放指定任务的Docker容器，清理相关资源。
    
    - **task_id**: 任务ID
    
    返回释放结果
    """
    log.info(f"开始释放任务Docker容器: {task_id}")
    
    try:
        # 检查任务是否存在
        task = get_test_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"测试任务不存在: {task_id}")
        
        # 调用释放容器函数
        from agents.execution_agent import release_algorithm_container
        result = await release_algorithm_container(task_id)
        
        if result.get("success"):
            log.info(f"Docker容器释放成功: {task_id}")
            return {
                "message": "Docker容器已成功释放",
                "success": True,
                "task_id": task_id,
                "container_name": result.get("container_name"),
                "details": result.get("result", {})
            }
        else:
            error_message = result.get("error", "未知错误")
            log.error(f"Docker容器释放失败: {error_message}")
            raise HTTPException(
                status_code=500, 
                detail=f"Docker容器释放失败: {error_message}"
            )
    except HTTPException:
        # 直接重新抛出HTTP异常
        raise
    except Exception as e:
        log.error(f"释放Docker容器失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"释放Docker容器失败: {str(e)}")


# 创建Docker容器
@router.post("/tasks/{task_id}/setup-container", response_model=DockerSetupResponse)
async def setup_task_container(
    task_id: str = Path(..., description="任务ID")
):
    """
    为任务创建Docker容器
    
    该接口会为指定任务创建Docker容器，包括拉取镜像、创建容器等步骤。
    
    - **task_id**: 任务ID
    
    返回容器创建结果
    """
    log.info(f"开始为任务创建Docker容器: {task_id}")
    
    try:
        # 检查任务是否存在
        task = get_test_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"测试任务不存在: {task_id}")
        
        # 检查算法镜像是否已配置
        if not task.algorithm_image:
            raise HTTPException(status_code=400, detail="算法镜像未配置，请先配置算法镜像")
        
        # 调用容器设置函数
        from agents.execution_agent import setup_algorithm_container
        result = await setup_algorithm_container(task_id)
        
        if result.get("success"):
            log.info(f"Docker容器创建成功: {task_id}")
            return {
                "message": "Docker容器创建成功",
                "success": True,
                "task_id": task_id,
                "container_name": result.get("container_name"),
                "algorithm_image": result.get("algorithm_image"),
                "dataset_url": result.get("dataset_url"),
                "details": result.get("result", {})
            }
        else:
            error_message = result.get("error", "未知错误")
            log.error(f"Docker容器创建失败: {error_message}")
            return {
                "message": f"Docker容器创建失败: {error_message}",
                "success": False,
                "task_id": task_id,
                "error": error_message
            }
    except HTTPException:
        # 直接重新抛出HTTP异常
        raise
    except Exception as e:
        log.error(f"创建Docker容器失败: {str(e)}")
        return {
            "message": f"创建Docker容器失败: {str(e)}",
            "success": False,
            "task_id": task_id,
            "error": str(e)
        }


# 检查容器状态
@router.get("/tasks/{task_id}/container-status", response_model=Dict[str, Any])
async def check_container_status(
    task_id: str = Path(..., description="任务ID")
):
    """
    检查任务的Docker容器状态
    
    该接口会检查指定任务的Docker容器运行状态。
    
    - **task_id**: 任务ID
    
    返回容器状态信息
    """
    log.info(f"检查任务Docker容器状态: {task_id}")
    
    try:
        # 检查任务是否存在
        task = get_test_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"测试任务不存在: {task_id}")
        
        # 检查是否有容器名称
        if not task.container_name:
            return {
                "success": False,
                "task_id": task_id,
                "error": "任务未关联Docker容器"
            }
        
        # 这里可以添加实际的容器状态检查逻辑
        # 目前返回基本信息
        return {
            "success": True,
            "task_id": task_id,
            "container_name": task.container_name,
            "status": "running",  # 这里应该是实际的容器状态
            "message": f"容器 {task.container_name} 运行正常"
        }
        
    except HTTPException:
        # 直接重新抛出HTTP异常
        raise
    except Exception as e:
        log.error(f"检查容器状态失败: {str(e)}")
        return {
            "success": False,
            "task_id": task_id,
            "error": str(e)
        }


# 删除容器
@router.delete("/tasks/{task_id}/remove-container", response_model=DockerReleaseResponse)
async def remove_task_container(
    task_id: str = Path(..., description="任务ID")
):
    """
    删除任务的Docker容器
    
    该接口会删除指定任务的Docker容器，清理相关资源。
    这是release_task_docker的别名接口。
    
    - **task_id**: 任务ID
    
    返回删除结果
    """
    # 直接调用释放容器接口
    return await release_task_docker(task_id)
