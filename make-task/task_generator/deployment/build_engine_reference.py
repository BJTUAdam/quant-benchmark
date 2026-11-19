"""Fetch or verify a portable AKQuant 0.3.76 documentation snapshot.

The source commit is fixed to the release tag; --refresh only fetches public
documentation and never alters the engine, images, or runtime configuration.
Without --refresh this command is fully offline and verifies saved hashes.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import posixpath
import re
import time
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "skills/finlab-strategy/references/akquant"
VERSION = "0.3.76"
TAG = "v0.3.76"
COMMIT = "269fab31a389a607da5c5d4872ef6b54390de6d4"
REPOSITORY = "https://github.com/akfamily/akquant"
RAW_BASE = f"https://raw.githubusercontent.com/akfamily/akquant/{COMMIT}/"
BLOB_BASE = f"{REPOSITORY}/blob/{COMMIT}/"
SOURCES = (
    "docs/zh/reference/api.md",
    "docs/zh/start/quickstart.md",
    "docs/zh/start/first_strategy.md",
    "docs/zh/guide/strategy.md",
    "docs/zh/guide/data.md",
    "docs/zh/guide/testing.md",
    "docs/zh/advanced/warm_start.md",
    "docs/zh/advanced/timezone.md",
    "docs/zh/advanced/tick_size_alignment.md",
    "docs/zh/textbook/06_stock_a.md",
    "docs/zh/textbook/07_futures.md",
    "docs/zh/textbook/09_funds.md",
    "docs/zh/textbook/appendix_pitfalls.md",
)


def sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def local_path(source: str) -> str:
    return "official/" + source.removeprefix("docs/zh/")


def fetch(source: str) -> bytes:
    for attempt in range(3):
        try:
            request = Request(RAW_BASE + source, headers={"User-Agent": "Finlab-Offline-Docs/2.0"})
            with urlopen(request, timeout=30) as response:
                raw = response.read(4_000_001)
            if len(raw) > 4_000_000 or not raw:
                raise ValueError("documentation size outside allowed range")
            return raw
        except Exception:
            if attempt == 2:
                raise
            time.sleep(attempt + 1)


def resolve_link(source: str, target: str, *, image: bool = False) -> str:
    """Keep saved documents local; point other assets to the exact source commit."""
    parts = urlsplit(target)
    if parts.scheme or parts.netloc or not parts.path:
        return target
    absolute = posixpath.normpath(posixpath.join(posixpath.dirname(source), parts.path))
    if absolute.startswith("../") or absolute.startswith("/"):
        # Root-relative upstream URLs are website routes, not repository paths.
        if parts.path.startswith("/"):
            return "https://akquant.akfamily.xyz" + target
        return BLOB_BASE + absolute.lstrip("./")
    if absolute in SOURCES and not image:
        relative = posixpath.relpath(local_path(absolute), posixpath.dirname(local_path(source)))
        return urlunsplit(("", "", relative, parts.query, parts.fragment))
    return urlunsplit(("https", "github.com", f"/akfamily/akquant/blob/{COMMIT}/{absolute}", parts.query, parts.fragment))


LINK = re.compile(r"(!?)\[([^\]\n]*)\]\(([^\s)]+)(?:\s+([\"'][^\n]*?[\"']))?\)")
REF_LINK = re.compile(r"^(\s*\[[^\]]+\]:\s*)(\S+)(.*)$", re.M)
HTML_IMAGE = re.compile(r"<img\b[^>]*\bsrc=[\"']([^\"']+)[\"'][^>]*>", re.I)


def rewrite_links(source: str, text: str) -> str:
    """Rewrite Markdown outside fenced code and replace embedded image tags."""
    output = []
    fence = None
    for line in text.splitlines(keepends=True):
        opening = re.match(r"^\s*(`{3,}|~{3,})", line)
        if opening:
            token = opening[1][0]
            if fence is None:
                fence = token
            elif fence == token:
                fence = None
            output.append(line)
            continue
        if fence:
            output.append(line)
            continue

        def link(match):
            image, label, target, title = match.groups()
            resolved = resolve_link(source, target, image=bool(image))
            if image:
                return "[原文图片：" + (label or "示意图") + "](" + resolved + ")"
            return "[" + label + "](" + resolved + (" " + title if title else "") + ")"

        line = LINK.sub(link, line)
        line = REF_LINK.sub(lambda m: m[1] + resolve_link(source, m[2]) + m[3], line)
        line = HTML_IMAGE.sub(lambda m: "[原文图片](" + resolve_link(source, m[1], image=True) + ")", line)
        output.append(line)
    return "".join(output)


def render(source: str, raw: bytes) -> bytes:
    text = raw.decode("utf-8-sig")
    banner = (
        f"> AKQuant {VERSION} 官方版本参考，固定提交 `{COMMIT}`。\n"
        f"> 原始来源：{BLOB_BASE + source}\n"
        "> 这是引擎原生API文档。本系统参与者实现 `from finlab.sdk import Strategy` 和 `on_bars(ctx)`，"
        "不要照搬原生 `on_bar`、`run_backtest`、`import akshare`、联网取数或自行保存检查点的例子。\n"
        "> 策略接口和允许的调用以本技能的 `strategy-sdk.md`、`submission.md`、当前任务契约为准；"
        "下面的官方样例是教学资料，不是任务数据、参考答案或平台实测结果。\n"
        "> 图片仅保留来源链接；未随包提供的延伸阅读指向同一固定提交。正文版权和许可归原作者。\n\n"
    )
    return (banner + rewrite_links(source, text)).encode("utf-8")


def validate(destination: Path = DEST) -> dict:
    destination = Path(destination)
    manifest = json.loads((destination / "snapshot.json").read_text(encoding="utf-8"))
    if manifest.get("engine_version") != VERSION or manifest.get("source_commit") != COMMIT:
        raise ValueError("documentation snapshot version does not match fixed engine")
    entries = manifest["documents"]
    if {entry["source_path"] for entry in entries} != set(SOURCES):
        raise ValueError("documentation source allowlist mismatch")
    for entry in [*entries, manifest["license"]]:
        relative = PurePosixPath(entry["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("invalid snapshot path")
        raw = (destination / entry["path"]).read_bytes()
        if sha(raw) != entry["sha256"]:
            raise ValueError("saved documentation hash mismatch: " + entry["path"])
    return {
        "status": "passed",
        "mode": "offline_hash_validation",
        "engine_version": VERSION,
        "source_commit": COMMIT,
        "official_documents": len(entries),
        "license": manifest["license"]["path"],
        "runtime_changed": False,
        "strategy_native_api_authorized": False,
    }


def build(refresh: bool = False, destination: Path = DEST) -> dict:
    destination = Path(destination)
    if not refresh:
        return validate(destination)
    destination.mkdir(parents=True, exist_ok=True)

    def one(source):
        raw = fetch(source)
        body = render(source, raw)
        target = destination / local_path(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        heading = re.search(r"^#\s+(.+)$", raw.decode("utf-8-sig"), re.M)
        return {"source_path": source, "path": local_path(source),
                "title": heading[1].strip() if heading else source,
                "source_url": BLOB_BASE + source, "raw_url": RAW_BASE + source,
                "raw_sha256": sha(raw), "sha256": sha(body), "bytes": len(body)}

    with ThreadPoolExecutor(max_workers=4) as pool:
        documents = list(pool.map(one, SOURCES))
    license_raw = fetch("LICENSE")
    license_name = "LICENSE.upstream.txt"
    (destination / license_name).write_bytes(license_raw)
    manifest = {
        "schema_version": "finlab.skill.akquant_docs.v1",
        "engine_version": VERSION,
        "source_tag": TAG,
        "source_commit": COMMIT,
        "source_repository": REPOSITORY,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "official_documents": len(documents),
        "scope": "Selected Chinese release documentation relevant to data, strategy, matching, assets, and recovery",
        "transformations": ["prepend platform compatibility notice", "local links for saved documents", "fixed-commit links for unsaved references", "convert embedded images to source links"],
        "runtime_changed": False,
        "documents": documents,
        "license": {"path": license_name, "source_path": "LICENSE", "source_url": BLOB_BASE + "LICENSE",
                    "raw_url": RAW_BASE + "LICENSE", "sha256": sha(license_raw), "bytes": len(license_raw)},
    }
    (destination / "snapshot.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    index = [
        "# AKQuant 0.3.76 · 官方版本参考", "",
        "本目录是引擎官方资料的离线补充，帮助理解撮合、资产和恢复语义。参与者代码先读上层技能的 `strategy-sdk.md`、`submission.md`。", "",
        "本系统使用 AKQuant 回测，AKShare 是另一套行情获取库。官方入门中用 AKShare 获取数据的示例不适用于本系统的受限策略容器。", "",
        "原生 `Strategy.on_bar`、`buy/sell`、`run_backtest`、`run_from_checkpoint` 等入口由可信引擎适配；参与者使用 `finlab.sdk.Strategy.on_bars(ctx)`、`ctx.order` 和平台启动入口。不能把官方引擎功能直接等同于当前平台开放的能力。", "",
        f"固定版本 `{TAG}`，源码提交 `{COMMIT}`。共 {len(documents)} 份中文官方文档；不是整个上游网站的镜像，也不声明所有原生功能均在本平台开放。", "",
        "|文档|适用阅读场景|", "|---|---|",
    ]
    for doc in documents:
        index.append(f"|[{doc['title']}]({doc['path']})|`{doc['source_path']}`|")
    index.extend([
        "", "[来源、版本及逐文件SHA-256](snapshot.json) · [上游许可证](LICENSE.upstream.txt)", "",
        "正文中的数字、证券代码、日期和策略例子只用于官方教学，不是本任务数据或答案；不要复制到任务契约或用来声称回测成绩。", "",
        "当前平台对期货逐日现金结算、按手费用、平今差异费率及动态保证金仍有限制，详见技能的回测注意事项。以平台能力和当前任务绑定规则为准。", "",
        "未下载的图片和延伸文档仅保留固定提交来源链接，阅读这些本地Markdown不需要外部资源加载。", "",
    ])
    (destination / "index.md").write_text("\n".join(index), encoding="utf-8")
    result = validate(destination)
    result["mode"] = "fetched_public_fixed_release_and_verified"
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="Fetch the same fixed release's public documentation")
    parser.add_argument("--output", type=Path, default=DEST)
    options = parser.parse_args()
    print(json.dumps(build(options.refresh, options.output), ensure_ascii=False, indent=2))
