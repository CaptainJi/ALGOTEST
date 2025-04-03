import os
from typing import Dict, Any, List
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from core.logger import get_logger
from core.database import get_test_task, Session, get_db
from core.database import TestCase as DBTestCase

# 创建路由器
router = APIRouter(prefix="/api/reports", tags=["reports"])

# 获取日志记录器
log = get_logger("reports")

def get_report_path(task_id: str, report_name: str) -> str:
    """获取报告文件路径"""
    reports_dir = "data/report"
    os.makedirs(reports_dir, exist_ok=True)
    return os.path.join(reports_dir, report_name)

@router.get("/download/{task_id}/{report_name}")
async def download_report(task_id: str, report_name: str):
    """
    下载指定任务的报告文件
    
    - **task_id**: 任务ID
    - **report_name**: 报告文件名
    
    返回报告文件
    """
    log.info(f"请求下载报告: {report_name}, 任务ID: {task_id}")
    
    try:
        # 检查任务是否存在
        task = get_test_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
        
        # 获取报告文件路径
        report_path = get_report_path(task_id, report_name)
        
        # 检查文件是否存在
        if not os.path.exists(report_path):
            raise HTTPException(status_code=404, detail=f"报告文件不存在: {report_name}")
        
        # 返回文件
        return FileResponse(
            path=report_path,
            filename=report_name,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"下载报告失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"下载报告失败: {str(e)}")

@router.get("/{task_id}/list", response_model=Dict[str, Any])
async def list_task_reports(task_id: str):
    """
    获取指定任务的所有报告文件列表
    
    - **task_id**: 任务ID
    
    返回报告文件列表
    """
    log.info(f"获取任务报告列表: {task_id}")
    
    try:
        # 检查任务是否存在
        task = get_test_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
        
        # 获取报告目录
        reports_dir = "data/reports"
        if not os.path.exists(reports_dir):
            return {
                "message": "暂无报告文件",
                "reports": []
            }
        
        # 获取该任务的所有报告文件
        reports = []
        for filename in os.listdir(reports_dir):
            if filename.startswith(f"test_report_{task_id}_"):
                file_path = os.path.join(reports_dir, filename)
                reports.append({
                    "filename": filename,
                    "size": os.path.getsize(file_path),
                    "created_at": os.path.getctime(file_path)
                })
        
        # 按创建时间倒序排序
        reports.sort(key=lambda x: x["created_at"], reverse=True)
        
        return {
            "message": f"找到{len(reports)}个报告文件",
            "reports": reports
        }
        
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"获取报告列表失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"获取报告列表失败: {str(e)}") 