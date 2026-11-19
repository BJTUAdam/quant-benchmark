"""Trusted simulator host; execute only Agent decide() inside Docker over stdio."""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import pickle
import select
import subprocess
import sys
import time

from agent_tools import validate_strategy
from docker_sandbox import validate_image


MAX_RESPONSE = 2 * 1024 * 1024
MAX_REQUEST = 1024 * 1024 * 1024


def read_exact(stream, count: int, timeout: float = 60.0) -> bytes:
    result = bytearray()
    deadline = time.monotonic() + timeout
    while len(result) < count:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([stream], [], [], remaining)[0]:
            raise TimeoutError("Docker strategy response timed out")
        chunk = os.read(stream.fileno(), count - len(result))
        if not chunk:
            raise RuntimeError("Docker strategy process exited")
        result.extend(chunk)
    return bytes(result)


def container_command(image: str, name: str, here: pathlib.Path, uid: int, gid: int) -> list[str]:
    """Only service code enters the container; task and operator files never mount."""
    return ["docker", "run", "--rm", "--pull", "never", "--name", name,
            "--network", "none", "--user", f"{uid}:{gid}",
            "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--pids-limit", "128", "--cpus", "2", "--memory", "4g", "--memory-swap", "4g",
            "--ulimit", "fsize=52428800:52428800",
            "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=64m",
            "--mount", f"type=bind,src={(here / 'strategy_service.py').resolve()},dst=/service.py,readonly",
            "--mount", f"type=bind,src={(here / 'strategy_guard.py').resolve()},dst=/guard.py,readonly",
            "--env", "HOME=/tmp", "--env", "PYTHONDONTWRITEBYTECODE=1",
            "--workdir", "/tmp", "-i", image, "/usr/local/bin/python", "/service.py"]


class DockerStrategy:
    def __init__(self, source: str, engine, image: str, name: str):
        validate_strategy(source)  # Static check on trusted host; do not execute it here.
        image = validate_image(image)
        if os.name != "posix" or os.getuid() == 0:
            raise RuntimeError("Docker strategy requires a non-root Linux controller")
        here = pathlib.Path(__file__).resolve().parent
        command = container_command(image, name, here, os.getuid(), os.getgid())
        self.clean_env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": "/tmp"}
        self.name = name
        self.proc = subprocess.Popen(command, cwd="/tmp", env=self.clean_env,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, bufsize=0)
        try:
            info = self.request({"kind": "init", "source": source})
            if not info.get("ok"):
                raise RuntimeError("隔离策略加载失败：" + str(info.get("error", "unknown")))
            if not isinstance(info.get("params"), dict):
                raise RuntimeError("策略 PARAMS 无效")
            self.PARAMS = info["params"]
            times = info.get("decision_times")
            if hasattr(engine, "MINUTE_POSITION"):
                times = list(times if times is not None else engine.DEFAULT_DECISION_TIMES)
                if not times or any(type(t) is not int or t not in engine.MINUTE_POSITION for t in times):
                    raise RuntimeError("策略 DECISION_TIMES 无效")
                self.DECISION_TIMES = sorted(set(times))
        except BaseException:
            self.close()
            raise

    def request(self, value: dict) -> dict:
        payload = pickle.dumps(value, protocol=5)
        if not 0 < len(payload) <= MAX_REQUEST:
            raise RuntimeError("策略输入过大")
        frame = memoryview(len(payload).to_bytes(8, "big") + payload)
        offset = 0
        while offset < len(frame):
            sent = self.proc.stdin.write(frame[offset:])
            if not sent:
                raise RuntimeError("Docker strategy input closed")
            offset += sent
        header = read_exact(self.proc.stdout, 8)
        length = int.from_bytes(header, "big")
        if not 0 < length <= MAX_RESPONSE:
            raise RuntimeError("策略返回数据过大或协议错误")
        response = json.loads(read_exact(self.proc.stdout, length).decode("utf-8"))
        if not isinstance(response, dict) or type(response.get("ok")) is not bool:
            raise RuntimeError("策略返回协议错误")
        return response

    def decide(self, *args):
        memory = args[-1]
        if not isinstance(memory, dict):
            raise RuntimeError("策略记忆必须是字典")
        response = self.request({"kind": "decide", "args": args})
        updated = response.get("memory")
        if updated is not None:
            if not isinstance(updated, dict):
                raise RuntimeError("策略记忆返回无效")
            memory.clear()
            memory.update(updated)
        if not response["ok"]:
            raise RuntimeError(str(response.get("error", "策略运行失败"))[:500])
        return response.get("target")

    def close(self) -> None:
        if self.proc.poll() is None:
            try:
                payload = pickle.dumps({"kind": "stop"}, protocol=5)
                self.proc.stdin.write(len(payload).to_bytes(8, "big") + payload)
                self.proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=2)
        subprocess.run(["docker", "rm", "-f", self.name], cwd="/tmp", env=self.clean_env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--container-name", required=True)
    parser.add_argument("--eval", required=True)
    args, simulate_args = parser.parse_known_args()
    stage = pathlib.Path(args.eval).resolve(strict=True)
    sys.path.insert(0, str(stage))
    from backtester import engine
    services = []

    def load_strategy(path):
        source = pathlib.Path(path).read_text(encoding="utf-8")
        service = DockerStrategy(source, engine, args.image,
                                 args.container_name + f"-{len(services)}")
        services.append(service)
        return service

    engine.load_strategy = load_strategy
    import simulate
    os.chdir(stage)
    sys.argv = [str(stage / "simulate.py"), *simulate_args]
    try:
        simulate.main()
    finally:
        for service in reversed(services):
            service.close()


if __name__ == "__main__":
    main()
