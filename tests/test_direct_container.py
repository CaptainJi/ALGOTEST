#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
直接使用指定容器名称执行命令的测试脚本
"""

import os
import sys
import asyncio
import json
from typing import Dict, Any

# 添加项目根目录到Python路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.execution_agent import ZhipuAIClient, MCPClient
from core.logger import get_logger

# 获取日志记录器
log = get_logger("test_direct_container")

async def execute_with_timeout(client, command, timeout=60):
    """带超时机制的命令执行"""
    try:
        # 创建超时任务
        return await asyncio.wait_for(client.execute_command(command), timeout=timeout)
    except asyncio.TimeoutError:
        log.error(f"命令执行超时（{timeout}秒）")
        return {"success": False, "error": f"命令执行超时（{timeout}秒）"}

async def test_direct_container_command():
    """使用指定容器名称执行命令的测试函数"""
    # 指定的容器名称
    container_name = "algotest_TASK1742525623_6049f9a3d00c"
    
    # 使用json模块生成参数
    params = {"draw_confidence": True}
    params_json = json.dumps(params)
    
    # 构建完整命令
    command = "docker exec {} ./ev_sdk/bin/test-ji-api -f 1 -i /data/000000.jpg -o ./output.jpg -a '{}'".format(
        container_name, params_json
    )
    
    # 输出测试信息
    log.info("开始使用指定容器名称执行测试")
    log.info(f"容器名称: {container_name}")
    log.info(f"测试命令: {command}")
    

    
    try:
        # 从环境变量获取MCP服务器配置
        from dotenv import load_dotenv
        load_dotenv()
        
        mcp_host = os.getenv("MCP_HOST", "172.16.100.108")
        mcp_port = int(os.getenv("MCP_PORT", "2800"))
        
        # 创建MCP客户端
        client = MCPClient(mcp_host, mcp_port)
        
        # 尝试连接到MCP服务器
        log.info(f"正在连接到MCP服务器: {mcp_host}:{mcp_port}")
        
        # 执行命令
        log.info("准备执行命令...")
        
        # 连接服务器
        print("\n正在连接MCP服务器...")
        connection_success = await asyncio.wait_for(client.connect(), timeout=10)
        if not connection_success:
            log.error("无法连接到MCP服务器")
            return False
        
        print("连接成功，准备执行命令...")
        
        try:
            # 执行命令，添加30秒超时
            print(f"执行命令: {command}")
            result = await execute_with_timeout(client, command, timeout=30)
            
            # 检查执行结果
            if result.get("success"):
                log.success("命令执行成功!")
                print("命令执行成功!")
                # 输出结果详情
                if "result" in result:
                    if hasattr(result["result"], "stdout"):
                        output = result["result"].stdout
                        log.info(f"命令输出: {output}")
                        print(f"命令输出: {output}")
                    if hasattr(result["result"], "stderr") and result["result"].stderr:
                        error = result["result"].stderr
                        log.warning(f"命令错误: {error}")
                        print(f"命令错误: {error}")
                return True
            else:
                error_msg = result.get("error", "未知错误")
                log.error(f"命令执行失败: {error_msg}")
                print(f"命令执行失败: {error_msg}")
                return False
                
        finally:
            # 断开连接
            print("正在断开MCP连接...")
            await client.disconnect()
            log.info("已断开MCP服务器连接")
            print("已断开MCP服务器连接")
        
    except Exception as e:
        log.error(f"测试过程中出错: {str(e)}")
        print(f"测试过程中出错: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    # 运行测试
    print("开始测试直接使用指定容器名称执行命令...")
    
    try:
        # 运行异步测试函数，设置总超时时间为120秒
        result = asyncio.run(asyncio.wait_for(test_direct_container_command(), timeout=120))
        
        # 输出结果
        if result:
            print("\n测试结果: 成功 ✅")
            sys.exit(0)
        else:
            print("\n测试结果: 失败 ❌")
            sys.exit(1)
    except asyncio.TimeoutError:
        print("\n测试超时 ⏱️")
        sys.exit(2)
    except KeyboardInterrupt:
        print("\n测试被用户中断 ⛔")
        sys.exit(3)
    except Exception as e:
        print(f"\n测试过程发生异常: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(4) 