"""Run only staged task code in a constrained Docker container.

The trusted scheduler, model client, credentials, and package source stay on the
host. An image ID is required so a tag cannot silently change during a run.
"""
from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess
import uuid


IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")


def validate_image(image: str | None) -> str:
    if not isinstance(image, str) or not IMAGE_ID.fullmatch(image):
        raise RuntimeError("Docker 执行后端须登记不可变的 sha256 镜像 ID")
    if not shutil.which("docker"):
        raise RuntimeError("Docker CLI 不可用；不会退回到不隔离执行")
    return image


def inspect_image(image: str | None) -> None:
    image = validate_image(image)
    result = subprocess.run(["docker", "image", "inspect", "--format", "{{.Id}}", image],
                            capture_output=True, text=True, timeout=20)
    if result.returncode or result.stdout.strip() != image:
        raise RuntimeError("登记的 Docker 镜像 ID 在本机不存在或已改变")


def _mount(source: pathlib.Path, target: str, readonly: bool = True) -> list[str]:
    source = source.resolve(strict=True)
    if "," in str(source):
        raise RuntimeError("Docker 挂载路径不能包含逗号")
    value = f"type=bind,src={source},dst={target}"
    if readonly:
        value += ",readonly"
    return ["--mount", value]


def execute(image: str | None, mode: str, workspace: pathlib.Path,
            args: list[str] | None = None, code: str | None = None,
            timeout: int = 300) -> subprocess.CompletedProcess[str]:
    """Mount one materialized stage; never mount a package root or the host project."""
    image = validate_image(image)
    if os.name != "posix" or os.getuid() == 0:
        raise RuntimeError("Docker 沙箱须从非 root Linux / WSL 控制端启动")
    if mode not in {"research", "backtest"}:
        raise RuntimeError("未知 Docker 沙箱操作")
    here = pathlib.Path(__file__).resolve().parent
    name = "financial-agent-" + uuid.uuid4().hex[:16]
    command = ["docker", "run", "--rm", "--pull", "never", "--name", name,
               "--network", "none", "--user", f"{os.getuid()}:{os.getgid()}",
               "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
               "--pids-limit", "128", "--cpus", "2", "--memory", "4g", "--memory-swap", "4g",
               "--ulimit", "fsize=52428800:52428800",
               "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=64m",
               "--env", "HOME=/tmp", "--env", "PYTHONDONTWRITEBYTECODE=1"]
    output = workspace / "output"
    if output.is_symlink():
        raise RuntimeError("研究输出目录不能是符号链接")
    output.mkdir(exist_ok=True)
    if not output.is_dir() or output.resolve(strict=True).parent != workspace.resolve(strict=True):
        raise RuntimeError("研究输出目录越过当前任务工作区")
    owner = os.environ.get("FINLAB_BATCH_OWNER")
    if owner:
        command += ["--label", "finlab.batch-owner=" + owner]
    command += _mount(workspace, "/workspace") + _mount(output, "/workspace/output", False)
    command += ["--workdir", "/workspace"]
    if mode == "research":
        command += _mount(here / "research_runner.py", "/research.py")
        program = ["/usr/local/bin/python", "/research.py", code or ""]
    else:
        command += _mount(here / "strategy_guard.py", "/guard.py")
        program = ["/usr/local/bin/python", "/guard.py", "--backtest", *(args or [])]
    command += [image, *program]
    # Never forward model credentials or a host Docker socket into the container.
    clean_env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": "/tmp",
                 "DOCKER_CONFIG": str(pathlib.Path.home() / ".docker")}
    clean_env.update({k: os.environ[k] for k in ("DOCKER_HOST", "DOCKER_CONFIG", "DOCKER_CONTEXT") if k in os.environ})
    try:
        return subprocess.run(command, cwd="/tmp", env=clean_env, capture_output=True,
                              text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        subprocess.run(["docker", "rm", "-f", name], cwd="/tmp", env=clean_env,
                       capture_output=True, text=True, timeout=20)
        raise
