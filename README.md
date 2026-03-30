# ACPS Leader Agent

[**ACPS（Agent Communication Protocol Suite）**](https://github.com/AIP-PUB) 智能体通信协议族中 **Leader Agent** 的参考实现。

ACPS 由北京邮电大学牵头、中国电子技术标准化研究院参与制定，定义了智能体间发现（ADP）、交互（AIP）和可信注册（ATR）三组核心协议。本项目展示了如何让一个通用大模型智能体化身为 ACPS Leader：在 **ATR** 流程下完成智能体登记与 mTLS 证书准备，建立可信身份；通过 **ADP** 发现 Partner Agent；通过 **AIP** 委派任务并汇总结果，向用户提供统一交互入口。

## 核心设计

本项目在同一个后端中并行实现了 **Skill** 和 **MCP** 两条独立的 Agent 路径，证明了 ACPS 协议族的通用性。

- **双路由架构**：后端注册两组独立的 FastAPI Router（`/api/skill/chat/stream` 与 `/api/mcp/chat/stream`），各自绑定独立的 LangGraph Agent，前端通过请求路径切换模式。·
- **Skill 模式**：智能体通过 `read_file`、`write_file`、`run_python` 等基础工具，自主阅读 `SKILL.md` 协议文档并执行 `scripts/` 下的脚本完成 ADP/AIP 流程。适用于 Cursor、LangChain 等支持文件操作和代码执行的平台。
- **MCP 模式**：ACPS 协议逻辑封装在独立的 FastMCP Server 中，后端通过 `langchain-mcp-adapters` 以 SSE 获取工具（`discover`、`start_task` 等）。适用于 Dify 等支持 MCP 的客户端。
- **共享层**：两种模式共享 SSE 流式处理、请求 Schema 和 `generate_response` 工具（基于 LangGraph interrupt 实现用户交互暂停）。

## ATR 可信注册

在接入公网 ACPS 生态前，须完成 **可信注册（ATR）** 并取得用于 mTLS 的证书。建议按下述顺序执行；其中注册与发现依赖公网服务，Challenge 服务须由部署方自行暴露为 **公网可访问** 的 HTTPS 端点（例如通过反向代理或内网穿透）。

### 1. 部署 CA Challenge Server

CA Challenge Server 运行于 Agent 侧，用于响应 CA 在证书签发流程中发起的 HTTP-01 校验请求。ACS 中的 `securitySchemes.mtls.x-caChallengeBaseUrl` 必须指向该服务对外可达的基准 URL。

```bash
git clone https://github.com/AIP-PUB/ACPs-CA-Challenge.git
cd ACPs-CA-Challenge
python3 -m venv venv && source venv/bin/activate
pip install poetry && poetry install
cp .env.example .env    # 按实际部署编辑 .env，确保对外服务地址与 ACS 中声明一致
./run.sh start
```

### 2. 在注册门户提交 ACS 并等待审核

使用符合规范的 **ACS（Agent Capability Statement）** 文档，在注册门户提交智能体登记申请：

- 注册门户：[https://ioa.pub/registry-web/](https://ioa.pub/registry-web/)

提交前请确认 ACS 已正确配置 `x-caChallengeBaseUrl`（见上一步），且 **勿在待审批的 ACS 中填写 `aic` 字段**（审批通过后由注册系统分配 **AIC**，即 Agent Identity Code）。审核策略以运营方为准；须待 **审核通过** 后方可进行证书申请与对外互通。

### 3. 通过 ADP 发现服务核验登记结果（可选）

审核通过后，可通过 **ADP 发现接口** 检索已登记的智能体，以确认元数据已发布：

- 发现服务：`https://ioa.pub/discovery/acps-adp-v2/discover`

（本仓库中 `config.yaml` 的 `default_discovery_url` 即指向上述地址，可与本地配置对照。）

### 4. 安装并配置 CA Client，向 CA Server 申请证书

使用 **CA Client** 通过 ACME 协议向 **CA Server** 申请 mTLS 证书。请先安装客户端：

```bash
git clone https://github.com/AIP-PUB/ACPs-CA-Client.git
cd ACPs-CA-Clientatr/ca-client
python3 -m venv venv && source venv/bin/activate
pip install poetry && poetry install
cp ca-client.conf.example .ca-client.conf
```

在 `.ca-client.conf` 中至少配置以下两项（公网 CA 服务地址须与下表一致）：

| 配置项 | 说明 |
|--------|------|
| `CA_SERVER_BASE_URL` | CA Server 基准地址，公网环境为 **`https://ioa.pub/ca-server/acps-atr-v2`** |
| `CHALLENGE_SERVER_BASE_URL` | 与 ACS 中 `x-caChallengeBaseUrl` 一致的 Challenge 服务基准地址（须公网可达） |

示例：

```ini
CA_SERVER_BASE_URL = https://ioa.pub/ca-server/acps-atr-v2
CHALLENGE_SERVER_BASE_URL = https://your-public-domain.example/acps-atr-v2
```

将签发结果部署到本仓库约定目录（`<YOUR_AIC>` 替换为注册系统分配的 AIC）：

```bash
ca-client new-cert --aic <YOUR_AIC> \
  --key-path  /path/to/acps_leader/backend/private/<YOUR_AIC>.key \
  --cert-path /path/to/acps_leader/backend/certs/<YOUR_AIC>.pem \
  --trust-bundle-path /path/to/acps_leader/backend/certs/trust-bundle.pem
```

证书续期、吊销等命令行用法见 [ACPs-CA-Client USAGE.md](https://github.com/AIP-PUB/ACPs-CA-Client/blob/master/USAGE.md)。

> **说明**：若 Challenge 公网地址或 ACS 中的 `x-caChallengeBaseUrl` 发生变更，须同步更新注册信息及 CA Client 配置，否则将影响后续签发与校验。

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

可选：若需以 mTLS 访问 Partner 的 `https` 端点，在 `backend/.env` 中按 `backend/.env.example` 补充证书相关变量；未配置或加载失败时仍按普通 HTTP 行为运行。

### `mcp_server/.env`

与 `backend/.env` 相互独立（MCP 进程不读取 `backend/.env`）。复制示例后按需填写：

```bash
cp mcp_server/.env.example mcp_server/.env
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
cp .env.example .env
cp state/config/config.yaml.example state/config/config.yaml
bash start.sh
```

在 MCP 客户端配置 SSE 地址 `http://<host>:7004/sse`（端口以 `.env` 中 `MCP_SERVER_PORT` 为准）。

### 方式三：完整部署

适用于暂无可集成的智能体平台的场景。本项目提供完整的前后端系统，支持在 Skill 和 MCP 模式间切换对比。

按上文完成各组件配置后，依次启动（首次运行会自动创建 venv 并安装依赖）：

```bash
# 1. MCP Server（默认端口 7004，见 mcp_server/.env）
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

- [ACPS 智能体注册门户](https://ioa.pub/registry-web/)
- [ACPS 项目主页](https://github.com/AIP-PUB)
- [ACPS 协议规范](https://github.com/AIP-PUB/Agent-Interconnection-Protocol-Project)
- [ACPs-CA-Challenge](https://github.com/AIP-PUB/ACPs-CA-Challenge)
- [ACPs-CA-Client](https://github.com/AIP-PUB/ACPs-CA-Client)
- [ACPs-SDK](https://github.com/AIP-PUB/ACPs-SDK)
