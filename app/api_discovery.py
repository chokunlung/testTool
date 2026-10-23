from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx
import yaml


HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head"}


def _sample(schema: dict[str, Any] | None) -> Any:
    schema = schema or {}
    if "example" in schema:
        return schema["example"]
    if "default" in schema:
        return schema["default"]
    if "enum" in schema and schema["enum"]:
        return schema["enum"][0]
    kind = schema.get("type")
    if kind == "object" or "properties" in schema:
        return {key: _sample(value) for key, value in schema.get("properties", {}).items()}
    if kind == "array":
        return [_sample(schema.get("items", {}))]
    if kind in {"integer", "number"}:
        return 0
    if kind == "boolean":
        return False
    return ""


def _resolve_ref(document: dict[str, Any], value: Any) -> Any:
    if not isinstance(value, dict) or "$ref" not in value:
        return value
    ref = value["$ref"]
    if not str(ref).startswith("#/"):
        return value
    current: Any = document
    for part in str(ref)[2:].split("/"):
        current = current.get(part.replace("~1", "/").replace("~0", "~"), {}) if isinstance(current, dict) else {}
    return current


def _request_from_operation(document: dict[str, Any], path_item: dict[str, Any], operation: dict[str, Any]) -> dict[str, Any]:
    request: dict[str, Any] = {"path_params": {}, "query_params": {}, "headers": {}, "body": None}
    parameters = list(path_item.get("parameters", [])) + list(operation.get("parameters", []))
    for parameter in parameters:
        parameter = _resolve_ref(document, parameter)
        location = parameter.get("in")
        name = parameter.get("name", "")
        if not name:
            continue
        value = parameter.get("example", _sample(_resolve_ref(document, parameter.get("schema", {}))))
        if location == "path":
            request["path_params"][name] = value
        elif location == "query":
            request["query_params"][name] = value
        elif location == "header":
            request["headers"][name] = value
    content = operation.get("requestBody", {}).get("content", {})
    if content:
        content_type, media = next(iter(content.items()))
        request["content_type"] = content_type
        if "example" in media:
            request["body"] = media["example"]
        else:
            request["body"] = _sample(_resolve_ref(document, media.get("schema", {})))
    return request


def parse_openapi(document: dict[str, Any], source: str = "openapi") -> list[dict[str, Any]]:
    endpoints: list[dict[str, Any]] = []
    for path, path_item in document.get("paths", {}).items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method.lower() not in HTTP_METHODS or not isinstance(operation, dict):
                continue
            upper = method.upper()
            operation_id = str(operation.get("operationId", ""))
            endpoints.append(
                {
                    "method": upper,
                    "path": path,
                    "summary": operation.get("summary") or operation.get("description", ""),
                    "operation_id": operation_id,
                    "api_name": operation.get("x-api-name") or operation_id or f"{upper} {path}",
                    "tags": operation.get("tags", []),
                    "source": source,
                    "request_source": "openapi",
                    "request": _request_from_operation(document, path_item, operation),
                    "expected": {"http_statuses": [200], "return_code_path": "", "success_codes": []},
                }
            )
    return sorted(endpoints, key=lambda item: (item["path"], item["method"]))


async def discover_url(env: dict[str, Any]) -> list[dict[str, Any]]:
    url = env["base_url"].rstrip("/") + "/" + env.get("openapi_path", "/v3/api-docs").lstrip("/")
    async with httpx.AsyncClient(verify=env.get("verify_ssl", True), timeout=env.get("timeout_seconds", 30)) as client:
        response = await client.get(url)
        response.raise_for_status()
        document = response.json()
    return parse_openapi(document, "openapi_url")


def _load_openapi_file(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    document = json.loads(text) if path.suffix.lower() == ".json" else yaml.safe_load(text)
    if not isinstance(document, dict) or "paths" not in document:
        raise ValueError(f"{path} 不是有效的 OpenAPI 文件")
    return parse_openapi(document, "openapi_file")


def _join_paths(base: str, child: str) -> str:
    parts = [str(base or "").strip("/"), str(child or "").strip("/")]
    return "/" + "/".join(part for part in parts if part)


def _annotation_path(args: str) -> str:
    match = re.search(r'(?:value|path)\s*=\s*[\{\[]?\s*"([^"]+)"', args)
    if not match:
        match = re.search(r'^[\s\{\[]*"([^"]+)"', args)
    return match.group(1) if match else ""


def _scan_java(repo: Path) -> list[dict[str, Any]]:
    endpoints: list[dict[str, Any]] = []
    mapping = {"GetMapping": "GET", "PostMapping": "POST", "PutMapping": "PUT", "PatchMapping": "PATCH", "DeleteMapping": "DELETE"}
    controller_pattern = re.compile(r"@(?:RestController|Controller)\b")
    class_pattern = re.compile(r"\b(?:class|interface)\s+\w+")
    method_pattern = re.compile(
        r"@(GetMapping|PostMapping|PutMapping|PatchMapping|DeleteMapping|RequestMapping)\s*(?:\(([^)]*)\))?"
        r"[\s\S]{0,500}?(?:public|protected|private)\s+[\w<>, ?\[\].]+\s+(\w+)\s*\(",
        re.MULTILINE,
    )
    for file in repo.rglob("*.java"):
        try:
            text = file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if not controller_pattern.search(text):
            continue
        class_match = class_pattern.search(text)
        if not class_match:
            continue
        prefix = text[:class_match.start()]
        class_mappings = list(re.finditer(r"@RequestMapping\s*\(([^)]*)\)", prefix, re.DOTALL))
        base = _annotation_path(class_mappings[-1].group(1)) if class_mappings else ""
        for match in method_pattern.finditer(text[class_match.end():]):
            annotation, args, method_name = match.groups()
            method = mapping.get(annotation)
            if not method:
                method_match = re.search(r"RequestMethod\.(GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD)", args or "")
                method = method_match.group(1) if method_match else "GET"
            path = _join_paths(base, _annotation_path(args or ""))
            endpoints.append(
                {
                    "method": method,
                    "path": path,
                    "summary": f"從 {file.name} 掃描",
                    "operation_id": method_name,
                    "api_name": method_name,
                    "tags": [file.stem],
                    "source": "spring_source",
                    "request_source": "schema",
                    "request": {"path_params": {}, "query_params": {}, "headers": {}, "body": None},
                    "expected": {"http_statuses": [200], "return_code_path": "", "success_codes": []},
                }
            )
    unique = {(item["method"], item["path"]): item for item in endpoints}
    return sorted(unique.values(), key=lambda item: (item["path"], item["method"]))


def discover_repo(repo_path: str) -> list[dict[str, Any]]:
    repo = Path(repo_path).expanduser().resolve()
    if not repo.is_dir():
        raise ValueError(f"Repo 路徑不存在：{repo}")
    names = ("openapi.yaml", "openapi.yml", "openapi.json", "swagger.yaml", "swagger.yml", "swagger.json")
    candidates: list[Path] = []
    for name in names:
        candidates.extend(repo.rglob(name))
    if candidates:
        return _load_openapi_file(sorted(candidates, key=lambda p: (len(p.parts), str(p)))[0])
    endpoints = _scan_java(repo)
    if not endpoints:
        raise ValueError("Repo 中找不到 OpenAPI 文件或 Spring Controller API")
    return endpoints
