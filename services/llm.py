"""One small interface over the AI providers: backend.structured(system, user_text, tool, image) -> dict.

`tool` is {name, description, input_schema(JSON Schema)}. Anthropic gets it as a forced tool call; Gemini gets the schema as
a JSON response schema. Backends turn every failure into LLMError(code, friendly message, http status); keys are never logged.
"""
import base64
import json
import logging
import re

log = logging.getLogger("billshocker")
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class LLMError(Exception):
    def __init__(self, code, message, status=502):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


# ------------------------------------------------------------------ Anthropic
class AnthropicBackend:
    name, label = "anthropic", "Anthropic Claude"

    def __init__(self, api_key, model, timeout=60, client=None):
        self.api_key, self.model, self.timeout, self._client = api_key, model, timeout, client

    @property
    def configured(self):
        return bool(self.api_key) or self._client is not None

    def _get_client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic(api_key=self.api_key, timeout=self.timeout, max_retries=1)
        return self._client

    def structured(self, system, user_text, tool, image=None, max_tokens=3000):
        if not self.configured:
            raise LLMError("not_configured", "The AI service is not configured on this server (no API key). You can still enter your bill values manually.", 503)
        client = self._get_client()
        import anthropic
        if image:
            content = [{"type": "image", "source": {"type": "base64", "media_type": image[1], "data": base64.b64encode(image[0]).decode("ascii")}},
                       {"type": "text", "text": user_text}]
        else:
            content = user_text
        try:
            resp = client.messages.create(model=self.model, max_tokens=max_tokens, system=system, tools=[tool],
                                          tool_choice={"type": "tool", "name": tool["name"]},
                                          messages=[{"role": "user", "content": content}])
        except (anthropic.APIConnectionError, anthropic.APITimeoutError):
            raise LLMError("ai_unreachable", "Could not reach the AI service. Check your connection and try again.", 503)
        except anthropic.RateLimitError:
            raise LLMError("ai_busy", "The AI service is busy right now. Please try again in a minute.", 429)
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError):
            raise LLMError("ai_auth", "The server's AI credentials were rejected. The site owner needs to check the API key.", 503)
        except anthropic.APIStatusError as e:
            raise LLMError("ai_error", f"The AI service returned an error ({getattr(e, 'status_code', '?')}). Please try again.", 502)
        for block in getattr(resp, "content", []) or []:
            if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == tool["name"] and isinstance(block.input, dict):
                return block.input
        raise LLMError("bad_response", "The AI did not return readable data. Please try again or use a clearer photo.", 502)


# ------------------------------------------------------------------ Gemini
_GTYPE = {"string": "STRING", "number": "NUMBER", "integer": "INTEGER", "boolean": "BOOLEAN", "array": "ARRAY", "object": "OBJECT"}


def to_gemini_schema(node):
    """JSON Schema (as used for the Anthropic tool) -> the OpenAPI-subset Gemini accepts."""
    if not isinstance(node, dict):
        return node
    out = {}
    t = node.get("type")
    if isinstance(t, list):
        non_null = [x for x in t if x != "null"]
        out["type"] = _GTYPE.get(non_null[0] if non_null else "string", "STRING")
        if "null" in t:
            out["nullable"] = True
    elif t:
        out["type"] = _GTYPE.get(t, "STRING")
    for k in ("description", "enum", "required", "maxItems", "minItems"):
        if k in node:
            out[k] = node[k]
    if "properties" in node:
        out["properties"] = {k: to_gemini_schema(v) for k, v in node["properties"].items()}
    if "items" in node:
        out["items"] = to_gemini_schema(node["items"])
    return out


class GeminiBackend:
    name, label = "gemini", "Google Gemini"

    def __init__(self, api_key, model, timeout=60, http=None):
        self.api_key, self.model, self.timeout, self._http = api_key, model, timeout, http

    @property
    def configured(self):
        return bool(self.api_key)

    def _post(self, body):
        import httpx
        http = self._http or httpx.Client(timeout=self.timeout)
        try:
            return http.post(GEMINI_URL.format(model=self.model), json=body,
                             headers={"x-goog-api-key": self.api_key, "Content-Type": "application/json"})
        except httpx.TimeoutException:
            raise LLMError("ai_unreachable", "The AI service took too long to answer. Please try again.", 503)
        except httpx.HTTPError:
            raise LLMError("ai_unreachable", "Could not reach the AI service. Check your connection and try again.", 503)

    @staticmethod
    def _api_message(resp):
        try:
            return str(resp.json().get("error", {}).get("message", ""))[:300]
        except Exception:
            return ""

    def _raise_for_status(self, resp):
        s, msg = resp.status_code, self._api_message(resp)
        if s == 200:
            return
        if s in (401, 403) or (s == 400 and re.search(r"api key", msg, re.I)):
            raise LLMError("ai_auth", "Gemini rejected the API key (or this region/model isn't allowed for it). Check GEMINI_API_KEY and GEMINI_MODEL.", 503)
        if s == 404:
            raise LLMError("ai_model", f"Gemini doesn't know the model “{self.model}”. Set GEMINI_MODEL to a model listed in Google AI Studio.", 503)
        if s == 429:
            raise LLMError("ai_busy", "Gemini's free-tier limit was reached. Wait a minute and try again.", 429)
        if s >= 500:
            raise LLMError("ai_error", "Gemini had a temporary problem. Please try again.", 502)
        log.warning("gemini error %s: %s", s, msg)
        raise LLMError("ai_error", f"Gemini rejected the request ({s}). Please try again.", 502)

    def structured(self, system, user_text, tool, image=None, max_tokens=8192):
        if not self.configured:
            raise LLMError("not_configured", "The AI service is not configured on this server (no GEMINI_API_KEY). You can still enter your bill values manually.", 503)
        parts = []
        if image:
            parts.append({"inlineData": {"mimeType": image[1], "data": base64.b64encode(image[0]).decode("ascii")}})
        parts.append({"text": user_text})
        gen = {"temperature": 0, "maxOutputTokens": max_tokens, "responseMimeType": "application/json",
               "responseSchema": to_gemini_schema(tool["input_schema"])}
        body = {"systemInstruction": {"parts": [{"text": system}]}, "contents": [{"role": "user", "parts": parts}], "generationConfig": gen}
        resp = self._post(body)
        if resp.status_code == 400 and not re.search(r"api key", self._api_message(resp), re.I):
            # Schema not accepted? Retry once with the schema written into the prompt instead.
            log.warning("gemini rejected responseSchema (%s); retrying with schema in prompt", self._api_message(resp)[:120])
            gen2 = {k: v for k, v in gen.items() if k != "responseSchema"}
            sys2 = system + "\n\nReply with ONE JSON object that follows this JSON Schema exactly (null for unknown values):\n" + json.dumps(tool["input_schema"])
            resp = self._post({**body, "systemInstruction": {"parts": [{"text": sys2}]}, "generationConfig": gen2})
        self._raise_for_status(resp)
        try:
            data = resp.json()
        except ValueError:
            raise LLMError("bad_response", "The AI did not return readable data. Please try again.", 502)
        cands = data.get("candidates") or []
        if not cands:
            reason = (data.get("promptFeedback") or {}).get("blockReason")
            if reason:
                raise LLMError("blocked", "The AI declined to read this image. Please try a different photo.", 422)
            raise LLMError("bad_response", "The AI did not return readable data. Please try again.", 502)
        cand = cands[0]
        if cand.get("finishReason") in ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION"):
            raise LLMError("blocked", "The AI declined to read this image. Please try a different photo.", 422)
        text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts", []) if not p.get("thought"))
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
        try:
            out = json.loads(text)
        except ValueError:
            raise LLMError("bad_response", "The AI answer was cut off or unreadable. Please try again.", 502)
        if not isinstance(out, dict):
            raise LLMError("bad_response", "The AI did not return readable data. Please try again.", 502)
        return out


def make_backend(cfg):
    if cfg.get("LLM_PROVIDER") == "anthropic":
        return AnthropicBackend(cfg["ANTHROPIC_API_KEY"], cfg["ANTHROPIC_MODEL"], cfg["ANTHROPIC_TIMEOUT_S"])
    return GeminiBackend(cfg["GEMINI_API_KEY"], cfg["GEMINI_MODEL"], cfg["ANTHROPIC_TIMEOUT_S"])
