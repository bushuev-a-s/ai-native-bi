"""Клиент LLM. Поддерживает Ollama и любой OpenAI-совместимый сервер (vLLM, LM Studio, llama.cpp, облачный ключ организаторов).
Провайдер и модель задаются только настройками — жюри подключит свою модель.

Дополнительные переменные окружения (все необязательные):
  LLM_NUM_CTX=8192            размер контекста (Ollama)
  LLM_KEEP_ALIVE=30m          сколько держать модель в памяти между запросами (Ollama): без этого большая модель перезагружается
  LLM_TIMEOUT=300             таймаут запроса, секунды
  LLM_EXTRA_PARAMS={"...":..} JSON, который добавляется в тело запроса как есть (например отключение «размышлений» у Qwen:
                              {"chat_template_kwargs": {"enable_thinking": false}} для vLLM или {"think": false} для Ollama)
"""
import json
import os
import re

import requests

from . import config


class LLMError(RuntimeError):
    pass


_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.S)


def strip_thinking(text: str) -> str:
    """Убирает блоки рассуждений <think>...</think>, которые выдают некоторые модели (в т.ч. Qwen):
    иначе они ломают разбор JSON и SQL."""
    text = _THINK_RE.sub("", text)
    if "</think>" in text:                       # открывающего тега нет, остался только закрывающий
        text = text.split("</think>")[-1]
    if "<think>" in text:                        # блок не закрыт (ответ оборван): берём то, что до него
        text = text.split("<think>")[0]
    return text.strip()


def _check(r) -> None:
    """Понятная ошибка вместо голого «404 Not Found»: показываем ответ сервера и подсказку."""
    if r.status_code < 400:
        return
    body = (getattr(r, "text", "") or "").strip()[:300]
    hint = ""
    if r.status_code == 404:
        hint = (" Скорее всего, модель не скачана на сервере: выполните `ollama pull <модель>` "
                "или задайте LLM_MODEL именем из `ollama list`.")
    elif r.status_code in (401, 403):
        hint = " Проверьте LLM_API_KEY."
    raise LLMError(f"HTTP {r.status_code}: {body}{hint}")


def _norm(name: str) -> str:
    return name if ":" in name else name + ":latest"


class LLMClient:
    def __init__(self, provider: str | None = None, base_url: str | None = None,
                 model: str | None = None, api_key: str | None = None):
        self.provider = (provider or config.LLM_PROVIDER).lower()
        self.base_url = (base_url or config.LLM_BASE_URL).rstrip("/")
        self.model = model or config.LLM_MODEL
        self.api_key = api_key or config.LLM_API_KEY
        if self.provider not in ("ollama", "openai"):
            raise ValueError("LLM_PROVIDER должен быть 'ollama' или 'openai'")
        self.num_ctx = int(os.getenv("LLM_NUM_CTX", "8192"))
        self.keep_alive = os.getenv("LLM_KEEP_ALIVE", "30m")
        self.timeout = int(os.getenv("LLM_TIMEOUT", "300"))
        try:
            self.extra = json.loads(os.getenv("LLM_EXTRA_PARAMS") or "{}")
            if not isinstance(self.extra, dict):
                raise ValueError
        except ValueError as e:
            raise ValueError("LLM_EXTRA_PARAMS должен быть JSON-объектом, например {\"think\": false}") from e

    # ------------------------------------------------------------------ чат
    def chat(self, messages: list[dict], temperature: float = 0.0, json_mode: bool = False) -> str:
        try:
            if self.provider == "ollama":
                text = self._chat_ollama(messages, temperature, json_mode)
            else:
                text = self._chat_openai(messages, temperature, json_mode)
            return strip_thinking(text)
        except LLMError:
            raise
        except Exception as e:  # noqa: BLE001
            raise LLMError(f"Модель недоступна ({self.base_url}): {e}") from e

    def _chat_ollama(self, messages, temperature, json_mode) -> str:
        payload = {"model": self.model, "messages": messages, "stream": False,
                   "keep_alive": self.keep_alive,
                   "options": {"temperature": temperature, "num_ctx": self.num_ctx}}
        if json_mode:
            payload["format"] = "json"
        payload.update(self.extra)
        r = requests.post(f"{self.base_url}/api/chat", json=payload, timeout=self.timeout)
        _check(r)
        return r.json()["message"]["content"]

    def _chat_openai(self, messages, temperature, json_mode) -> str:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        payload = {"model": self.model, "messages": messages, "temperature": temperature}
        payload.update(self.extra)
        url = f"{self.base_url}/chat/completions"
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        r = requests.post(url, json=payload, headers=headers, timeout=self.timeout)
        if r.status_code == 400 and json_mode:
            # не все серверы знают response_format — повторяем без него, JSON достанем из текста
            payload.pop("response_format")
            r = requests.post(url, json=payload, headers=headers, timeout=self.timeout)
        _check(r)
        return r.json()["choices"][0]["message"]["content"]

    # ------------------------------------------------------------------ статус
    def available(self) -> tuple[bool, str]:
        """Зелёный статус только если ТОЧНО эта модель есть на сервере (а не любая похожая)."""
        try:
            if self.provider == "ollama":
                r = requests.get(f"{self.base_url}/api/tags", timeout=3)
                _check(r)
                names = [m["name"] for m in r.json().get("models", [])]
            else:
                r = requests.get(f"{self.base_url}/models", timeout=3,
                                 headers={"Authorization": f"Bearer {self.api_key}"})
                _check(r)
                names = [m["id"] for m in r.json().get("data", [])]
            if names and _norm(self.model) not in {_norm(n) for n in names}:
                return False, (f"модель {self.model} пока не найдена на сервере (есть: {', '.join(names[:5]) or 'ничего'}). "
                               f"Если запущено через docker compose, модель скачивается при первом запуске: подождите "
                               f"и обновите страницу. Иначе выполните `ollama pull {self.model}` или задайте LLM_MODEL")
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
