"""Request schemas use closed objects; local specs may contain provenance metadata."""
import copy


def for_model(schema):
    result = copy.deepcopy(schema)
    # Parameter design remains open prose; arbitrary object keys are unsuitable for strict output.
    if "parameter_space" in result.get("properties", {}):
        result["properties"]["parameter_space"]["items"] = {"type": "string"}
    def close(node):
        if isinstance(node, list):
            return [close(x) for x in node]
        if not isinstance(node, dict):
            return node
        node = {k: close(v) for k, v in node.items() if k not in {"$schema", "uniqueItems"}}
        if "const" in node:
            node["enum"] = [node.pop("const")]
        if "enum" in node and all(isinstance(x, str) for x in node["enum"]):
            node.setdefault("type", "string")
        if node.get("type") == "object":
            node["additionalProperties"] = False
            node["required"] = list(node.get("properties", {}))
        return node
    return close(result)
