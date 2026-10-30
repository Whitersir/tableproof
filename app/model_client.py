"""中转站适配层：只支持 Chat Completions 的标准 function tool 格式。"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import httpx


class ModelError(RuntimeError):
    pass


@dataclass
class ModelConfig:
    base_url: str = ""
    model: str = ""
    api_key: str = field(default="", repr=False)
    extra_json: str = "{}"

    @property
    def ready(self):
        return bool(self.base_url and self.model and self.api_key)

    @classmethod
    def load(cls, root: Path, settings_dir: Path | None = None):
        values = {}
        env_file = root / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8-sig").splitlines():
                if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    values[key.strip()] = value.strip().strip('"\'')
        private_file = (settings_dir or root / "data") / "model-settings.json"
        private = {}
        if private_file.exists():
            try:
                private = json.loads(private_file.read_text(encoding="utf-8"))
                if not isinstance(private, dict) or any(not isinstance(v, str) for v in private.values()):
                    raise ValueError
            except (ValueError, OSError):
                raise ModelError("本地模型设置文件无法读取，请在设置中重新保存。") from None
        def get(key, default=""):
            return os.environ.get(key, private.get(key, values.get(key, default))).strip()
        return cls(get("TABLE_AGENT_BASE_URL"), get("TABLE_AGENT_MODEL"), get("TABLE_AGENT_API_KEY"), get("TABLE_AGENT_EXTRA_JSON", "{}"))

    def validate(self):
        if not self.ready:
            raise ModelError("模型尚未配置。请在模型设置中填写 API 地址、模型名和密钥。")
        parsed = urlparse(self.base_url)
        if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost", "::1")):
            raise ModelError("中转站地址必须使用 HTTPS；本机调试地址可使用 HTTP。")
        if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ModelError("中转站地址格式不正确；不要在地址中放密钥或查询参数。")
        if parsed.path.rstrip("/").endswith("chat/completions"):
            raise ModelError("BASE_URL 请填写接口根地址，不要包含 /chat/completions。")
        try:
            extra = json.loads(self.extra_json)
            if not isinstance(extra, dict) or set(extra) - {"thinking", "reasoning_effort", "temperature", "top_p"}:
                raise ValueError
        except (ValueError, TypeError):
            raise ModelError("TABLE_AGENT_EXTRA_JSON 不是有效配置；允许 thinking、reasoning_effort、temperature、top_p。") from None
        return extra


class ModelClient:
    def __init__(self, config: ModelConfig, transport=None):
        self.config, self.transport = config, transport

    def complete(self, messages: list, tools: list, *, tool_choice="auto", max_tokens=1800):
        c = self.config
        extra = c.validate()
        body = {"model": c.model, "messages": messages, "tools": tools, "tool_choice": tool_choice, "max_tokens": max_tokens, **extra}
        # 不打印请求头、响应正文、异常原文，避免中转站错误信息泄露凭据。
        try:
            with httpx.Client(timeout=httpx.Timeout(45, connect=10), follow_redirects=False, transport=self.transport) as client:
                response = client.post(c.base_url.rstrip("/") + "/chat/completions", headers={"Authorization": "Bearer " + c.api_key}, json=body)
                if response.status_code != 200:
                    hints = {401: "密钥无效或已过期", 403: "模型或接口无权限", 404: "接口地址或模型名不正确", 429: "额度或频率受限，请稍后重试"}
                    raise ModelError(f"模型接口返回 HTTP {response.status_code}：{hints.get(response.status_code, '服务暂不可用或参数不兼容')}。")
                payload = response.json()
                message = payload["choices"][0]["message"]
                if not isinstance(message, dict):
                    raise ValueError
                usage = payload.get("usage")
                return message, usage if isinstance(usage, dict) else {}
        except ModelError:
            raise
        except httpx.TimeoutException:
            raise ModelError("模型接口请求超时；任务记录已保留，可稍后重试。") from None
        except httpx.HTTPError:
            raise ModelError("无法连接模型接口，请检查中转站地址、网络和证书。") from None
        except (ValueError, KeyError, IndexError, TypeError):
            raise ModelError("模型接口响应格式不兼容，未收到标准 Chat Completions 消息。") from None
