# ACPS Leader Agent

本仓库提供了 **Agent Communication Protocol Suite (ACPS)** 协议族中 **Leader Agent**的参考实现。

在 ACPS 生态中，Leader 智能体的角色是通过 ADP（发现协议）寻找合适的 Partner Agent，并通过 AIP（交互协议）将任务委派给它们执行。本项目旨在展示：**任何通用的大模型智能体**，只要采用本项目提供的两种标准方式之一，都可以轻松化身为 ACPS Leader。

## 两种接入方式

为了突显 ACPS 的通用性和易用性，本项目在同一个后端和前端下，实现了两种不同的 Leader 智能体接入方式。可以在前端界面无缝切换对比：

### 1. Agent Skill（智能体技能）模式
- **原理解析**：为智能体提供一份 Markdown 技能说明文档（`SKILL.md`）和一组 Python 协议脚本。智能体利用基础工具（`read_file`、`write_file`、`run_python`）自主阅读协议流程、缓存状态数据并执行调用。
- **核心价值**：任何支持文件读取和代码执行的智能体（例如 Cursor、Codeium，或标准的 LangChain 架构），只需将 `skills/acps` 目录放入其工作区，即可瞬间掌握 ACPS 协议。

### 2. MCP Server（模型上下文协议）模式
- **原理解析**：ACPS 协议的核心逻辑被封装在了一个独立的 **MCP Server** 中。智能体只需作为 MCP Client 连接该服务，即可直接获得类型安全、自描述的协议操作工具（如 `discover`、`start_task` 等）。
- **核心价值**：任何支持 MCP 标准的智能体或应用（例如 Claude Desktop），无需具备本地脚本执行能力，只需连接服务即可零成本接入 ACPS 发现与委派网络。

## 目录结构

- `frontend/` — 基于 React 的对话界面，实时展示智能体的思考过程、工具调用和协议状态变化。支持在 Skill 和 MCP 两种模式间一键切换。
- `backend/` — FastAPI 后端服务，同时托管了 Skill 模式和 MCP 模式的两个智能体实例，提供统一的 SSE 流式接口。
- `backend/skills/acps/` — ACPS 技能的核心资产（包括指令文档、执行脚本和底层 SDK）。
- `mcp_server/` — 独立的 FastMCP 服务，将 ACPS 操作转化为标准的 MCP 工具。

## 快速开始

### 1. 环境配置

需要配置以下三个位置以确保服务正常运行：

**后端环境 (`backend/.env`)**
进入 `backend/` 目录，复制示例环境文件并填入大模型配置：
```bash
cp .env.example .env
```
修改 `.env` 填入 API 密钥：
```env
OPENAI_API_KEY=your_key_here
OPENAI_BASE_URL=https://api.openai.com/v1
MODEL_NAME=gpt-4o
```

**前端环境 (`frontend/.env`)**
如果后端不在 `localhost` 运行，需要修改前端 API 指向：
```env
VITE_API_BASE_URL=http://localhost:7002
```

**发现服务地址配置**
在智能体执行发现流程前，需要配置ADP发现服务器，请复制示例配置并填写：
```bash
# Agent Skill 模式
cp backend/skills/acps/state/config/config.yaml.example backend/skills/acps/state/config/config.yaml
# MCP Server 模式
cp mcp_server/state/config/config.yaml.example mcp_server/state/config/config.yaml
```
将其中的 `custom_discovery_url` 字段填入真实的发现服务地址。

### 2. 一键启动

在项目根目录下，执行一键启动脚本（会自动安装各个服务的依赖并按顺序启动）：
```bash
./start_all.sh
```

所有服务启动成功后：
- 访问 **http://localhost:7003** 即可打开交互界面。
- 后端服务运行在 `7002`，MCP Server 运行在 `7004`。

### 3. 停止服务

使用完毕后，执行以下脚本即可一键停止所有相关进程：
```bash
./stop_all.sh
```
