"""Bailian dense embeddings over HTTP, with a fixed and validated vector space."""

import math
import os
import re
import time
from collections.abc import Sequence
from concurrent.futures import CancelledError
from threading import Event
from typing import Any, Dict, List, Optional, Union
from urllib.parse import unquote, urlsplit

import httpx


_EMBEDDING_PATH = "/services/embeddings/text-embedding/text-embedding"
_ALGORITHM = "dashscope-dense-v1"


class DashScopeEmbeddingFunction:
    """Use document/query conventions without downloading a local fallback model.

    The 8192 UTF-8 byte input bound deliberately overestimates token count, so
    untrusted long inputs are rejected before any batch is charged. It is not a
    substitute for the document chunker. Cancellation is checked between HTTP
    calls; an in-flight synchronous call is bounded by the configured timeout.
    """

    provider = "dashscope"
    max_batch_size = 10
    max_input_tokens = 8192
    max_input_bytes = 8192
    document_text_type = "document"
    query_text_type = "query"

    def __init__(
        self,
        model_name: str = "text-embedding-v4",
        api_key: Optional[str] = None,
        *,
        base_url: Optional[str] = None,
        endpoint: Optional[str] = None,
        dimensions: Optional[int] = None,
        client: Optional[httpx.Client] = None,
        cancel_event: Optional[Event] = None,
    ):
        self.model_name = model_name.strip()
        if not re.fullmatch(r"[A-Za-z0-9._-]+", self.model_name):
            raise ValueError("EMBEDDING_MODEL must be a cloud model identifier.")
        self.api_key = (os.getenv("DASHSCOPE_API_KEY", "") if api_key is None else api_key).strip()
        if not self.api_key:
            raise RuntimeError("DASHSCOPE_API_KEY is required in the private server environment.")
        if any(character.isspace() for character in self.api_key):
            raise ValueError("DASHSCOPE_API_KEY must be a valid private API key.")
        self.dimensions = self._number_setting("EMBEDDING_DIMENSION", 1024, 1024, 1024, integer=True)
        if dimensions is not None and (type(dimensions) is not int or dimensions != self.dimensions):
            raise ValueError("EMBEDDING_DIMENSION must be 1024 for this vector space.")
        self.endpoint = self._resolve_endpoint(base_url, endpoint)
        self.timeout_seconds = self._number_setting("DASHSCOPE_TIMEOUT_SECONDS", 30, 1, 60)
        self.max_retries = self._number_setting("DASHSCOPE_MAX_RETRIES", 0, 0, 2, integer=True)
        self.retry_backoff_seconds = self._number_setting("DASHSCOPE_RETRY_BACKOFF_SECONDS", 1, 0, 5)
        self._client = client
        self._cancel_event = cancel_event

    @staticmethod
    def _number_setting(name: str, default: int, minimum: int, maximum: int, *, integer: bool = False):
        raw = os.getenv(name, str(default)).strip()
        try:
            value = int(raw) if integer else float(raw)
            valid = math.isfinite(value) and minimum <= value <= maximum
        except (ValueError, OverflowError):
            valid = False
        if not valid:
            raise ValueError(f"{name} must be between {minimum} and {maximum}.")
        return value

    @staticmethod
    def _resolve_endpoint(base_url: Optional[str], endpoint: Optional[str]) -> str:
        # Explicit constructor arguments take priority over all environment values.
        selected_endpoint = endpoint if endpoint is not None else (
            os.getenv("DASHSCOPE_EMBEDDING_ENDPOINT", "") if base_url is None else "")
        selected_base = base_url if base_url is not None else os.getenv("DASHSCOPE_HTTP_BASE_URL", "")
        value = (selected_endpoint or selected_base).strip().rstrip("/")
        if not value:
            raise RuntimeError(
                "Configure the account endpoint in DASHSCOPE_EMBEDDING_ENDPOINT or DASHSCOPE_HTTP_BASE_URL.")
        try:
            parsed = urlsplit(value)
            valid = (
                parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username
                and not parsed.password and not parsed.query and not parsed.fragment
                and not any(character in unquote(value) for character in "{}<>")
                and not any(character.isspace() for character in value)
            )
            parsed.port  # Validate a malformed port without returning any private URL in the error.
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("DashScope endpoint must be an HTTPS URL without credentials or placeholders.")
        if selected_endpoint:
            return value
        if parsed.path in ("", "/"):
            value += "/api/v1"
        elif parsed.path.rstrip("/") != "/api/v1":
            raise ValueError("DASHSCOPE_HTTP_BASE_URL must be an HTTPS host or an /api/v1 base URL.")
        return value + _EMBEDDING_PATH

    @staticmethod
    def name() -> str:
        # Chroma registers classes by a static provider name; model identity is in fingerprint/config.
        return "dashscope"

    @property
    def dimension(self) -> int:
        return self.dimensions

    @property
    def fingerprint(self) -> str:
        return (
            f"{self.provider}:{self.model_name}:dim={self.dimensions}:algorithm={_ALGORITHM}"
            f":document={self.document_text_type}:query={self.query_text_type}"
        )

    def get_config(self) -> Dict[str, Any]:
        # Chroma configuration must never persist credentials or account-specific endpoints.
        return {
            "model_name": self.model_name, "dimensions": self.dimensions, "algorithm": _ALGORITHM,
            "document_text_type": self.document_text_type, "query_text_type": self.query_text_type,
        }

    @staticmethod
    def build_from_config(config: Dict[str, Any]) -> "DashScopeEmbeddingFunction":
        expected = {"algorithm": _ALGORITHM, "document_text_type": "document", "query_text_type": "query"}
        if not isinstance(config, dict) or any(config.get(key) != value for key, value in expected.items()):
            raise ValueError("Incompatible DashScope embedding algorithm configuration; rebuild the collection.")
        return DashScopeEmbeddingFunction(model_name=config["model_name"], dimensions=config["dimensions"])

    @staticmethod
    def is_legacy() -> bool:
        return False

    @staticmethod
    def default_space() -> str:
        return "cosine"

    @staticmethod
    def supported_spaces() -> List[str]:
        return ["cosine", "l2", "ip"]

    @staticmethod
    def _normalize_texts(value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, str):
            texts = [value.strip()] if value.strip() else []
        elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
            texts = []
            for text in value:
                if not isinstance(text, str) or not text.strip():
                    raise ValueError("Each embedding document/query text must be a nonempty string.")
                texts.append(text.strip())
        else:
            raise ValueError("Embedding text must be a string or a sequence of document strings.")
        for text in texts:
            try:
                too_long = len(text.encode("utf-8")) > DashScopeEmbeddingFunction.max_input_bytes
            except UnicodeEncodeError:
                raise ValueError("Embedding text must contain valid UTF-8 characters.") from None
            if too_long:
                raise ValueError("Embedding text exceeds the conservative 8192-byte/token bound; split the document first.")
        return texts

    def _check_cancelled(self) -> None:
        if self._cancel_event is not None and self._cancel_event.is_set():
            raise CancelledError("DashScope embedding cancelled.")

    def _wait_for_retry(self, attempt: int) -> None:
        self._check_cancelled()
        delay = self.retry_backoff_seconds * (attempt + 1)
        if self._cancel_event is None:
            time.sleep(delay)
        else:
            self._cancel_event.wait(delay)
        self._check_cancelled()

    @staticmethod
    def _response_error(status: int, payload: Any):
        code = ""
        if isinstance(payload, dict):
            code = str(payload.get("code") or "")
            if not code and isinstance(payload.get("error"), dict):
                code = str(payload["error"].get("code") or "")
        normalized_code = re.sub(r"[^a-z]", "", code.lower())
        if status in (401, 403) or any(word in normalized_code for word in (
            "invalidapikey", "accessdenied", "unauthorized", "forbidden", "authentication")):
            return RuntimeError(f"DashScope embedding authentication failed (HTTP {status}); check DASHSCOPE_API_KEY."), False
        if status in (402, 429) or any(word in normalized_code for word in (
            "balance", "quota", "arrear", "billing", "overdue", "insufficient", "allocation")):
            return RuntimeError(f"DashScope embedding balance/quota unavailable (HTTP {status}); check account billing and quota."), False
        return RuntimeError(f"DashScope embedding request failed (HTTP {status})."), status in (408, 500, 502, 503, 504)

    def _request_batch(self, client: httpx.Client, texts: List[str], text_type: str) -> List[List[float]]:
        for attempt in range(self.max_retries + 1):
            self._check_cancelled()
            try:
                response = client.post(
                    self.endpoint,
                    headers={"Authorization": "Bearer " + self.api_key},
                    json={"model": self.model_name, "input": {"texts": texts}, "parameters": {
                        "dimension": self.dimensions, "text_type": text_type, "output_type": "dense"}},
                    timeout=httpx.Timeout(self.timeout_seconds, connect=min(10, self.timeout_seconds)),
                    follow_redirects=False,
                )
            except httpx.TimeoutException:
                failure = RuntimeError("DashScope embedding request timed out.")
                retryable = True
            except httpx.TransportError:
                failure = RuntimeError("DashScope embedding network request failed.")
                retryable = True
            else:
                self._check_cancelled()
                try:
                    payload = response.json()
                except ValueError:
                    if response.status_code == 200:
                        raise RuntimeError("DashScope embedding response is not valid JSON.") from None
                    payload = None
                application_error = isinstance(payload, dict) and (
                    bool(payload.get("code")) or bool(payload.get("error"))
                    or payload.get("status_code") not in (None, 200, "200"))
                if response.status_code == 200 and not application_error:
                    return self._parse_embeddings(payload, len(texts))
                failure, retryable = self._response_error(response.status_code, payload)
            if not retryable or attempt >= self.max_retries:
                # SDK/network exceptions and provider messages can contain keys or source text.
                raise failure from None
            self._wait_for_retry(attempt)
        raise RuntimeError("DashScope embedding request failed.")  # Defensive: loop always executes once.

    def _parse_embeddings(self, payload: Any, expected_count: int) -> List[List[float]]:
        output = payload.get("output") if isinstance(payload, dict) else None
        embeddings = output.get("embeddings") if isinstance(output, dict) else None
        if not isinstance(embeddings, list) or len(embeddings) != expected_count:
            raise RuntimeError("DashScope embedding response cardinality does not match the input.")
        vectors: Dict[int, List[float]] = {}
        for item in embeddings:
            index = item.get("text_index") if isinstance(item, dict) else None
            if type(index) is not int or index < 0 or index >= expected_count or index in vectors:
                raise RuntimeError("DashScope embedding response has invalid or duplicate indices.")
            vector = item.get("embedding")
            if not isinstance(vector, list) or len(vector) != self.dimensions:
                raise RuntimeError("DashScope embedding response dimension must be 1024.")
            parsed = []
            for number in vector:
                if type(number) not in (int, float):
                    raise RuntimeError("DashScope embedding response must contain numeric vector values.")
                try:
                    number = float(number)
                except (ValueError, OverflowError):
                    raise RuntimeError("DashScope embedding response must contain finite vector values.") from None
                if not math.isfinite(number):
                    raise RuntimeError("DashScope embedding response must contain finite vector values.")
                parsed.append(number)
            vectors[index] = parsed
        return [vectors[index] for index in range(expected_count)]

    def _embed(self, texts: List[str], text_type: str) -> List[List[float]]:
        if not texts:
            return []
        self._check_cancelled()

        def embed_batches(client: httpx.Client) -> List[List[float]]:
            vectors = []
            for offset in range(0, len(texts), self.max_batch_size):
                vectors.extend(self._request_batch(client, texts[offset:offset + self.max_batch_size], text_type))
            return vectors

        if self._client is not None:
            return embed_batches(self._client)
        with httpx.Client(follow_redirects=False, trust_env=False) as client:
            return embed_batches(client)

    def __call__(self, input: Sequence[str]) -> List[List[float]]:
        return self._embed(self._normalize_texts(input), self.document_text_type)

    def embed_documents(self, texts: Optional[Sequence[str]] = None, **kwargs) -> List[List[float]]:
        if texts is None:
            texts = kwargs.get("input", [])
        return self._embed(self._normalize_texts(texts), self.document_text_type)

    def embed_query(self, text: Optional[str] = None, **kwargs) -> Union[List[float], List[List[float]]]:
        """A scalar returns one vector; Chroma's sequence input returns a matrix."""
        value = kwargs.get("input", "") if text is None else text
        vectors = self._embed(self._normalize_texts(value), self.query_text_type)
        return (vectors[0] if vectors else []) if isinstance(value, str) else vectors
