# ALGOTEST

## 项目概述

ALGOTEST是一个大模型驱动的算法测试系统，能够分析用户输入的算法需求文档，自动生成全面的测试用例。系统通过FastAPI封装为API服务，提供RESTful接口供外部调用。

系统主要特点：
- 使用langgraph构建工作流
- 采用loguru进行日志管理
- 使用SQLite进行数据存储
- 通过pydantic-settings进行配置管理

## 系统架构

系统由核心分析Agent组成：
- **分析Agent**：负责分析用户输入的算法需求文档，生成测试用例

## 安装说明

### 环境要求
- Python 3.8+
- Docker

### 安装步骤

1. 克隆仓库
```bash
git clone https://github.com/tsingsx/ALGOTEST.git
cd ALGOTEST
```

2. 创建虚拟环境
```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
# 或
venv\Scripts\activate  # Windows
```

3. 安装依赖
```bash
pip install -r requirements.txt
```

4. 配置环境变量
```bash
cp .env.example .env
# 编辑.env文件，填写必要的配置信息，特别是ZHIPU_API_KEY
# 将指令格式文档放在data文件夹下
```

## 使用方法

### 启动服务
```bash
python main.py
```

或者使用热重载模式（开发时推荐）：
```bash
export API_RELOAD=true && python main.py
```

### API接口

#### 文档管理
- **POST /api/documents**：上传需求文档
  - 请求：multipart/form-data格式，包含PDF文件
  - 响应：返回文档ID和存储路径

#### 算法镜像和数据集管理
- **POST /api/documents/{document_id}/algorithm-image**：通过文档ID更新算法镜像地址
  - 请求：JSON格式，包含algorithm_image字段
  - 响应：返回更新成功的消息

- **POST /api/documents/{document_id}/dataset-url**：通过文档ID更新数据集地址
  - 请求：JSON格式，包含dataset_url字段
  - 响应：返回更新成功的消息

- **POST /api/tasks/{task_id}/algorithm-image**：通过任务ID更新算法镜像地址
  - 请求：JSON格式，包含algorithm_image字段
  - 响应：返回更新成功的消息

- **POST /api/tasks/{task_id}/dataset-url**：通过任务ID更新数据集地址
  - 请求：JSON格式，包含dataset_url字段
  - 响应：返回更新成功的消息

- **GET /api/documents/{document_id}/task-info**：获取文档关联的任务信息
  - 请求：文档ID
  - 响应：返回文档关联的任务信息，包括算法镜像地址和数据集URL

#### 测试用例生成
- **POST /api/documents/{document_id}/analyze**：分析文档并生成测试用例
  - 请求：文档ID
  - 响应：返回生成的测试用例列表

- **POST /api/generate-testcases**：直接从上传文档生成测试用例
  - 请求：multipart/form-data格式，包含PDF文件
  - 响应：返回生成的测试用例列表

#### 测试用例管理
- **GET /api/testcases**：获取测试用例列表
  - 参数：document_id（可选，按文档ID筛选）
  - 响应：返回测试用例列表

- **GET /api/testcases/{case_id}**：获取单个测试用例详情
  - 参数：case_id（测试用例ID）
  - 响应：返回测试用例详情

- **POST /api/testcases**：创建新的测试用例
  - 请求体：包含测试用例信息（名称、目的、步骤、预期结果、验证方法、所属文档ID）
  - 响应：返回创建的测试用例

- **PUT /api/testcases/{case_id}**：更新测试用例
  - 请求体：包含要更新的测试用例字段
  - 响应：返回更新后的测试用例

- **DELETE /api/testcases/{case_id}**：删除测试用例
  - 参数：case_id（测试用例ID）
  - 响应：返回删除结果

### 测试用例数据结构

测试用例包含以下信息：
- **id**: 测试用例唯一标识
- **document_id**: 所属文档ID
- **name**: 测试名称
- **purpose**: 测试目的
- **steps**: 测试步骤
- **expected_result**: 预期结果
- **validation_method**: 验证方法

### API文档

系统提供了Swagger UI文档，可以通过访问 `http://localhost:8000/api/docs` 查看和测试API接口。

### 测试任务管理

#### 获取所有测试任务

获取数据库中所有的测试任务列表。

```
GET /api/tasks
```

**响应示例**：

```json
{
  "message": "成功获取所有测试任务",
  "tasks": [
    {
      "id": 1,
      "task_id": "TASK_123456",
      "algorithm_image": "example/algorithm:v1.0",
      "dataset_url": "http://example.com/dataset",
      "status": "completed",
      "created_at": "2025-03-18T12:30:45",
      "updated_at": "2025-03-18T14:20:30",
      "test_cases_count": 5
    },
    {
      "id": 2,
      "task_id": "TASK_789012",
      "algorithm_image": "example/algorithm:v2.0",
      "dataset_url": null,
      "status": "pending",
      "created_at": "2025-03-19T09:15:22",
      "updated_at": "2025-03-19T09:15:22",
      "test_cases_count": 0
    }
  ]
}
```

### 任务执行和Docker管理

#### 准备任务执行环境

```
POST /api/tasks/{task_id}/prepare
```

在执行测试任务前，准备Docker容器环境。该接口会根据任务信息设置Docker容器。

**请求参数：**
- **task_id** (路径参数): 测试任务ID

**响应格式：**
```json
{
  "success": true,
  "task_id": "TASK_123456",
  "container_name": "algotest_TASK_123456",
  "algorithm_image": "example/algorithm:v1.0",
  "dataset_url": "http://example.com/dataset"
}
```

#### 设置算法Docker容器

```
POST /api/tasks/{task_id}/docker/setup
```

通过MCP在远程服务器上设置Docker容器，拉取算法镜像并启动容器。

**请求参数：**
- **task_id** (路径参数): 测试任务ID

**响应格式：**
```json
{
  "success": true,
  "task_id": "TASK_123456",
  "container_name": "algotest_TASK_123456",
  "algorithm_image": "example/algorithm:v1.0",
  "dataset_url": "http://example.com/dataset"
}
```

#### 在Docker容器中执行命令

```
POST /api/tasks/{task_id}/docker/exec
```

通过MCP在远程服务器的Docker容器中执行命令

**请求参数：**
- **task_id** (路径参数): 测试任务ID
- **command** (请求体): 要执行的命令

**请求体格式：**
```json
{
  "command": "ls -la /data"
}
```

**响应格式：**
```json
{
  "success": true,
  "task_id": "TASK_123456",
  "container_name": "algotest_TASK_123456",
  "command": "ls -la /data",
  "result": {
    "stdout": "命令执行结果...",
    "stderr": ""
  }
}
```

## 开发指南

请参考项目文档了解更多开发信息。

## 许可证

[MIT License](LICENSE)