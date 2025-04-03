# ALGOTEST 前后端一体化开发计划

## 项目概述

ALGOTEST 是一个大模型驱动的算法测试系统，将采用基于 FastAPI 的前后端一体化架构，不使用独立的 npm 前端项目，而是利用 FastAPI 的模板引擎来实现用户界面。

## 技术选型

1. **后端框架**：FastAPI
2. **模板引擎**：Jinja2
3. **前端样式**：Bootstrap/Tailwind CSS
4. **交互增强**：原生 JavaScript + Htmx
5. **数据可视化**：Chart.js
6. **响应式设计**：CSS Grid/Flexbox

## 模板结构规划

```
templates/
├── base.html             # 基础模板，包含通用头部、导航和尾部
├── index.html            # 首页/仪表盘
├── documents/
│   ├── list.html         # 文档列表页
│   ├── upload.html       # 文档上传页
│   └── detail.html       # 文档详情页
├── testcases/
│   ├── list.html         # 测试用例列表页
│   ├── detail.html       # 测试用例详情页
│   └── edit.html         # 测试用例编辑页
├── tasks/
│   ├── list.html         # 任务列表页
│   ├── detail.html       # 任务详情页
│   └── configure.html    # 任务配置页
├── execution/
│   ├── console.html      # 执行控制台页
│   └── monitor.html      # 执行监控页
└── reports/
    ├── summary.html      # 测试结果统计页
    └── detail.html       # 报告详情页
```

## 静态资源结构

```
static/
├── css/
│   ├── main.css          # 主样式文件
│   └── vendor/           # 第三方CSS库
├── js/
│   ├── main.js           # 主JavaScript文件
│   ├── components/       # 组件化JS文件
│   └── vendor/           # 第三方JS库
└── img/                  # 图片资源
```

## 功能模块

### 1. 文档管理模块
- 文档上传表单和处理
- 文档列表展示与分页
- 文档分析触发按钮与进度反馈

### 2. 测试用例管理模块
- 测试用例列表与筛选
- 测试用例详情展示
- 测试用例编辑表单
- 自动生成测试用例流程

### 3. 测试任务管理模块
- 任务列表与状态显示
- 任务配置表单（算法镜像、数据集）
- 任务执行状态实时更新（WebSocket）

### 4. 测试执行模块
- 单个测试用例执行控制
- 批量测试执行与监控
- 执行结果实时展示

### 5. 测试结果分析模块
- 测试结果数据可视化
- 分析报告生成与下载
- 测试数据管理界面

## 路由规划

1. **首页/仪表盘**
   - `GET /` → `templates/index.html`

2. **文档管理**
   - `GET /documents` → `templates/documents/list.html`
   - `GET /documents/upload` → `templates/documents/upload.html`
   - `GET /documents/{document_id}` → `templates/documents/detail.html`
   - `POST /api/documents` → 文档上传API
   - `POST /api/documents/{document_id}/analyze` → 文档分析API

3. **测试用例管理**
   - `GET /testcases` → `templates/testcases/list.html`
   - `GET /testcases/{case_id}` → `templates/testcases/detail.html`
   - `GET /testcases/create` → `templates/testcases/edit.html`
   - `GET /testcases/{case_id}/edit` → `templates/testcases/edit.html`
   - 相应的API调用使用现有的API路由

4. **任务管理**
   - `GET /tasks` → `templates/tasks/list.html`
   - `GET /tasks/{task_id}` → `templates/tasks/detail.html`
   - `GET /tasks/{task_id}/configure` → `templates/tasks/configure.html`
   - 相应的API调用使用现有的API路由

5. **执行管理**
   - `GET /execution/{task_id}` → `templates/execution/console.html`
   - `GET /execution/{task_id}/monitor` → `templates/execution/monitor.html`
   - 使用WebSocket进行实时状态更新

6. **报告管理**
   - `GET /reports` → `templates/reports/summary.html`
   - `GET /reports/{task_id}` → `templates/reports/detail.html`
   - `GET /reports/{task_id}/download` → 报告下载API

## 前后端交互方式

1. **表单提交**：使用HTML表单直接提交到相应的API端点
2. **异步更新**：使用Htmx或Fetch API进行无刷新数据更新
3. **实时通信**：对于测试执行等需要实时更新的功能，使用WebSocket
4. **组件加载**：使用Htmx的部分加载功能实现组件化

## 开发任务

### 阶段一：基础结构搭建

1. 创建模板目录结构
2. 设计基础模板和布局
3. 配置静态资源服务
4. 添加前端路由处理器到FastAPI应用

### 阶段二：页面开发

1. 开发基础模板和导航组件
2. 实现文档管理相关页面
3. 开发测试用例管理页面
4. 实现任务和执行管理界面
5. 设计测试报告和分析页面

### 阶段三：交互增强

1. 添加表单验证和错误处理
2. 实现异步数据加载和页面更新
3. 开发WebSocket连接以支持实时更新
4. 添加数据可视化组件
5. 优化移动端响应式设计

### 阶段四：集成与优化

1. 与后端API完全集成
2. 性能优化和缓存策略
3. 用户体验改进
4. 浏览器兼容性测试
5. 可访问性优化

## main.py 修改计划

需要修改 main.py 文件，添加以下功能：

1. 配置Jinja2模板引擎
2. 添加静态文件服务
3. 增加前端页面路由
4. 确保WebSocket支持
5. 保持现有API功能不变

## 时间规划

1. 基础结构搭建：2天
2. 页面开发：4-5天
3. 交互增强：3-4天
4. 集成与优化：2-3天

总计：约11-14天 