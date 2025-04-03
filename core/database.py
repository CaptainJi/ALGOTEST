#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
文件作用：数据库模型和操作模块，定义数据库表结构和提供数据库操作接口
开发规划：使用SQLAlchemy ORM实现数据库操作，支持测试任务、测试用例、测试结果和测试报告的存储和查询
"""

from datetime import datetime
from typing import List, Optional, Dict, Any
from contextlib import contextmanager

from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Boolean, JSON, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker, Session

from core.config import get_settings
from core.logger import get_logger

# 获取日志记录器
logger = get_logger("database")

# 创建基类
Base = declarative_base()

# 创建数据库引擎
settings = get_settings()
engine = create_engine(settings.db_url)

# 创建会话工厂
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

class TestTask(Base):
    """测试任务模型"""
    __tablename__ = "test_tasks"
    
    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(String(50), unique=True, index=True)
    document_id = Column(String(50), index=True, nullable=True)  # 添加文档ID字段
    requirement_doc = Column(Text)
    algorithm_image = Column(String(255))
    dataset_url = Column(String(255), nullable=True)  # 数据集URL
    container_name = Column(String(255), nullable=True)  # 容器名称
    document_hash = Column(String(32), nullable=True)  # 文档MD5哈希值，用于检测重复文档
    status = Column(String(20), default="pending")  # pending, running, completed, failed
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    
    # 关系
    test_cases = relationship("TestCase", back_populates="task", cascade="all, delete-orphan")

class TestCase(Base):
    """测试用例模型"""
    __tablename__ = "test_cases"
    
    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(String(50), ForeignKey("test_tasks.task_id", ondelete="CASCADE"))
    case_id = Column(String(50), unique=True, index=True)
    document_id = Column(String(50), index=True)
    input_data = Column(JSON)
    expected_output = Column(JSON, nullable=True)
    test_data = Column(String(500), nullable=True)  # 添加test_data字段，用于存储测试图片路径
    
    # 从TestResult表移过来的字段
    actual_output = Column(Text, nullable=True)  # 实际输出结果
    result_analysis = Column(Text, nullable=True) # 结果分析
    is_passed = Column(Boolean, default=False)   # 是否通过测试
    status = Column(String(20), default="pending")  # pending, running, completed, failed
    
    created_at = Column(DateTime, default=datetime.now)
    
    # 关系
    task = relationship("TestTask", back_populates="test_cases")

def init_db():
    """初始化数据库，创建所有表（警告：会删除所有现有数据）"""
    logger.info("初始化数据库...")
    # 删除所有现有的表
    Base.metadata.drop_all(bind=engine)
    # 创建所有表
    Base.metadata.create_all(bind=engine)
    logger.info("数据库初始化完成")

def ensure_db():
    """确保数据库表存在，但不删除现有数据"""
    logger.info("检查数据库表结构...")
    # 只创建不存在的表，不会删除或修改现有的表
    Base.metadata.create_all(bind=engine)
    logger.info("数据库表结构检查完成")

def get_db() -> Session:
    """获取数据库会话"""
    db = SessionLocal()
    try:
        return db
    except Exception as e:
        logger.error(f"获取数据库会话失败: {str(e)}")
        db.close()
        raise

# 数据库操作函数
def create_test_task(task_data: Dict[str, Any], db: Session = None) -> TestTask:
    """
    创建测试任务
    
    Args:
        task_data: 测试任务数据
        db: 数据库会话，如果不提供则创建新会话
        
    Returns:
        TestTask: 创建的测试任务对象
    """
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True
    try:
        task = TestTask(**task_data)
        db.add(task)
        db.commit()
        db.refresh(task)
        logger.info(f"创建测试任务: {task.task_id}")
        return task
    except Exception as e:
        db.rollback()
        logger.error(f"创建测试任务失败: {str(e)}")
        raise
    finally:
        if close_db:
            db.close()

def get_test_task(task_id: str) -> Optional[TestTask]:
    """
    获取测试任务
    
    Args:
        task_id: 测试任务ID
        
    Returns:
        TestTask: 测试任务对象，不存在返回None
    """
    db = SessionLocal()
    try:
        task = db.query(TestTask).filter(TestTask.task_id == task_id).first()
        if task:
            # 保持数据库会话，不要关闭
            setattr(task, "_session", db)  # 附加会话到对象，防止会话被回收
        return task
    except Exception as e:
        logger.error(f"获取测试任务失败: {str(e)}")
        db.close()
        return None

def get_test_task_by_document_id(document_id: str) -> Optional[TestTask]:
    """
    根据文档ID获取测试任务
    
    Args:
        document_id: 文档ID
        
    Returns:
        TestTask: 测试任务对象，不存在返回None
    """
    with get_db() as db:
        task = db.query(TestTask).filter(TestTask.document_id == document_id).first()
        return task

def update_test_task(task_id: str, update_data: Dict[str, Any]) -> Optional[TestTask]:
    """
    更新测试任务
    
    Args:
        task_id: 测试任务ID
        update_data: 更新数据
        
    Returns:
        TestTask: 更新后的测试任务对象，不存在返回None
    """
    with get_db() as db:
        task = db.query(TestTask).filter(TestTask.task_id == task_id).first()
        if task:
            for key, value in update_data.items():
                setattr(task, key, value)
            db.commit()
            db.refresh(task)
            logger.info(f"更新测试任务: {task_id}")
        return task

def create_test_case(case_data: Dict[str, Any], db: Session = None) -> TestCase:
    """
    创建测试用例
    
    Args:
        case_data: 测试用例数据
        db: 数据库会话，如果不提供则创建新会话
        
    Returns:
        TestCase: 创建的测试用例对象
    """
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True
    try:
        case = TestCase(**case_data)
        db.add(case)
        db.commit()
        db.refresh(case)
        logger.info(f"创建测试用例: {case.case_id}")
        return case
    except Exception as e:
        db.rollback()
        logger.error(f"创建测试用例失败: {str(e)}")
        raise
    finally:
        if close_db:
            db.close()

def get_all_test_tasks() -> List[Dict[str, Any]]:
    """
    获取所有测试任务
    
    Returns:
        List[Dict[str, Any]]: 所有测试任务数据列表
    """
    with get_db() as db:
        tasks = db.query(TestTask).all()
        
        # 转换为字典列表
        result = []
        for task in tasks:
            task_dict = {
                "id": task.id,
                "task_id": task.task_id,
                "document_id": task.document_id if task.document_id is not None else None,
                "requirement_doc": task.requirement_doc if task.requirement_doc is not None else None,
                "algorithm_image": task.algorithm_image if task.algorithm_image is not None else None,
                "dataset_url": task.dataset_url if task.dataset_url is not None else None,
                "container_name": task.container_name if task.container_name is not None else None,
                "status": task.status if task.status is not None else "unknown",
                "created_at": task.created_at.isoformat() if task.created_at else None,
                "updated_at": task.updated_at.isoformat() if task.updated_at else None,
                "test_cases_count": len(task.test_cases) if task.test_cases else 0
            }
            result.append(task_dict)
            
        logger.info(f"获取所有测试任务，共{len(result)}条记录")
        return result

def update_test_case_status(case_id: str, status: str, result: Any = None) -> Optional[TestCase]:
    """
    更新测试用例状态和结果
    
    Args:
        case_id: 测试用例ID
        status: 状态 (pending, running, completed, failed)
        result: 执行结果，可以是文本或字典
        
    Returns:
        TestCase: 更新后的测试用例对象，不存在返回None
    """
    with get_db() as db:
        case = db.query(TestCase).filter(TestCase.case_id == case_id).first()
        if not case:
            logger.warning(f"更新状态失败，测试用例不存在: {case_id}")
            return None
        
        # 更新状态
        case.status = status
        
        if status == "completed" or status == "failed":
            # 判断result的类型，处理文本或字典类型
            success = False
            
            if isinstance(result, dict):
                # 如果是字典类型，尝试提取成功标志
                success = result.get("success", False)
            elif isinstance(result, str):
                # 如果是字符串类型，作为原始输出保存
                # 通过简单启发式检测执行是否成功（输出中不包含常见错误关键词）
                error_keywords = ["错误", "Error", "Failed", "失败", "Exception", "异常"]
                success = not any(keyword in result for keyword in error_keywords)
            else:
                # 其他类型，尝试转换为字符串
                result = str(result) if result is not None else ""
            
            # 直接更新测试用例对象的字段
            if result is not None:
                case.actual_output = result
                case.is_passed = success
            
            logger.info(f"更新测试结果: {case_id}")
        
        db.commit()
        db.refresh(case)
        logger.info(f"更新测试用例状态: {case_id} -> {status}")
        return case

def update_test_task_status(task_id: str, status: str) -> Optional[TestTask]:
    """
    更新测试任务状态
    
    Args:
        task_id: 测试任务ID
        status: 状态 (pending, running, completed, failed)
        
    Returns:
        TestTask: 更新后的测试任务对象，不存在返回None
    """
    with get_db() as db:
        task = db.query(TestTask).filter(TestTask.task_id == task_id).first()
        if task:
            task.status = status
            db.commit()
            db.refresh(task)
            logger.info(f"更新测试任务状态: {task_id} -> {status}")
        else:
            logger.warning(f"更新状态失败，测试任务不存在: {task_id}")
        return task

# 在数据库操作完成后需要关闭会话的函数
def close_task_session(task: TestTask):
    """
    关闭与任务对象关联的数据库会话
    
    Args:
        task: 测试任务对象
    """
    if task and hasattr(task, "_session"):
        session = getattr(task, "_session")
        session.close()
        delattr(task, "_session")

# 添加新的函数用于更新任务的算法镜像
def update_task_algorithm_image(task_id: str, algorithm_image: str) -> Optional[TestTask]:
    """
    更新测试任务的算法镜像地址
    
    Args:
        task_id: 测试任务ID
        algorithm_image: 算法镜像地址
        
    Returns:
        TestTask: 更新后的测试任务对象，不存在返回None
    """
    return update_test_task(task_id, {"algorithm_image": algorithm_image})

# 添加新的函数用于更新任务的数据集URL
def update_task_dataset_url(task_id: str, dataset_url: str) -> Optional[TestTask]:
    """
    更新测试任务的数据集URL
    
    Args:
        task_id: 测试任务ID
        dataset_url: 数据集URL
        
    Returns:
        TestTask: 更新后的测试任务对象，不存在返回None
    """
    return update_test_task(task_id, {"dataset_url": dataset_url})

# 添加新的函数用于更新任务的容器名称
def update_task_container_name(task_id: str, container_name: str) -> Optional[TestTask]:
    """
    更新测试任务的容器名称
    
    Args:
        task_id: 测试任务ID
        container_name: 容器名称
        
    Returns:
        TestTask: 更新后的测试任务对象，不存在返回None
    """
    return update_test_task(task_id, {"container_name": container_name})
