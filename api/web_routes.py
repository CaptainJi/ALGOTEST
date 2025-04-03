#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
文件作用：前端页面路由处理，提供基于模板的网页界面
开发规划：实现基于Jinja2模板的前端页面渲染
"""

from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.templating import Jinja2Templates
from core.database import get_db, Session
from core.logger import get_logger
from typing import Optional
import os

# 创建路由器
router = APIRouter(tags=["web"])

# 获取日志记录器
log = get_logger("web")

# 配置模板
templates = Jinja2Templates(directory="templates")

# 首页
@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """渲染首页/仪表盘"""
    return templates.TemplateResponse(
        "index.html", 
        {"request": request, "title": "ALGOTEST - 大模型驱动的算法测试系统"}
    )

# 文档管理路由
@router.get("/documents", response_class=HTMLResponse)
async def documents_list(request: Request):
    """渲染文档列表页"""
    return templates.TemplateResponse(
        "documents/list.html", 
        {"request": request, "title": "文档管理"}
    )

@router.get("/documents/upload", response_class=HTMLResponse)
async def documents_upload(request: Request):
    """渲染文档上传页"""
    return templates.TemplateResponse(
        "documents/upload.html", 
        {"request": request, "title": "上传文档"}
    )

@router.get("/documents/{document_id}", response_class=HTMLResponse)
async def document_detail(request: Request, document_id: str):
    """渲染文档详情页"""
    return templates.TemplateResponse(
        "documents/detail.html", 
        {
            "request": request, 
            "title": "文档详情", 
            "document_id": document_id
        }
    )

# 测试用例管理路由
@router.get("/testcases", response_class=HTMLResponse)
async def testcases_list(request: Request, document_id: Optional[str] = None):
    """渲染测试用例列表页"""
    return templates.TemplateResponse(
        "testcases/list.html", 
        {
            "request": request, 
            "title": "测试用例管理",
            "document_id": document_id
        }
    )

@router.get("/testcases/create", response_class=HTMLResponse)
async def testcase_create(request: Request, document_id: Optional[str] = None):
    """渲染测试用例创建页"""
    return templates.TemplateResponse(
        "testcases/edit.html", 
        {
            "request": request, 
            "title": "创建测试用例",
            "document_id": document_id,
            "is_new": True
        }
    )

@router.get("/testcases/{case_id}", response_class=HTMLResponse)
async def testcase_detail(request: Request, case_id: str):
    """渲染测试用例详情页"""
    return templates.TemplateResponse(
        "testcases/detail.html", 
        {
            "request": request, 
            "title": "测试用例详情",
            "case_id": case_id
        }
    )

@router.get("/testcases/{case_id}/edit", response_class=HTMLResponse)
async def testcase_edit(request: Request, case_id: str):
    """渲染测试用例编辑页"""
    return templates.TemplateResponse(
        "testcases/edit.html", 
        {
            "request": request, 
            "title": "编辑测试用例",
            "case_id": case_id,
            "is_new": False
        }
    )

@router.get("/testcases/batch-data", response_class=HTMLResponse)
async def testcases_batch_data(request: Request):
    """渲染批量设置测试数据页面"""
    return templates.TemplateResponse(
        "testcases/batch_data.html", 
        {"request": request, "title": "批量设置测试数据"}
    )

# 任务管理路由
@router.get("/tasks", response_class=HTMLResponse)
async def tasks_list(request: Request):
    """渲染任务列表页"""
    return templates.TemplateResponse(
        "tasks/list.html", 
        {"request": request, "title": "任务管理"}
    )

@router.get("/tasks/{task_id}", response_class=HTMLResponse)
async def task_detail(request: Request, task_id: str):
    """渲染任务详情页"""
    return templates.TemplateResponse(
        "tasks/detail.html", 
        {
            "request": request, 
            "title": "任务详情",
            "task_id": task_id
        }
    )

@router.get("/tasks/{task_id}/configure", response_class=HTMLResponse)
async def task_configure(request: Request, task_id: str):
    """渲染任务配置页"""
    return templates.TemplateResponse(
        "tasks/configure.html", 
        {
            "request": request, 
            "title": "任务配置",
            "task_id": task_id
        }
    )

# 执行管理路由
@router.get("/execution/{task_id}", response_class=HTMLResponse)
async def execution_console(request: Request, task_id: str):
    """渲染执行控制台页"""
    return templates.TemplateResponse(
        "execution/console.html", 
        {
            "request": request, 
            "title": "执行控制台",
            "task_id": task_id
        }
    )

@router.get("/execution/{task_id}/monitor", response_class=HTMLResponse)
async def execution_monitor(request: Request, task_id: str):
    """渲染执行监控页"""
    return templates.TemplateResponse(
        "execution/monitor.html", 
        {
            "request": request, 
            "title": "执行监控",
            "task_id": task_id
        }
    )

# 报告管理路由
@router.get("/reports", response_class=HTMLResponse)
async def reports_summary(request: Request):
    """渲染报告汇总页"""
    return templates.TemplateResponse(
        "reports/summary.html", 
        {"request": request, "title": "测试报告"}
    )

@router.get("/reports/{task_id}", response_class=HTMLResponse)
async def report_detail(request: Request, task_id: str):
    """渲染报告详情页"""
    return templates.TemplateResponse(
        "reports/detail.html", 
        {
            "request": request, 
            "title": "报告详情",
            "task_id": task_id
        }
    )

@router.get("/download/data/report/{filename:path}", response_class=FileResponse)
async def download_report(filename: str):
    """下载报告文件"""
    file_path = os.path.join("data", "report", filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="报告文件不存在")
    return FileResponse(
        path=file_path,
        filename=filename,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    ) 