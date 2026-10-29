"""Клиент LLM: понятные ошибки, честный статус, блоки <think>, дополнительные параметры.
Запуск: uv run python -m tests.test_llm"""
import core.llm as m


class R:
    def __init__(self, code=200, data=None, text=""):
        self.status_code, self._d, self.text = code, data or {}, text

    def json(self):
        return self._d


def _patch(get=None, post=None):
    orig = (m.requests.get, m.requests.post)
    if get:
        m.requests.get = get
    if post:
        m.requests.post = post
    return orig


def _restore(orig):
    m.requests.get, m.requests.post = orig


def test_404_explains_missing_model_and_shows_server_body():
    orig = _patch(post=lambda url, **k: R(404, text='{"error":"model \'qwen2.5:7b-instruct\' not found"}'))
    try:
        m.LLMClient("ollama", "http://x:11434", "qwen2.5:7b-instruct").chat([{"role": "user", "content": "hi"}])
    except m.LLMError as e:
        msg = str(e)
        assert "404" in msg and "not found" in msg and "ollama pull" in msg
    else:
        raise AssertionError("ожидали LLMError")
    finally:
        _restore(orig)


def test_status_is_green_only_for_exact_model():
    orig = _patch(get=lambda url, **k: R(200, {"models": [{"name": "qwen2.5:7b"}, {"name": "llama3:latest"}]}))
    try:
        assert m.LLMClient("ollama", "http://x", "qwen2.5:7b-instruct").available()[0] is False   # раньше было True
        assert m.LLMClient("ollama", "http://x", "qwen2.5:7b").available()[0] is True
        assert m.LLMClient("ollama", "http://x", "llama3").available()[0] is True                  # :latest
    finally:
        _restore(orig)


def test_strip_thinking():
    assert m.strip_thinking("<think>рассуждаю {x}</think>\n```sql\nSELECT 1\n```") == "```sql\nSELECT 1\n```"
    assert m.strip_thinking("рассуждения без открывающего</think>Ответ") == "Ответ"
    assert m.strip_thinking("Ответ<think>обрыв") == "Ответ"
    assert m.strip_thinking("обычный текст") == "обычный текст"


def test_thinking_does_not_break_json_and_sql():
    raw = '<think>варианты: {"intent": "reject"}</think>{"intent": "query"}'
    orig = _patch(post=lambda url, **k: R(200, {"message": {"content": raw}}))
    try:
        out = m.LLMClient("ollama", "http://x", "q").chat([{"role": "user", "content": "hi"}], json_mode=True)
        assert m.extract_json(out) == {"intent": "query"}
    finally:
        _restore(orig)


def test_extra_params_keep_alive_and_ctx_reach_the_request(monkeypatch=None):
    import os
    seen = {}

    def post(url, json=None, **k):
        seen.update(json)
        return R(200, {"message": {"content": "ok"}})

    env = {"LLM_EXTRA_PARAMS": '{"think": false}', "LLM_KEEP_ALIVE": "1h", "LLM_NUM_CTX": "16384"}
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    orig = _patch(post=post)
    try:
        m.LLMClient("ollama", "http://x", "q").chat([{"role": "user", "content": "hi"}])
        assert seen["think"] is False and seen["keep_alive"] == "1h" and seen["options"]["num_ctx"] == 16384
        os.environ["LLM_EXTRA_PARAMS"] = "не json"
        try:
            m.LLMClient("ollama", "http://x", "q")
        except ValueError:
            pass
        else:
            raise AssertionError("битый LLM_EXTRA_PARAMS должен давать понятную ошибку")
    finally:
        _restore(orig)
        for k, v in old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


# ---------------------------------------------------------------- провайдер по умолчанию: openai
def _with_env(env, fn):
    """Выполняет fn при заданных переменных окружения и перечитанном config; потом всё возвращает."""
    import importlib
    import os
    keys = ["LLM_PROVIDER", "LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY", "LLM_MAX_TOKENS",
            "LLM_EXTRA_PARAMS", "LLM_NUM_CTX", "LLM_KEEP_ALIVE", "OLLAMA_URL", "OLLAMA_MODEL"]
    old = {k: os.environ.get(k) for k in keys}
    for k in keys:
        os.environ.pop(k, None)
    os.environ.update(env)
    importlib.reload(m.config)
    try:
        return fn()
    finally:
        for k in keys:
            os.environ.pop(k, None)
            if old[k] is not None:
                os.environ[k] = old[k]
        importlib.reload(m.config)


def test_default_provider_is_openai_with_local_ollama_v1():
    c = _with_env({}, m.LLMClient)
    assert c.provider == "openai" and c.base_url == "http://localhost:11434/v1"
    assert c.model == "qwen2.5:7b-instruct" and c.api_key == "not-needed"


def test_old_ollama_env_vars_are_ignored_and_ollama_provider_still_works():
    c = _with_env({"OLLAMA_URL": "http://old:11434", "OLLAMA_MODEL": "old"}, m.LLMClient)
    assert c.base_url == "http://localhost:11434/v1" and c.model == "qwen2.5:7b-instruct"
    n = _with_env({"LLM_PROVIDER": "ollama"}, m.LLMClient)
    assert n.provider == "ollama" and n.base_url == "http://localhost:11434"          # у родного API нет /v1
    n2 = _with_env({"LLM_PROVIDER": "ollama", "LLM_BASE_URL": "http://ollama:11434/v1"}, m.LLMClient)
    assert n2.base_url == "http://ollama:11434"                                       # лишний /v1 срезается


def test_openai_request_shape_key_and_max_tokens():
    seen = {}

    def post(url, json=None, headers=None, **k):
        seen.update(url=url, json=json, headers=headers)
        return R(200, {"choices": [{"message": {"content": "ok"}}]})

    def run():
        orig = _patch(post=post)
        try:
            c = m.LLMClient()
            assert c.chat([{"role": "user", "content": "hi"}], temperature=0.2, json_mode=True) == "ok"
        finally:
            _restore(orig)

    _with_env({"LLM_BASE_URL": "https://api.example.com/v1/", "LLM_MODEL": "Qwen/Qwen3-32B",
               "LLM_API_KEY": "sk-secret", "LLM_MAX_TOKENS": "2048"}, run)
    assert seen["url"] == "https://api.example.com/v1/chat/completions"
    assert seen["headers"]["Authorization"] == "Bearer sk-secret"
    j = seen["json"]
    assert j["model"] == "Qwen/Qwen3-32B" and j["temperature"] == 0.2 and j["max_tokens"] == 2048
    assert j["response_format"] == {"type": "json_object"}
    assert "keep_alive" not in j and "options" not in j          # параметры Ollama в openai-режиме не отправляем


def test_422_also_retries_without_response_format():
    calls = []

    def post(url, json=None, **k):
        calls.append(dict(json))
        return R(422, text="unknown field") if "response_format" in json else R(200, {"choices": [{"message": {"content": "{}"}}]})

    orig = _patch(post=post)
    try:
        assert m.LLMClient("openai", "http://x/v1", "q").chat([{"role": "user", "content": "hi"}], json_mode=True) == "{}"
        assert len(calls) == 2 and "response_format" not in calls[1]
    finally:
        _restore(orig)


def test_openai_hints_point_to_base_url_key_and_limits():
    def fail(code):
        orig = _patch(post=lambda url, **k: R(code, text="x"))
        try:
            m.LLMClient("openai", "http://x/v1", "q").chat([{"role": "user", "content": "hi"}])
        except m.LLMError as e:
            return str(e)
        finally:
            _restore(orig)

    assert "LLM_BASE_URL" in fail(404) and "/v1" in fail(404)
    assert "LLM_API_KEY" in fail(401)
    assert "лимит" in fail(429)


def test_openai_model_list_status_and_key_not_leaked():
    seen = {}

    def get(url, headers=None, **k):
        seen["url"], seen["h"] = url, headers
        return R(200, {"data": [{"id": "Qwen/Qwen3-32B"}]})

    orig = _patch(get=get)
    try:
        ok = m.LLMClient("openai", "http://x/v1", "Qwen/Qwen3-32B", "sk-secret")
        assert ok.available()[0] is True and seen["url"] == "http://x/v1/models"
        assert seen["h"]["Authorization"] == "Bearer sk-secret"
        bad = m.LLMClient("openai", "http://x/v1", "Qwen/Other", "sk-secret").available()
        assert bad[0] is False and "sk-secret" not in bad[1]
    finally:
        _restore(orig)

    def boom(url, **k):
        raise ConnectionError("нет связи")

    orig = _patch(post=boom)
    try:
        m.LLMClient("openai", "http://x/v1", "q", "sk-secret").chat([{"role": "user", "content": "hi"}])
    except m.LLMError as e:
        assert "sk-secret" not in str(e)                             # ключ не попадает в сообщения об ошибках
    finally:
        _restore(orig)


def test_per_call_max_tokens_reaches_both_providers():
    seen = {}

    def post(url, json=None, **k):
        seen[url] = json
        return R(200, {"message": {"content": "ok"}, "choices": [{"message": {"content": "ok"}}]})

    orig = _patch(post=post)
    try:
        m.LLMClient("ollama", "http://x", "q").chat([{"role": "user", "content": "hi"}], max_tokens=123)
        m.LLMClient("openai", "http://x/v1", "q").chat([{"role": "user", "content": "hi"}], max_tokens=77)
        m.LLMClient("openai", "http://y/v1", "q").chat([{"role": "user", "content": "hi"}])
    finally:
        _restore(orig)
    assert seen["http://x/api/chat"]["options"]["num_predict"] == 123
    assert seen["http://x/v1/chat/completions"]["max_tokens"] == 77
    assert "max_tokens" not in seen["http://y/v1/chat/completions"]          # без лимита ничего лишнего не отправляем


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\nВсе тесты llm пройдены: {len(tests)}")
