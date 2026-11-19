"""Build a portable, offline strategy skill from versioned official documents.

No credentials or market data are collected. Only public documentation is read.
The runtime catalog is intentionally not changed by a documentation refresh.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys
import time
from urllib.parse import urljoin
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills/finlab-strategy"
REF = SKILL / "references/tushare"
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


class ArticleMarkdown(HTMLParser):
    """Extract the main Tushare article, preserving tables and code blocks."""

    def __init__(self, base_url):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.active = False
        self.done = False
        self.depth = 0
        self.skip = 0
        self.parts = []
        self.pre = False
        self.table = None
        self.row = None
        self.cell = None
        self.anchors = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if self.done:
            return
        if not self.active:
            classes = attrs.get("class", "").split()
            if tag == "div" and "content" in classes and "col-md-9" in classes:
                self.active = True
                self.depth = 1
            return
        if tag == "div":
            self.depth += 1
        if self.skip:
            if tag not in VOID:
                self.skip += 1
            return
        if tag in {"script", "style"} or "search-panel" in attrs.get("class", "").split():
            self.skip = 1
            return
        if tag == "pre":
            self.pre = True
            self.parts.append("\n\n```text\n")
        elif self.pre:
            return
        elif tag == "table":
            self.table = []
        elif tag == "tr" and self.table is not None:
            self.row = []
        elif tag in {"th", "td"} and self.row is not None:
            self.cell = []
        elif self.cell is not None:
            if tag == "br":
                self.cell.append(" / ")
        elif re.fullmatch(r"h[1-6]", tag):
            self.parts.append("\n\n" + "#" * min(4, int(tag[1])) + " ")
        elif tag in {"p", "ul", "ol", "blockquote"}:
            self.parts.append("\n\n")
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in {"br", "hr"}:
            self.parts.append("\n")
        elif tag == "a":
            self.parts.append("[")
            self.anchors.append(urljoin(self.base_url, attrs.get("href", "")))
        elif tag == "img":
            self.parts.append(" [原文图片：" + attrs.get("alt", "示意图") + "] ")

    def handle_endtag(self, tag):
        if not self.active or self.done:
            return
        if tag == "div":
            self.depth -= 1
            if self.depth == 0:
                self.done = True
                return
        if self.skip:
            self.skip -= 1
            return
        if tag == "pre":
            self.parts.append("\n```\n\n")
            self.pre = False
        elif self.pre:
            return
        elif tag in {"td", "th"} and self.cell is not None:
            self.row.append(re.sub(r"\s+", " ", "".join(self.cell)).strip().replace("|", "\\|"))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.table.append(self.row)
            self.row = None
        elif tag == "table" and self.table is not None:
            if self.table:
                width = max(map(len, self.table))
                rows = [r + [""] * (width - len(r)) for r in self.table]
                self.parts.append("\n\n| " + " | ".join(rows[0]) + " |\n| " + " | ".join(["---"] * width) + " |\n")
                self.parts.extend("| " + " | ".join(r) + " |\n" for r in rows[1:])
                self.parts.append("\n")
            self.table = None
        elif self.cell is not None:
            return
        elif tag == "a" and self.anchors:
            self.parts.append("](" + self.anchors.pop() + ")")
        elif tag in {"p", "li", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.active or self.done or self.skip:
            return
        if self.cell is not None:
            self.cell.append(data)
        elif self.pre:
            # A long backtick fence is escaped instead of breaking the document.
            self.parts.append(data.replace("```", "` ` `"))
        else:
            self.parts.append(re.sub(r"\s+", " ", data))

    def result(self, allow_short=False):
        if not self.done:
            raise ValueError("official main article not found or incomplete")
        segments = ''.join(self.parts).split('```')
        for i in range(0, len(segments), 2):
            segments[i] = re.sub(r"\n[ \t]+", "\n", segments[i])
            segments[i] = re.sub(r"\n{3,}", "\n\n", segments[i])
        result = '```'.join(segments).strip()
        if len(result) < 80 and not allow_short:
            raise ValueError("official article too short")
        return result + "\n"


def fetch(url):
    for attempt in range(3):
        try:
            req = Request(url, headers={"User-Agent": "Finlab-Offline-Docs/2.0"})
            with urlopen(req, timeout=30) as r:
                body = r.read(8_000_001)
            if len(body) > 8_000_000:
                raise ValueError("document size limit")
            return body
        except Exception:
            if attempt == 2:
                raise
            time.sleep(attempt + 1)


def category(name):
    if name.startswith(("fut_", "ft_")):
        return "期货"
    if name.startswith(("fund_", "etf_", "rt_etf")):
        return "基金与ETF"
    if name.startswith(("index_", "idx_", "sw_", "ci_", "tdx_", "ths_", "dc_index")):
        return "指数与成分"
    if name.startswith(("hk_", "ggt_", "rt_hk")):
        return "港股"
    if name.startswith("us_"):
        return "美股"
    if name.startswith(("cb_", "bond_", "repo_", "bc_", "sge_")):
        return "债券与其他资产"
    if name in {"income", "balancesheet", "cashflow", "forecast", "express", "dividend"} or name.startswith("fina_"):
        return "财务与公司行为"
    if name.startswith(("cn_", "eco_", "shibor", "hibor", "libor", "wz_", "sf_")):
        return "宏观与利率"
    if name.startswith("p_"):
        return "组合管理（非策略回放调用）"
    return "股票与其他接口"


def build(refresh=False):
    catalog = json.loads((ROOT / "catalog/tushare_pro.v2.json").read_text(encoding="utf-8"))
    endpoints = catalog["endpoints"]
    by_id = {str(ep["doc_id"]): ep for ep in endpoints.values()}
    all_ids = set(by_id) | set(catalog["non_api_pages"])
    REF.mkdir(parents=True, exist_ok=True)
    previous_path = REF / "snapshot.json"
    previous = json.loads(previous_path.read_text(encoding="utf-8")) if previous_path.is_file() else {}
    saved = {x["doc_id"]: x for x in previous.get("documents", [])}
    if refresh:
        home = fetch("https://tushare.pro/document/2").decode("utf-8")
        all_ids |= set(re.findall(r"/document/2\?doc_id=(\d+)", home))
    documents, errors = [], []

    def localize(article):
        def local_reference(match):
            if match[1] not in all_ids:
                return match[0]
            linked = by_id.get(match[1])
            return "../apis/" + linked["api_name"] + ".md" if linked else "../guides/" + match[1] + ".md"
        return re.sub(r"https?://tushare\.pro/document/2\?doc_id=(\d+)(?=[)#\s])", local_reference, article)

    def one(doc_id):
        ep = by_id.get(doc_id)
        relative = "apis/" + ep["api_name"] + ".md" if ep else "guides/" + doc_id + ".md"
        target = REF / relative
        if not refresh and target.is_file() and doc_id in saved:
            row = saved[doc_id]
            if hashlib.sha256(target.read_bytes()).hexdigest() != row["sha256"]:
                raise ValueError("saved documentation hash changed")
            text = target.read_text(encoding="utf-8")
            header, marker, article = text.partition("## 官方正文\n")
            localized = header + marker + localize(article) if marker else text
            if localized != text:
                target.write_text(localized, encoding="utf-8")
                row["sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
            return row
        if not refresh:
            raise ValueError("snapshot missing; run with --refresh")
        url = f"https://tushare.pro/document/2?doc_id={doc_id}"
        raw = fetch(url)
        parser = ArticleMarkdown(url)
        parser.feed(raw.decode("utf-8"))
        if ep:
            article = parser.result()
            content_status = "full_article"
        elif parser.done:
            article = parser.result(allow_short=True)
            content_status = "guide_article" if len(article.strip()) >= 80 else "navigation_or_empty"
        else:
            article = "该官方导航入口未返回可提取的文档正文。它不是已登记的Pro接口；本地未补写或推测其API。\n"
            content_status = "no_extractable_article"
        # Internal references remain navigable after copying the skill offline.
        # The source URL above the article is deliberately kept unchanged.
        article = localize(article)
        h = re.search(r"^#{1,4} (.+)$", article, re.M)
        title = h[1].strip() if h else ep["title"] if ep else "官方说明 " + doc_id
        policy = (
            "> 来源为官方公开文档的离线正文；表格、限额、权限说明与示例保留。\n"
            "> 官方示例中的行情值、日期和输出仅为接口说明，不是本任务数据、答案或平台实测。\n"
            "> 本系统策略应使用 `from finlab import tushare as ts` 或 `ctx.query`。不要直接调用官方上游、设置真实Tushare令牌或照搬示例日期。\n"
            "> 文档列出接口不表示当前任务已授权、已冻结或具备历史可见性；以当前任务契约为准。\n\n"
        )
        text = f"# {ep['api_name'] + ' · ' if ep else ''}{title}\n\n来源：{url}\n\n" + policy + "## 官方正文\n\n" + article
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return {"doc_id": doc_id, "api_name": ep["api_name"] if ep else None,
                "title": title, "group": category(ep["api_name"]) if ep else "官方使用说明与包装",
                "path": relative, "source_url": url, "fetched_at": datetime.now(timezone.utc).isoformat(),
                "content_status": content_status,
                "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                "raw_sha256": hashlib.sha256(raw).hexdigest(),
                "official_params": list(ep.get("param_docs", {})) if ep else [],
                "official_fields": list(ep.get("field_docs", {})) if ep else []}

    with ThreadPoolExecutor(max_workers=6) as pool:
        jobs = {pool.submit(one, doc_id): doc_id for doc_id in sorted(all_ids, key=int)}
        for job in as_completed(jobs):
            try:
                documents.append(job.result())
            except Exception as exc:
                errors.append({"doc_id": jobs[job], "error": type(exc).__name__})
            if (len(documents) + len(errors)) % 40 == 0:
                print(f"official documents: {len(documents)} passed, {len(errors)} failed", flush=True)
    documents.sort(key=lambda x: (x["api_name"] or "~", int(x["doc_id"])))
    snapshot = {"schema_version": "finlab.skill.offline_docs.v1", "catalog_fetched_at": catalog["fetched_at"],
                "catalog_api_count": len(endpoints), "document_count": len(documents),
                "covered_api_count": sum(bool(x["api_name"]) for x in documents), "failed": errors,
                "full_api_articles": sum(x.get("content_status") == "full_article" for x in documents),
                "navigation_or_empty_pages": [x["doc_id"] for x in documents if x.get("content_status") in {"navigation_or_empty", "no_extractable_article"}],
                "scope": "versioned Pro catalog plus official navigation guides; not legacy Tushare crawl APIs",
                "runtime_catalog_changed": False, "live_tushare_account_tested": False,
                "documents": documents}
    previous_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    index = ["# Tushare Pro 离线接口索引", "",
             f"当前版本：{len(endpoints)}个已登记Pro接口，{len(documents)}份官方正文；失败{len(errors)}页。",
             "资料包括输入、输出、单位、权限、限额和官方示例。历史可见性、实际权限与任务授权另行判断。",
             "旧版Tushare网络爬虫接口不在本系统Pro HTTP范围内；本机SDK1.4.29与文档快照日期分别记录。", "",
             "用`scripts/api_docs.py --search 关键词`检索；用`--api daily`读单个接口。",
             "完整出处和SHA-256见[snapshot.json](snapshot.json)。不要一次加载全部文档。", ""]
    for group in dict.fromkeys(x["group"] for x in documents):
        index += ["## " + group, "", "|接口或文档|说明|", "|---|---|"]
        for d in documents:
            if d["group"] == group:
                index.append(f"|[{d['api_name'] or 'doc-' + d['doc_id']}]({d['path']})|{d['title']}|")
        index.append("")
    (REF / "index.md").write_text("\n".join(index), encoding="utf-8")
    if errors or snapshot["covered_api_count"] != len(endpoints):
        print(json.dumps({k:v for k,v in snapshot.items() if k != "documents"},ensure_ascii=False,indent=2))
        raise SystemExit(2)
    return {k:v for k,v in snapshot.items() if k != "documents"}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--refresh", action="store_true", help="Fetch public official documentation; no API token needed")
    args = p.parse_args()
    print(json.dumps(build(args.refresh), ensure_ascii=False, indent=2))
