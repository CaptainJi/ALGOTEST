#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
文件作用：配置管理模块，负责加载和管理应用配置
开发规划：使用pydantic-settings从环境变量和.env文件加载配置，提供统一的配置访问接口
"""

from typing import Optional, Dict, Any, List
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    """
    应用配置类，从环境变量和.env文件加载配置
    """
    # API服务配置
    api_host: str = Field(default="0.0.0.0", validation_alias="API_HOST")
    api_port: int = Field(default=8000, validation_alias="API_PORT")
    api_workers: int = Field(default=4, validation_alias="API_WORKERS")
    api_timeout: int = Field(default=60, validation_alias="API_TIMEOUT")
    api_reload: bool = Field(default=True, validation_alias="API_RELOAD")
    
    # 数据库配置
    db_url: str = Field(default="sqlite:///algotest.db", validation_alias="DB_URL")
    
    # 日志配置
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")
    log_rotation: str = Field(default="500 MB", validation_alias="LOG_ROTATION")
    log_retention: str = Field(default="10 days", validation_alias="LOG_RETENTION")
    log_file: str = "logs/algotest_{time}.log"
    
    # Docker配置
    docker_registry: Optional[str] = Field(default=None, validation_alias="DOCKER_REGISTRY")
    docker_username: Optional[str] = Field(default=None, validation_alias="DOCKER_USERNAME")
    docker_password: Optional[str] = Field(default=None, validation_alias="DOCKER_PASSWORD")
    docker_timeout: int = Field(default=300, validation_alias="DOCKER_TIMEOUT")  # Docker容器执行超时时间(秒)
    
    # 智谱AI配置
    zhipu_api_key: str = Field(default="", validation_alias="ZHIPU_API_KEY")
    zhipu_model_chat: str = Field(default="glm-4-flash", validation_alias="ZHIPU_MODEL_CHAT")
    zhipu_model_vision: str = Field(default="glm-4v-flash", validation_alias="ZHIPU_MODEL_VISION")
    zhipu_temperature: float = 0.7
    zhipu_max_tokens: int = Field(default=6000, validation_alias="ZHIPU_MAX_TOKENS")
    
    # 大模型重试配置
    llm_retry_count: int = Field(default=3, validation_alias="ZHIPU_RETRY_COUNT")
    llm_retry_delay: int = Field(default=5, validation_alias="ZHIPU_RETRY_DELAY")
    llm_retry_backoff: float = Field(default=2.0, validation_alias="ZHIPU_RETRY_BACKOFF")
    llm_timeout: int = Field(default=60, validation_alias="ZHIPU_TIMEOUT")  # API调用超时时间(秒)
    
    # 报告配置
    report_template_path: str = "templates/report_template.md"
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )
    
    def get_llm_config(self) -> Dict[str, Any]:
        """
        获取智谱AI大模型配置
        
        Returns:
            Dict[str, Any]: 大模型配置字典
        """
        return {
            "api_key": self.zhipu_api_key,
            "model_chat": self.zhipu_model_chat,
            "model_vision": self.zhipu_model_vision,
            "temperature": self.zhipu_temperature,
            "max_tokens": self.zhipu_max_tokens,
            "retry_count": self.llm_retry_count,
            "retry_delay": self.llm_retry_delay,
            "retry_backoff": self.llm_retry_backoff,
            "timeout": self.llm_timeout  # 添加超时配置
        }

# 加载配置
settings = Settings()

def get_settings() -> Settings:
    """
    获取应用配置
    
    Returns:
        Settings: 应用配置对象
    """
    return settings

def get_llm_config() -> Dict[str, Any]:
    """
    获取大模型配置
    
    Returns:
        Dict[str, Any]: 大模型配置字典
    """
    return settings.get_llm_config()
