"""Versioned official Pro catalog; transport breadth is distinct from PIT readiness."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import unescape
import re
from urllib.request import urlopen
from .common import ROOT, LabError, read, write, now

CATALOG = ROOT / "catalog" / "tushare_pro.v2.json"
IDENTITY = re.compile(r"(^|_)(?:code|symbol|name|contract|exchange|market|industry|area|curr_type|fullname|enname|cnspell)($|_)")


def classify(name, typ, desc):
    n = name.lower()
    if IDENTITY.search(n) or n in {"ts_code", "con_code", "fund_name", "product", "ticker"}:
        return "identity"
    if any(x in n for x in ("datetime", "timestamp")):
        return "datetime" if "str" in typ else "unclassified"
    if n in {"year", "years", "f_year"} or "年度" in desc and "年份" in desc:
        return "year"
    if n.endswith("_year") or "出生年份" in desc:
        return "year"
    if n in {"d_month", "trade_month", "birth_month"}:
        return "month"
    if n in {"birthday", "last_ddate"}:
        return "date" if n == "last_ddate" else "month"
    if n in {"month", "m", "start_m", "end_m"} and ("月" in desc or "YYYYMM" in desc):
        return "month"
    if n in {"quarter", "start_q", "end_q"}:
        return "quarter"
    if any(t in desc for t in ("YYYY-MM-DD HH", "YYYYMMDDHH", "时点", "时间戳")):
        return "unclassified"  # requires an explicit precision/format override
    if n.endswith(("_date", "_day")) or n in {"date", "cal_date", "ann_date", "f_ann_date", "end_date", "period", "list_date", "delist_date"}:
        return "date"
    if "日期" in desc or "YYYYMMDD" in desc or "年月日" in desc:
        return "date"
    if n.endswith("_time") or n in {"time", "trade_time", "update_time"}:
        return "unclassified"
    if any(t in typ.lower() for t in ("float", "int", "double", "number")):
        return "number"
    return "text"


def plain(html):
    return unescape(re.sub(r"<[^>]+>", "", html)).strip()


def parse_doc(doc_id, html):
    content = html.split('<div class="content', 1)[-1].split('<p><strong>接口示例', 1)[0]
    text = plain(content)
    match = re.search(r"接口[：:]\s*([a-zA-Z][a-zA-Z0-9_]+)", text)
    if not match:
        return None
    name = match.group(1)
    if name in {"pro", "ts"}:
        return None
    tables = re.findall(r"<table[^>]*>(.*?)</table>", content, re.S)
    input_fields, output_fields = {}, {}
    input_docs, output_docs = {}, {}
    for table_match in re.finditer(r"<table[^>]*>(.*?)</table>", content, re.S):
        table = table_match.group(1)
        rows = [[plain(cell) for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)]
                for row in re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.S)]
        if not rows or len(rows[0]) < 3:
            continue
        if not any("类型" in cell for cell in rows[0]):
            continue
        sections = re.findall(r"输入参数|输出参数|返回参数|输出字段", plain(content[:table_match.start()]))
        is_input = (not sections or sections[-1] == "输入参数")
        dest, docs = (input_fields, input_docs) if is_input else (output_fields, output_docs)
        for row in rows[1:]:
            if len(row) < 3 or not re.fullmatch(r"[A-Za-z_]\w*", row[0]):
                continue
            desc = row[-1]
            dest[row[0]] = classify(row[0], row[1], desc)
            docs[row[0]] = {"type": row[1], "description": desc, "required_or_default": row[2]}
    if not output_fields:
        return None
    for k in ("limit", "offset"):
        input_fields.setdefault(k, "number")
    return {"api_name": name, "doc_id": str(doc_id), "source_url": f"https://tushare.pro/document/2?doc_id={doc_id}",
            "title": plain(re.search(r"<h2[^>]*>(.*?)</h2>", content, re.S).group(1)) if re.search(r"<h2", content) else name,
            "params": input_fields, "fields": output_fields, "param_docs": input_docs, "field_docs": output_docs,
            "permission": "account_dependent_see_source", "transport": "pro_http",
            "pagination": "operator_partition_or_offset_verified_per_binding",
            "temporal_status": "requires_review" if "unclassified" in [*input_fields.values(), *output_fields.values()] else "typed_from_official_docs",
            "replayability": "not_proven", "mock_tested": False, "live_tested": False}


def update_catalog(path=CATALOG):
    home = urlopen("https://tushare.pro/document/2", timeout=30).read().decode("utf-8")
    ids = sorted(set(re.findall(r"(?:/document/2\?doc_id=)(\d+)", home)), key=int)
    endpoints, errors, non_api = {}, [], []
    def fetch(i):
        raw = urlopen(f"https://tushare.pro/document/2?doc_id={i}", timeout=30).read().decode("utf-8")
        return parse_doc(i, raw)
    with ThreadPoolExecutor(max_workers=8) as pool:
        jobs = {pool.submit(fetch, i): i for i in ids}
        for job in as_completed(jobs):
            i = jobs[job]
            try:
                ep = job.result()
                if ep:
                    endpoints[ep["api_name"]] = ep
                else:
                    non_api.append(i)
            except Exception:
                errors.append(i)
    # Explicit reviewed overrides are applied separately and remain versioned.
    result = {"schema_version": "tushare_catalog.v2", "fetched_at": now(), "source": "https://tushare.pro/document/2",
              "document_pages": len(ids), "non_api_pages": non_api, "failed_pages": errors,
              "api_count": len(endpoints), "endpoints": dict(sorted(endpoints.items()))}
    write(path, result)
    return {"api_count": len(endpoints), "failed_pages": errors, "path": str(path)}


def load_catalog(path=None):
    obj = read(path or CATALOG)
    override_path = ROOT / "catalog" / "tushare_overrides.v2.json"
    if override_path.is_file():
        for name, override in read(override_path).items():
            if name not in obj["endpoints"]:
                obj["endpoints"][name] = {"api_name": name, "params": {}, "fields": {}, "live_tested": False}
            ep = obj["endpoints"][name]
            for k, v in override.items():
                if k in {"params", "fields"}:
                    ep.setdefault(k, {}).update(v)
                else:
                    ep[k] = v
    return obj


def endpoint(name, catalog=None):
    ep = (catalog or load_catalog())["endpoints"].get(name)
    if not ep:
        raise LabError("API_NOT_CATALOGUED", "先更新目录或登记接口规则")
    return ep


def check_catalog(path=None):
    obj = load_catalog(path)
    rows = []
    for name, ep in obj["endpoints"].items():
        unresolved = [k for k, v in {**ep.get("params", {}), **ep.get("fields", {})}.items() if v == "unclassified"]
        rows.append({"api_name": name, "transport": "pro_http", "field_count": len(ep.get("fields", {})),
                     "parameter_count": len(ep.get("params", {})), "permission": ep.get("permission", "account_dependent_see_source"),
                     "pagination": ep.get("pagination", "requires_binding"),
                     "mock_schema_roundtrip": mock_endpoint(ep),
                     "unclassified": unresolved, "replayability": ep.get("replayability", "not_proven"),
                     "live_tested": ep.get("live_tested", False)})
    return {"api_count": len(rows), "failed_document_pages": obj.get("failed_pages", []), "endpoints": rows}


def mock_endpoint(ep):
    """Synthetic type roundtrip, deliberately distinct from an upstream test."""
    from .temporal import TimeMap
    mapping = TimeMap()
    samples = {"number": 12.5, "date": "20240229", "identity": "M2509.DCE", "text": "登记于2024年2月29日",
               "temporal": "2024-02-29T15:00:00+08:00", "datetime": "2024-02-29T15:00:00+08:00",
               "year": 2024, "month": "202402", "quarter": "2024Q1"}
    try:
        for group in ("params", "fields"):
            source = {k: samples[v] for k, v in ep[group].items()}
            virtual = mapping.record(source, ep[group])
            if mapping.record(virtual, ep[group], inverse=True) != source: return "failed"
        return "passed_synthetic_types_only"
    except (LabError, KeyError, TypeError):
        return "requires_rule"
