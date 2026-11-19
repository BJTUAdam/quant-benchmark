"""Project-local dependencies shared by the host and isolated subprocesses."""
import importlib
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent


def check_runtime():
    versions = {}
    for name, minimum in (("numpy", (1, 24)), ("pandas", (2, 0))):
        try:
            module = importlib.import_module(name)
        except ImportError:
            raise RuntimeError(f"缺少 {name}；请先在运行器目录执行 uv sync --locked") from None
        version = module.__version__
        match = re.match(r"^(\d+)\.(\d+)", version)
        if not match or tuple(map(int, match.groups())) < minimum:
            raise RuntimeError(f"{name} {version} 不满足 >={minimum[0]}.{minimum[1]}；"
                               "请使用运行器的 uv 环境")
        versions[name] = version
    return versions


def sandbox_runtime_mounts():
    version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    venv = HERE.parents[1] / ".venv"
    if not venv.is_dir() or pathlib.Path(sys.prefix) == HERE / ".venv":
        venv = HERE / ".venv"
    site = venv / "lib" / version / "site-packages"
    if not all((site / name / "__init__.py").is_file() for name in ("numpy", "pandas")):
        raise RuntimeError("项目隔离环境尚未安装；请先在运行器目录执行 uv sync --locked")
    # The system interpreter finds /usr/local before /usr/lib. Keep dependency
    # reads under the strategy guard's existing /usr whitelist, read-only.
    return ["--ro-bind", str(site), f"/usr/local/lib/{version}/dist-packages"]
