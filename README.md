<p align="center">
  <img src="https://raw.githubusercontent.com/零感科技/aimate/main/docs/零感-横版.svg" alt="零感 LingGan" height="60">
</p>

<h1 align="center">零感调度器 · AIMate</h1>
<p align="center"><strong>Claude Code + Codex CLI 协同调度器</strong></p>
<p align="center">分析需求 → 智能拆解 → 分派给最适合的 AI 工具执行</p>

<p align="center">
  <img src="https://img.shields.io/badge/version-2.2-7C3AED?style=flat-square" alt="version">
  <img src="https://img.shields.io/badge/python-3.10+-10B981?style=flat-square" alt="python">
  <img src="https://img.shields.io/badge/license-MIT-7C3AED?style=flat-square" alt="license">
  <img src="https://img.shields.io/badge/零感-出品-10B981?style=flat-square" alt="零感出品">
</p>

---

## 这是什么？

你给一个需求，它自动思考该怎么做，然后把活分给 Claude Code 和 Codex CLI 各干各的。

> 比如你说"用 Python 写个 REST API，带用户注册登录，SQLite 存数据，写完自动测试并 Docker 化"
>
> 调度器会拆成 3-4 个子任务，把"写代码"派给 Claude，把"跑测试 + Docker 化"派给 Codex，你看着进度条等结果就行。

**零感调度器是 [零感品牌](https://github.com/零感科技) 的第二款开源工具**，延续"零门槛上手 AI"的理念——装好就能用，Web 界面操作，不需要记命令。

---

## 架构

```
你 (浏览器)
  │
  ▼
┌─────────────────────────────────┐
│         零感调度器 (FastAPI)      │  ← 分析引擎：调用 DeepSeek 拆解任务
│   http://127.0.0.1:7799         │
└──────┬──────────────────┬───────┘
       │                  │
       ▼                  ▼
┌──────────────┐  ┌──────────────┐
│  Claude Code  │  │  Codex CLI   │
│  复杂代码      │  │  终端操作     │
│  架构设计      │  │  脚本测试     │
│  跨文件重构    │  │  部署运维     │
└──────────────┘  └──────────────┘
```

- **分析引擎**：把需求拆成 2-5 个子任务，判断每个任务适合 Claude 还是 Codex
- **协同执行**：子进程调用各工具，SSE 实时推送进度
- **Web 控制台**：输入需求 → 查看方案 → 一键执行 → 查看结果
- **翻译桥**：Codex → DeepSeek API 格式转换，支持流式 + 工具调用

---

## 快速开始

### 前提

- Windows 10/11 64 位（macOS/Linux 也能跑，但没测过）
- Python 3.10+
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code/overview) 已安装
- [Codex CLI](https://github.com/anthropics/codex) 已安装
- DeepSeek API Key（[免费注册](https://platform.deepseek.com/api_keys) 送 500 万 token）

### 安装

```bash
# 1. 克隆
git clone https://github.com/零感科技/aimate.git
cd aimate

# 2. 装依赖
pip install fastapi uvicorn pyyaml httpx flask waitress requests

# 3. 设 API Key（二选一）
# 方式 A：系统环境变量
setx DEEPSEEK_API_KEY "sk-你的key"

# 方式 B：项目 .env 文件（优先级更高）
echo DEEPSEEK_API_KEY=sk-你的key > .env

# 4. 启动
python server.py
# 或者双击 start.bat
```

浏览器打开 `http://127.0.0.1:7799`

---

## 功能

### 智能拆解
输入自然语言需求，AI 自动分析、拆解为独立子任务，标注每个任务适合 Claude 还是 Codex，给出理由。

### 实时执行
SSE 推送进度，每个子任务的运行状态、耗时、输出预览一目了然。

### Web 配置面板
点右上角齿轮，直接在浏览器里改模型、temperature、API 端点、工具参数。保存即时生效，持久化到配置文件。

### 费用追踪
每次分析抓取 token 消耗，按 DeepSeek 公开定价估算费用。Header 显示累计费用，点 `≈ $x` 徽章查看每次分析的 token 明细。Claude/Codex 执行费用无法统计（外部进程），面板有提示。

### 工具可用性检测
页面加载时自动检测 `claude` 和 `codex` 是否在 PATH 中，Header 绿点/红点指示状态，hover 显示安装指引。

### 帮助面板
首次打开自动弹出使用说明，之后随时点 `?` 查看——API Key 配法、模型换法、费用算法、工作流程全覆盖。

---

## 配置

所有配置通过 Web 面板修改，或直接编辑 `config.yaml`：

```yaml
analyzer:
  provider: deepseek
  model: deepseek-chat          # 分析模型
  base_url: https://api.deepseek.com/v1
  api_key_env: DEEPSEEK_API_KEY  # 从环境变量名读取 Key
  max_tokens: 4096
  temperature: 0.3

tools:
  claude:
    command: claude
    args: ["-p"]                # Claude Code 参数
  codex:
    command: codex
    args: ["exec", "--skip-git-repo-check", "-m", "deepseek-chat"]
```

> API Key 从系统环境变量或 `.env` 文件读取。页面上只填**环境变量名**，不填 Key 值。Key 永远不会出现在前端，不会泄露。

---

## 项目结构

```
aimate/
├── server.py              # FastAPI 主服务 (7799)
├── bridge.py              # Codex ←→ DeepSeek 翻译桥 (5000)
├── config.yaml            # 配置文件
├── start.bat              # Windows 一键启动
├── requirements.txt       # Python 依赖
├── .gitignore
├── static/
│   └── style.css          # 深色主题样式
└── templates/
    └── index.html         # 单页 Web 控制台
```

---

## 常见问题

<details>
<summary><strong>分析失败：API Key 无效？</strong></summary>

检查环境变量是否设置正确：
```bash
echo %DEEPSEEK_API_KEY%
```
如果为空，按上方安装步骤重新设置。Key 获取地址：https://platform.deepseek.com/api_keys
</details>

<details>
<summary><strong>Header 显示红色指示灯？</strong></summary>

表示 Claude Code 或 Codex CLI 未在 PATH 中找到。Hover 指示灯查看安装指引。

Claude Code：`npm install -g @anthropic-ai/claude-code`
Codex CLI：`npm install -g @anthropic-ai/codex`
</details>

<details>
<summary><strong>费用准确吗？</strong></summary>

费用基于 DeepSeek 公开定价估算，不包含税费和折扣，仅供参考。实际费用以 [DeepSeek 控制台账单](https://platform.deepseek.com/usage) 为准。
</details>

<details>
<summary><strong>分析器、Claude、Codex 能用不同模型吗？</strong></summary>

完全可以。三个组件独立运行，不要求模型一致。分析器用 deepseek-chat 拆解任务，Claude 用 claude-sonnet-4-6 写代码，Codex 用 deepseek-v4-pro 跑脚本——各管各的，互不干扰。
</details>

---

## 关于零感

**零感 (LingGan)** — 零门槛上手 AI，激发编程灵感。

- 🛠 [零感安装器](https://github.com/零感科技/ai-tools-installer) — AI 开发环境一键部署
- 🗓 零感调度器 (本项目) — AI 工具协同调度
- 👥 QQ 群 **956118706** (零感实验室) — 交流答疑、教程分享

---

<p align="center">
  <sub>Made with ❤️ by 零感团队 · <a href="https://github.com/零感科技">GitHub</a> · QQ群 956118706</sub>
</p>
