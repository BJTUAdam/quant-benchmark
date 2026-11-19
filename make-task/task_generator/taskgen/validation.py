from __future__ import annotations
from datetime import date
import math
from .io_utils import catalog, digest, read_json


def shape_errors(value, schema, path="$"):
    errors = []
    kinds = schema.get("type")
    if kinds:
        kinds = [kinds] if isinstance(kinds, str) else kinds
        matches = {"object": isinstance(value, dict), "array": isinstance(value, list),
                   "string": isinstance(value, str), "null": value is None, "boolean": isinstance(value, bool),
                   "number": isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value),
                   "integer": isinstance(value, int) and not isinstance(value, bool)}
        if not any(matches.get(t, False) for t in kinds):
            return [path + "字段类型错误，应为" + "/".join(kinds)]
    if "const" in schema and value != schema["const"]:
        errors.append(path + "固定值不匹配")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(path + "不在允许值中")
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(path + "缺少" + key)
        for key, rule in schema.get("properties", {}).items():
            if key in value:
                errors.extend(shape_errors(value[key], rule, path + "." + key))
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            errors.append(path + "数组太短")
        if len(value) > schema.get("maxItems", float("inf")):
            errors.append(path + "数组太长")
        if "items" in schema:
            for i, item in enumerate(value):
                errors.extend(shape_errors(item, schema["items"], path + "[" + str(i) + "]"))
    if isinstance(value, str) and len(value) < schema.get("minLength", 0):
        errors.append(path + "文本过短")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and "minimum" in schema and value < schema["minimum"]:
        errors.append(path + "小于允许最小值")
    return errors


REQUIRED = {"schema_version", "title", "family_id", "source_ids", "core_mechanism",
            "decision_object", "required_decisions", "decision_dependencies", "maintenance_targets",
            "research_question", "research_modules", "maintenance_rationale", "environment_id",
            "data_requirements", "parameter_space", "novelty_claim", "lineage"}


def validate(spec, root):
    errors, warnings = [], []
    if not isinstance(spec, dict):
        return {"passed": False, "errors": ["TaskSpec必须为JSON对象"], "warnings": [], "scope": "specification_only"}
    errors = shape_errors(spec, read_json(root / "schemas" / "task_spec.v2.schema.json"))
    if errors:
        return {"passed": False, "errors": errors, "warnings": [], "spec_hash": digest(spec), "scope": "specification_only"}
    missing = sorted(REQUIRED - spec.keys())
    if missing:
        errors.append("缺少字段：" + ", ".join(missing))
        return {"passed": False, "errors": errors, "warnings": warnings, "spec_hash": digest(spec), "scope": "specification_only"}
    if spec["schema_version"] != "finance_task_spec.v2":
        errors.append("仅支持原生v2任务；旧任务只作为历史对照")
    for key in ("title", "research_question", "maintenance_rationale", "novelty_claim"):
        if not isinstance(spec[key], str) or not spec[key].strip():
            errors.append(key + "必须为非空字符串")
    families = {x["id"] for x in catalog(root, "families")}
    if spec["family_id"] not in families:
        errors.append("未知family_id")
    # A family label alone cannot turn a calendar-spread problem into cross-product research.
    classified = catalog(root, "seeds") + catalog(root, "historical_tasks")
    allowed_in_family = [x for x in classified if x.get("family_id") == spec["family_id"]]
    for key in ("core_mechanism", "decision_object"):
        if spec[key] not in {x.get(key) for x in allowed_in_family}:
            errors.append(key + "与family_id方向不匹配；需按机制归类，不能仅更换家族标签")
    codes = catalog(root, "decisions")
    for key, group in (("core_mechanism", "mechanisms"), ("decision_object", "objects")):
        if not isinstance(spec[key], str) or spec[key] not in codes[group]:
            errors.append(key + "必须使用catalog/decisions.json中的规范ID")
    decisions = spec["required_decisions"]
    if not isinstance(decisions, list) or any(not isinstance(x, str) for x in decisions):
        errors.append("required_decisions必须为字符串数组")
        decisions = []
    if len(set(decisions)) < 3:
        errors.append("至少需要三项不同的必需决策")
    for code in decisions:
        if code not in codes["decisions"]:
            errors.append("未知决策ID：" + code)
    maintenance = spec["maintenance_targets"]
    if not isinstance(maintenance, list) or not maintenance:
        errors.append("maintenance_targets必须为非空数组")
    elif any(not isinstance(x, str) or x not in codes["maintenance"] for x in maintenance):
        errors.append("maintenance_targets存在未知规范ID")
    edges = spec["decision_dependencies"]
    connected = set()
    if not isinstance(edges, list) or not edges:
        errors.append("需要说明决策之间的相互影响")
    else:
        for edge in edges:
            if (not isinstance(edge, list) or len(edge) != 2 or
                    any(not isinstance(x, str) or x not in decisions for x in edge) or edge[0] == edge[1]):
                errors.append("无效decision_dependencies边")
            else:
                connected.update(edge)
        if set(decisions) - connected:
            errors.append("存在未说明交互关系的装饰性决策：" + ",".join(sorted(set(decisions) - connected)))
    modules = spec["research_modules"]
    if not isinstance(modules, list):
        errors.append("research_modules必须为数组")
    else:
        described = set()
        for module in modules:
            if not isinstance(module, dict) or not all(isinstance(module.get(k), str) and module[k].strip()
                                                     for k in ("decision_id", "description", "interaction")):
                errors.append("研究模块需decision_id/description/interaction")
            else:
                described.add(module["decision_id"])
        if described != set(decisions):
            errors.append("research_modules必须逐项覆盖必需决策，不得额外堆叠模块")
    sources = {x["id"]: x for x in catalog(root, "sources")}
    if not isinstance(spec["source_ids"], list) or not spec["source_ids"]:
        errors.append("source_ids不能为空")
    else:
        for source in spec["source_ids"]:
            if not isinstance(source, str) or source not in sources:
                errors.append("来源ID不存在")
            elif str(sources[source].get("status", "")).startswith("pending"):
                warnings.append("来源尚未核验：" + source)
    envs = {x["id"]: x for x in catalog(root, "environments")}
    if spec["environment_id"] not in envs:
        errors.append("未知环境；只能使用当前AKQuant v2环境")
    elif spec["family_id"] not in envs[spec["environment_id"]].get("supported_families", []):
        errors.append("当前执行环境未支持本家族")
    data = spec["data_requirements"]
    if not isinstance(data, dict):
        errors.append("data_requirements必须为对象")
    else:
        expected_profile = {"akquant_stock_etf_v2": "stock_etf_daily", "akquant_futures_v2": "futures_daily"}.get(spec["environment_id"])
        if data.get("profile_id") != expected_profile:
            errors.append("数据profile与执行环境不一致")
        for field in ("symbols", "extra_fields", "notes"):
            if not isinstance(data.get(field), list) or any(not isinstance(x, str) for x in data.get(field, [])):
                errors.append("data_requirements." + field + "必须为字符串数组")
        if spec["environment_id"] == "akquant_futures_v2":
            import re
            products = envs.get("akquant_futures_v2", {}).get("supported_products", ["B", "M", "Y", "SC", "FU", "LU", "FG", "SA"])
            for symbol in data.get("symbols", []):
                match = re.fullmatch(r"([A-Za-z]+)(?:\d{3,4})?(?:\.[A-Z]+)?", symbol)
                if not match or match.group(1).upper() not in products:
                    errors.append("期货需求对象超出现有报价单位/规格能力：" + symbol + "；需先扩展标准，不能只因同为国内市场就标支持")
                elif "." in symbol:
                    exchange_by_product = {"B": "DCE", "M": "DCE", "Y": "DCE", "FU": "SHF",
                                           "SC": "INE", "LU": "INE", "FG": "ZCE", "SA": "ZCE"}
                    product = match.group(1).upper()
                    exchange = symbol.rsplit(".", 1)[1]
                    if exchange != exchange_by_product[product]:
                        errors.append("期货交易所后缀与品种不匹配：" + symbol + "；" + product + "应使用." + exchange_by_product[product] + "，品种池也可不填月份和后缀待绑定")
        if not isinstance(data.get("minimum_years"), (int, float)) or isinstance(data.get("minimum_years"), bool) or data["minimum_years"] < 2:
            errors.append("数据最低连续范围应不少于2年")
        start, end = data.get("date_start"), data.get("date_end")
        if start is None or end is None:
            warnings.append("数据需求起止日期待确定，未绑定实际数据")
        else:
            try:
                if date.fromisoformat(start) >= date.fromisoformat(end):
                    errors.append("日期范围无效")
            except (ValueError, TypeError):
                errors.append("需求日期必须为YYYY-MM-DD")
    if not isinstance(spec["parameter_space"], list):
        errors.append("parameter_space必须为数组")
    if not isinstance(spec["lineage"], dict) or not isinstance(spec["lineage"].get("derived_from"), list):
        errors.append("lineage需derived_from数组和kind")
    if not errors:
        from finlab.tasks import validate as validate_contracts
        errors.extend(validate_contracts(spec, root))
    warnings.append("仅完成规格检查：数据可得性、长程实际难度及管线执行均未验证")
    return {"passed": not errors, "errors": errors, "warnings": warnings,
            "spec_hash": digest(spec), "scope": "specification_only"}


def validate_review(review, neighbor_ids):
    issues = []
    if not isinstance(review, dict):
        return ["模型初筛必须为JSON对象"]
    if not isinstance(review.get("classification"), str):
        return ["缺少有效去重分类"]
    if review.get("classification") not in {"duplicate", "variant", "distinct", "uncertain"}:
        issues.append("缺少有效去重分类")
    if not isinstance(review.get("rationale"), str) or not review["rationale"].strip():
        issues.append("初筛必须说明理由")
    comparisons = review.get("comparisons")
    compared = set()
    if not isinstance(comparisons, list):
        issues.append("初筛缺少逐题比较")
    else:
        for row in comparisons:
            if not isinstance(row, dict) or not isinstance(row.get("task_id"), str) or row.get("task_id") not in neighbor_ids:
                issues.append("初筛引用了不在对照集中的题目")
            elif any(not isinstance(row.get(key), list) or not row[key] or
                     any(not isinstance(x, str) or not x.strip() for x in row[key]) for key in ("similarities", "differences")):
                issues.append("每项比较必须列出相同点和差异（可明确无差异）")
            else:
                compared.add(row["task_id"])
    if set(neighbor_ids) - compared:
        issues.append("初筛没有覆盖全部已提供的最近邻")
    closest = review.get("closest_task_id")
    if neighbor_ids and (not isinstance(closest, str) or closest not in neighbor_ids):
        issues.append("closest_task_id必须指向真实对照题")
    if not isinstance(review.get("issues"), list) or any(not isinstance(x, str) for x in review.get("issues", [])):
        issues.append("初筛issues必须为数组")
    if "pending_bindings" in review and (not isinstance(review["pending_bindings"], list) or
            any(not isinstance(x, str) for x in review["pending_bindings"])):
        issues.append("pending_bindings必须为字符串数组")
    for key in ("public_information_safe", "long_horizon_valid"):
        if not isinstance(review.get(key), bool):
            issues.append("初筛缺少布尔字段：" + key)
    return issues
