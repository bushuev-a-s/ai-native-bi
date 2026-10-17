"""Клиент LLM. Поддерживает Ollama и любой OpenAI-совместимый сервер (vLLM, LM Studio, llama.cpp).
Жюри подключит свою open source модель — поэтому провайдер и модель задаются только настройками."""
import json
import re

import requests

from . import config


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(self, provider: str | None = None, base_url: str | None = None,
                 model: str | None = None, api_key: str | None = None):
        self.provider = (provider or config.LLM_PROVIDER).lower()
        self.base_url = (base_url or config.LLM_BASE_URL).rstrip("/")
        self.model = model or config.LLM_MODEL
        self.api_key = api_key or config.LLM_API_KEY
        if self.provider not in ("ollama", "openai"):
            raise ValueError("LLM_PROVIDER должен быть 'ollama' или 'openai'")

    # ------------------------------------------------------------------ чат
    def chat(self, messages: list[dict], temperature: float = 0.0, json_mode: bool = False) -> str:
        try:
            if self.provider == "ollama":
                return self._chat_ollama(messages, temperature, json_mode)
            return self._chat_openai(messages, temperature, json_mode)
        except LLMError:
            raise
        except Exception as e:  # noqa: BLE001
            raise LLMError(f"Модель недоступна ({self.base_url}): {e}") from e

    def _chat_ollama(self, messages, temperature, json_mode) -> str:
        payload = {"model": self.model, "messages": messages, "stream": False,
                   "options": {"temperature": temperature, "num_ctx": 8192}}
        if json_mode:
            payload["format"] = "json"
        r = requests.post(f"{self.base_url}/api/chat", json=payload, timeout=300)
        r.raise_for_status()
        return r.json()["message"]["content"]

    def _chat_openai(self, messages, temperature, json_mode) -> str:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        payload = {"model": self.model, "messages": messages, "temperature": temperature}
        url = f"{self.base_url}/chat/completions"
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        r = requests.post(url, json=payload, headers=headers, timeout=300)
        if r.status_code == 400 and json_mode:
            # не все серверы знают response_format — повторяем без него, JSON достанем из текста
            payload.pop("response_format")
            r = requests.post(url, json=payload, headers=headers, timeout=300)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    # ------------------------------------------------------------------ статус
    def available(self) -> tuple[bool, str]:
        try:
            if self.provider == "ollama":
                r = requests.get(f"{self.base_url}/api/tags", timeout=3)
                r.raise_for_status()
                names = [m["name"] for m in r.json().get("models", [])]
            else:
                r = requests.get(f"{self.base_url}/models", timeout=3,
                                 headers={"Authorization": f"Bearer {self.api_key}"})
                r.raise_for_status()
                names = [m["id"] for m in r.json().get("data", [])]
            if names and not any(n == self.model or n.startswith(self.model.split(":")[0]) for n in names):
                return False, f"модель {self.model} не найдена на сервере (есть: {', '.join(names[:5])})"
            return True, f"{self.model} · {self.provider}"
        except Exception as e:  # noqa: BLE001
            return False, f"сервер модели недоступен: {e}"


OllamaLLM = LLMClient   # обратная совместимость


def extract_json(text: str) -> dict:
    text = text.strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        pass
    a, b = text.find("{"), text.rfind("}")
    if a != -1 and b > a:
        try:
            return json.loads(text[a:b + 1])
        except json.JSONDecodeError:
            return {}
    return {}


def extract_sql(text: str) -> str:
    m = re.search(r"```(?:sql)?\s*(.*?)```", text, re.S | re.I)
    return (m.group(1) if m else text).strip()
