#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
文件作用：执行Agent模块，负责执行测试用例
开发规划：实现基于MCP的测试用例执行功能，包括命令解析和执行
"""

import os
import json
import asyncio
from typing import Dict, Any, List, Optional, Tuple, TypedDict
from datetime import datetime
from loguru import logger
from langgraph.graph import StateGraph, END
import zhipuai
from dotenv import load_dotenv
from mcp.client.session import ClientSession
from mcp.client.sse import sse_client
from pydantic import BaseModel
from sqlalchemy.sql import text
import time
import re
import logging

from core.config import get_settings, get_llm_config
from core.database import (
    get_db, 
    update_test_case_status, 
    update_test_task_status,
    get_test_task,
    TestCase,
    TestTask as DBTestTask  # 添加这个导入
)
from core.utils import generate_unique_id, format_timestamp, ensure_dir
from core.logger import get_logger
from core.mcp_config import get_mcp_config, get_cmd_format_path

# 加载环境变量
load_dotenv()

# 获取带上下文的logger
log = get_logger("execution_agent")

# 智谱AI配置
ZHIPU_API_KEY = os.getenv("ZHIPU_API_KEY")
ZHIPU_MODEL = os.getenv("ZHIPU_MODEL_CHAT", "glm-4-flash")
ZHIPU_API_BASE = os.getenv("ZHIPU_API_BASE", "https://open.bigmodel.cn/api/paas/v4")

# MCP服务器配置通过导入获取，不再在此处定义
# MCP_HOST = os.getenv("MCP_HOST", "172.16.100.108")
# MCP_PORT = int(os.getenv("MCP_PORT", "2800"))
# SSE_URL = f"http://{MCP_HOST}:{MCP_PORT}/sse"

class CommandStrategy(BaseModel):
    """命令策略模型"""
    tool: str
    parameters: Dict[str, Any]
    description: Optional[str] = None


class CommandStrategies(BaseModel):
    """多个命令策略的集合"""
    strategies: List[CommandStrategy]
    description: Optional[str] = None


class ExecutionState(TypedDict):
    """执行Agent状态定义"""
    task_id: str  # 任务ID
    case_id: Optional[str]  # 测试用例ID，可选
    algorithm_image: str  # 算法镜像
    dataset_url: Optional[str]  # 数据集URL
    test_cases: List[Dict[str, Any]]  # 测试用例列表
    current_case_index: int  # 当前执行的测试用例索引
    command_strategies: Optional[CommandStrategies]  # 命令策略集合
    current_strategy_index: int  # 当前执行的命令策略索引
    execution_result: Optional[Dict[str, Any]]  # 执行结果
    errors: List[str]  # 错误信息
    status: str  # 任务状态
    container_ready: bool  # Docker容器是否准备好


class ZhipuAIClient:
    """智谱AI大模型客户端"""
    
    def __init__(self, api_key: str = None, model: str = None):
        """
        初始化智谱AI客户端
        
        Args:
            api_key: 智谱AI API密钥，默认从环境变量或配置文件获取
            model: 要使用的模型名称，默认从配置文件获取
        """
        # 设置日志记录器
        self.log = logging.getLogger("zhipuai")
        
        # 获取API密钥
        self.api_key = api_key or get_llm_config().get("api_key", "")
        
        # 获取模型名称
        self.model = model or get_llm_config().get("model", "glm-4-flash")
        
        # 命令格式文档路径
        self.cmd_format_path = get_cmd_format_path()
        
        # 是否开启详细日志
        self.verbose_logging = True
        
        zhipuai.api_key = self.api_key
        
    async def parse_command(self, command: str, available_tools: List[Dict[str, Any]], container_name: str = None) -> CommandStrategies:
        """
        使用智谱AI解析自然语言命令，返回一组命令策略
        
        Args:
            command: 自然语言命令/测试步骤描述
            available_tools: 可用工具列表
            container_name: Docker容器名称，用于构建正确的命令格式
            
        Returns:
            解析后的命令策略集合
        """
        log = self.log

        log.info(f"准备解析命令: 容器名称={container_name}")
        log.info(f"测试步骤内容: {command[:200]}..." if len(command) > 200 else f"测试步骤内容: {command}")

        # 读取命令格式要求文档
        cmd_format_doc = ""
        try:
            if os.path.exists(self.cmd_format_path):
                with open(self.cmd_format_path, 'r', encoding='utf-8') as f:
                    cmd_format_doc = f.read()
                log.info(f"成功读取命令格式要求文档: {self.cmd_format_path}")
                if self.verbose_logging:
                    log.debug(f"命令格式要求文档内容(前500字符): {cmd_format_doc[:500]}")
            else:
                log.warning(f"命令格式要求文档不存在: {self.cmd_format_path}")
        except Exception as e:
            log.error(f"读取命令格式要求文档时出错: {e}")
        
        # 构建Docker命令示例
        container_example = container_name if container_name else "algotest_TASK1742443129_45b17f0db380"
        docker_cmd_example = f"docker exec {container_example} ./ev_sdk/bin/test-ji-api -f 1 -i /data/000000.jpg -o ./output.jpg -a '{{\"draw_confidence\": true}}'"
            
        # 构建提示词
        system_prompt = f"""你是一个专业的测试工程师，负责将测试用例转换为可执行的命令。
请按照以下格式要求准确解析命令:
{cmd_format_doc}
"""

        # 构建用户提示词 - 修改：只需返回一条命令
        user_prompt = f"""
请将以下测试用例步骤转换为JSON格式的执行策略。只需返回一条最关键的、能够完成测试目标的命令即可。如果是多个步骤，则将每个步骤的参数叠加到一条命令。


可用的工具有:

1. execute_command - 执行单个CLI命令
   参数:
   - command (字符串, 必需): 要执行的命令
   - working_dir (字符串, 可选): 命令执行的工作目录，默认为当前目录

2. execute_script - 执行多行脚本
   参数:
   - script (字符串, 必需): 要执行的脚本内容
   - working_dir (字符串, 可选): 脚本执行的工作目录，默认为当前目录

3. list_directory - 列出目录内容
   参数:
   - directory (字符串, 必需): 要列出内容的目录路径
   - recursive (布尔值, 可选): 是否递归列出子目录，默认为false

4. read_file - 读取文件内容
   参数:
   - file_path (字符串, 必需): 要读取的文件路径

测试用例步骤:
{command}

容器名称: {container_name if container_name else "未提供，需要在命令中使用容器名称参数"}


请返回一个JSON对象，包含工具名称、参数和描述。请确保只返回一条命令。
例如:
{{
  "tool": "execute_command",
  "parameters": {{
    "command": "docker exec {container_name} ./ev_sdk/bin/test-ji-api -f 1 -i /data/test.jpg -o ./output.jpg"
  }},
  "description": "运行算法检测图像"
}}
"""

        log.debug(f"提示内容预览: {user_prompt[:500]}...")

        # 模拟API调用或使用真实API
        if self.api_key == "mock":
            # 模拟API调用（用于测试）
            log.info("使用模拟API响应（仅测试用）")
            content = """```json
{
  "tool": "execute_command",
  "parameters": {
    "command": "docker exec algotest_TEST ./ev_sdk/bin/test-ji-api -f 1 -i /data/test.jpg -o ./output.jpg -a '{\"visual_object\": true}'"
  },
  "description": "运行算法检测图像，设置visual_object参数为true"
}
```"""
            time.sleep(0.5)  # 模拟API调用延迟
        else:
            # 调用真实API
            try:
                log.info(f"开始调用智谱AI API (模型: {self.model})...")
                start_time = time.time()
                
                client = zhipuai.ZhipuAI(api_key=self.api_key)
                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ]
                response = client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    max_tokens=4096,
                    temperature=0.01,
                )
                content = response.choices[0].message.content
                
                end_time = time.time()
                log.info(f"智谱AI API调用完成，耗时: {end_time - start_time:.2f}秒")
            except Exception as e:
                log.error(f"智谱AI API调用失败: {e}")
                # 使用默认指令
                if container_name:
                    command = f"docker exec {container_name} ./ev_sdk/bin/test-ji-api -f 1 -i /data/test.jpg -o ./output.jpg -a '{{\"visual_object\": true}}'"
                else:
                    command = "./ev_sdk/bin/test-ji-api -f 1 -i /data/000000.jpg -o ./output.jpg -a '{\"visual_object\": true}'"
                
                return CommandStrategies(strategies=[
                    CommandStrategy(
                        tool="execute_command", 
                        parameters={"command": command}, 
                        description="默认测试命令（API调用失败）"
                    )
                ])

        # 打印AI返回的原始数据
        log.info(f"模型返回内容: {content}")
        
        # 尝试解析返回的JSON
        log.info("尝试直接解析返回的JSON...")
        try:
            # 直接尝试解析JSON
            json_data = json.loads(content)
            
            # 如果解析结果是列表，只取第一个元素
            if isinstance(json_data, list) and len(json_data) > 0:
                log.info(f"解析到多条命令，只使用第一条命令")
                json_data = json_data[0]
            
            # 创建单个策略
            strategy = CommandStrategy(
                tool=json_data.get("tool", "execute_command"),
                parameters=json_data.get("parameters", {}),
                description=json_data.get("description", "未提供描述")
            )
            
            log.info(f"成功解析JSON，创建单条命令策略")
            return CommandStrategies(strategies=[strategy])
        
        except json.JSONDecodeError:
            log.warning("直接解析JSON失败，尝试从文本中提取JSON...")
            
            # 尝试提取JSON对象或数组
            json_content = None
            
            # 尝试匹配单个JSON对象 {...}
            json_obj_match = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', content)
            if json_obj_match:
                log.info("从markdown代码块中提取到JSON对象内容")
                json_content = json_obj_match.group(1)
            else:
                # 尝试直接匹配 {...} 格式
                json_obj_match = re.search(r'\{\s*"[^"]+"\s*:[\s\S]*?\}', content)
                if json_obj_match:
                    log.info("从文本中直接提取到JSON对象内容")
                    json_content = json_obj_match.group(0)
                else:
                    # 尝试匹配```json [...] ```格式
                    json_arr_match = re.search(r'```(?:json)?\s*(\[[\s\S]*?\])\s*```', content)
                    if json_arr_match:
                        log.info("从markdown代码块中提取到JSON数组内容")
                        json_content = json_arr_match.group(1)
                    else:
                        # 尝试直接匹配 [...] 格式
                        json_arr_match = re.search(r'\[\s*\{[\s\S]*?\}\s*\]', content)
                        if json_arr_match:
                            log.info("从文本中直接提取到JSON数组内容")
                            json_content = json_arr_match.group(0)
            
            if json_content:
                log.info(f"从响应中提取的JSON片段: {json_content[:100]}...")
                
                try:
                    json_data = json.loads(json_content)
                    
                    # 如果是数组，只取第一个元素
                    if isinstance(json_data, list) and len(json_data) > 0:
                        log.info(f"提取到JSON数组，只使用第一条命令")
                        json_data = json_data[0]
                    
                    # 创建单个策略
                    strategy = CommandStrategy(
                        tool=json_data.get("tool", "execute_command"),
                        parameters=json_data.get("parameters", {}),
                        description=json_data.get("description", "未提供描述")
                    )
                    
                    log.info(f"通过提取和清理成功解析JSON，创建单条命令策略")
                    return CommandStrategies(strategies=[strategy])
                    
                except json.JSONDecodeError as e:
                    log.error(f"解析JSON失败: {e}")
                    
                    # 使用默认命令
                    if container_name:
                        default_command = f"docker exec {container_name} ./ev_sdk/bin/test-ji-api -f 1 -i /data/test.jpg -o ./output.jpg -a '{{\"visual_object\": true}}'"
                    else:
                        default_command = "./ev_sdk/bin/test-ji-api -f 1 -i /data/test.jpg -o ./output.jpg -a '{\"visual_object\": true}'"
                    
                    log.warning(f"将使用默认命令: {default_command}")
                    default_strategy = CommandStrategy(
                        tool="execute_command",
                        parameters={"command": default_command},
                        description="默认测试命令（解析JSON失败）"
                    )
                    return CommandStrategies(strategies=[default_strategy])
            
            # 如果未找到JSON
            log.error("未能从响应中提取JSON对象或数组")
            # 使用默认命令
            if container_name:
                default_command = f"docker exec {container_name} ./ev_sdk/bin/test-ji-api -f 1 -i /data/test.jpg -o ./output.jpg -a '{{\"visual_object\": true}}'"
            else:
                default_command = "./ev_sdk/bin/test-ji-api -f 1 -i /data/000000.jpg -o ./output.jpg -a '{\"visual_object\": true}'"
            
            log.warning(f"将使用默认命令: {default_command}")
            default_strategy = CommandStrategy(
                tool="execute_command",
                parameters={"command": default_command},
                description="默认测试命令（未找到JSON）"
            )
            return CommandStrategies(strategies=[default_strategy])


class MCPClient:
    """MCP客户端"""
    
    def __init__(self, host: str = None, port: int = None):
        """
        初始化MCP客户端
        
        Args:
            host: MCP服务器主机，默认从配置获取
            port: MCP服务器端口，默认从配置获取
        """
        # 获取MCP配置
        mcp_config = get_mcp_config()
        
        # 设置主机和端口
        self.host = host or mcp_config["host"]
        self.port = port or mcp_config["port"]
        self.sse_url = f"http://{self.host}:{self.port}/sse"
        
        # 初始化智谱AI客户端
        self.ai_client = ZhipuAIClient(ZHIPU_API_KEY, ZHIPU_MODEL)
        self.session = None
        
    async def connect(self):
        """连接到MCP服务器"""
        log.info(f"正在连接到MCP服务器 {self.sse_url}...")
        
        try:
            # 建立SSE连接
            async with sse_client(self.sse_url) as (read, write):
                # 创建客户端会话
                async with ClientSession(read, write) as session:
                    # 初始化连接
                    await session.initialize()
                    log.info("MCP服务器连接成功！")
                    
                    # 保存会话
                    self.session = session
                    
                    # 等待会话结束
                    while True:
                        await asyncio.sleep(0.1)
            
            return True
        except Exception as e:
            log.error(f"连接到MCP服务器时出错: {e}")
            return False
    
    async def disconnect(self):
        """断开与MCP服务器的连接"""
        self.session = None
        log.info("已断开与MCP服务器的连接")
    
    async def execute_strategy(self, strategy: CommandStrategy) -> Dict[str, Any]:
        """
        直接执行已解析的命令策略，不进行二次解析
        
        Args:
            strategy: 已解析的命令策略对象
            
        Returns:
            执行结果
        """
        if not self.session:
            raise Exception("未连接到MCP服务器")
        
        try:
            log.info(f"直接执行已解析的命令策略: {strategy.tool}")
            
            # 预处理参数
            if "working_dir" in strategy.parameters:
                # 处理特殊的工作目录值
                if strategy.parameters["working_dir"] in ["current_directory", ".", "current", "current dir"]:
                    log.info(f"将工作目录从 '{strategy.parameters['working_dir']}' 修改为 '/'")
                    strategy.parameters["working_dir"] = "/"
            
            # 对于execute_script，确保脚本不使用sudo
            if strategy.tool == "execute_script" and "script" in strategy.parameters:
                script = strategy.parameters["script"]
                if "sudo " in script:
                    clean_script = script.replace("sudo ", "")
                    log.info(f"移除脚本中的sudo命令: '{script}' -> '{clean_script}'")
                    strategy.parameters["script"] = clean_script
            
            # 对于execute_command，确保命令不使用sudo
            if strategy.tool == "execute_command" and "command" in strategy.parameters:
                cmd = strategy.parameters["command"]
                if cmd.startswith("sudo "):
                    clean_cmd = cmd.replace("sudo ", "", 1)
                    log.info(f"移除命令中的sudo: '{cmd}' -> '{clean_cmd}'")
                    strategy.parameters["command"] = clean_cmd
            
            log.info(f"处理后的参数: {strategy.parameters}")
            
            # 调用相应的工具
            result = await self.session.call_tool(strategy.tool, strategy.parameters)
            log.info("命令执行成功")
            
            return {
                "success": True,
                "strategy": strategy,
                "result": result
            }
            
        except Exception as e:
            log.error(f"执行命令策略时出错: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def execute_command(self, command: str) -> Dict[str, Any]:
        """
        执行命令
        
        Args:
            command: 要执行的命令
            
        Returns:
            执行结果
        """
        if not self.session:
            raise Exception("未连接到MCP服务器")
        
        try:
            # 获取可用工具列表
            tools = await self.session.list_tools()
            available_tools = []
            if hasattr(tools, 'tools'):
                for tool in tools.tools:
                    available_tools.append({
                        "name": tool.name,
                        "description": tool.description
                    })
            
            # 检查命令是否包含docker exec，预处理容器操作
            container_name = None
            if "docker exec" in command:
                # 从命令中提取容器名称
                import re
                container_match = re.search(r'docker exec\s+(\S+)', command)
                if container_match:
                    container_name = container_match.group(1)
                    log.info(f"从命令中提取到容器名称: {container_name}")
                    
                    # 将docker exec命令转换为在容器内执行的命令
                    # 示例: docker exec container_name <cmd> -> <cmd>
                    container_cmd = re.sub(r'docker exec\s+\S+\s+', '', command)
                    log.info(f"将在容器 {container_name} 内执行命令: {container_cmd}")
                    command = container_cmd
            
            # 使用智谱AI解析命令
            strategy = await self.ai_client.parse_command(command, available_tools, container_name)
            
            log.info(f"解析结果: {strategy}")
            log.info(f"将调用工具: {strategy.tool}")
            
            # 直接调用execute_strategy执行已解析的命令策略
            return await self.execute_strategy(strategy)
            
        except Exception as e:
            log.error(f"执行命令时出错: {e}")
            return {
                "success": False,
                "error": str(e)
            }


def load_test_cases(state: ExecutionState) -> ExecutionState:
    """
    按照task_id批量加载测试用例信息，如果提供了case_id则只加载指定用例
    
    Args:
        state: 当前状态
        
    Returns:
        更新后的状态
    """
    task_id = state['task_id']
    case_id = state.get('case_id')
    
    if case_id:
        log.info(f"开始加载指定的测试用例: 任务ID={task_id}, 用例ID={case_id}")
    else:
        log.info(f"开始加载任务 {task_id} 的所有测试用例")
    
    try:
        # 从数据库加载测试用例
        with get_db() as db:
            if case_id:
                # 只加载指定的测试用例
                # 使用text()包装SQL查询
                stmt = text("SELECT * FROM test_cases WHERE case_id = :case_id AND task_id = :task_id")
                result = db.execute(stmt, {"case_id": case_id, "task_id": task_id})
                
                # 获取列名
                columns = result.keys()
                
                # 将结果转换为字典列表
                rows = result.fetchall()
                if not rows:
                    raise ValueError(f"找不到指定的测试用例: 任务ID={task_id}, 用例ID={case_id}")
                
                # 将每行转换为字典
                test_cases = []
                for row in rows:
                    test_case = {}
                    for i, column in enumerate(columns):
                        test_case[column] = row[i]
                    test_cases.append(test_case)
                
                log.info(f"找到指定的测试用例: {case_id}")
            else:
                # 按task_id获取所有测试用例
                # 使用text()包装SQL查询
                stmt = text("SELECT * FROM test_cases WHERE task_id = :task_id")
                result = db.execute(stmt, {"task_id": task_id})
                
                # 获取列名
                columns = result.keys()
                
                # 将结果转换为字典列表
                rows = result.fetchall()
                if not rows or len(rows) == 0:
                    raise ValueError(f"任务 {task_id} 没有测试用例")
                
                # 将每行转换为字典
                test_cases = []
                for row in rows:
                    test_case = {}
                    for i, column in enumerate(columns):
                        test_case[column] = row[i]
                    test_cases.append(test_case)
                
                log.info(f"找到 {len(test_cases)} 个测试用例")
            
            # 更新状态
            return {
                **state,
                "test_cases": test_cases,
                "current_case_index": 0,  # 从第一个测试用例开始
                "status": "loaded"
            }
    except Exception as e:
        log.error(f"加载测试用例失败: {str(e)}")
        return {
            **state,
            "errors": state.get("errors", []) + [str(e)],
            "status": "error"
        }


async def parse_command(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    解析命令节点 - 从测试用例中解析命令
    
    Args:
        state: 执行状态
        
    Returns:
        更新后的执行状态，包含解析后的命令策略集合
    """
    log.info("开始解析命令节点")

    # 获取当前测试用例的索引
    current_index = state.get("current_case_index", 0)
    test_cases = state.get("test_cases", [])

    if not test_cases:
        log.error("执行状态中缺少test_cases")
        raise ValueError("执行状态中没有测试用例")

    if current_index >= len(test_cases):
        log.error(f"当前索引超出测试用例列表范围: {current_index} >= {len(test_cases)}")
        raise ValueError(f"当前索引超出测试用例列表范围")

    # 获取当前测试用例信息
    current_case = test_cases[current_index]
    case_id = current_case.get("case_id") or current_case.get("id")

    if not case_id:
        log.error("测试用例中缺少ID")
        raise ValueError("测试用例中缺少ID")

    log.info(f"开始处理测试用例: {case_id} (索引 {current_index + 1}/{len(test_cases)})")

    # 从input_data字段提取测试步骤
    input_data = current_case.get("input_data", "")
    if not input_data:
        log.warning(f"测试用例中没有input_data: case_id={case_id}")
        return {
            **state,
            "status": "error",
            "error": "测试用例中没有input_data"
        }
    
    # 解析input_data中的JSON数据
    try:
        if isinstance(input_data, str):
            input_data_json = json.loads(input_data)
            log.info(f"成功解析input_data JSON数据")
        else:
            input_data_json = input_data
            log.info(f"input_data已经是JSON对象")
        
        # 获取steps字段
        steps = input_data_json.get("steps", "")
        if not steps:
            log.warning(f"测试用例中没有测试步骤: case_id={case_id}")
            return {
                **state,
                "status": "error",
                "error": "测试用例中没有测试步骤"
            }
    except Exception as e:
        log.error(f"解析input_data JSON失败: {e}")
        return {
            **state,
            "status": "error",
            "error": f"解析input_data JSON失败: {e}"
        }

    log.info(f"获取到测试步骤: {steps[:100]}..." if len(steps) > 100 else f"获取到测试步骤: {steps}")

    # 查询任务信息以获取容器名称和测试数据
    try:
        db = get_db()
        log.info(f"从数据库获取任务和测试用例信息: task_id={state.get('task_id')}, case_id={case_id}")
        
        # 获取容器名称
        query = text("SELECT container_name FROM test_tasks WHERE task_id = :id")
        result = db.execute(query, {"id": state.get("task_id")}).fetchone()
        if not result or not result[0]:
            log.error(f"未找到容器名称")
            raise ValueError("容器名称未设置，请先设置Docker容器")
        
        container_name = result[0]
        log.info(f"获取到容器名称: {container_name}")
            
        # 获取测试数据路径
        test_data_query = text("SELECT test_data FROM test_cases WHERE case_id = :case_id")
        test_data_result = db.execute(test_data_query, {"case_id": case_id}).fetchone()
        if not test_data_result or not test_data_result[0]:
            log.error(f"未找到测试数据路径")
            raise ValueError("测试数据路径未设置，请先设置测试数据")
        
        test_data_path = test_data_result[0]
        log.info(f"获取到测试数据路径: {test_data_path}")
        
    except Exception as e:
        log.error(f"查询任务信息失败: {e}")
        return {
            **state,
            "errors": state.get("errors", []) + [str(e)],
            "status": "error"
        }

    # 创建智谱AI客户端
    ai_client = ZhipuAIClient()

    # 构建包含测试数据路径的步骤
    steps_with_data = f"{steps}\n测试数据路径: {test_data_path}"
    log.info(f"添加测试数据路径到步骤中: {test_data_path}")

    # 解析命令 - 使用智谱AI方法，传入容器名称和测试数据路径
    log.info(f"使用智谱AI解析命令，使用容器名称: {container_name}")
    command_strategies = await ai_client.parse_command(steps_with_data, [], container_name)
    
    log.info(f"命令解析成功, 共获取到 {len(command_strategies.strategies)} 条命令策略")
    if command_strategies.strategies:
        first_strategy = command_strategies.strategies[0]
        log.info(f"第一条命令策略: {first_strategy.tool} - {first_strategy.description}")
    
    # 更新状态
    return {
        **state,
        "case_id": case_id,  # 设置当前执行的case_id
        "command_strategies": command_strategies,
        "current_strategy_index": 0,
        "status": "parsed"
    }


async def execute_command(state: ExecutionState) -> ExecutionState:
    """
    执行命令
    
    Args:
        state: 当前状态
        
    Returns:
        更新后的状态
    """
    case_id = state.get("case_id")
    log.info(f"开始执行命令: 用例ID={case_id}")
    
    try:
        # 获取命令策略
        command_strategies = state.get("command_strategies")
        if not command_strategies or not command_strategies.strategies:
            raise ValueError("命令策略未解析或为空")
        
        # 直接获取第一条命令策略，因为现在我们只使用一条命令
        command_strategy = command_strategies.strategies[0]
        
        log.info("*"*60)
        log.info(f"【测试用例执行开始】: {case_id}")
        log.info("*"*60)
        
        # 获取要执行的命令
        command = command_strategy.parameters.get("command", "")
        if not command:
            log.warning(f"命令策略缺少command参数，无法执行")
            return {
                **state,
                "execution_result": {
                    "success": False,
                    "all_results": [],
                    "success_count": 0,
                    "fail_count": 1,
                    "error": "命令策略缺少command参数"
                },
                "status": "error"
            }
        
        # 记录开始时间
        start_time = time.time()
        
        # 检查测试用例中是否有外部输出
        external_output = None
        try:
            # 获取当前测试用例
            test_cases = state.get("test_cases", [])
            current_index = state.get("current_case_index", 0)
            
            if test_cases and current_index < len(test_cases):
                current_case = test_cases[current_index]
                # 检查是否有external_output字段
                if current_case.get("external_output"):
                    external_output = current_case.get("external_output")
                    log.info(f"发现测试用例提供的外部输出，长度: {len(external_output)} 字符")
        except Exception as e:
            log.warning(f"获取外部输出时出错: {e}")
        
        # 在Docker容器中执行命令
        log.info(f"执行命令: {command}")
        result = await execute_container_command(state["task_id"], command, external_output)
        
        # 记录结束时间并计算执行时间（毫秒）
        end_time = time.time()
        execution_time_ms = int((end_time - start_time) * 1000)
        log.info(f"命令执行完成，耗时: {execution_time_ms}毫秒")
        
        # 将执行时间添加到结果中
        if result:
            result["execution_time"] = execution_time_ms
        
        # 记录结果
        all_results = [{
            "strategy_index": 0,
            "strategy": command_strategy,
            "result": result,
            "full_output": result.get("full_output", ""),
            "raw_stdout": result.get("raw_stdout", ""),
            "raw_stderr": result.get("raw_stderr", "")
        }]
        
        # 确定执行是否成功
        success = result.get("success", False)
        if not success:
            error_msg = result.get("error", "未知错误")
            log.error(f"命令执行失败: {error_msg}")
            
            # 如果执行失败但用户在消息中提供了输出，尝试使用它作为执行结果
            user_output = state.get("user_output")
            if user_output and not external_output:
                log.info(f"使用用户提供的输出作为执行结果")
                
                # 创建带有用户输出的新结果
                new_result = await execute_container_command(state["task_id"], command, user_output)
                new_result["execution_time"] = execution_time_ms
                
                # 更新all_results
                all_results = [{
                    "strategy_index": 0,
                    "strategy": command_strategy,
                    "result": new_result,
                    "full_output": new_result.get("full_output", ""),
                    "raw_stdout": new_result.get("raw_stdout", ""),
                    "raw_stderr": new_result.get("raw_stderr", "")
                }]
                
                # 更新成功标志
                success = True
        
        log.info("*"*60)
        log.info(f"【测试用例执行完成】: {case_id}")
        log.info(f"命令执行{'成功' if success else '失败'}")
        log.info("*"*60)
        
        # 更新状态
        return {
            **state,
            "execution_result": {
                "success": success,
                "all_results": all_results,
                "success_count": 1 if success else 0,
                "fail_count": 0 if success else 1,
                "execution_time": execution_time_ms,  # 添加执行时间到执行结果
                "raw_stdout": result.get("raw_stdout", ""),  # 保存完整的标准输出
                "raw_stderr": result.get("raw_stderr", "")   # 保存完整的标准错误
            },
            "status": "executed"
        }
            
    except Exception as e:
        log.error(f"执行命令失败: {str(e)}")
        return {
            **state,
            "errors": state.get("errors", []) + [str(e)],
            "status": "error"
        }


async def save_result(state: ExecutionState) -> ExecutionState:
    """
    保存执行结果，并决定是否继续执行下一个测试用例
    
    Args:
        state: 当前状态
        
    Returns:
        更新后的状态
    """
    case_id = state.get("case_id")
    log.info(f"开始保存执行结果: 用例ID={case_id}")
    
    try:
        # 获取执行结果
        execution_result = state.get("execution_result")
        if not execution_result:
            raise ValueError("执行结果为空")
        
        # 获取执行成功/失败信息
        success = execution_result.get("success", False)
        success_count = execution_result.get("success_count", 0)
        fail_count = execution_result.get("fail_count", 0)
        all_results = execution_result.get("all_results", [])
        
        # 获取执行时间（毫秒）
        execution_time = execution_result.get("execution_time", 0)
        
        # 生成详细执行摘要
        summary = {
            "success": success,
            "total_commands": len(all_results),
            "success_count": success_count,
            "fail_count": fail_count,
            "execution_time": execution_time,
            "commands": []
        }
        
        # 原始完整输出
        raw_outputs = []
        
        # 添加每条命令的详细结果
        for result_item in all_results:
            strategy = result_item.get("strategy")
            result = result_item.get("result", {})
            
            # 收集原始输出内容
            full_output = result_item.get("full_output", "")
            stdout = result.get("result", {}).get("stdout", "") if result.get("result") else ""
            stderr = result.get("result", {}).get("stderr", "") if result.get("result") else ""
            raw_stdout = result.get("raw_stdout", "")
            raw_stderr = result.get("raw_stderr", "")
            
            raw_outputs.append({
                "full_output": full_output,
                "stdout": stdout,
                "stderr": stderr,
                "raw_stdout": raw_stdout,
                "raw_stderr": raw_stderr
            })
            
            # 提取重要信息，但保留完整的输出数据
            command_result = {
                "index": result_item.get("strategy_index"),
                "description": strategy.description if strategy else "未知命令",
                "command": strategy.parameters.get("command") if strategy else "未知命令",
                "success": result.get("success", False),
                # 保存完整的输出内容
                "full_output": full_output,
                "stdout": stdout,
                "stderr": stderr,
                "raw_stdout": raw_stdout,
                "raw_stderr": raw_stderr,
                "error": result.get("error", ""),
                # 保存执行时间
                "execution_time": result.get("execution_time", 0)
            }
            
            summary["commands"].append(command_result)
        
        log.info(f"执行摘要: 总计 {len(all_results)} 条命令，成功 {success_count} 条，失败 {fail_count} 条，耗时 {execution_time}毫秒")
        
        # 准备要存储到数据库的完整测试结果数据
        # 替换为简单的错误描述，不再单独存储error_message
        error_description = ""
        if not success and all_results and len(all_results) > 0:
            # 如果失败，提取错误信息
            first_result = all_results[0].get("result", {})
            error_description = first_result.get("error", "")
        
        # 获取原始命令输出作为实际输出
        raw_output_text = ""
        if all_results and len(all_results) > 0:
            # 获取第一个结果的原始输出
            first_result = all_results[0]
            if first_result.get("full_output"):
                raw_output_text = first_result.get("full_output")
            elif first_result.get("raw_stdout"):
                raw_output_text = first_result.get("raw_stdout")
                if first_result.get("raw_stderr"):
                    raw_output_text += "\n\nSTDERR:\n" + first_result.get("raw_stderr")
        
        # 添加基本结果分析
        result_analysis = f"执行{'成功' if success else '失败'}"
        if error_description:
            result_analysis += f"，错误原因: {error_description}"
        if execution_time > 0:
            result_analysis += f"，执行耗时: {execution_time}毫秒"
        
        # 更新测试用例状态和结果
        with get_db() as db:
            update_test_case_status(
                case_id=case_id,
                status="completed" if success else "failed",
                result=raw_output_text  # 传递原始文本输出
            )
            
            # 手动更新result_analysis字段
            case = db.query(TestCase).filter(TestCase.case_id == case_id).first()
            if case:
                case.result_analysis = result_analysis
                db.commit()
        
        log.info(f"执行结果保存成功: 用例ID={case_id}")
        
        # 获取当前测试用例索引
        current_index = state.get("current_case_index", 0)
        test_cases = state.get("test_cases", [])
        
        # 检查是否还有下一个测试用例需要执行
        if current_index + 1 < len(test_cases):
            # 还有测试用例需要执行，更新索引并准备执行下一个
            log.info(f"准备执行下一个测试用例，当前进度: {current_index + 1}/{len(test_cases)}")
            next_state = {
                **state,
                "current_case_index": current_index + 1,
                "case_id": None,  # 清除当前case_id
                "command_strategies": None,  # 清除当前命令策略
                "execution_result": None,  # 清除当前执行结果
                "status": "next_case"  # 设置状态为下一个测试用例
            }
            
            # 调用parse_command函数解析下一个测试用例的命令
            next_state = await parse_command(next_state)
            
            # 如果解析成功，继续执行命令
            if next_state.get("status") == "parsed":
                next_state = await execute_command(next_state)
                
                # 如果执行成功，递归调用save_result保存结果
                if next_state.get("status") == "executed":
                    return await save_result(next_state)
            
            # 如果过程中出现错误，返回错误状态
            return next_state
        else:
            # 所有测试用例已执行完成，更新任务状态
            with get_db() as db:
                # 更新测试任务状态
                update_test_task_status(
                    task_id=state["task_id"],
                    status="completed"
                )
            
            log.info(f"所有测试用例执行完成: 任务ID={state['task_id']}, 共{len(test_cases)}个测试用例")
            
            # 更新状态
            return {
                **state,
                "status": "completed"
            }
    except Exception as e:
        log.error(f"保存执行结果失败: {str(e)}")
        return {
            **state,
            "errors": state.get("errors", []) + [str(e)],
            "status": "error"
        }


async def setup_algorithm_container(task_id: str) -> Dict[str, Any]:
    """
    通过MCP在远程服务器上设置算法Docker容器
    
    Args:
        task_id: 测试任务ID
        
    Returns:
        设置结果
    """
    log.info(f"开始通过MCP设置算法Docker容器: {task_id}")
    
    try:
        # 从数据库获取任务信息
        task = get_test_task(task_id)
        if not task:
            raise ValueError(f"测试任务不存在: {task_id}")
        
        # 获取算法镜像和数据集URL
        algorithm_image = task.algorithm_image
        dataset_url = task.dataset_url
        
        if not algorithm_image:
            raise ValueError(f"算法镜像未设置: {task_id}")
        
        # 构建容器名称
        container_name = f"algotest_{task_id}"
        
        # 准备数据集挂载参数
        dataset_mount = ""
        if dataset_url:
            # 在Docker容器中的数据集路径
            container_dataset_path = "/data"
            dataset_mount = f"-v {dataset_url}:{container_dataset_path}"
        
        # 构建Docker操作脚本
        script = f"""
# 检查是否已存在同名容器
container_id=$(docker ps -a --filter name={container_name} -q)
if [ ! -z "$container_id" ]; then
    echo "发现同名容器，正在删除: {container_name}"
    docker rm -f {container_name}
fi

# 运行新容器
echo "正在启动新容器: {container_name}"
docker run --gpus=all -itd --privileged -v /etc/localtime:/etc/localtime:ro -e LANG=C.UTF-8 --name {container_name} {dataset_mount} {algorithm_image}

# 检查容器是否成功启动
sleep 2
container_status=$(docker inspect -f '{{{{.State.Running}}}}' {container_name} 2>/dev/null || echo "false")

if [ "$container_status" != "true" ]; then
    echo "容器启动失败，输出日志:"
    docker logs {container_name}
    exit 1
fi

echo "容器启动成功: {container_name}"
echo "算法镜像: {algorithm_image}"
"""
        
        if dataset_url:
            script += f'echo "数据集URL: {dataset_url} 已挂载到容器 {container_dataset_path}"\n'
        
        log.info(f"准备通过MCP执行Docker脚本...")
        
        # 从配置获取SSE URL
        sse_url = get_mcp_config()["sse_url"]
        
        # 连接到MCP服务器并执行脚本
        async with sse_client(sse_url) as (read, write):
            async with ClientSession(read, write) as session:
                # 初始化连接
                await session.initialize()
                log.info("MCP服务器连接成功")
                
                # 使用execute_script工具执行Docker脚本
                log.info("正在执行Docker配置脚本...")
                result = await session.call_tool("execute_script", {"script": script})
                
                # 检查脚本执行结果
                if hasattr(result, 'stderr') and result.stderr:
                    log.error(f"Docker脚本执行出错: {result.stderr}")
                    return {
                        "success": False,
                        "task_id": task_id,
                        "error": f"Docker容器启动失败: {result.stderr}"
                    }
                
                # 再次检查容器是否真的在运行
                verify_script = f"""
container_status=$(docker inspect -f '{{{{.State.Running}}}}' {container_name} 2>/dev/null || echo "false")
if [ "$container_status" != "true" ]; then
    echo "容器状态检查失败: 未运行"
    exit 1
fi
echo "容器状态检查成功: 正在运行"
"""
                
                try:
                    # 等待容器完全启动
                    await asyncio.sleep(3)
                    # 验证容器状态
                    verify_result = await session.call_tool("execute_script", {"script": verify_script})
                    
                    if hasattr(verify_result, 'stderr') and verify_result.stderr:
                        log.error(f"容器状态验证失败: {verify_result.stderr}")
                        return {
                            "success": False,
                            "task_id": task_id,
                            "error": f"容器启动后未正常运行: {verify_result.stderr}"
                        }
                    
                    # 检查验证脚本输出
                    if hasattr(verify_result, 'stdout') and "容器状态检查成功" not in verify_result.stdout:
                        log.error(f"容器状态验证未通过: {verify_result.stdout}")
                        return {
                            "success": False,
                            "task_id": task_id,
                            "error": f"容器状态验证未通过: {verify_result.stdout}"
                        }
                    
                    log.info(f"Docker容器验证成功: {container_name}")
                    
                except Exception as e:
                    log.error(f"验证容器状态时出错: {str(e)}")
                    return {
                        "success": False,
                        "task_id": task_id,
                        "error": f"验证容器状态时出错: {str(e)}"
                    }
                
                log.info(f"Docker容器设置完成: {container_name}")
        
        return {
            "success": True,
            "task_id": task_id,
            "container_name": container_name,
            "algorithm_image": algorithm_image,
            "dataset_url": dataset_url,
            "result": {
                "stdout": result.stdout if hasattr(result, "stdout") else str(result),
                "stderr": result.stderr if hasattr(result, "stderr") else ""
            }
        }
    except Exception as e:
        log.error(f"设置Docker容器失败: {str(e)}")
        return {
            "success": False,
            "task_id": task_id,
            "error": str(e)
        }


async def execute_container_command(task_id: str, command: str, external_output: str = None) -> Dict[str, Any]:
    """
    通过MCP在远程服务器上执行命令，命令应已包含完整的docker exec前缀
    
    Args:
        task_id: 测试任务ID
        command: 要执行的命令（已包含docker exec前缀）
        external_output: 外部提供的命令输出（可选），用于手动注入完整的命令输出
        
    Returns:
        执行结果
    """
    log.info(f"开始执行命令: {command}")
    log.info("="*50)
    log.info(f"【命令开始】: {command}")
    log.info("="*50)
    
    try:
        # 构建容器名称（仅用于记录）
        container_name = f"algotest_{task_id}"
        
        # 如果提供了外部输出，则跳过实际执行命令，直接使用外部输出
        if external_output:
            log.info("使用外部提供的命令输出")
            raw_stdout = external_output
            raw_stderr = ""
            is_error = False
            
            # 构建完整输出
            full_output = f"STDOUT:\n{raw_stdout}\n"
            
            log.info("="*50)
            log.info(f"【使用外部输出】: {command}")
            log.info("-"*50)
            log.info(f"输出内容前500字符:\n{raw_stdout[:500]}..." if len(raw_stdout) > 500 else f"输出内容:\n{raw_stdout}")
            log.info("="*50)
            
            return {
                "success": True,
                "task_id": task_id,
                "container_name": container_name,
                "command": command,
                "full_output": full_output,
                "raw_stdout": raw_stdout, 
                "raw_stderr": raw_stderr,
                "is_external_output": True,
                "result": {
                    "stdout": raw_stdout,
                    "stderr": raw_stderr
                }
            }
        
        # 直接执行传入的命令，不再添加docker exec前缀
        log.info(f"准备通过MCP执行命令...")
        
        # 从配置获取SSE URL
        sse_url = get_mcp_config()["sse_url"]
        
        # 连接到MCP服务器并执行命令
        async with sse_client(sse_url) as (read, write):
            async with ClientSession(read, write) as session:
                # 初始化连接
                await session.initialize()
                log.info("MCP服务器连接成功")
                
                # 直接使用execute_command工具执行命令
                log.info("正在执行命令...")
                start_time = time.time()
                result = await session.call_tool("execute_command", {"command": command})
                end_time = time.time()
                
                execution_time = end_time - start_time
                log.info(f"命令执行完成，耗时: {execution_time:.2f}秒")
        
        # 从result中提取真实的stdout和stderr内容
        # 检查result对象的类型和属性
        raw_stdout = ""
        raw_stderr = ""
        is_error = False
        
        # 打印原始结果以便调试
        log.debug(f"原始结果对象: {result}")
        log.debug(f"原始结果对象类型: {type(result)}")
        log.debug(f"原始结果对象属性: {dir(result)}")
        
        # 处理返回结果格式
        if hasattr(result, "stdout"):
            # 直接获取stdout
            stdout_raw = result.stdout
        else:
            # 尝试从content属性中提取
            stdout_raw = str(result)
        
        log.debug(f"提取的stdout原始内容: {stdout_raw}")
            
        # 尝试从TextContent对象中提取真实内容
        if "TextContent" in stdout_raw and "text=" in stdout_raw:
            try:
                # 使用正则表达式提取text字段的内容
                import re
                text_match = re.search(r"text='([^']*)'", stdout_raw)
                if text_match:
                    raw_content = text_match.group(1)
                    log.info(f"成功从TextContent中提取原始内容")
                    
                    # 检查是否包含"命令执行成功"或"命令执行失败"前缀
                    if "命令执行成功" in raw_content:
                        # 移除"命令执行成功:"前缀，保留真实的命令输出
                        raw_stdout = raw_content.replace("命令执行成功:", "").strip()
                        is_error = False
                    elif "命令执行失败" in raw_content:
                        # 提取失败信息
                        failure_match = re.search(r"命令执行失败[^:]*:(.+)", raw_content, re.DOTALL)
                        if failure_match:
                            raw_stderr = failure_match.group(1).strip()
                        else:
                            raw_stderr = raw_content
                        is_error = True
                    else:
                        # 没有明确前缀，保留整个内容
                        raw_stdout = raw_content
                else:
                    # 如果无法提取，保留原始内容
                    raw_stdout = stdout_raw
            except Exception as e:
                log.warning(f"从TextContent提取内容时出错: {e}")
                raw_stdout = stdout_raw
        else:
            # 非TextContent格式，直接使用
            # 检查是否包含"命令执行成功"前缀
            if "命令执行成功" in stdout_raw:
                success_match = re.search(r"命令执行成功:(.+)", stdout_raw, re.DOTALL) 
                if success_match:
                    raw_stdout = success_match.group(1).strip()
                else:
                    raw_stdout = stdout_raw.replace("命令执行成功:", "").strip()
            else:
                raw_stdout = stdout_raw
            
        # 提取stderr内容
        stderr_raw = result.stderr if hasattr(result, "stderr") else ""
        raw_stderr = stderr_raw
        
        # 检查isError属性
        if hasattr(result, "isError") and result.isError:
            is_error = True
            if not raw_stderr and raw_stdout:
                raw_stderr = raw_stdout
                raw_stdout = ""
        
        # 完整记录输出内容（去除MCP框架的封装）
        full_output = f"STDOUT:\n{raw_stdout}\n"
        if raw_stderr:
            full_output += f"STDERR:\n{raw_stderr}"
            
        # 检查返回标志中是否包含错误信息（例如"返回码"）
        if "返回码:" in raw_stdout or "执行命令时出错" in raw_stdout:
            is_error = True
            if not raw_stderr:
                raw_stderr = raw_stdout
                
        # 保存完整的输出内容
        log.info("="*50)
        log.info(f"【命令结果】: {command}")
        log.info("-"*50)
        log.info(f"输出内容前500字符:\n{full_output[:500]}..." if len(full_output) > 500 else f"输出内容:\n{full_output}")
        log.info("="*50)
        
        # 检查是否存在错误标识
        error_msg = ""
        
        # 检查stdout中是否包含常见错误字符串
        error_keywords = ["脚本执行失败", "返回码:", "错误:", "Error:", "Failed:"]
        for keyword in error_keywords:
            if keyword in raw_stdout:
                is_error = True
                error_msg = raw_stdout
                break
        
        # 如果stderr不为空，也认为有错误
        if raw_stderr and not error_msg:
            is_error = True
            error_msg = raw_stderr
            
        if is_error:
            log.error(f"命令执行返回错误: {error_msg}")
            return {
                "success": False,
                "task_id": task_id,
                "container_name": container_name, 
                "command": command,
                "error": error_msg,
                "full_output": full_output,
                "raw_stdout": raw_stdout,
                "raw_stderr": raw_stderr,
                "result": {
                    "stdout": raw_stdout,
                    "stderr": raw_stderr
                }
            }
        
        log.info(f"命令执行成功")
        return {
            "success": True,
            "task_id": task_id,
            "container_name": container_name,
            "command": command,
            "full_output": full_output,
            "raw_stdout": raw_stdout, 
            "raw_stderr": raw_stderr,
            "result": {
                "stdout": raw_stdout,
                "stderr": raw_stderr
            }
        }
    except Exception as e:
        log.error(f"执行命令失败: {str(e)}")
        log.info("="*50)
        log.info(f"【命令失败】: {command}")
        log.info(f"错误信息: {str(e)}")
        log.info("="*50)
        return {
            "success": False,
            "task_id": task_id,
            "command": command,
            "error": str(e)
        }


async def setup_docker_for_workflow(state: ExecutionState) -> ExecutionState:
    """
    为工作流设置Docker容器
    
    Args:
        state: 当前状态
        
    Returns:
        更新后的状态
    """
    log.info(f"为任务 {state['task_id']} 设置Docker容器")
    
    try:
        # 调用函数设置Docker容器
        result = await setup_algorithm_container(state['task_id'])
        
        if not result.get('success'):
            error_msg = result.get('error', '未知错误')
            log.error(f"设置Docker容器失败: {error_msg}")
            return {
                **state,
                "errors": state.get("errors", []) + [f"设置Docker容器失败: {error_msg}"],
                "status": "error",
                "container_ready": False
            }
        
        log.info(f"Docker容器设置成功: {result.get('container_name')}")
        
        # 更新状态
        return {
            **state,
            "algorithm_image": result.get("algorithm_image", state["algorithm_image"]),
            "dataset_url": result.get("dataset_url", state["dataset_url"]),
            "container_ready": True,
            "status": "container_ready"
        }
    except Exception as e:
        log.error(f"设置Docker容器异常: {str(e)}")
        return {
            **state,
            "errors": state.get("errors", []) + [str(e)],
            "status": "error",
            "container_ready": False
        }


def create_execution_graph() -> StateGraph:
    """
    创建执行Agent工作流图
    
    Returns:
        工作流图
    """
    # 创建工作流图
    execution_graph = StateGraph(ExecutionState)
    
    # 添加节点
    execution_graph.add_node("setup_docker", setup_docker_for_workflow)
    execution_graph.add_node("load_test_cases", load_test_cases)
    execution_graph.add_node("parse_command", parse_command)
    execution_graph.add_node("execute_command", execute_command)
    execution_graph.add_node("save_result", save_result)
    
    # 添加边
    execution_graph.add_edge("setup_docker", "load_test_cases")
    execution_graph.add_edge("load_test_cases", "parse_command")
    execution_graph.add_edge("parse_command", "execute_command")
    execution_graph.add_edge("execute_command", "save_result")
    
    # 添加条件边 - 如果需要执行下一个测试用例，回到parse_command
    execution_graph.add_conditional_edges(
        "save_result",
        lambda state: "parse_command" if state.get("status") == "next_case" else "end",
        {
            "parse_command": "parse_command",
            "end": END
        }
    )
    
    # 设置入口点和结束点
    execution_graph.set_entry_point("setup_docker")
    
    return execution_graph


async def run_execution(task_id: str, case_id: Optional[str] = None, algorithm_image: str = None, dataset_url: Optional[str] = None) -> Dict[str, Any]:
    """
    运行执行Agent
    
    Args:
        task_id: 任务ID
        case_id: 测试用例ID（可选），如果提供则只执行指定的测试用例
        algorithm_image: 算法镜像（可选，如果不提供则从数据库获取）
        dataset_url: 数据集URL（可选）
        
    Returns:
        执行结果
    """
    # 如果没有提供算法镜像，则从数据库获取
    if not algorithm_image:
        task = get_test_task(task_id)
        if not task:
            raise ValueError(f"测试任务不存在: {task_id}")
        algorithm_image = task.algorithm_image
        if not algorithm_image:
            raise ValueError(f"算法镜像未设置: {task_id}")
    
    # 创建工作流图
    execution_graph = create_execution_graph()
    
    # 编译工作流
    execution_app = execution_graph.compile()
    
    # 创建初始状态
    initial_state = {
        "task_id": task_id,
        "case_id": case_id,
        "algorithm_image": algorithm_image,
        "dataset_url": dataset_url,
        "test_cases": [],
        "current_case_index": 0,
        "command_strategies": None,
        "current_strategy_index": 0,
        "execution_result": None,
        "errors": [],
        "status": "created",
        "container_ready": False
    }
    
    # 运行工作流
    log.info(f"开始运行执行Agent: 任务ID={task_id}")
    result = execution_app.invoke(initial_state)
    log.info(f"执行Agent运行完成: 任务ID={task_id}, 状态: {result['status']}")
    
    return result


async def release_algorithm_container(task_id: str) -> Dict[str, Any]:
    """
    通过MCP释放指定任务的Docker容器
    
    Args:
        task_id: 测试任务ID
        
    Returns:
        释放结果
    """
    log.info(f"开始通过MCP释放算法Docker容器: {task_id}")
    
    try:
        # 从数据库获取任务信息
        task = get_test_task(task_id)
        if not task:
            raise ValueError(f"测试任务不存在: {task_id}")
        
        # 获取容器名称
        container_name = task.container_name
        if not container_name:
            raise ValueError(f"任务 {task_id} 未关联Docker容器")
        
        # 构建Docker释放脚本
        script = f"""
# 检查容器是否存在
container_id=$(docker ps -a --filter name={container_name} -q)
if [ -z "$container_id" ]; then
    echo "容器不存在: {container_name}"
    exit 0
fi

# 停止并删除容器
echo "正在停止并删除容器: {container_name}"
docker stop {container_name} || true
docker rm -f {container_name} || true

# 验证容器是否已被删除
container_exists=$(docker ps -a --filter name={container_name} -q)
if [ ! -z "$container_exists" ]; then
    echo "容器删除失败: {container_name}"
    exit 1
fi

echo "容器已成功删除: {container_name}"
"""
        
        log.info(f"准备通过MCP执行Docker释放脚本...")
        
        # 从配置获取SSE URL
        sse_url = get_mcp_config()["sse_url"]
        
        # 连接到MCP服务器并执行脚本
        async with sse_client(sse_url) as (read, write):
            async with ClientSession(read, write) as session:
                # 初始化连接
                await session.initialize()
                log.info("MCP服务器连接成功")
                
                # 使用execute_script工具执行Docker脚本
                log.info("正在执行Docker释放脚本...")
                result = await session.call_tool("execute_script", {"script": script})
                
                # 检查脚本执行结果
                if hasattr(result, 'stderr') and result.stderr:
                    log.error(f"Docker释放脚本执行出错: {result.stderr}")
                    return {
                        "success": False,
                        "task_id": task_id,
                        "error": f"Docker容器释放失败: {result.stderr}"
                    }
                
                # 验证容器是否真的被删除
                verify_script = f"""
container_exists=$(docker ps -a --filter name={container_name} -q)
if [ ! -z "$container_exists" ]; then
    echo "容器仍然存在: {container_name}"
    exit 1
fi
echo "容器验证成功: 已完全删除"
"""
                
                try:
                    # 验证容器状态
                    verify_result = await session.call_tool("execute_script", {"script": verify_script})
                    
                    if hasattr(verify_result, 'stderr') and verify_result.stderr:
                        log.error(f"容器删除验证失败: {verify_result.stderr}")
                        return {
                            "success": False,
                            "task_id": task_id,
                            "error": f"容器删除验证失败: {verify_result.stderr}"
                        }
                    
                    # 检查验证脚本输出
                    if hasattr(verify_result, 'stdout') and "容器验证成功" not in verify_result.stdout:
                        log.error(f"容器删除验证未通过: {verify_result.stdout}")
                        return {
                            "success": False,
                            "task_id": task_id,
                            "error": f"容器删除验证未通过: {verify_result.stdout}"
                        }
                    
                    log.info(f"Docker容器删除验证成功: {container_name}")
                    
                    # 清除数据库中的容器名称
                    with get_db() as db:
                        task = db.query(DBTestTask).filter(DBTestTask.task_id == task_id).first()
                        if task:
                            task.container_name = None
                            db.commit()
                            log.info(f"已清除数据库中的容器名称记录: {task_id}")
                    
                except Exception as e:
                    log.error(f"验证容器删除状态时出错: {str(e)}")
                    return {
                        "success": False,
                        "task_id": task_id,
                        "error": f"验证容器删除状态时出错: {str(e)}"
                    }
                
                log.info(f"Docker容器释放完成: {container_name}")
        
        return {
            "success": True,
            "task_id": task_id,
            "container_name": container_name,
            "result": {
                "stdout": result.stdout if hasattr(result, "stdout") else str(result),
                "stderr": result.stderr if hasattr(result, "stderr") else ""
            }
        }
    except Exception as e:
        log.error(f"释放Docker容器失败: {str(e)}")
        return {
            "success": False,
            "task_id": task_id,
            "error": str(e)
        }
