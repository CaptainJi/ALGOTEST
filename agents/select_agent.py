#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
文件作用：选择Agent模块，负责为测试用例选择合适的测试图片
开发规划：实现基于LangGraph、MCP和大模型的测试图片自动选择功能
"""

import os
import sys
import json
import asyncio
import time
import re
import logging
from typing import Dict, Any, List, Optional, Tuple, TypedDict, Union, Callable, cast
from datetime import datetime
from loguru import logger
from dotenv import load_dotenv

# 添加项目根目录到Python路径
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from langgraph.graph import StateGraph, END
from pydantic import BaseModel, Field

from mcp.client.session import ClientSession
from mcp.client.sse import sse_client

from core.config import get_settings, get_llm_config
from core.database import (
    get_db, 
    update_test_case_status,
    get_test_task,
    TestCase
)
from core.utils import generate_unique_id, format_timestamp, ensure_dir
from core.logger import get_logger
from core.mcp_config import get_mcp_config, get_cmd_format_path
import zhipuai

# 加载环境变量
load_dotenv()

# 获取带上下文的logger
log = get_logger("select_agent")

# 智谱AI配置
ZHIPU_API_KEY = os.getenv("ZHIPU_API_KEY")
ZHIPU_MODEL = os.getenv("ZHIPU_MODEL_CHAT", "glm-4-flash")
ZHIPU_API_BASE = os.getenv("ZHIPU_API_BASE", "https://open.bigmodel.cn/api/paas/v4")


# 定义Agent状态
class SelectAgentState(TypedDict):
    """图片选择Agent状态"""
    task_id: str                             # 任务ID
    dataset_url: Optional[str]               # 数据集URL
    label_data: Optional[str]                # 标签内容
    label_content_ready: bool                # 标签内容是否包含文件内容而非仅文件名
    label_files: Optional[List[str]]         # 标签文件路径列表
    test_cases: Optional[List[Dict[str, Any]]]  # 测试用例列表
    image_mapping: Optional[Dict[str, str]]  # 测试用例与图片的映射
    updated_count: Optional[int]             # 更新的测试用例数量
    errors: List[str]                        # 错误信息列表
    status: str                              # 当前状态
    attempt_count: int                       # 尝试次数


class CommandStrategy(BaseModel):
    """命令策略模型"""
    tool: str
    parameters: Dict[str, Any]
    description: Optional[str] = None


class CommandStrategies(BaseModel):
    """多个命令策略的集合"""
    strategies: List[CommandStrategy]
    description: Optional[str] = None


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
        
        # 是否开启详细日志
        self.verbose_logging = True
        
        zhipuai.api_key = self.api_key
        
    async def get_dataset_labels(self, dataset_url: str) -> CommandStrategy:
        """
        使用智谱AI分析数据集标签内容
        
        Args:
            dataset_url: 数据集URL路径
            
        Returns:
            解析后的命令策略
        """
        log = self.log
        log.info(f"准备分析数据集标签: {dataset_url}")

        # 构建系统提示词
        system_prompt = """你是一个专业的计算机视觉工程师，负责帮助用户分析数据集并选择合适的测试图片。
请根据用户提供的数据集路径，设计命令来查询标签文件夹中每个数据的标签内容。

你可以使用以下工具：
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
   - path (字符串, 必需): 要列出内容的目录路径

4. read_file - 读取文件内容
   参数:
   - file_path (字符串, 必需): 要读取的文件路径

请返回一个JSON对象，包含工具名称、参数和描述。确保命令能够有效地列出和分析数据集中的标签。"""

        # 构建用户提示词
        user_prompt = f"""
请帮我查询这个数据集路径下标签文件夹中每个数据的标签内容并返回:
{dataset_url}

数据集下有多个子文件夹，Images（存放图片）和Annotaions（存放标签）。请找到标签文件夹，然后分析其中的标签文件内容。

标签文件可能是.txt、.json、.xml等格式，通常与图片文件同名但扩展名不同。
例如：如果图片是 000001.jpg，对应的标签文件可能是 000001.txt 或 000001.json 等。

请返回一个最合适的命令来分析这些标签文件。重要：我需要获取标签文件的内容而不仅是文件列表。
"""

        log.debug(f"提示内容预览: {user_prompt[:500]}...")

        # 调用API
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
            # 使用默认命令
            default_command = f"find {dataset_url}/Annotations -name '*.txt' -o -name '*.json' -o -name '*.xml' | head -n 10 | xargs cat"
            return CommandStrategy(
                tool="execute_command", 
                parameters={"command": default_command}, 
                description="默认数据集标签分析命令（API调用失败）"
            )

        # 打印AI返回的原始数据
        log.info(f"模型返回内容: {content}")
        
        # 打印返回内容详情
        if len(content) > 1000:
            log.info(f"模型返回内容详情（前1000字符）:\n{content[:1000]}\n...\n（后100字符）:\n{content[-100:]}")
        else:
            log.info(f"模型返回内容详情完整内容:\n{content}")
        
        # 尝试解析返回的JSON
        log.info("尝试解析返回的JSON...")
        try:
            # 尝试提取JSON对象 {...}
            json_obj_match = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', content)
            if json_obj_match:
                json_content = json_obj_match.group(1)
            else:
                # 尝试直接匹配 {...} 格式
                json_obj_match = re.search(r'\{\s*"[^"]+"\s*:[\s\S]*?\}', content)
                if json_obj_match:
                    json_content = json_obj_match.group(0)
                else:
                    # 如果找不到JSON，使用默认命令
                    log.warning("无法从返回内容中提取JSON，使用默认命令")
                    default_command = f"find {dataset_url}/Annotations -name '*.txt' -o -name '*.json' -o -name '*.xml' | head -n 10 | xargs cat"
                    return CommandStrategy(
                        tool="execute_command", 
                        parameters={"command": default_command}, 
                        description="默认数据集标签分析命令（无法提取JSON）"
                    )
            
            # 解析JSON内容
            json_data = json.loads(json_content)
            return CommandStrategy(
                tool=json_data.get("tool", "execute_command"),
                parameters=json_data.get("parameters", {}),
                description=json_data.get("description", "未提供描述")
            )
            
        except Exception as e:
            log.error(f"解析JSON失败: {e}")
            # 使用默认命令
            default_command = f"find {dataset_url}/Annotations -name '*.txt' -o -name '*.json' -o -name '*.xml' | head -n 10 | xargs cat"
            return CommandStrategy(
                tool="execute_command", 
                parameters={"command": default_command}, 
                description="默认数据集标签分析命令（解析JSON失败）"
            )
            
    async def select_test_images(self, test_cases: List[Dict[str, Any]], label_data: str) -> Dict[str, str]:
        """
        使用智谱AI为测试用例选择合适的测试图片
        
        Args:
            test_cases: 测试用例列表
            label_data: 数据集标签内容
            
        Returns:
            测试用例ID与对应测试图片路径的映射
        """
        log = self.log
        log.info(f"准备为{len(test_cases)}个测试用例选择测试图片")

        # 准备测试用例信息
        test_cases_info = []
        for case in test_cases:
            # 提取测试用例详细信息
            input_data = {}
            try:
                if case.get("input_data"):
                    if isinstance(case.get("input_data"), str):
                        input_data = json.loads(case.get("input_data"))
                    else:
                        input_data = case.get("input_data")
            except Exception as e:
                log.warning(f"解析测试用例输入数据失败: {e}")
            
            # 从input_data中提取测试目的和名称
            test_purpose = ""
            test_name = ""
            if input_data:
                test_purpose = input_data.get("purpose", "")
                test_name = input_data.get("name", "")
                
                # 如果input_data中没有name或purpose，尝试从details中提取
                if not test_purpose and input_data.get("details"):
                    if isinstance(input_data.get("details"), dict):
                        test_purpose = input_data.get("details").get("测试目的", "")
                    elif isinstance(input_data.get("details"), str):
                        try:
                            details = json.loads(input_data.get("details"))
                            test_purpose = details.get("测试目的", "")
                        except:
                            pass
            
            case_info = {
                "case_id": case.get("case_id"),
                "name": test_name or "未命名测试用例",
                "purpose": test_purpose or "未指定测试目的",
                "input_data": input_data
            }
            test_cases_info.append(case_info)
        
        # 构建系统提示词
        system_prompt = """你是一个专业的计算机视觉测试工程师，负责为测试用例选择最合适的测试图片。
请根据提供的数据集标签内容和测试用例信息，为每个测试用例选择一张最合适的测试图片。

标签内容已按照文件名分组，每个图片的标注内容都清晰列出。请仔细分析每个图片的对象和边界框信息，
选择与测试用例目的最匹配的图片。注意识别图片中的对象类型、数量和位置等特征。

对于每个测试用例，请分析其测试目的和要求，然后从数据集中选择一张最能满足测试需求的图片。
返回JSON格式，包含测试用例ID和对应的图片文件名映射。

重要：只返回图片文件名（如 000001.jpg），不要包含任何路径信息。"""

        # 构建用户提示词
        user_prompt = f"""
## 数据集标签内容：
{label_data[:20000] if len(label_data) > 20000 else label_data}

## 测试用例信息：
{json.dumps(test_cases_info, indent=2, ensure_ascii=False)}

请为每个测试用例选择一张最合适的测试图片。对于每个测试用例，分析其测试目的和要求，然后从数据集中选择最适合的图片。
注意分析每个图片的标注内容，包括对象类型、数量、尺寸和位置信息。

请返回JSON格式，格式如下：
```json
{{
  "case_id1": "000001.jpg",
  "case_id2": "000002.jpg",
  ...
}}
```

重要说明：
1. 只返回图片文件名，不要包含任何路径信息
2. 请确保返回的是有效的JSON格式
3. 如果无法确定某个测试用例的合适图片，请使用默认图片："default.jpg"
"""

        # 调用API
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
            # 返回默认映射
            default_mapping = {}
            for case in test_cases:
                default_mapping[case.get("case_id")] = "default.jpg"
            return default_mapping

        # 打印AI返回的原始数据
        log.info(f"模型返回内容: {content}")
        
        # 打印返回内容详情
        if len(content) > 1000:
            log.info(f"模型返回内容详情（前1000字符）:\n{content[:1000]}\n...\n（后100字符）:\n{content[-100:]}")
        else:
            log.info(f"模型返回内容详情完整内容:\n{content}")
        
        # 尝试解析返回的JSON
        try:
            # 尝试提取JSON对象 {...}
            json_obj_match = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', content)
            if json_obj_match:
                json_content = json_obj_match.group(1)
            else:
                # 尝试直接匹配 {...} 格式
                json_obj_match = re.search(r'\{\s*"[^"]+"\s*:[\s\S]*?\}', content)
                if json_obj_match:
                    json_content = json_obj_match.group(0)
                else:
                    # 如果找不到JSON，使用默认映射
                    log.warning("无法从返回内容中提取JSON，使用默认映射")
                    default_mapping = {}
                    for case in test_cases:
                        default_mapping[case.get("case_id")] = "default.jpg"
                    return default_mapping
            
            # 处理可能的转义问题 - 将反斜杠替换为正斜杠以确保一致性
            json_content = json_content.replace('\\\\', '/').replace('\\', '/')
            
            # 解析JSON内容
            try:
                image_mapping = json.loads(json_content)
            except json.JSONDecodeError as e:
                log.error(f"JSON解析失败，尝试修复: {e}")
                # 尝试修复常见的JSON格式问题
                json_content = re.sub(r'([^\\])\\([^\\"/bfnrtu])', r'\1\\\\\2', json_content)
                try:
                    image_mapping = json.loads(json_content)
                except json.JSONDecodeError as e2:
                    log.error(f"JSON修复后仍解析失败: {e2}")
                    # 最后尝试直接提取键值对
                    image_mapping = {}
                    pairs = re.findall(r'"([^"]+)"\s*:\s*"([^"]+)"', json_content)
                    for case_id, path in pairs:
                        image_mapping[case_id] = path
            
            # 确保所有路径符合格式要求 (使用正斜杠格式)
            formatted_mapping = {}
            for case_id, path in image_mapping.items():
                # 提取文件名
                if '/' in path:
                    filename = path.split('/')[-1]
                elif '\\' in path:
                    filename = path.split('\\')[-1]
                else:
                    filename = path
                
                # 确保文件名格式正确
                if not filename.lower().endswith(('.jpg', '.jpeg', '.png', '.bmp')):
                    # 如果没有扩展名，添加默认的.jpg扩展名
                    filename = f"{filename}.jpg"
                
                # 只使用文件名部分，不包含路径
                formatted_mapping[case_id] = filename
                log.info(f"测试用例 {case_id} 的图片: {filename} -> 格式化路径: {formatted_mapping[case_id]}")
            
            return formatted_mapping
            
        except Exception as e:
            log.error(f"解析JSON失败: {e}")
            # 返回默认映射
            default_mapping = {}
            for case in test_cases:
                default_mapping[case.get("case_id")] = "default.jpg"
            return default_mapping


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
        执行命令策略
        
        Args:
            strategy: 命令策略对象
            
        Returns:
            执行结果
        """
        if not self.session:
            raise Exception("未连接到MCP服务器")
        
        try:
            log.info(f"执行命令策略: {strategy.tool}")
            
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
    
    async def analyze_dataset_labels(self, dataset_url: str) -> Dict[str, Any]:
        """
        分析数据集标签内容
        
        Args:
            dataset_url: 数据集URL路径
            
        Returns:
            分析结果
        """
        if not self.session:
            raise Exception("未连接到MCP服务器")
        
        try:
            log.info(f"开始分析数据集标签: {dataset_url}")
            
            # 使用智谱AI生成分析数据集标签的命令
            strategy = await self.ai_client.get_dataset_labels(dataset_url)
            
            log.info(f"获取到分析命令: {strategy.tool} - {strategy.parameters}")
            
            # 执行命令
            result = await self.execute_strategy(strategy)
            
            if not result.get("success"):
                raise Exception(f"执行命令失败: {result.get('error')}")
            
            # 从result中提取标签内容
            command_result = result.get("result")
            label_content = ""
            
            if hasattr(command_result, "stdout"):
                label_content = command_result.stdout
            elif hasattr(command_result, "text"):
                label_content = command_result.text
            else:
                label_content = str(command_result)
            
            log.info(f"成功获取标签内容，长度: {len(label_content)} 字符")
            
            # 输出标签内容的详细信息
            if len(label_content) > 1000:
                log.info(f"标签内容预览（前1000字符）:\n{label_content[:1000]}\n...\n（后100字符）:\n{label_content[-100:]}")
            else:
                log.info(f"标签内容完整内容:\n{label_content}")
            
            # 将标签内容保存到文件，便于查看完整内容
            try:
                timestamp = int(time.time())
                labels_dir = os.path.join("data", "labels")
                os.makedirs(labels_dir, exist_ok=True)
                labels_file = os.path.join(labels_dir, f"labels_mcp_{timestamp}.txt")
                with open(labels_file, "w", encoding="utf-8") as f:
                    f.write(label_content)
                log.info(f"标签内容已保存到文件: {labels_file}")
            except Exception as e:
                log.warning(f"保存标签内容到文件失败: {e}")
            
            return {
                "success": True,
                "label_content": label_content
            }
            
        except Exception as e:
            log.error(f"分析数据集标签失败: {e}")
            return {
                "success": False,
                "error": str(e)
            }


# 工具函数
async def execute_mcp_command(strategy: Union[CommandStrategy, Dict[str, Any]]) -> Any:
    """执行MCP命令的通用函数"""
    # 从配置获取SSE URL
    sse_url = get_mcp_config()["sse_url"]
    
    # 连接到MCP服务器并执行命令
    async with sse_client(sse_url) as (read, write):
        async with ClientSession(read, write) as session:
            # 初始化连接
            await session.initialize()
            
            # 执行命令
            if isinstance(strategy, dict):
                tool = strategy.get("tool")
                parameters = strategy.get("parameters", {})
            else:
                tool = strategy.tool
                parameters = strategy.parameters
                
            result = await session.call_tool(tool, parameters)
            return result

def extract_content_from_result(result: Any) -> str:
    """从执行结果中提取内容"""
    if hasattr(result, "stdout"):
        return result.stdout
    elif hasattr(result, "text"):
        return result.text
    else:
        return str(result)

def parse_label_files(content: str) -> List[str]:
    """从内容中解析出标签文件列表"""
    files = []
    lines = content.splitlines()
    for line in lines:
        # 匹配各种可能的标签文件
        match = re.search(r'(\S+\.xml|\S+\.json|\S+\.txt)$', line.strip())
        if match:
            files.append(match.group(1))
    return files

def is_file_content(content: str) -> bool:
    """
    判断获取的内容是否为文件具体内容而不仅是文件列表
    
    简单判断方法：
    1. 如果包含XML标签如<annotation>、<object>等，可能是XML标签文件内容
    2. 如果包含JSON格式的内容，可能是JSON标签文件内容
    3. 如果只包含文件列表（如ls命令输出），则不是文件内容
    """
    # 检查是否包含XML标签
    xml_patterns = ['<annotation>', '<object>', '<name>', '<bndbox>']
    for pattern in xml_patterns:
        if pattern in content:
            return True
            
    # 检查是否包含JSON格式内容
    json_patterns = ['"bbox":', '"category_id":', '"segmentation":']
    for pattern in json_patterns:
        if pattern in content:
            return True
            
    # 判断是否仅包含文件列表
    file_list_patterns = ['Annotations', 'Images', '.xml', '.json', '.txt']
    lines = content.strip().split('\n')
    if len(lines) < 20 and all(any(p in line for p in file_list_patterns) for line in lines):
        return False
        
    # 默认情况
    return len(content) > 1000  # 如果内容较长，可能是文件内容

def organize_label_content(content: str) -> str:
    """
    整理标签文件内容，按文件名分组，方便大模型理解
    
    Args:
        content: 原始标签内容
        
    Returns:
        整理后的标签内容
    """
    log.info("开始整理标签文件内容，按文件名分组")
    
    # 检查内容类型
    if "<annotation>" not in content:
        log.warning("标签内容不是XML格式，跳过整理")
        return content
        
    # 按<annotation>标签分割内容
    annotations = re.findall(r'<annotation>[\s\S]*?</annotation>', content)
    log.info(f"共找到{len(annotations)}个标注块")
    
    if not annotations:
        return content
    
    # 提取每个标注的文件名和内容
    organized_content = "# 标签内容按文件名整理\n\n"
    files_info = {}
    
    for annotation in annotations:
        # 提取文件名
        filename_match = re.search(r'<filename>(.*?)</filename>', annotation)
        if not filename_match:
            continue
            
        filename = filename_match.group(1)
        
        # 将标注内容添加到对应文件名下
        if filename not in files_info:
            files_info[filename] = []
        
        files_info[filename].append(annotation)
    
    # 按文件名组织内容
    for filename, anns in files_info.items():
        organized_content += f"## 文件: {filename}\n\n"
        for i, ann in enumerate(anns):
            organized_content += f"### 标注 {i+1}\n```xml\n{ann}\n```\n\n"
    
    log.info(f"整理完成，共处理了{len(files_info)}个不同的文件")
    return organized_content


# LangGraph节点函数

async def get_task_info(state: SelectAgentState) -> SelectAgentState:
    """获取任务信息和数据集URL"""
    try:
        log.info(f"获取任务 {state['task_id']} 的信息")
        
        # 从数据库获取任务信息
        task = get_test_task(state["task_id"])
        if not task:
            raise ValueError(f"测试任务不存在: {state['task_id']}")
        
        # 获取数据集URL
        dataset_url = task.dataset_url
        if not dataset_url:
            # 如果数据集URL为空，使用默认值
            dataset_url = "/data"
            log.warning(f"任务未设置数据集URL，使用默认路径: {dataset_url}")
        
        log.info(f"获取到任务数据集URL: {dataset_url}")
        
        # 更新状态
        return {
            **state,
            "dataset_url": dataset_url,
            "status": "task_info_ready"
        }
    except Exception as e:
        error_msg = f"获取任务信息失败: {str(e)}"
        log.error(error_msg)
        return {
            **state,
            "errors": state.get("errors", []) + [error_msg],
            "status": "error"
        }

async def list_label_files(state: SelectAgentState) -> SelectAgentState:
    """列出数据集中的标签文件"""
    try:
        log.info(f"开始列出数据集标签文件: {state['dataset_url']}")
        dataset_url = state["dataset_url"]
        
        # 使用智谱AI生成分析数据集标签的命令
        ai_client = ZhipuAIClient()
        strategy = await ai_client.get_dataset_labels(dataset_url)
        
        log.info(f"获取到分析命令: {strategy.tool} - {strategy.parameters}")
        
        # 执行命令
        result = await execute_mcp_command(strategy)
        label_data = extract_content_from_result(result)
        
        # 判断是否获取到了文件内容
        is_content = is_file_content(label_data)
        
        # 如果只获取到文件列表，则解析出标签文件
        label_files = []
        if not is_content:
            label_files = parse_label_files(label_data)
            log.info(f"解析出 {len(label_files)} 个标签文件")
        
        # 输出标签内容的详细信息
        if len(label_data) > 1000:
            log.info(f"标签内容预览（前1000字符）:\n{label_data[:1000]}\n...\n（后100字符）:\n{label_data[-100:]}")
        else:
            log.info(f"标签内容完整内容:\n{label_data}")
        
        # 将标签内容保存到文件，便于查看完整内容
        task_id = state["task_id"]
        try:
            labels_dir = os.path.join("data", "labels")
            os.makedirs(labels_dir, exist_ok=True)
            labels_file = os.path.join(labels_dir, f"labels_{task_id}_{int(time.time())}.txt")
            with open(labels_file, "w", encoding="utf-8") as f:
                f.write(label_data)
            log.info(f"标签内容已保存到文件: {labels_file}")
        except Exception as e:
            log.warning(f"保存标签内容到文件失败: {e}")
        
        # 更新状态
        return {
            **state,
            "label_data": label_data,
            "label_files": label_files,
            "label_content_ready": is_content,
            "attempt_count": state.get("attempt_count", 0) + 1,
            "status": "label_files_ready" if not is_content else "label_content_ready"
        }
    except Exception as e:
        error_msg = f"列出标签文件失败: {str(e)}"
        log.error(error_msg)
        return {
            **state,
            "errors": state.get("errors", []) + [error_msg],
            "status": "error"
        }

async def read_label_file_contents(state: SelectAgentState) -> SelectAgentState:
    """读取标签文件的具体内容"""
    try:
        log.info("开始读取标签文件具体内容")
        
        # 获取标签文件列表
        label_files = state.get("label_files", [])
        if not label_files:
            raise ValueError("未找到标签文件")
        
        # 选择一部分文件进行内容读取
        files_to_read = label_files[:5]  # 读取前5个文件
        log.info(f"将读取以下标签文件: {files_to_read}")
        
        # 构建读取文件内容的命令
        dataset_url = state["dataset_url"]
        
        # 构建命令 - 假设文件在Annotations目录下
        file_paths = []
        for file in files_to_read:
            possible_paths = [
                f"{dataset_url}/Annotations/{file}",
                f"{dataset_url}/annotations/{file}",
                f"{dataset_url}/labels/{file}",
                f"{dataset_url}/Labels/{file}",
                f"{dataset_url}/{file}"
            ]
            file_paths.extend(possible_paths)
        
        # 尝试查找文件
        find_command = f"find {dataset_url} -name " + " -o -name ".join([f"'{file}'" for file in files_to_read])
        find_strategy = CommandStrategy(
            tool="execute_command",
            parameters={"command": find_command}
        )
        
        find_result = await execute_mcp_command(find_strategy)
        find_output = extract_content_from_result(find_result)
        
        # 如果找到了文件，使用这些路径
        found_paths = []
        if find_output:
            found_paths = [line.strip() for line in find_output.splitlines() if line.strip()]
            log.info(f"找到以下标签文件路径: {found_paths}")
        
        # 如果没有找到文件，使用默认路径
        if not found_paths:
            found_paths = file_paths[:5]  # 只使用前5个可能的路径
            log.warning(f"未找到标签文件，将使用默认路径: {found_paths}")
        
        # 读取文件内容
        cat_command = f"cat {' '.join(found_paths)} 2>/dev/null || echo '无法读取文件'"
        cat_strategy = CommandStrategy(
            tool="execute_command",
            parameters={"command": cat_command}
        )
        
        cat_result = await execute_mcp_command(cat_strategy)
        file_contents = extract_content_from_result(cat_result)
        
        # 检查是否读取到文件内容
        if len(file_contents) < 10 or "无法读取文件" in file_contents:
            log.warning("未能读取到文件内容，尝试使用find命令查找所有可能的标签文件")
            
            # 使用find命令查找所有可能的标签文件
            find_all_command = f"find {dataset_url} -name '*.xml' -o -name '*.json' -o -name '*.txt' | head -n 5"
            find_all_strategy = CommandStrategy(
                tool="execute_command",
                parameters={"command": find_all_command}
            )
            
            find_all_result = await execute_mcp_command(find_all_strategy)
            all_paths = extract_content_from_result(find_all_result)
            
            if all_paths:
                # 读取找到的文件内容
                paths_list = [path.strip() for path in all_paths.splitlines() if path.strip()]
                cat_all_command = f"cat {' '.join(paths_list)} 2>/dev/null || echo '无法读取文件'"
                cat_all_strategy = CommandStrategy(
                    tool="execute_command",
                    parameters={"command": cat_all_command}
                )
                
                cat_all_result = await execute_mcp_command(cat_all_strategy)
                file_contents = extract_content_from_result(cat_all_result)
        
        # 判断是否成功读取到文件内容
        is_content = is_file_content(file_contents)
        
        if not is_content:
            log.warning("仍未能获取标签文件内容，将进行最后一次尝试")
            
            # 最后一次尝试 - 直接使用find和xargs cat
            final_command = f"find {dataset_url} -name '*.xml' -o -name '*.json' -o -name '*.txt' | head -n 5 | xargs cat 2>/dev/null || echo '无法读取文件'"
            final_strategy = CommandStrategy(
                tool="execute_command",
                parameters={"command": final_command}
            )
            
            final_result = await execute_mcp_command(final_strategy)
            file_contents = extract_content_from_result(final_result)
            is_content = is_file_content(file_contents)
        
        # 输出文件内容的详细信息
        if len(file_contents) > 1000:
            log.info(f"文件内容预览（前1000字符）:\n{file_contents[:1000]}\n...\n（后100字符）:\n{file_contents[-100:]}")
        else:
            log.info(f"文件内容完整内容:\n{file_contents}")
        
        # 将文件内容保存到文件，便于查看完整内容
        task_id = state["task_id"]
        try:
            contents_dir = os.path.join("data", "labels")
            os.makedirs(contents_dir, exist_ok=True)
            contents_file = os.path.join(contents_dir, f"contents_{task_id}_{int(time.time())}.txt")
            with open(contents_file, "w", encoding="utf-8") as f:
                f.write(file_contents)
            log.info(f"文件内容已保存到文件: {contents_file}")
        except Exception as e:
            log.warning(f"保存文件内容到文件失败: {e}")
        
        # 更新状态
        return {
            **state,
            "label_data": file_contents if is_content else state["label_data"],
            "label_content_ready": is_content,
            "attempt_count": state.get("attempt_count", 0) + 1,
            "status": "label_content_ready" if is_content else "label_content_failed"
        }
    except Exception as e:
        error_msg = f"读取标签文件内容失败: {str(e)}"
        log.error(error_msg)
        return {
            **state,
            "errors": state.get("errors", []) + [error_msg],
            "status": "error"
        }

async def get_test_cases(state: SelectAgentState) -> SelectAgentState:
    """获取任务的所有测试用例"""
    try:
        log.info(f"开始获取任务 {state['task_id']} 的测试用例")
        
        task_id = state["task_id"]
        with get_db() as db:
            test_cases = db.query(TestCase).filter(TestCase.task_id == task_id).all()
            
            if not test_cases:
                raise ValueError(f"任务中没有测试用例: {task_id}")
            
            # 将测试用例转换为字典
            test_cases_dict = []
            for case in test_cases:
                # 将ORM对象转换为字典
                case_dict = {
                    "case_id": case.case_id,
                    "input_data": case.input_data
                }
                test_cases_dict.append(case_dict)
            
            log.info(f"获取到 {len(test_cases_dict)} 个测试用例")
        
        # 更新状态
        return {
            **state,
            "test_cases": test_cases_dict,
            "status": "test_cases_ready"
        }
    except Exception as e:
        error_msg = f"获取测试用例失败: {str(e)}"
        log.error(error_msg)
        return {
            **state,
            "errors": state.get("errors", []) + [error_msg],
            "status": "error"
        }

async def select_test_images_node(state: SelectAgentState) -> SelectAgentState:
    """为测试用例选择测试图片（节点函数）"""
    try:
        log.info("开始为测试用例选择合适的测试图片")
        
        test_cases = state.get("test_cases", [])
        label_data = state.get("label_data", "")
        
        if not test_cases:
            raise ValueError("没有测试用例")
        
        if not label_data:
            raise ValueError("标签数据为空")
            
        # 整理标签内容，按文件名分组
        organized_label_data = organize_label_content(label_data)
        
        # 将整理后的标签内容保存到文件
        task_id = state["task_id"]
        try:
            timestamp = generate_unique_id()
            labels_dir = os.path.join("data", "labels")
            os.makedirs(labels_dir, exist_ok=True)
            organized_file = os.path.join(labels_dir, f"labels_{task_id}_{timestamp}_organized.txt")
            with open(organized_file, "w", encoding="utf-8") as f:
                f.write(organized_label_data)
            log.info(f"整理后的标签内容已保存到文件: {organized_file}")
        except Exception as e:
            log.warning(f"保存整理后的标签内容失败: {e}")
        
        # 使用大模型为测试用例选择合适的测试图片
        ai_client = ZhipuAIClient()
        image_mapping = await ai_client.select_test_images(test_cases, organized_label_data)
        
        log.info(f"成功选择测试图片，共 {len(image_mapping)} 个映射")
        
        # 保存原始结果用于记录
        original_mapping = image_mapping.copy()
        
        # 输出详细的映射结果
        log.info("测试用例与图片的原始映射结果:")
        for case_id, filename in original_mapping.items():
            log.info(f"  - 测试用例ID: {case_id} -> 图片文件名: {filename}")
        
        # 将映射结果保存到文件
        try:
            mapping_dir = os.path.join("data", "mappings")
            os.makedirs(mapping_dir, exist_ok=True)
            mapping_file = os.path.join(mapping_dir, f"mapping_{task_id}_{int(time.time())}.json")
            with open(mapping_file, "w", encoding="utf-8") as f:
                json.dump(original_mapping, f, ensure_ascii=False, indent=2)
            log.info(f"映射结果已保存到文件: {mapping_file}")
        except Exception as e:
            log.warning(f"保存映射结果到文件失败: {e}")
        
        # 更新状态
        return {
            **state,
            "image_mapping": original_mapping,
            "status": "images_selected"
        }
    except Exception as e:
        error_msg = f"选择测试图片失败: {str(e)}"
        log.error(error_msg)
        return {
            **state,
            "errors": state.get("errors", []) + [error_msg],
            "status": "error"
        }

async def update_database(state: SelectAgentState) -> SelectAgentState:
    """更新测试用例的test_data字段"""
    try:
        log.info("开始更新测试用例的test_data字段")
        
        image_mapping = state.get("image_mapping", {})
        if not image_mapping:
            raise ValueError("没有图片映射数据")
        
        with get_db() as db:
            updated_count = 0
            for case_id, filename in image_mapping.items():
                case = db.query(TestCase).filter(TestCase.case_id == case_id).first()
                if case:
                    # 确保文件名格式正确
                    if not filename.lower().endswith(('.jpg', '.jpeg', '.png', '.bmp')):
                        # 如果没有扩展名，添加默认的.jpg扩展名
                        filename = f"{filename}.jpg"
                    
                    # 使用标准格式：data/Images/图片名.jpg
                    final_path = f"data/Images/{filename}"
                    
                    # 更新数据库
                    case.test_data = final_path
                    updated_count += 1
                    log.info(f"更新测试用例 {case_id} 的test_data: {final_path}")
            
            db.commit()
            log.info(f"成功更新 {updated_count} 个测试用例的测试数据")
        
        # 更新状态
        return {
            **state,
            "updated_count": updated_count,
            "status": "completed"
        }
    except Exception as e:
        error_msg = f"更新测试用例测试数据失败: {str(e)}"
        log.error(error_msg)
        return {
            **state,
            "errors": state.get("errors", []) + [error_msg],
            "status": "error"
        }

def build_selection_workflow() -> StateGraph:
    """构建测试图片选择工作流图"""
    log.info("构建测试图片选择工作流图")
    
    # 创建工作流图
    workflow = StateGraph(SelectAgentState)
    
    # 添加节点
    workflow.add_node("get_task_info", get_task_info)
    workflow.add_node("list_label_files", list_label_files)
    workflow.add_node("read_label_file_contents", read_label_file_contents)
    workflow.add_node("get_test_cases", get_test_cases)
    workflow.add_node("select_test_images", select_test_images_node)  # 注意这里使用了重命名的节点函数
    workflow.add_node("update_database", update_database)
    
    # 设置入口点
    workflow.set_entry_point("get_task_info")
    
    # 从get_task_info出发的基本边
    workflow.add_edge("get_task_info", "list_label_files")
    
    # 从list_label_files出发的条件边
    workflow.add_conditional_edges(
        "list_label_files",
        lambda state: 
            "read_label_file_contents" if not state.get("label_content_ready", False) and state.get("attempt_count", 0) < 3
            else "get_test_cases" if state.get("label_content_ready", False)
            else END,
        {
            "read_label_file_contents": "read_label_file_contents",
            "get_test_cases": "get_test_cases",
            END: END
        }
    )
    
    # 从read_label_file_contents出发的条件边
    workflow.add_conditional_edges(
        "read_label_file_contents",
        lambda state: 
            "get_test_cases" if state.get("label_content_ready", False)
            else END if state.get("attempt_count", 0) >= 3
            else "read_label_file_contents",
        {
            "get_test_cases": "get_test_cases",
            "read_label_file_contents": "read_label_file_contents",
            END: END
        }
    )
    
    # 从get_test_cases到select_test_images的基本边
    workflow.add_edge("get_test_cases", "select_test_images")
    
    # 从select_test_images到update_database的基本边
    workflow.add_edge("select_test_images", "update_database")
    
    # 从update_database到终点的基本边
    workflow.add_edge("update_database", END)
    
    return workflow


async def run_selection_workflow(task_id: str) -> Dict[str, Any]:
    """运行测试图片选择工作流"""
    log.info(f"开始为任务 {task_id} 运行测试图片选择工作流")
    
    # 构建工作流图
    workflow = build_selection_workflow()
    
    # 编译工作流
    app = workflow.compile()
    
    # 初始状态
    initial_state = {
        "task_id": task_id,
        "dataset_url": None,
        "label_data": None,
        "label_content_ready": False,
        "label_files": [],
        "test_cases": None,
        "image_mapping": None,
        "updated_count": None,
        "errors": [],
        "status": "initialized",
        "attempt_count": 0
    }
    
    try:
        # 执行工作流
        log.info("开始执行工作流")
        result = await app.ainvoke(initial_state)
        log.info(f"工作流执行完毕，状态: {result.get('status')}")
        
        # 检查结果是否成功
        if result.get("status") == "error" or result.get("status") == "label_content_failed":
            log.error(f"执行工作流失败: {', '.join(result.get('errors', []))}")
            return {
                "success": False,
                "task_id": task_id,
                "errors": result.get("errors", [])
            }
            
        if result.get("status") == "completed":
            log.info(f"工作流成功完成，更新了 {result.get('updated_count', 0)} 个测试用例")
            return {
                "success": True,
                "task_id": task_id,
                "updated_count": result.get("updated_count", 0),
                "image_mapping": result.get("image_mapping", {})
            }
        
        # 异常状态
        log.warning(f"工作流未正常完成，状态: {result.get('status')}")
        return {
            "success": False,
            "task_id": task_id,
            "status": result.get("status"),
            "errors": result.get("errors", []) + ["工作流未正常完成"]
        }
        
    except Exception as e:
        log.error(f"执行工作流异常: {str(e)}")
        return {
            "success": False,
            "task_id": task_id,
            "errors": [str(e)]
        }


async def select_test_images(task_id: str) -> Dict[str, Any]:
    """
    为任务中的测试用例选择合适的测试图片 (主函数)
    
    Args:
        task_id: 任务ID
        
    Returns:
        选择结果
    """
    log.info(f"开始为任务 {task_id} 的测试用例选择测试图片")
    
    # 直接调用工作流
    result = await run_selection_workflow(task_id)
    return result


# 主函数
async def main():
    """主函数"""
    # 如果从命令行运行，支持传入任务ID
    import sys
    
    log.info("====== 图片选择Agent开始执行 ======")
    log.info(f"Python版本: {sys.version}")
    log.info(f"当前工作目录: {os.getcwd()}")
    
    # 检查环境变量
    log.info(f"智谱AI API Key: {'已设置' if ZHIPU_API_KEY else '未设置'}")
    log.info(f"智谱AI 模型: {ZHIPU_MODEL}")
    
    if len(sys.argv) > 1:
        task_id = sys.argv[1]
        log.info(f"接收到任务ID: {task_id}")
        
        try:
            # 检查数据库连接
            with get_db() as db:
                test_task = get_test_task(task_id)
                if not test_task:
                    log.error(f"未找到任务ID为 {task_id} 的任务")
                    return
                
                log.info(f"成功找到任务: {test_task.task_name if hasattr(test_task, 'task_name') else task_id}")
                log.info(f"数据集URL: {test_task.dataset_url or '未设置'}")
            
            # 运行选择测试图片过程
            log.info("开始执行图片选择工作流...")
            result = await select_test_images(task_id)
            
            # 详细输出结果
            if result.get("success"):
                log.info("====== 图片选择工作流执行成功 ======")
                log.info(f"成功为任务 {task_id} 选择测试图片")
                log.info(f"已更新 {result.get('updated_count')} 个测试用例")
                
                # 输出部分映射示例
                image_mapping = result.get("image_mapping", {})
                if image_mapping:
                    sample_count = min(5, len(image_mapping))
                    log.info(f"选择的图片映射示例 (显示 {sample_count} 个):")
                    for i, (case_id, image_path) in enumerate(list(image_mapping.items())[:sample_count]):
                        log.info(f"  {i+1}. 测试用例 {case_id}: {image_path}")
            else:
                log.error("====== 图片选择工作流执行失败 ======")
                errors = result.get("errors", ["未知错误"])
                log.error(f"错误详情: {', '.join(errors)}")
                status = result.get("status", "未知状态")
                log.error(f"工作流状态: {status}")
                
                # 检查特定阶段的失败
                if status == "error" and any("JSON" in err for err in errors):
                    log.error("检测到JSON解析错误，请检查模型返回的内容格式")
                elif status == "label_content_failed":
                    log.error("无法获取标签内容，请检查数据集路径和标签文件是否存在")
        except Exception as e:
            log.exception(f"执行过程中发生异常: {str(e)}")
    else:
        log.error("请提供任务ID参数，用法: python select_agent.py <task_id>")
    
    log.info("====== 图片选择Agent执行结束 ======")


if __name__ == "__main__":
    try:
        # 使用asyncio运行主函数
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("用户中断执行")
    except Exception as e:
        log.exception(f"执行main函数时发生未捕获的异常: {str(e)}")
    finally:
        log.info("程序退出") 