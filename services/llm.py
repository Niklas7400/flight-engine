"""LLM-Anbindung (Gemini oder Groq) fuer strukturierte JSON-Antworten.

Vertrag: `complete_json` gibt ein dict oder None zurueck und wirft nie eine Exception.
Geheimnisse (GEMINI_API_KEY, GROQ_API_KEY) werden nie geloggt.
"""

import json
import logging
import os
import re
from typing import Optional

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

TIMEOUT_S = 30.0
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
# Alias auf das aktuelle Flash-Modell, damit ein abgeschaltetes Modell nicht zu 404 fuehrt
DEFAULT_GEMINI_MODEL = "gemini-flash-latest"
DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_MODELS_URL = "https://api.groq.com/openai/v1/models"
# Bevorzugte Modelle, falls das konfigurierte abgeschaltet ist (Reihenfolge = Vorrang).
GROQ_PREFERRED = ("openai/gpt-oss-120b", "llama-3.3-70b-versatile", "openai/gpt-oss-20b")
# Keine Chat-Modelle (Audio, Sprachausgabe, Moderation)
_GROQ_NON_CHAT = ("whisper", "tts", "guard", "playai", "orpheus", "distil")

_last_status: Optional[int] = None

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)

# Grounding-URLs des letzten Gemini-Calls (nur gefuellt, wenn use_search=True).
_last_sources: list[str] = []


def last_sources() -> list[str]:
    """Grounding-URLs des letzten Gemini-Calls mit Suche (sonst leer)."""
    return list(_last_sources)


def complete_json(prompt: str, system: str, use_search: bool = False) -> Optional[dict]:
    """Sendet Prompt an den konfigurierten Provider und liefert das JSON-Objekt der Antwort.

    Gibt None zurueck bei: Provider "none", fehlendem Key, HTTP-/Netzwerkfehler,
    Timeout, ungueltigem JSON oder JSON, das kein Objekt ist.
    """
    global _last_sources
    _last_sources = []
    provider = (os.getenv("LLM_PROVIDER") or "gemini").strip().lower()
    try:
        if provider == "none":
            return None
        if provider == "gemini":
            text = _gemini(prompt, system, use_search)
            if text is None and use_search:
                # Websuche (Grounding) hat im kostenlosen Kontingent eigene, engere Limits
                # (HTTP 429) -> einmal ohne Suche versuchen.
                logger.warning("Gemini mit Websuche fehlgeschlagen, neuer Versuch ohne Suche")
                text = _gemini(prompt, system, False)
            if text is None and os.getenv("GROQ_API_KEY"):
                logger.warning("Gemini fehlgeschlagen, Ausweichen auf Groq")
                provider = "groq"
                text = _groq(prompt, system)
        elif provider == "groq":
            text = _groq(prompt, system)
        else:
            logger.warning("Unbekannter LLM_PROVIDER=%r, LLM uebersprungen", provider)
            return None
        if text is None:
            return None
        data = _extract_json(text)
        if data is None:
            logger.warning("LLM-Antwort ist kein gueltiges JSON-Objekt (provider=%s)", provider)
        return data
    except Exception as exc:  # letzte Sicherung: nie Exception nach aussen
        logger.warning("LLM-Aufruf fehlgeschlagen (provider=%s): %s", provider, type(exc).__name__)
        return None


def _gemini(prompt: str, system: str, use_search: bool) -> Optional[str]:
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        logger.warning("GEMINI_API_KEY fehlt, LLM uebersprungen")
        return None
    model = os.getenv("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL

    generation_config: dict = {"temperature": 0.2}
    body: dict = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": generation_config,
    }
    if use_search:
        # Grounding ist nicht mit responseMimeType kombinierbar.
        body["tools"] = [{"google_search": {}}]
    else:
        generation_config["responseMimeType"] = "application/json"

    data = _post(
        "gemini",
        GEMINI_URL.format(model=model),
        headers={"x-goog-api-key": key},
        body=body,
    )
    if data is None:
        return None

    candidates = data.get("candidates") or []
    if not candidates or not isinstance(candidates[0], dict):
        logger.warning("Gemini-Antwort ohne Kandidaten")
        return None
    candidate = candidates[0]
    parts = (candidate.get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))

    global _last_sources
    _last_sources = _grounding_urls(candidate)
    return text


def _groq(prompt: str, system: str) -> Optional[str]:
    # use_search wird bei Groq bewusst ignoriert (kein Web-Grounding).
    key = os.getenv("GROQ_API_KEY")
    if not key:
        logger.warning("GROQ_API_KEY fehlt, LLM uebersprungen")
        return None
    model = os.getenv("GROQ_MODEL") or DEFAULT_GROQ_MODEL

    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {key}"}
    data = _post("groq", GROQ_URL, headers=headers, body=body)
    if data is None and _last_status == 404:
        # Modell abgeschaltet -> verfuegbares Modell bei Groq erfragen und einmal neu versuchen
        fallback = _groq_available_model(headers, exclude=model)
        if fallback:
            logger.warning("Groq-Modell %s nicht verfuegbar, nutze %s", model, fallback)
            data = _post("groq", GROQ_URL, headers=headers, body={**body, "model": fallback})
    if data is None:
        return None
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        logger.warning("Groq-Antwort hat unerwartete Struktur")
        return None
    return content if isinstance(content, str) else None


def _groq_available_model(headers: dict, exclude: str) -> Optional[str]:
    """Waehlt aus Groqs Modellliste ein Chat-Modell (bevorzugte zuerst)."""
    try:
        resp = httpx.get(GROQ_MODELS_URL, headers=headers, timeout=TIMEOUT_S)
        ids = [m.get("id") for m in resp.json().get("data", [])] if resp.status_code == 200 else []
    except Exception as exc:  # Modellliste ist nur ein Rettungsversuch
        logger.warning("Groq-Modellliste nicht abrufbar: %s", type(exc).__name__)
        return None
    ids = [i for i in ids if isinstance(i, str) and i != exclude
           and not any(x in i.lower() for x in _GROQ_NON_CHAT)]
    for preferred in GROQ_PREFERRED:
        if preferred in ids:
            return preferred
    return sorted(ids)[0] if ids else None


def _post(provider: str, url: str, *, headers: dict, body: dict) -> Optional[dict]:
    """POST mit Timeout; gibt das geparste JSON-Objekt der Antwort oder None zurueck.
    Der HTTP-Status landet in _last_status (None bei Netzwerkfehler/Timeout)."""
    global _last_status
    _last_status = None
    try:
        resp = httpx.post(url, headers=headers, json=body, timeout=TIMEOUT_S)
    except httpx.TimeoutException:
        logger.warning("LLM-Timeout nach %ss (provider=%s)", TIMEOUT_S, provider)
        return None
    except httpx.HTTPError as exc:
        logger.warning("LLM-Netzwerkfehler (provider=%s): %s", provider, type(exc).__name__)
        return None

    _last_status = resp.status_code
    if resp.status_code != 200:
        logger.warning("LLM-HTTP-Fehler %s (provider=%s)", resp.status_code, provider)
        return None
    try:
        data = resp.json()
    except ValueError:
        logger.warning("LLM-Antwort ist kein JSON (provider=%s)", provider)
        return None
    if not isinstance(data, dict):
        logger.warning("LLM-Antwort ist kein Objekt (provider=%s)", provider)
        return None
    return data


def _grounding_urls(candidate: dict) -> list[str]:
    chunks = (candidate.get("groundingMetadata") or {}).get("groundingChunks") or []
    urls: list[str] = []
    for chunk in chunks:
        uri = ((chunk or {}).get("web") or {}).get("uri")
        if uri and uri not in urls:
            urls.append(uri)
    return urls


def _extract_json(text: str) -> Optional[dict]:
    """Direktes json.loads, sonst ```json```-Block, sonst groesster {...}-Block."""
    if not text:
        return None
    candidates = [text.strip()]
    fence = _FENCE_RE.search(text)
    if fence:
        candidates.append(fence.group(1).strip())
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])

    for candidate in candidates:
        try:
            obj = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(obj, dict):
            return obj
    return None
