"""Render the non-secret OpenAI settings into the JSON consumed by each existing CLI."""
import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path


PLUGIN = Path(__file__).resolve().parents[1] / "mem-plugin"


def trajectory_credential(run):
    digest = hashlib.sha256(os.fsencode(run)).hexdigest()
    label = f"trajectory:{run.name}:{digest[:16]}"
    return PLUGIN / "data/trajectory-credentials" / f"{digest}.json", label


def render(kind, source, *, isolated_url=None, status=False):
    config = json.loads(source.read_text(encoding="utf-8-sig"))
    if config.pop("isolated_memory", False):
        if kind != "trajectory" or (isolated_url is None and not status):
            raise ValueError("隔离记忆配置请使用 trajectory-runner/start.sh 逐题运行")
        if isolated_url is not None:
            memory = [skill for skill in config.get("skills", [])
                      if skill["name"] == "mcp" and skill.get("enabled", True)]
            servers = memory[0]["config"]["servers"] if len(memory) == 1 else []
            if len(servers) != 1 or servers[0]["name"] != "memory":
                raise ValueError("隔离记忆需要启用一个名为 memory 的 MCP 服务")
            servers[0].update(url=isolated_url, token_env="FINLAB_ISOLATED_MEMORY_TOKEN")
    if kind == "batch":
        template = source.parent / config["template"]
        config["template"] = str(render("trajectory", template))
    else:
        url = os.environ["OPENAI_BASE_URL"].strip()
        model = os.environ["OPENAI_MODEL"].strip()
        temperature = float(os.environ["OPENAI_TEMPERATURE"])
        if not url or not model or not 0 <= temperature <= 2:
            raise ValueError("请检查 OPENAI_BASE_URL、OPENAI_MODEL 和 OPENAI_TEMPERATURE（0 到 2）")
        config.update(base_url=url, api_key_env="OPENAI_API_KEY", temperature=temperature)
        for key in ("api_key_file", "thinking", "reasoning_effort"):
            config.pop(key, None)
        if kind == "task":
            config.update(adapter="chat_completions", provider_name="openai_compatible",
                          generation_model=model, review_model=model)
        else:
            config.update(provider="openai_compatible", model=model)

    target_dir = source.parent
    if kind == "trajectory":
        # Move the generated profile out of the source tree while preserving path semantics.
        for key in ("finlab_root", "finlab_python", "runs_root"):
            config[key] = os.path.abspath(source.parent / config[key])
        task = config.get("task", {})
        if task.get("kind") == "bound_task":
            task["binding"] = os.path.abspath(source.parent / task["binding"])
        target_dir = Path(config["runs_root"]) / ".configs"
        target_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    data = (json.dumps(config, ensure_ascii=False, indent=2) + "\n").encode()
    digest = hashlib.sha256(data).hexdigest()[:16]
    target = target_dir / f".{source.stem}.{digest}.local.json"
    if not target.exists() or target.read_bytes() != data:
        with tempfile.NamedTemporaryFile(dir=target_dir, delete=False) as temp:
            temp.write(data)
        try:
            os.replace(temp.name, target)
        finally:
            Path(temp.name).unlink(missing_ok=True)
    return target


def scoped_memory_connection(source, config):
    import fcntl

    run_id = config["run_id"]
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,39}", run_id):
        raise ValueError("run_id 无效")
    run = (source.parent / config["runs_root"] / run_id).resolve()
    python = PLUGIN / ".venv/bin/python"
    if not python.is_file():
        raise ValueError("请先准备 mem-plugin 的独立 uv 环境")
    path, label = trajectory_credential(run)
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    # The operator CLI reads only the plugin's own configuration.
    process_settings = {"PATH", "HOME", "USER", "LOGNAME", "LANG", "TMPDIR"}
    plugin_env = {k: v for k, v in os.environ.items()
                  if k in process_settings or k.startswith("LC_")}
    with path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not path.is_file():
            try:
                subprocess.run(
                    [str(python), "-I", "-B", "-m", "mem_plugin", "credentials",
                     "issue", "--label", label, "--output", str(path)],
                    cwd=PLUGIN, env=plugin_env, stdout=subprocess.DEVNULL, check=True, timeout=30,
                )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                raise ValueError("无法签发轨迹 MCP 凭证，请检查 mem-plugin 的迁移和配置") from None
        connection = json.loads(path.read_text())
    header = connection["headers"]["Authorization"]
    if connection["label"] != label or not re.fullmatch(r"Bearer mp_[A-Za-z0-9_-]{43}", header):
        raise ValueError("MCP 凭证与当前轨迹标签不匹配")
    if datetime.fromisoformat(connection["expires_at"]) <= datetime.now(UTC):
        raise ValueError(f"MCP 凭证已过期，请轮换标签 {label} 的凭证文件 {path}")
    return connection["url"], header.removeprefix("Bearer "), label


def reset_trajectory_memory(run, token):
    import fcntl

    from mem_plugin.bootstrap.container import create_note_service
    from mem_plugin.config.settings import load_settings
    from mem_plugin.engine.models import AccessContext
    from mem_plugin.infrastructure.persistence.postgres.credentials import CredentialStore
    from mem_plugin.infrastructure.persistence.postgres.database import Database
    from mem_plugin.infrastructure.persistence.postgres.unit_of_work import PostgresUnitOfWork

    path, label = trajectory_credential(run)
    lock_path = path.with_suffix(".lock")
    if path.is_symlink() or lock_path.is_symlink():
        raise ValueError("轨迹记忆凭证必须是普通文件")
    with lock_path.open("rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        connection = json.loads(path.read_text())
        if connection["label"] != label or connection["headers"]["Authorization"] != "Bearer " + token:
            raise ValueError("轨迹记忆凭证不匹配")
        settings = load_settings()
        database = Database(settings.database_url)
        try:
            database.check_schema()
            principal = CredentialStore(database.engine).resolve(token)
            if not principal or principal["label"] != label:
                raise ValueError("轨迹记忆凭证已失效")
            namespace = principal["namespace"]
            if connection["namespace"] != namespace or not re.fullmatch(r"client_[0-9a-f]{32}", namespace):
                raise ValueError("轨迹记忆空间不匹配")
            access = AccessContext(namespace, namespace, namespace)
            with PostgresUnitOfWork(database, access) as uow:
                if not uow.nodes.ids(1):
                    return 0
            with create_note_service(settings.database_url, use_models=False) as memory:
                deleted = memory.forget_all(access)
            with PostgresUnitOfWork(database, access) as uow:
                if uow.nodes.ids(1):
                    raise ValueError("轨迹记忆未清空")
            return deleted
        finally:
            database.close()


async def check_memory_service(url, token):
    from fastmcp import Client

    try:
        async with Client(url, auth=token, timeout=5, init_timeout=5) as client:
            tools = {item.name for item in await client.list_tools()}
            if not {"memory.search", "memory.record"} <= tools:
                raise ValueError("MCP 缺少必需的记忆工具")
    except Exception:
        raise ValueError("无法用轨迹凭证连接常驻 mem-plugin，请确认服务已启动并完成迁移") from None


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "reset-trajectory-memory":
        try:
            print(reset_trajectory_memory(Path(sys.argv[2]).resolve(), sys.stdin.read().strip()))
        except Exception:
            sys.exit("无法校验或清理轨迹记忆")
        sys.exit(0)
    kind, template, *command = sys.argv[1:]
    try:
        needs_key = any(arg in command for arg in ("generate", "start", "run", "--api"))
        if needs_key and "--help" not in command and not os.environ.get("OPENAI_API_KEY", "").strip():
            sys.exit("请先在 quant-benchmark/.env 填写 OPENAI_API_KEY。")
        source = Path(template).resolve()
        isolated_url = None
        if kind == "trajectory" and "status" not in command:
            config = json.loads(source.read_text(encoding="utf-8-sig"))
            if config.get("isolated_memory"):
                isolated_url, token, label = scoped_memory_connection(source, config)
                if "start" in command:
                    asyncio.run(check_memory_service(isolated_url, token))
                    print(f"已连接常驻记忆服务，轨迹标签 {label}", flush=True)
                os.environ.pop("MEM_PLUGIN_MCP_TOKEN", None)
                os.environ["FINLAB_ISOLATED_MEMORY_TOKEN"] = token
        profile = render(kind, source, isolated_url=isolated_url, status="status" in command)
        args = [str(profile) if arg == "{profile}" else arg for arg in command]
        os.execvp(args[0], args)
    except (KeyError, OSError, ValueError) as error:
        sys.exit(f"无法生成实验配置，请核对模板及根目录 .env。{error}")
