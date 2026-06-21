#!/usr/bin/env python3
"""AIMate — 智能任务调度器。分析需求→拆解分派→Claude/Codex 各司其职。"""
import asyncio, json, os, re, shutil, subprocess, sys, tempfile, threading, queue, time
from datetime import datetime
from pathlib import Path
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
import httpx

ROOT = Path(__file__).parent
CONFIG = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))

PRICING = {
    "deepseek-chat":     {"input": 0.14, "output": 0.28},
    "deepseek-reasoner": {"input": 0.55, "output": 2.19},
    "default":           {"input": 0.14, "output": 0.28},
}

INSTALL_HINTS = {
    "claude": "npm install -g @anthropic-ai/claude-code  或访问 https://claude.ai/download",
    "codex":  "npm install -g @anthropic-ai/codex  或访问 https://github.com/anthropics/codex",
}

session_cost = {"total": 0.0, "analyses": []}

app = FastAPI(title="AIMate", version="2.2")
app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")
event_queues: list[queue.Queue] = []


# ─── 工具函数 ───────────────────────────────────────

def _find_exe(name: str) -> str:
    if os.name != "nt":
        return name
    if Path(name).suffix:
        return name
    for path_dir in os.environ.get("PATH", "").split(os.pathsep):
        for ext in (".cmd", ".bat", ".exe"):
            candidate = Path(path_dir) / f"{name}{ext}"
            if candidate.is_file():
                return str(candidate)
    return name


def _calculate_cost(usage: dict) -> float:
    model = CONFIG["analyzer"].get("model", "deepseek-chat")
    pricing = PRICING.get(model, PRICING["default"])
    prompt_tokens = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)
    input_cost = prompt_tokens / 1_000_000 * pricing["input"]
    output_cost = completion_tokens / 1_000_000 * pricing["output"]
    return round(input_cost + output_cost, 6)


def _save_config():
    config_path = ROOT / "config.yaml"
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", dir=str(ROOT),
        delete=False, encoding="utf-8"
    ) as tmp:
        yaml.dump(CONFIG, tmp, default_flow_style=False, allow_unicode=True)
        tmp_path = tmp.name
    os.replace(tmp_path, config_path)


def _install_hint(name: str) -> str:
    return INSTALL_HINTS.get(name, f"请查阅 {name} 官方文档安装")


def _extract_codex_model(args: list) -> str:
    try:
        idx = args.index("-m")
        return args[idx + 1]
    except (ValueError, IndexError):
        return "deepseek-chat"


def run_tool(cmd: list[str], workdir: str = None, timeout: int = 600) -> dict:
    exe = _find_exe(cmd[0]) if os.name == "nt" else cmd[0]
    if workdir:
        os.makedirs(workdir, exist_ok=True)
    penv = os.environ.copy()
    penv.update(CONFIG.get("env", {}))
    try:
        if os.name == "nt":
            full = subprocess.list2cmdline([exe] + cmd[1:])
            p = subprocess.run(full, capture_output=True, text=True,
                               cwd=workdir or str(Path.home()), timeout=timeout, shell=True, env=penv)
        else:
            p = subprocess.run([exe] + cmd[1:], capture_output=True, text=True,
                               cwd=workdir or str(Path.home()), timeout=timeout, shell=False, env=penv)
        return {"ok": p.returncode == 0, "stdout": p.stdout or "", "stderr": p.stderr or "", "code": p.returncode}
    except subprocess.TimeoutExpired:
        return {"ok": False, "stdout": "", "stderr": "超时", "code": -1}
    except FileNotFoundError:
        return {"ok": False, "stdout": "", "stderr": f"未找到: {cmd[0]}", "code": -1}


def emit(event: str, data: dict):
    for q in event_queues[:]:
        try:
            q.put_nowait({"event": event, "data": json.dumps(data, ensure_ascii=False)})
        except queue.Full:
            event_queues.remove(q)


def start_bridge():
    """启动 Codex 翻译桥（子进程）。"""
    cfg = CONFIG.get("bridge", {})
    script = cfg.get("script", "./bridge.py")
    script_path = ROOT / script
    if not script_path.exists():
        print(f"[AIMate] 警告: 未找到翻译桥脚本 {script_path}，Codex 将不可用")
        return None
    try:
        proc = subprocess.Popen(
            [sys.executable, str(script_path)],
            cwd=str(ROOT),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env={**os.environ, "DEEPSEEK_DEBUG": "0"},
        )
        print(f"[AIMate] 翻译桥已启动 (PID {proc.pid}): http://127.0.0.1:{cfg.get('port', 5000)}")
        return proc
    except Exception as e:
        print(f"[AIMate] 翻译桥启动失败: {e}")
        return None


# ─── 分析引擎 ────────────────────────────────────────

ANALYZE_PROMPT = """你是一个技术项目经理。分析以下需求，拆解为 2-5 个子任务，并判断每个子任务更适合用哪个工具。

可用工具：
- claude (Claude Code): 擅长复杂代码编写、跨文件重构、架构设计、代码审查
- codex (Codex CLI): 擅长终端命令执行、脚本自动化、运行测试、Docker/部署、文件操作

规则：
1. 子任务之间尽量独立，可以顺序执行
2. 明确每个子任务为什么选这个工具
3. 输出严格 JSON 数组，不要加任何其他文字

用户需求：
{task}

输出格式：
[{{"title": "...", "tool": "claude|codex", "reason": "...", "prompt": "具体告诉工具该做什么"}}]"""


async def analyze_task(task: str) -> tuple[list[dict], dict]:
    cfg = CONFIG["analyzer"]
    key = os.environ.get(cfg["api_key_env"], "")
    if not key:
        raise HTTPException(500, f"请设置环境变量 {cfg['api_key_env']}，获取地址: https://platform.deepseek.com/api_keys")
    prompt = ANALYZE_PROMPT.format(task=task)
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    body = {"model": cfg["model"], "messages": [{"role": "user", "content": prompt}],
            "max_tokens": cfg["max_tokens"], "temperature": cfg["temperature"]}
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(f"{cfg['base_url']}/chat/completions", headers=headers, json=body)
            if resp.status_code == 401:
                raise HTTPException(500, f"API Key 无效，请检查环境变量 {cfg['api_key_env']}。获取地址: https://platform.deepseek.com/api_keys")
            if resp.status_code == 429:
                raise HTTPException(500, "请求过于频繁，请稍后再试")
            if resp.status_code != 200:
                raise HTTPException(500, f"分析失败 ({resp.status_code}): {resp.text[:200]}")
            data = resp.json()
            usage = data.get("usage", {})
            content = data["choices"][0]["message"]["content"]
    except httpx.ConnectError:
        raise HTTPException(500, f"无法连接到 {cfg['base_url']}，请检查网络和 base_url 配置")
    except httpx.TimeoutException:
        raise HTTPException(500, "分析超时，请稍后重试")
    match = re.search(r"\[.*\]", content, re.DOTALL)
    if not match:
        raise HTTPException(500, f"无法解析分析结果: {content[:300]}")
    try:
        plan = json.loads(match.group(0))
        return plan, usage
    except json.JSONDecodeError:
        raise HTTPException(500, f"分析结果 JSON 损坏: {content[:300]}")


# ─── API 端点 ────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(content=(ROOT / "templates" / "index.html").read_text(encoding="utf-8"))


@app.post("/api/analyze")
async def api_analyze(req: dict):
    task = req.get("task", "").strip()
    if not task:
        raise HTTPException(400, "任务描述不能为空")
    if len(task) > 5000:
        raise HTTPException(400, "任务描述过长（最多 5000 字符）")
    plan, usage = await analyze_task(task)
    cost = _calculate_cost(usage)
    session_cost["total"] += cost
    session_cost["analyses"].append({
        "task": task[:100],
        "model": CONFIG["analyzer"]["model"],
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "cost": cost,
        "time": time.time(),
        "datetime": datetime.now().strftime("%H:%M:%S"),
    })
    return {
        "plan": plan,
        "count": len(plan),
        "usage": {
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
        },
        "cost": cost,
        "cumulative_cost": round(session_cost["total"], 6),
    }


@app.get("/api/stream")
async def api_stream():
    q: queue.Queue = queue.Queue(maxsize=100)
    event_queues.append(q)
    async def generator():
        try:
            while True:
                while not q.empty():
                    item = q.get_nowait()
                    yield f"event: {item['event']}\ndata: {item['data']}\n\n"
                await asyncio.sleep(0.3)
        except asyncio.CancelledError:
            pass
        finally:
            if q in event_queues:
                event_queues.remove(q)
    return StreamingResponse(generator(), media_type="text/event-stream")


@app.post("/api/execute")
async def api_execute(req: dict):
    plan = req.get("plan", [])
    workdir = req.get("workdir", str(Path.home()))
    if not plan:
        raise HTTPException(400, "执行计划为空")
    emit("status", {"phase": "start", "total": len(plan), "msg": f"执行 {len(plan)} 个子任务"})
    results = []
    for i, item in enumerate(plan):
        tool_cfg = CONFIG["tools"].get(item["tool"])
        if not tool_cfg:
            emit("error", {"index": i, "msg": f"未知工具: {item['tool']}"})
            continue
        cmd = [tool_cfg["command"]] + list(tool_cfg.get("args", [])) + [item["prompt"]]
        emit("progress", {"index": i, "total": len(plan), "title": item["title"],
                          "tool": item["tool"], "status": "running", "cmd": " ".join(cmd)})
        start = time.time()
        result = await asyncio.to_thread(run_tool, cmd, workdir)
        elapsed = round(time.time() - start, 1)
        ok = result["ok"]
        results.append({"index": i, "title": item["title"], "tool": item["tool"],
                        "ok": ok, "output": result["stdout"][:5000],
                        "error": result["stderr"][:2000], "elapsed": elapsed})
        emit("progress", {"index": i, "total": len(plan), "title": item["title"],
                          "tool": item["tool"], "status": "done" if ok else "failed",
                          "elapsed": elapsed, "output_preview": result["stdout"][:500]})
    emit("status", {"phase": "done", "total": len(plan),
                    "completed": sum(1 for r in results if r["ok"]),
                    "failed": sum(1 for r in results if not r["ok"]),
                    "note": "工具执行成本无法追踪（外部进程），仅分析阶段有成本统计"})
    return {"results": results}


@app.get("/api/config")
async def api_config():
    cfg = CONFIG["analyzer"]
    tools_cfg = CONFIG["tools"]
    return {
        "analyzer_summary": f"{cfg.get('provider', '?')} / {cfg['model']}",
        "analyzer": {
            "provider": cfg.get("provider", ""),
            "model": cfg["model"],
            "base_url": cfg["base_url"],
            "api_key_env": cfg["api_key_env"],
            "max_tokens": cfg["max_tokens"],
            "temperature": cfg["temperature"],
        },
        "tools": {
            "claude": {
                "command": tools_cfg["claude"]["command"],
                "args": tools_cfg["claude"]["args"],
                "name": tools_cfg["claude"].get("name", ""),
                "desc": tools_cfg["claude"].get("desc", ""),
            },
            "codex": {
                "command": tools_cfg["codex"]["command"],
                "args": tools_cfg["codex"]["args"],
                "name": tools_cfg["codex"].get("name", ""),
                "desc": tools_cfg["codex"].get("desc", ""),
                "model": _extract_codex_model(tools_cfg["codex"]["args"]),
            },
        },
    }


def _validate_analyzer(values: dict):
    if "model" in values and (not values["model"] or not isinstance(values["model"], str)):
        raise HTTPException(400, "模型名不能为空")
    if "base_url" in values:
        if not isinstance(values["base_url"], str) or not values["base_url"].startswith(("http://", "https://")):
            raise HTTPException(400, "base_url 必须以 http:// 或 https:// 开头")
    if "api_key_env" in values and (not values["api_key_env"] or not isinstance(values["api_key_env"], str)):
        raise HTTPException(400, "环境变量名不能为空")
    if "max_tokens" in values:
        v = values["max_tokens"]
        if not isinstance(v, int) or v < 1 or v > 131072:
            raise HTTPException(400, "max_tokens 必须在 1-131072 之间")
    if "temperature" in values:
        v = values["temperature"]
        if not isinstance(v, (int, float)) or v < 0 or v > 2:
            raise HTTPException(400, "temperature 必须在 0-2 之间")


def _validate_and_apply_tools(values: dict):
    for tool_name, tool_values in values.items():
        if tool_name not in CONFIG["tools"]:
            raise HTTPException(400, f"未知工具: {tool_name}")
        existing = CONFIG["tools"][tool_name]
        if "command" in tool_values:
            cmd = tool_values["command"]
            if not cmd or not isinstance(cmd, str):
                raise HTTPException(400, f"{tool_name}.command 不能为空")
            existing["command"] = cmd
        if "args" in tool_values:
            args = tool_values["args"]
            if not isinstance(args, list):
                raise HTTPException(400, f"{tool_name}.args 必须是数组")
            existing["args"] = args


@app.put("/api/config")
async def api_config_update(req: dict):
    allowed_sections = {"analyzer", "tools"}
    for section, values in req.items():
        if section not in allowed_sections:
            raise HTTPException(400, f"不可修改的配置节: {section}")
        if section not in CONFIG:
            raise HTTPException(400, f"配置节不存在: {section}")
        if section == "analyzer":
            _validate_analyzer(values)
            CONFIG["analyzer"].update(values)
        elif section == "tools":
            _validate_and_apply_tools(values)
    _save_config()
    return {"ok": True, "message": "配置已保存"}


@app.get("/api/tools/status")
async def api_tools_status():
    result = {}
    for name, cfg in CONFIG.get("tools", {}).items():
        exe = cfg.get("command", name)
        path = shutil.which(exe)
        result[name] = {
            "available": path is not None,
            "command": exe,
            "path": path,
            "install_hint": _install_hint(name),
        }
    return {"tools": result}


@app.get("/api/cost")
async def api_cost():
    return {
        "total": round(session_cost["total"], 6),
        "analysis_count": len(session_cost["analyses"]),
        "disclaimer": "费用为估算值，基于公开定价计算，不包含税费/折扣，实际费用以 API 平台账单为准",
        "analyses": [
            {
                "task": a["task"],
                "model": a["model"],
                "prompt_tokens": a["prompt_tokens"],
                "completion_tokens": a["completion_tokens"],
                "cost": a["cost"],
                "datetime": a.get("datetime", ""),
            }
            for a in session_cost["analyses"][-20:]
        ],
    }


if __name__ == "__main__":
    import uvicorn
    proc = start_bridge()
    host = CONFIG["server"]["host"]
    port = CONFIG["server"]["port"]
    print(f"\n  AIMate v2.1 已启动")
    print(f"  Web:   http://{host}:{port}")
    print(f"  桥:    http://127.0.0.1:{CONFIG.get('bridge',{}).get('port', 5000)}\n")
    try:
        uvicorn.run(app, host=host, port=port, log_level="warning")
    finally:
        if proc:
            proc.terminate()
