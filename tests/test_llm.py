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


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\nВсе тесты llm пройдены: {len(tests)}")
