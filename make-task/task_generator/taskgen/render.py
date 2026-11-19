"""Render current native v2 task packages."""
from finlab.tasks import PUBLIC_FILES, build


def build_package(root, path, spec, state):
    if spec.get("schema_version") != "finance_task_spec.v2":
        raise ValueError("仅渲染原生v2任务；历史规格只保留去重身份")
    return build(root, path, spec, state)
