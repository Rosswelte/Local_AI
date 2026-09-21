import json
from copy import deepcopy
from pathlib import Path


WORKFLOW_DIR = Path(__file__).parents[2] / "catalog" / "workflows"


def list_workflows() -> list[dict]:
    result = []
    for path in sorted(WORKFLOW_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        result.append({"id": path.stem, "label": data.get("label", path.stem)})
    return result


def load_workflow(workflow_id: str) -> dict:
    if not workflow_id or Path(workflow_id).name != workflow_id or Path(workflow_id).suffix:
        raise ValueError("workflow_id invalide")
    path = WORKFLOW_DIR / f"{workflow_id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return deepcopy(data["workflow"])


def apply_overrides(workflow: dict, *, prompt: str, seed: int | None, steps: int, cfg: float, width: int, height: int) -> dict:
    result = deepcopy(workflow)
    for node in result.values():
        class_type = node.get("class_type")
        inputs = node.setdefault("inputs", {})
        if class_type == "CLIPTextEncode" and inputs.get("text") == "__PROMPT__":
            inputs["text"] = prompt
        elif class_type == "KSampler":
            inputs.update({"steps": steps, "cfg": cfg})
            if seed is not None:
                inputs["seed"] = seed
        elif class_type == "EmptyLatentImage":
            inputs.update({"width": width, "height": height})
    return result
