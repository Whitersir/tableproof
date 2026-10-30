"""仅供本地单用户使用的模型配置。密钥只保存于服务端私密文件。"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .model_client import ModelConfig, ModelError


class SettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    base_url: str = Field(max_length=500)
    model: str = Field(max_length=150)
    api_key: str = Field(default="", max_length=4096, repr=False)
    extra_json: str = Field(default="{}", max_length=2000)


class ProbeRequest(SettingsRequest):
    consent_to_test: bool = False


class ModelSettings:
    def __init__(self, project: Path, data: Path):
        self.project, self.data = project, data
        self.path = data / "model-settings.json"
        self.lock = threading.Lock()

    def effective(self):
        return ModelConfig.load(self.project, self.data)

    def public(self):
        config = self.effective()
        overridden = [name for name in ("BASE_URL", "MODEL", "API_KEY", "EXTRA_JSON") if "TABLE_AGENT_" + name in os.environ]
        return {"base_url": config.base_url, "model": config.model, "has_api_key": bool(config.api_key),
                "extra_json": config.extra_json, "configured": config.ready,
                "local_settings_saved": self.path.exists(), "environment_overrides": overridden}

    def candidate(self, request: SettingsRequest):
        current = self.effective()
        # 换站时不自动转用旧站密钥，避免把凭据发给新的服务。
        same_endpoint = request.base_url.strip().rstrip("/") == current.base_url.rstrip("/")
        key = request.api_key.strip() or (current.api_key if same_endpoint else "")
        if not key:
            raise ModelError("请填写 API Key；更换 API 地址时需要重新输入对应密钥。")
        config = ModelConfig(request.base_url.strip().rstrip("/"), request.model.strip(), key, request.extra_json.strip())
        config.validate()
        return config

    def save(self, request):
        with self.lock:
            config = self.candidate(request)
            data = {"TABLE_AGENT_BASE_URL": config.base_url, "TABLE_AGENT_MODEL": config.model,
                    "TABLE_AGENT_API_KEY": config.api_key, "TABLE_AGENT_EXTRA_JSON": config.extra_json}
            temp = self.path.with_suffix(".tmp")
            # POSIX 新文件限当前用户读写；Windows 使用当前用户目录的文件权限。
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2)
            temp.replace(self.path)
        return self.public()

    def clear(self):
        with self.lock:
            self.path.unlink(missing_ok=True)
        return self.public()


def connection_probe(client):
    tools = [{"type": "function", "function": {"name": "connection_probe", "description": "确认工具调用格式。",
              "parameters": {"type": "object", "properties": {"value": {"type": "string", "enum": ["ok"]}}, "required": ["value"], "additionalProperties": False}}}]
    message, usage = client.complete(
        [{"role": "user", "content": "请调用 connection_probe，参数 value 为 ok。这是连接测试，没有用户表格。"}],
        tools, tool_choice={"type": "function", "function": {"name": "connection_probe"}}, max_tokens=256)
    try:
        calls = message.get("tool_calls") or []
        supported = any(c.get("function", {}).get("name") == "connection_probe" and
                        json.loads(c["function"]["arguments"]) == {"value": "ok"} for c in calls)
    except (ValueError, TypeError, KeyError, AttributeError):
        supported = False
    return {"reachable": True, "tool_call_verified": supported,
            "message": "连接成功，已收到有效工具调用。" if supported else "接口可连接，但本次没有返回有效工具调用；请确认模型支持 function tools。",
            "usage": {k: v for k, v in usage.items() if k in ("prompt_tokens", "completion_tokens") and isinstance(v, int)}}
