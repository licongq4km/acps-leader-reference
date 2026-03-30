# ACPS Leader Agent

[**ACPS（Agent Communication Protocol Suite）**](https://github.com/AIP-PUB) 智能体通信协议族中 **Leader Agent** 的参考实现。

ACPS 由北京邮电大学牵头、中国电子技术标准化研究院参与制定，定义了智能体间发现（ADP）、交互（AIP）和可信注册（ATR）三组核心协议。本项目展示了如何让一个通用大模型智能体化身为 ACPS Leader —— 通过 ADP 发现 Partner Agent，通过 AIP 委派任务并收集结果，最终呈现给用户。

## 核心设计

本项目在同一个后端中并行实现了 **Skill** 和 **MCP** 两条独立的 Agent 路径，证明了 ACPS 协议族的通用性。

- **双路由架构**：后端注册两组独立的 FastAPI Router（`/api/skill/chat/stream` 与 `/api/mcp/chat/stream`），各自绑定独立的 LangGraph Agent，前端通过请求路径切换模式。
- **Skill 模式**：智能体通过 `read_file`、`write_file`、`run_python` 等基础工具，自主阅读 `SKILL.md` 协议文档并执行 `scripts/` 下的脚本完成 ADP/AIP 流程。适用于 Cursor、LangChain 等支持文件操作和代码执行的平台。
- **MCP 模式**：ACPS 协议逻辑封装在独立的 FastMCP Server 中，后端通过 `langchain-mcp-adapters` 以 SSE 获取工具（`discover`、`start_task` 等）。适用于 Dify 等支持 MCP 的客户端。
- **共享层**：两种模式共享 SSE 流式处理、请求 Schema 和 `generate_response` 工具（基于 LangGraph interrupt 实现用户交互暂停）。

## ATR 可信注册

使用前需完成 ACPS 中的可信注册流程，获取 mTLS 证书。需安装如下两个组件：

### 1. CA Challenge Server

部署在 Agent 侧，响应 CA 的域名验证挑战。

```bash
git clone https://github.com/AIP-PUB/ACPs-CA-Challenge.git
cd ACPs-CA-Challenge
python3 -m venv venv && source venv/bin/activate
pip install poetry && poetry install
cp .env.example .env    # 编辑 .env，生产环境需确保 BASE_URL 对应的地址公网可达
./run.sh start
```

### 2. CA Client

通过 ACME 协议申请证书。

```bash
git clone https://github.com/AIP-PUB/ACPs-CA-Client.git
cd ACPs-CA-Client
python3 -m venv venv && source venv/bin/activate
pip install poetry && poetry install
cp ca-client.conf.example .ca-client.conf
```

编辑 `.ca-client.conf`：

```ini
CA_SERVER_BASE_URL = https://ca.acps.pub/acps-atr-v2
CHALLENGE_SERVER_BASE_URL = https://your-public-domain.com/acps-atr-v2
```

申请证书并部署到 `backend/certs/` 和 `backend/private/`：

```bash
ca-client new-cert --aic <YOUR_AIC> \
  --key-path  /path/to/acps_leader/backend/private/<YOUR_AIC>.key \
  --cert-path /path/to/acps_leader/backend/certs/<YOUR_AIC>.pem \
  --trust-bundle-path /path/to/acps_leader/backend/certs/trust-bundle.pem
```

> 续期、吊销等用法详见 [ACPs-CA-Client USAGE.md](https://github.com/AIP-PUB/ACPs-CA-Client/blob/master/USAGE.md)。

## 配置

### `backend/.env`

```bash
cp backend/.env.example backend/.env
```

```env
OPENAI_API_KEY=sk-your-api-key
OPENAI_BASE_URL=https://api.openai.com/v1
MODEL_NAME=gpt-4o
LEADER_AIC=1.2.156.3088.xxxx.xxxx...
```

### `config.yaml`（发现服务地址）

Skill 和 MCP 模式各需一份：

```bash
cp backend/skills/acps/state/config/config.yaml.example backend/skills/acps/state/config/config.yaml
cp mcp_server/state/config/config.yaml.example mcp_server/state/config/config.yaml
```

```yaml
default_discovery_url: "https://ioa.pub/discovery/acps-adp-v2/discover"
custom_discovery_url: ""
```

### `frontend/.env`（如后端非 localhost）

```bash
cp frontend/.env.example frontend/.env
# 编辑 VITE_API_BASE_URL 指向后端地址
```

## 使用方式

### 方式一：Skill 模式集成

适用于已有支持文件读写和代码执行的智能体。无需部署额外服务。

安装 [ACPs-SDK](https://github.com/AIP-PUB/ACPs-SDK)（`pip install acps-sdk`），将 `backend/skills/acps/` 目录复制到智能体工作区，配置好 `config.yaml` 中的发现服务 URL，在系统提示中引导智能体阅读 `SKILL.md` 即可。

### 方式二：MCP Server 集成

适用于已有支持 MCP 协议的智能体。

```bash
pip install acps-sdk
cd mcp_server
cp state/config/config.yaml.example state/config/config.yaml
# 编辑 config.yaml
bash start.sh
```

在 MCP 客户端配置 SSE 地址 `http://<host>:7004/sse`。

### 方式三：完整部署

适用于暂无可集成的智能体平台的场景。本项目提供完整的前后端系统，支持在 Skill 和 MCP 模式间切换对比。

完成上述配置后，依次启动各组件（首次运行会自动创建 venv 并安装依赖）：

```bash
# 1. MCP Server（端口 7004）
bash mcp_server/start.sh

# 2. Backend API（端口 7002）
bash backend/start.sh

# 3. Frontend（端口 7003）
cd frontend && npm install && npx vite --host 0.0.0.0 --port 7003
```

停止服务：

```bash
bash mcp_server/stop.sh
bash backend/stop.sh
# 前端 Ctrl+C 退出即可
```

| 服务 | 端口 |
|------|------|
| MCP Server | 7004 |
| Backend API | 7002 |
| Frontend | 7003 |

访问 `http://localhost:7003` 打开对话界面，顶部切换 Skill / MCP 模式。

## 许可证

本项目采用 [木兰宽松许可证 第2版（MulanPSL-2.0）](http://license.coscl.org.cn/MulanPSL2)，与 ACPS 协议族的其他开源组件保持一致。

## 相关链接

- [ACPS 项目主页](https://github.com/AIP-PUB)
- [ACPS 协议规范](https://github.com/AIP-PUB/Agent-Interconnection-Protocol-Project)
- [ACPs-CA-Challenge](https://github.com/AIP-PUB/ACPs-CA-Challenge)
- [ACPs-CA-Client](https://github.com/AIP-PUB/ACPs-CA-Client)
- [ACPs-SDK](https://github.com/AIP-PUB/ACPs-SDK)
