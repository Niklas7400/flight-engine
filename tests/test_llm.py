import logging

import httpx
import pytest

from services import llm

SECRET = "geheim-KEY-123"


class Recorder:
    """Ersetzt httpx.post; zeichnet Aufrufe auf und liefert `response` bzw. wirft `exc`."""

    def __init__(self, response=None, exc=None):
        self.calls = []
        self.response = response
        self.exc = exc

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.exc is not None:
            raise self.exc
        return self.response


def ok(payload, status=200):
    return httpx.Response(status, json=payload)


def gemini_payload(text, sources=()):
    candidate = {"content": {"parts": [{"text": text}]}}
    if sources:
        candidate["groundingMetadata"] = {
            "groundingChunks": [{"web": {"uri": u}} for u in sources]
        }
    return {"candidates": [candidate]}


def groq_payload(content):
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Keine Env-Vars aus der Umgebung/.env; jeder ungemockte Request schlaegt laut fehl."""
    for name in ("LLM_PROVIDER", "GEMINI_API_KEY", "GEMINI_MODEL", "GROQ_API_KEY", "GROQ_MODEL"):
        monkeypatch.delenv(name, raising=False)

    def forbidden(*args, **kwargs):
        raise AssertionError("echter Netzwerkzugriff in Test")

    monkeypatch.setattr(llm.httpx, "post", forbidden)


@pytest.fixture
def recorder(monkeypatch):
    rec = Recorder()
    monkeypatch.setattr(llm.httpx, "post", rec)
    return rec


@pytest.fixture
def gemini_key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", SECRET)


@pytest.fixture
def groq_key(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", SECRET)


# --- Gemini --------------------------------------------------------------

def test_gemini_ohne_suche_setzt_json_mime_type(monkeypatch, recorder, gemini_key):
    recorder.response = ok(gemini_payload('{"action": "BUY_NOW"}'))

    result = llm.complete_json("Preise: 100 EUR", "Du bist Berater.")

    assert result == {"action": "BUY_NOW"}
    assert len(recorder.calls) == 1
    url, kwargs = recorder.calls[0]
    assert url == (
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent"
    )
    assert kwargs["headers"]["x-goog-api-key"] == SECRET
    assert kwargs["timeout"] == 30.0
    body = kwargs["json"]
    assert body["systemInstruction"] == {"parts": [{"text": "Du bist Berater."}]}
    assert body["contents"] == [{"role": "user", "parts": [{"text": "Preise: 100 EUR"}]}]
    assert body["generationConfig"] == {
        "temperature": 0.2,
        "responseMimeType": "application/json",
    }
    assert "tools" not in body
    assert llm.last_sources() == []


def test_gemini_mit_suche_setzt_google_search_ohne_mime_type(recorder, gemini_key):
    recorder.response = ok(
        gemini_payload(
            '{"ok": true}',
            sources=["https://a.example/x", "https://b.example/y", "https://a.example/x"],
        )
    )

    result = llm.complete_json("Frage", "System", use_search=True)

    assert result == {"ok": True}
    body = recorder.calls[0][1]["json"]
    assert body["tools"] == [{"google_search": {}}]
    assert body["generationConfig"] == {"temperature": 0.2}
    assert "responseMimeType" not in body["generationConfig"]
    assert llm.last_sources() == ["https://a.example/x", "https://b.example/y"]


def test_gemini_modell_aus_env(monkeypatch, recorder, gemini_key):
    monkeypatch.setenv("GEMINI_MODEL", "gemini-test-modell")
    recorder.response = ok(gemini_payload("{}"))

    llm.complete_json("p", "s")

    assert "/models/gemini-test-modell:generateContent" in recorder.calls[0][0]


def test_gemini_parts_werden_verbunden(recorder, gemini_key):
    payload = {
        "candidates": [
            {"content": {"parts": [{"text": '{"a": '}, {"text": "1}"}]}}
        ]
    }
    recorder.response = ok(payload)

    assert llm.complete_json("p", "s") == {"a": 1}


def test_gemini_ohne_key_kein_request(recorder, caplog):
    caplog.set_level(logging.WARNING, logger="services.llm")

    assert llm.complete_json("p", "s") is None
    assert recorder.calls == []
    assert "GEMINI_API_KEY" in caplog.text


# --- Groq ----------------------------------------------------------------

def test_groq_body_und_bearer(recorder, groq_key):
    recorder.response = ok(groq_payload('{"x": 2}'))

    result = llm.complete_json("Prompt", "Sys", use_search=True)

    assert result == {"x": 2}
    url, kwargs = recorder.calls[0]
    assert url == "https://api.groq.com/openai/v1/chat/completions"
    assert kwargs["headers"]["Authorization"] == f"Bearer {SECRET}"
    assert kwargs["timeout"] == 30.0
    body = kwargs["json"]
    assert body["model"] == "llama-3.3-70b-versatile"
    assert body["messages"] == [
        {"role": "system", "content": "Sys"},
        {"role": "user", "content": "Prompt"},
    ]
    assert body["temperature"] == 0.2
    assert body["response_format"] == {"type": "json_object"}
    assert "tools" not in body  # use_search wird ignoriert


def test_groq_modell_aus_env(monkeypatch, recorder, groq_key):
    monkeypatch.setenv("GROQ_MODEL", "groq-test-modell")
    recorder.response = ok(groq_payload("{}"))

    llm.complete_json("p", "s")

    assert recorder.calls[0][1]["json"]["model"] == "groq-test-modell"


def test_groq_ohne_key_kein_request(recorder, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "groq")

    assert llm.complete_json("p", "s") is None
    assert recorder.calls == []


# --- JSON-Extraktion -----------------------------------------------------

def test_json_in_codeblock(recorder, gemini_key):
    text = 'Hier ist das Ergebnis:\n```json\n{"action": "WAIT", "n": 3}\n```\nViel Erfolg.'
    recorder.response = ok(gemini_payload(text))

    assert llm.complete_json("p", "s") == {"action": "WAIT", "n": 3}


def test_erstes_objekt_ohne_codeblock(recorder, gemini_key):
    text = 'Antwort: {"action": "WATCH", "reasons": ["a"]} – fertig.'
    recorder.response = ok(gemini_payload(text))

    assert llm.complete_json("p", "s") == {"action": "WATCH", "reasons": ["a"]}


def test_kein_objekt_ergibt_none(recorder, gemini_key, caplog):
    caplog.set_level(logging.WARNING, logger="services.llm")
    recorder.response = ok(gemini_payload("[1, 2, 3]"))

    assert llm.complete_json("p", "s") is None
    assert "kein gueltiges JSON-Objekt" in caplog.text


# --- Fehlerfaelle --------------------------------------------------------

def test_ungueltiges_json_none_und_warning_ohne_key(recorder, gemini_key, caplog):
    caplog.set_level(logging.WARNING, logger="services.llm")
    recorder.response = ok(gemini_payload("das ist kein json {oops"))

    assert llm.complete_json("p", "s") is None
    assert any(r.levelno == logging.WARNING for r in caplog.records)
    assert SECRET not in caplog.text


def test_http_fehler_none_und_warning_ohne_key(recorder, gemini_key, caplog):
    caplog.set_level(logging.WARNING, logger="services.llm")
    recorder.response = httpx.Response(500, json={"error": "boom"})

    assert llm.complete_json("p", "s") is None
    assert "500" in caplog.text
    assert SECRET not in caplog.text


def test_timeout_none(recorder, gemini_key, caplog):
    caplog.set_level(logging.WARNING, logger="services.llm")
    recorder.exc = httpx.ReadTimeout("zu langsam")

    assert llm.complete_json("p", "s") is None
    assert "Timeout" in caplog.text
    assert SECRET not in caplog.text


def test_unerwarteter_fehler_wird_geschluckt(recorder, gemini_key):
    recorder.exc = RuntimeError("unerwartet")

    assert llm.complete_json("p", "s") is None


def test_provider_none_kein_request(monkeypatch, recorder, gemini_key):
    monkeypatch.setenv("LLM_PROVIDER", "none")

    assert llm.complete_json("p", "s") is None
    assert recorder.calls == []


def test_unbekannter_provider_none(monkeypatch, recorder, gemini_key):
    monkeypatch.setenv("LLM_PROVIDER", "mistral")

    assert llm.complete_json("p", "s") is None
    assert recorder.calls == []


def test_last_sources_werden_bei_fehler_geleert(recorder, gemini_key):
    recorder.response = ok(gemini_payload("{}", sources=["https://x.example"]))
    llm.complete_json("p", "s", use_search=True)
    assert llm.last_sources() == ["https://x.example"]

    recorder.response = httpx.Response(503)
    assert llm.complete_json("p", "s", use_search=True) is None
    assert llm.last_sources() == []
