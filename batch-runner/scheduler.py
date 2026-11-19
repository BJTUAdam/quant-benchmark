"""调度循环：并发上限、内存预检、超时、失败重试、中断续跑。

每条轨迹是一个独立子进程（`finlab_v2.py start`），拥有独立的 run_id 与运行目录；
轨迹内部的回测容器由产题系统按任务各自创建，批跑系统不干预。
"""
from __future__ import annotations

import contextlib
import datetime
import hashlib
import json
import os
import signal
import subprocess
import threading
import time

import report
import spec as specmod


class BatchBusy(RuntimeError):
    pass


@contextlib.contextmanager
def exclusive(batch_dir):
    """同一批次只允许一个控制器；锁在进程退出时由系统释放，不要手动删除。"""
    os.makedirs(batch_dir, exist_ok=True)
    path = os.path.join(batch_dir, ".controller.lock")
    with open(path, "a+b") as handle:
        if os.path.getsize(path) == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise BatchBusy("这个批次已经有控制器在运行；不要删除锁文件") from None
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def available_memory_mb():
    try:
        with open("/proc/meminfo", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def pid_alive(pid):
    if not pid or os.name != "posix":
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def pid_cmdline(pid):
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as handle:
            return [os.fsdecode(arg) for arg in handle.read().rstrip(b"\0").split(b"\0")]
    except OSError:
        return None


class Scheduler:
    def __init__(self, spec, store, out=None, clock=time.time, sleep=time.sleep):
        self.spec, self.store, self.clock, self.sleep = spec, store, clock, sleep
        self.out = out or (lambda message: print(message, flush=True))
        self.running = {}
        self.jobs = {job["job_id"]: job for job in spec["jobs"]}
        self.stop_requested = False
        self.next_launch = 0.0
        self.memory_waiting = False
        self.memory_baseline = None
        self.logs = os.path.join(spec["batch_dir"], "logs")
        os.makedirs(self.logs, exist_ok=True)

    # ---- 日志 ----
    def log(self, message):
        line = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S ") + message
        self.out(line)
        with open(os.path.join(self.spec["batch_dir"], "batch.log"), "a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    # ---- 子进程 ----
    def command(self, config_file):
        s = self.spec
        return [*s["command_prefix"], s["python"], "-I", "-B", s["runner_script"], "start", "--config", config_file]

    def owner(self, run_id):
        path = os.path.realpath(report.run_dir_of(self.spec, run_id))
        return hashlib.sha256(os.fsencode(path)).hexdigest()

    def cleanup(self, run_id):
        if self.spec["template"].get("execution_backend") != "docker":
            return
        env = {**os.environ, **self.spec["env"]}
        label = "label=finlab.batch-owner=" + self.owner(run_id)

        def docker(*args):
            try:
                return subprocess.run(["docker", *args], env=env, capture_output=True,
                                      text=True, check=True, timeout=30).stdout.splitlines()
            except (OSError, subprocess.SubprocessError):
                raise RuntimeError(f"无法确认 {run_id} 的 Docker 资源已清理，请检查 Docker 后恢复批次") from None

        containers = docker("ps", "-aq", "--filter", label)
        if containers:
            docker("rm", "-f", *containers)
        networks = docker("network", "ls", "-q", "--filter", label)
        if networks:
            docker("network", "rm", *networks)

    def signal_group(self, proc, name):
        if os.name == "posix":
            try:
                os.killpg(proc.pid, getattr(signal, name))
            except (ProcessLookupError, PermissionError):
                pass
        elif name == "SIGKILL":
            proc.kill()
        else:
            proc.terminate()

    def terminate(self, proc):
        """先 SIGINT（让轨迹进程自行清理回测容器），再 SIGTERM，最后 SIGKILL。"""
        if proc.poll() is not None:
            return proc.returncode
        grace = self.spec["grace_seconds"]
        steps = (("SIGINT", grace), ("SIGTERM", 10), ("SIGKILL", 10)) if os.name == "posix" \
            else (("SIGTERM", grace), ("SIGKILL", 10))
        for name, wait in steps:
            self.signal_group(proc, name)
            try:
                return proc.wait(timeout=wait)
            except subprocess.TimeoutExpired:
                continue
        raise RuntimeError(f"无法停止轨迹进程 {proc.pid}")

    def launch(self, row):
        spec, job_id, attempt = self.spec, row["job_id"], row["attempt"]
        job = {"job_id": job_id, "task": json.loads(row["task"]), "overrides": json.loads(row["overrides"])}
        if specmod.binding_digest(job["task"]) != self.jobs[job_id]["binding_digest"]:
            raise specmod.SpecError(f"{job_id} 的正式题绑定已变化，请恢复原文件或使用新批次")
        run_id, config_file = specmod.write_config(spec, job, attempt)
        if run_id != row["run_id"]:
            raise RuntimeError(f"run_id 不一致：{run_id} / {row['run_id']}")
        env = os.environ.copy()
        env.update(spec["env"])
        env["FINLAB_BATCH_OWNER"] = self.owner(run_id)
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        extra = {"start_new_session": True} if os.name == "posix" else {}
        log_file = os.path.join(self.logs, run_id + ".log")
        now = self.clock()
        self.store.start(job_id, None, now)
        try:
            with open(log_file, "ab") as handle:
                handle.write(f"\n===== {run_id} 第 {attempt} 次尝试 =====\n".encode("utf-8"))
                handle.flush()
                proc = subprocess.Popen(self.command(config_file), cwd=spec["runner_root"], env=env,
                                        stdin=subprocess.DEVNULL, stdout=handle, stderr=subprocess.STDOUT, **extra)
        except OSError as exc:
            error = f"LAUNCH_FAILED: {type(exc).__name__}"
            verdict = self.store.fail_or_retry(spec, job_id, now, None, error)
            self.log(f"启动失败 {job_id}（{error}）→ {verdict}")
            return
        self.running[job_id] = {"proc": proc, "started": now, "run_id": run_id, "log": log_file,
                                "config": config_file}
        self.store.start(job_id, proc.pid, now)
        self.log(f"启动 {job_id}  run_id={run_id}  第 {attempt}/{row['attempt_limit']} 次  pid={proc.pid}")

    def explain(self, item, rc, state):
        try:
            with open(item["log"], "rb") as handle:
                handle.seek(0, os.SEEK_END)
                handle.seek(max(0, handle.tell() - 8192))
                tail = handle.read().decode("utf-8", "replace")
            for line in reversed(tail.splitlines()):
                if line.startswith("FAILED:"):
                    return line[:240]
        except OSError:
            pass
        if state.get("last_error"):
            return "RUN_ERROR: " + str(state["last_error"])[:200]
        if rc == 0:
            return "STATE_NOT_COMPLETED"
        return f"EXIT_{rc}"

    def settle(self, job_id, item, rc, timed_out):
        now = self.clock()
        self.cleanup(item["run_id"])
        state = report.load(os.path.join(report.run_dir_of(self.spec, item["run_id"]), "state.json")) or {}
        if rc == 0 and not timed_out and state.get("status") == "completed" and state.get("segments") == 7:
            self.store.complete(job_id, now, rc)
            self.log(f"完成 {job_id}  run_id={item['run_id']}  用时 {int(now - item['started'])} 秒")
            self.export_html(item)
            return
        error = "JOB_TIMEOUT" if timed_out else self.explain(item, rc, state)
        verdict = self.store.fail_or_retry(self.spec, job_id, now, rc, error)
        self.log(f"失败 {job_id}  run_id={item['run_id']}  {error} → "
                 + ("将以新 run_id 重试" if verdict == "retry" else "已用完尝试次数"))

    def export_html(self, item):
        spec = self.spec
        script = os.path.join(spec["runner_root"], "project", "demo_runner", "export_trajectory_html.py")
        if not spec["export_html"] or not os.path.isfile(script):
            return
        try:
            result = subprocess.run([spec["python"], "-B", script, report.run_dir_of(spec, item["run_id"])],
                                    cwd=spec["runner_root"], capture_output=True, timeout=120)
            if result.returncode:
                self.log(f"轨迹页面导出失败 {item['run_id']}（不影响任务结果）")
        except (OSError, subprocess.SubprocessError):
            self.log(f"轨迹页面导出失败 {item['run_id']}（不影响任务结果）")

    # ---- 调度 ----
    def reap(self):
        limit = self.spec["job_timeout_seconds"]
        for job_id, item in list(self.running.items()):
            proc, timed_out = item["proc"], False
            rc = proc.poll()
            if rc is None and limit is not None and self.clock() - item["started"] > limit:
                self.log(f"超时 {job_id}（{limit} 秒），正在停止")
                rc, timed_out = self.terminate(proc), True
            if rc is None:
                continue
            self.settle(job_id, item, rc, timed_out)
            del self.running[job_id]

    def memory_ok(self):
        need = self.spec["per_job_memory_mb"]
        if not need:
            return True
        free = available_memory_mb()
        if free is None:
            return len(self.running) == 0
        if not self.running or self.memory_baseline is None:
            self.memory_baseline = free
        return min(free, self.memory_baseline - need * len(self.running)) >= need

    def launch_ready(self):
        while len(self.running) < self.spec["concurrency"] and not self.stop_requested:
            if self.clock() < self.next_launch:
                return
            row = self.store.next_pending(self.clock())
            if row is None:
                return
            if not self.memory_ok():
                if not self.running:
                    raise RuntimeError("可用内存低于单条轨迹的内存预留，批次保持待运行")
                if not self.memory_waiting:
                    self.log("可用内存不足，暂缓启动新任务")
                self.memory_waiting = True
                return
            self.memory_waiting = False
            self.launch(row)
            self.next_launch = self.clock() + self.spec["launch_interval_seconds"]

    def kill_orphan(self, row):
        """上次控制器异常退出时可能留下仍在运行的轨迹进程；仅在确认命令行匹配时终止。"""
        pid = row["pid"]
        if os.name != "posix":
            return
        config = specmod.config_path(self.spec, row["run_id"])
        if not pid:
            # A controller can exit between Popen and saving the child PID.
            matches = [int(name) for name in os.listdir("/proc") if name.isdecimal()
                       and config in (pid_cmdline(name) or [])]
            if len(matches) > 1:
                raise RuntimeError(f"无法唯一识别 {row['run_id']} 的残留进程")
            pid = next(iter(matches), None)
        if not pid_alive(pid):
            return
        cmdline = pid_cmdline(pid)
        if not cmdline or config not in cmdline or os.getpgid(pid) != pid:
            raise RuntimeError(f"无法确认旧进程 {pid} 属于 {row['run_id']}，暂停恢复")
        grace = self.spec["grace_seconds"]
        for name, wait in (("SIGINT", grace), ("SIGTERM", 10), ("SIGKILL", 10)):
            try:
                os.killpg(pid, getattr(signal, name))
            except (ProcessLookupError, PermissionError):
                return
            deadline = time.time() + wait
            while time.time() < deadline:
                if not pid_alive(pid):
                    return
                time.sleep(0.2)
        raise RuntimeError(f"旧进程 {pid} 尚未退出，暂停恢复")

    def interrupted(self, row):
        run_id = row["run_id"]
        self.cleanup(run_id)
        root = os.path.join(self.spec["batch_dir"], "runs", run_id)
        state = report.load(os.path.join(root, "state.json"))
        if state and state.get("status") == "completed" and state.get("segments") == 7:
            self.store.complete(row["job_id"], self.clock(), 0)
            self.export_html({"run_id": run_id})
            return
        # FinLab refuses to replay in-flight model/tool work or incomplete preparation.
        stages = os.path.join(root, "stages")
        records = [report.load(os.path.join(stages, name)) for name in os.listdir(stages)
                   if name.endswith(".json")] if os.path.isdir(stages) else []
        incomplete = (os.path.exists(os.path.join(root, "lock.json")) and not state)
        if incomplete or (state or {}).get("status") == "failed" or any(
                not item or item.get("pending") or item.get("status") == "failed" for item in records):
            verdict = self.store.fail_or_retry(self.spec, row["job_id"], self.clock(), None,
                                               "INTERRUPTED_WITHOUT_SAFE_CHECKPOINT")
            self.log(f"{run_id} 无安全续跑点，处理结果 {verdict}")
        else:
            self.store.interrupt(row["job_id"])
            self.log(f"{run_id} 已重新排队，将从已有检查点恢复")

    def recover(self):
        for row in self.store.with_status("running"):
            self.kill_orphan(row)
            self.interrupted(row)

    def shutdown(self):
        failures = []
        for job_id, item in list(self.running.items()):
            try:
                self.terminate(item["proc"])
                row = self.store.get(job_id)
                if row["status"] == "running":
                    self.interrupted(row)
            except RuntimeError as error:
                failures.append(str(error))
        self.running.clear()
        if failures:
            raise RuntimeError(" / ".join(failures))

    def on_signal(self, number, frame):
        if not self.stop_requested:
            self.log("收到停止信号，正在停止任务并检查可恢复状态")
        self.stop_requested = True

    def install_signals(self):
        saved = {}
        if threading.current_thread() is threading.main_thread():
            for name in ("SIGINT", "SIGTERM"):
                number = getattr(signal, name, None)
                if number is not None:
                    saved[number] = signal.signal(number, self.on_signal)
        return saved

    def run(self):
        self.recover()
        saved = self.install_signals()
        try:
            while not self.stop_requested:
                self.reap()
                self.launch_ready()
                if not self.running and not self.store.has_active():
                    break
                self.sleep(self.spec["poll_seconds"])
        finally:
            try:
                self.shutdown()
            finally:
                for number, handler in saved.items():
                    signal.signal(number, handler)
        counts = self.store.counts()
        self.log("批次结束：" + "  ".join(f"{k}={v}" for k, v in counts.items()))
        if self.stop_requested:
            return 130
        return 1 if counts["failed"] else 0
