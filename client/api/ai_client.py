"""AI 补全客户端（哩米需求 2026-08-15）：兼容 OpenAI Chat Completions 格式。

DeepSeek / Gemini 等厂商只要提供兼容 Base URL 即可使用，例如：
- DeepSeek: https://api.deepseek.com/v1
- Gemini OpenAI 兼容端点可按官方文档配置
"""
import json
import re
from typing import Any

import httpx
from loguru import logger

from client.config import get_settings


class AIError(Exception):
    """AI API 调用错误。"""


def _extract_json(text: str) -> dict[str, Any]:
    """从 LLM 返回文本中提取 JSON 对象（容忍 ```json 代码块和前后说明文字）。"""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise AIError(f"AI 返回内容中没有 JSON：{text[:200]}")
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        raise AIError(f"AI 返回 JSON 解析失败：{e}") from e


class AIClient:
    """OpenAI 兼容 Chat Completions 客户端。"""

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "",
        model: str = "",
        proxy: str = "",
        timeout: float = 60.0,
    ) -> None:
        settings = get_settings()
        self.api_key = api_key or settings.ai_api_key
        self.base_url = (base_url or settings.ai_base_url).rstrip("/")
        self.model = model or settings.ai_model
        proxy = proxy or settings.proxy
        self.timeout = timeout
        self._client = httpx.Client(timeout=timeout, proxy=proxy or None)

        if not self.api_key:
            raise AIError("请先在设置中填写 AI API Key")
        if not self.base_url:
            raise AIError("请先在设置中填写 AI Base URL")
        if not self.model:
            raise AIError("请先在设置中填写 AI Model")

    def _endpoint(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return f"{self.base_url}/chat/completions"

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """调用 Chat Completions，返回模型文本内容。"""
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            resp = self._client.post(self._endpoint(), json=payload, headers=headers)
        except httpx.HTTPError as e:
            raise AIError(f"AI 请求失败：{e}") from e
        if resp.status_code >= 400:
            raise AIError(f"AI API 返回 {resp.status_code}: {resp.text[:300]}")
        try:
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError) as e:
            raise AIError(f"AI 返回格式异常：{e}") from e


def build_metadata_prompt(appid: str, known_name: str, categories: list[dict]) -> tuple[str, str]:
    """构造元数据+分类建议的提示词。

    categories: [{"slug": "...", "name": "...", "path": "..."}]
    """
    cat_lines = "\n".join(
        f"- {c['slug']}：{c['path']}" for c in categories[:200]
    ) or "- （无分类树）"
    system = (
        "你是 Steam 游戏库分类助手。请根据给定 AppID 和已知名称，"
        "输出严格 JSON，不要输出多余文字。\n"
        "JSON 字段：\n"
        '{"name": "游戏名", "type": "game/software/dlc/demo/music/video/unknown", '
        '"developer": "开发商", "publisher": "发行商", '
        '"genres": ["类型1", "类型2"], '
        '"category_slug": "最合适的分类 slug，若无法确定则为空字符串"}\n'
        "分类可选值如下：\n"
        f"{cat_lines}"
    )
    user = (
        f"请补全 Steam AppID {appid} 的元数据。"
        f"已知名称：{known_name or '未知'}。"
        "如果没有把握，category_slug 返回空字符串。"
    )
    return system, user
