"""Create an isolated WSL runtime. No API credentials or model calls are used."""
import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

HERE = pathlib.Path(__file__).resolve().parent
VENV = HERE / ".venv"
INDEX = "https://pypi.tuna.tsinghua.edu.cn/simple"


def fetch(url, target):
    subprocess.run(["curl", "-q", "--fail", "--location", "--silent", "--show-error",
                    "--connect-timeout", "10", "--max-time", "60", "--retry", "2",
                    "--retry-delay", "1", "--retry-all-errors", "--output", str(target), url], check=True)


def main():
    if sys.platform != "linux":
        raise SystemExit("请在 WSL / Linux 中执行")
    subprocess.run([sys.executable, "-I", "-m", "venv", "--without-pip", str(VENV)], check=True)
    python = VENV / "bin/python3"
    with tempfile.TemporaryDirectory(prefix="runtime-bootstrap-") as temp_name:
        temp = pathlib.Path(temp_name)
        metadata_file = temp / "pip.json"
        fetch("https://pypi.org/pypi/pip/25.0.1/json", metadata_file)
        metadata = json.loads(metadata_file.read_text())
        wheel = next(item for item in metadata["urls"] if item["filename"].endswith(".whl"))
        archive = temp / "pip.whl"
        fetch(wheel["url"], archive)
        data = archive.read_bytes()
        if hashlib.sha256(data).hexdigest() != wheel["digests"]["sha256"]:
            raise RuntimeError("pip wheel 哈希不匹配")
        bootstrap = temp / "bootstrap"
        with zipfile.ZipFile(archive) as z:
            if any(pathlib.PurePosixPath(n).is_absolute() or ".." in pathlib.PurePosixPath(n).parts for n in z.namelist()):
                raise RuntimeError("pip wheel 路径不合法")
            z.extractall(bootstrap)
        code = "import sys,runpy;sys.path.insert(0," + repr(str(bootstrap)) + ");runpy.run_module('pip',run_name='__main__')"
        cmd = [str(python), "-I", "-c", code, "--isolated", "--disable-pip-version-check",
               "install", "--no-input", "--no-cache-dir", "--no-compile", "--only-binary=:all:", "--progress-bar", "off",
               "--timeout", "30", "--retries", "1", "--index-url", INDEX,
               "-r", str(HERE / "runtime_requirements.txt")]
        print("Installing pinned dependencies into demo_runner/.venv", flush=True)
        with (HERE / "runtime_setup.log").open("w", encoding="utf-8") as log:
            result = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, timeout=300)
        print((HERE / "runtime_setup.log").read_text(encoding="utf-8")[-2200:])
        if result.returncode:
            raise SystemExit(result.returncode)
    check = subprocess.run([str(python), "-I", "-c",
                            "import json,numpy,pandas,scipy;print(json.dumps({m.__name__:m.__version__ for m in (numpy,pandas,scipy)}))"],
                           check=True, capture_output=True, text=True)
    record = {"python": str(python), "versions": json.loads(check.stdout),
              "index_url": INDEX,
              "requirements_sha256": hashlib.sha256((HERE / "runtime_requirements.txt").read_bytes()).hexdigest(),
              "pip_bootstrap_sha256": wheel["digests"]["sha256"]}
    (HERE / "runtime_setup.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    print("RUNTIME READY: " + json.dumps(record["versions"]), flush=True)


if __name__ == "__main__":
    main()
