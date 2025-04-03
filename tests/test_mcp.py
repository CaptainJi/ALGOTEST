#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
MCP功能测试脚本
使用方法：python test_mcp.py
"""

import os
import json
import asyncio
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# 配置
ZHIPU_API_KEY = os.getenv("ZHIPU_API_KEY")
MCP_HOST = os.getenv("MCP_HOST", "172.16.100.108")
MCP_PORT = int(os.getenv("MCP_PORT", "2800"))

async def main():
    """主函数"""
    print("=== MCP功能测试 ===")
    print(f"MCP服务器地址: {MCP_HOST}:{MCP_PORT}")
    
    if not ZHIPU_API_KEY:
        print("警告: ZHIPU_API_KEY环境变量未设置，智谱AI功能将受限")
    
    try:
        # 动态导入以避免顶层导入错误
        from mcp.client.session import ClientSession
        from mcp.client.sse import sse_client
        import zhipuai
        
        zhipuai.api_key = ZHIPU_API_KEY
        
        print("\n正在测试MCP服务器连接...")
        try:
            # 建立SSE连接
            async with sse_client(f"http://{MCP_HOST}:{MCP_PORT}/sse") as (read, write):
                print("SSE连接成功！")
                
                # 创建客户端会话
                async with ClientSession(read, write) as session:
                    # 初始化连接
                    await session.initialize()
                    print("MCP会话初始化成功！")
                    
                    # 列出可用工具
                    tools = await session.list_tools()
                    print("\n可用工具:")
                    if hasattr(tools, 'tools'):
                        for tool in tools.tools:
                            print(f"- {tool.name}: {tool.description}")
                    else:
                        print("无法获取工具列表")
                    
                    # 交互式命令处理
                    print("\n输入自然语言命令进行测试 (输入'exit'退出):")
                    
                    while True:
                        command = input("\n> ").strip()
                        
                        if command.lower() in ["exit", "quit", "退出"]:
                            print("测试结束")
                            break
                        
                        if not command:
                            continue
                        
                        try:
                            # 使用智谱AI解析命令
                            print("正在调用智谱AI解析命令...")
                            
                            # 创建客户端
                            client = zhipuai.ZhipuAI(api_key=ZHIPU_API_KEY)
                            
                            # 构建提示词
                            tools_description = """
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
   - path (字符串, 可选): 要列出内容的目录路径，默认为当前目录
"""
                            
                            prompt = f"""
请将以下自然语言命令转换为JSON格式的执行策略。策略应包含要调用的工具名称和参数。

{tools_description}

自然语言命令: {command}

请以以下JSON格式返回:
{{
  "tool": "工具名称",
  "parameters": {{
    "参数名1": "参数值1",
    "参数名2": "参数值2"
  }},
  "description": "对这个操作的简短描述"
}}

只返回JSON，不要有其他文字。
"""
                            
                            # 调用模型
                            response = client.chat.completions.create(
                                model="glm-4",  # 使用GLM-4模型
                                messages=[
                                    {"role": "user", "content": prompt}
                                ],
                                temperature=0.1,
                                top_p=0.7,
                                max_tokens=1000,
                            )
                            
                            # 提取JSON响应
                            content = response.choices[0].message.content
                            print(f"智谱AI响应: {content[:100]}...")
                            
                            # 解析JSON
                            import re
                            import json
                            
                            # 尝试直接解析
                            try:
                                strategy = json.loads(content)
                            except json.JSONDecodeError as e:
                                # 尝试提取JSON部分
                                json_match = re.search(r'({[\s\S]*})', content)
                                if json_match:
                                    strategy = json.loads(json_match.group(1))
                                else:
                                    raise ValueError("无法从智谱AI响应中提取JSON")
                            
                            print(f"解析结果: {strategy}")
                            
                            # 执行命令
                            print("正在执行命令...")
                            result = await session.call_tool(strategy["tool"], strategy["parameters"])
                            
                            # 打印结果
                            print("\n=== 执行结果 ===")
                            print(f"工具: {strategy['tool']}")
                            print(f"参数: {strategy['parameters']}")
                            print("输出:")
                            print(result)
                            
                        except Exception as e:
                            print(f"命令执行失败: {str(e)}")
        
        except Exception as e:
            print(f"连接MCP服务器失败: {str(e)}")
            print("请确保MCP服务器正在运行，并检查MCP_HOST和MCP_PORT环境变量是否正确")
    
    except ImportError as e:
        print(f"导入错误: {str(e)}")
        print("请确保已安装所有依赖: pip install zhipuai mcp aiohttp")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n用户中断，程序退出") 