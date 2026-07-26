from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Self

import httpx
import pytest
import respx

from recebako.ai.ollama import (
    OLLAMA_CHAT_URL,
    OLLAMA_MODEL,
    OllamaError,
    extract_receipt,
    request_receipt_extraction,
    request_receipt_extraction_with_config,
)
from recebako.config import OllamaConfig
from recebako.domain import ReceiptExtraction


def _ollama_response(content: object) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "message": {
                "role": "assistant",
                "content": json.dumps(content, ensure_ascii=False),
            }
        },
    )


@respx.mock
def test_configured_request_canonicalizes_localhost_and_uses_model_settings(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "image.jpg"
    image_path.write_bytes(b"synthetic-image")
    config = OllamaConfig(
        base_url="http://localhost:12456/",
        model="configured-model:latest",
        temperature=0,
    )
    route = respx.post("http://127.0.0.1:12456/api/chat").mock(
        return_value=_ollama_response({"accepted": True})
    )

    result = request_receipt_extraction_with_config(image_path, config=config)

    assert json.loads(result) == {"accepted": True}
    assert config.base_url == "http://127.0.0.1:12456"
    request = route.calls.last.request
    assert str(request.url) == "http://127.0.0.1:12456/api/chat"
    payload = json.loads(request.content)
    assert payload["model"] == "configured-model:latest"
    assert payload["options"] == {"temperature": 0}


@respx.mock
def test_legacy_scalar_request_accepts_localhost_alternate_port(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "image.jpg"
    image_path.write_bytes(b"synthetic-image")
    route = respx.post("http://127.0.0.1:12457/api/chat").mock(
        return_value=_ollama_response({"legacy": True})
    )

    result = request_receipt_extraction(
        image_path,
        base_url="http://localhost:12457/",
        model="legacy-model",
        temperature=0,
    )

    assert json.loads(result) == {"legacy": True}
    assert str(route.calls.last.request.url) == "http://127.0.0.1:12457/api/chat"
    payload = json.loads(route.calls.last.request.content)
    assert payload["model"] == "legacy-model"
    assert payload["options"] == {"temperature": 0}


@pytest.mark.parametrize(
    "base_url",
    [
        "https://127.0.0.1:11434",
        "http://192.0.2.1:11434",
        "http://127.0.0.1:11434/api",
        "http://PRIVATE-SECRET@127.0.0.1:11434",
        "http://127.0.0.1:11434?target=remote",
        "http://127.0.0.1:11434#remote",
        "http://[::1]:11434",
        "http://2130706433:11434",
        "http://127.1:11434",
    ],
)
def test_legacy_scalar_rejects_unsafe_url_before_image_read_or_network(
    tmp_path: Path,
    respx_mock: respx.MockRouter,
    base_url: str,
) -> None:
    missing_image = tmp_path / "must-not-be-read.jpg"

    with pytest.raises(OllamaError) as captured:
        request_receipt_extraction(
            missing_image,
            base_url=base_url,
        )

    assert "安全条件を満たしていません" in str(captured.value)
    assert "PRIVATE-SECRET" not in str(captured.value)
    assert len(respx_mock.calls) == 0


@pytest.mark.parametrize(
    "unsafe_update",
    [
        {"base_url": "http://192.0.2.1:11434"},
        {"model": " "},
        {"temperature": 1},
    ],
)
def test_configured_request_revalidates_unsafe_model_copy_before_image_read(
    tmp_path: Path,
    respx_mock: respx.MockRouter,
    unsafe_update: dict[str, object],
) -> None:
    safe_config = OllamaConfig(
        base_url="http://localhost:12458",
        model="configured-model",
        temperature=0,
    )
    unsafe_config = safe_config.model_copy(update=unsafe_update)
    missing_image = tmp_path / "must-not-be-read.jpg"

    with pytest.raises(OllamaError, match="安全条件を満たしていません"):
        request_receipt_extraction_with_config(
            missing_image,
            config=unsafe_config,
        )

    assert len(respx_mock.calls) == 0


def test_configured_request_disables_environment_proxy_and_redirects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_path = tmp_path / "image.jpg"
    image_path.write_bytes(b"synthetic-image")
    monkeypatch.setenv("HTTP_PROXY", "http://192.0.2.10:8080")
    monkeypatch.setenv("HTTPS_PROXY", "http://192.0.2.11:8080")
    monkeypatch.setenv("ALL_PROXY", "http://192.0.2.12:8080")
    captured: dict[str, object] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "message": {
                    "role": "assistant",
                    "content": json.dumps({"accepted": True}),
                }
            }

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            captured["client_kwargs"] = kwargs

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def post(self, url: str, *, json: object) -> FakeResponse:
            captured["url"] = url
            captured["payload"] = json
            return FakeResponse()

    monkeypatch.setattr("recebako.ai.ollama.httpx.Client", FakeClient)
    config = OllamaConfig(
        base_url="http://localhost:12458",
        model="configured-model",
        temperature=0,
    )

    result = request_receipt_extraction_with_config(image_path, config=config)

    assert json.loads(result) == {"accepted": True}
    client_kwargs = captured["client_kwargs"]
    assert isinstance(client_kwargs, dict)
    assert client_kwargs["trust_env"] is False
    assert client_kwargs["follow_redirects"] is False
    assert captured["url"] == "http://127.0.0.1:12458/api/chat"


@respx.mock
def test_extract_receipt_sends_schema_and_validates_response(tmp_path: Path) -> None:
    image_bytes = b"not-a-real-receipt"
    image_path = tmp_path / "receipt.jpg"
    image_path.write_bytes(image_bytes)
    route = respx.post(OLLAMA_CHAT_URL).mock(
        return_value=_ollama_response(
            {
                "is_receipt": True,
                "store": "テスト商店",
                "date": "2026-07-25",
                "time": "12:34",
                "items": [{"name": "りんご", "qty": 2, "price": 300}],
                "subtotal": 273,
                "tax": 27,
                "total": 300,
                "payment": "cash",
                "confidence": 0.95,
            }
        )
    )

    result = extract_receipt(image_path)

    assert result.store == "テスト商店"
    assert result.total == 300
    request = route.calls.last.request
    payload = json.loads(request.content)
    assert payload["model"] == OLLAMA_MODEL
    assert payload["stream"] is False
    assert payload["think"] is False
    assert payload["options"] == {"temperature": 0}
    assert payload["format"] == ReceiptExtraction.model_json_schema()
    assert payload["format"]["properties"]["is_receipt"]["type"] == "boolean"
    assert "is_receipt" in payload["format"]["required"]
    item_schema = payload["format"]["$defs"]["ReceiptItem"]["properties"]
    assert {"price_raw", "tax_rate", "tax_treatment"} <= item_schema.keys()
    assert "tax_breakdowns" in payload["format"]["properties"]
    assert {"price_raw", "tax_rate", "tax_treatment"} <= set(
        payload["format"]["$defs"]["ReceiptItem"]["required"]
    )
    assert "tax_breakdowns" in payload["format"]["required"]
    assert "items[].price_raw" in payload["messages"][0]["content"]
    assert "tax_breakdowns" in payload["messages"][0]["content"]
    assert "is_receipt=false" in payload["messages"][0]["content"]
    assert payload["messages"][0]["images"] == [
        base64.b64encode(image_bytes).decode("ascii")
    ]


@respx.mock
def test_extract_receipt_accepts_explicit_non_receipt_result(tmp_path: Path) -> None:
    image_path = tmp_path / "image.jpg"
    image_path.write_bytes(b"synthetic-image")
    respx.post(OLLAMA_CHAT_URL).mock(
        return_value=_ollama_response(
            {
                "is_receipt": False,
                "store": "",
                "date": "",
                "time": "",
                "items": [],
                "subtotal": 0,
                "tax": 0,
                "tax_breakdowns": [],
                "total": 0,
                "payment": "unknown",
                "confidence": 0.95,
            }
        )
    )

    result = extract_receipt(image_path)

    assert result.is_receipt is False
    assert result.items == []


@pytest.mark.parametrize("value", ["false", 0, 1, None])
@respx.mock
def test_extract_receipt_rejects_non_boolean_is_receipt(
    tmp_path: Path,
    value: object,
) -> None:
    image_path = tmp_path / "image.jpg"
    image_path.write_bytes(b"synthetic-image")
    respx.post(OLLAMA_CHAT_URL).mock(
        return_value=_ollama_response(
            {
                "is_receipt": value,
                "store": "",
                "date": "",
                "time": "",
                "items": [],
                "subtotal": 0,
                "tax": 0,
                "tax_breakdowns": [],
                "total": 0,
                "payment": "unknown",
                "confidence": 0.95,
            }
        )
    )

    with pytest.raises(OllamaError, match="無効な構造化応答"):
        extract_receipt(image_path)


@respx.mock
def test_extract_receipt_rejects_missing_is_receipt(tmp_path: Path) -> None:
    image_path = tmp_path / "image.jpg"
    image_path.write_bytes(b"synthetic-image")
    respx.post(OLLAMA_CHAT_URL).mock(
        return_value=_ollama_response(
            {
                "store": "",
                "date": "",
                "time": "",
                "items": [],
                "subtotal": 0,
                "tax": 0,
                "tax_breakdowns": [],
                "total": 0,
                "payment": "unknown",
                "confidence": 0.95,
            }
        )
    )

    with pytest.raises(OllamaError, match="無効な構造化応答"):
        extract_receipt(image_path)


@respx.mock
def test_extract_receipt_rejects_malformed_json_content(tmp_path: Path) -> None:
    image_path = tmp_path / "image.jpg"
    image_path.write_bytes(b"synthetic-image")
    respx.post(OLLAMA_CHAT_URL).mock(
        return_value=httpx.Response(
            200,
            json={"message": {"role": "assistant", "content": "{not-json"}},
        )
    )

    with pytest.raises(OllamaError, match="無効な構造化応答"):
        extract_receipt(image_path)


@respx.mock
def test_extract_receipt_rejects_invalid_structured_output(tmp_path: Path) -> None:
    image_path = tmp_path / "receipt.jpg"
    image_path.write_bytes(b"synthetic-image")
    respx.post(OLLAMA_CHAT_URL).mock(
        return_value=_ollama_response(
            {
                "is_receipt": True,
                "store": "テスト商店",
                "date": "2026-07-25",
                "items": [],
                "total": "not-an-integer",
                "confidence": 0.9,
            }
        )
    )

    with pytest.raises(OllamaError, match="無効な構造化応答"):
        extract_receipt(image_path)


@respx.mock
def test_extract_receipt_accepts_structured_output_from_thinking_model(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "receipt.jpg"
    image_path.write_bytes(b"synthetic-image")
    receipt = {
        "is_receipt": True,
        "store": "テスト商店",
        "date": "2026-07-25",
        "time": "12:34",
        "items": [{"name": "りんご", "qty": 2, "price": 300}],
        "subtotal": 273,
        "tax": 27,
        "total": 300,
        "payment": "cash",
        "confidence": 0.95,
    }
    respx.post(OLLAMA_CHAT_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "message": {
                    "role": "assistant",
                    "content": "",
                    "thinking": json.dumps(receipt, ensure_ascii=False),
                }
            },
        )
    )

    result = extract_receipt(image_path)

    assert result.store == "テスト商店"
    assert result.total == 300


@respx.mock
def test_extract_receipt_reports_ollama_http_error(tmp_path: Path) -> None:
    image_path = tmp_path / "receipt.jpg"
    image_path.write_bytes(b"synthetic-image")
    respx.post(OLLAMA_CHAT_URL).mock(return_value=httpx.Response(500))

    with pytest.raises(OllamaError, match="HTTP 500"):
        extract_receipt(image_path)


@respx.mock
def test_extract_receipt_reports_inference_timeout_separately(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "receipt.jpg"
    image_path.write_bytes(b"synthetic-image")
    respx.post(OLLAMA_CHAT_URL).mock(
        side_effect=httpx.ReadTimeout("inference timed out")
    )

    with pytest.raises(OllamaError, match="推論が180秒以内"):
        extract_receipt(image_path)


@respx.mock
def test_extract_receipt_reports_connection_failure_separately(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "receipt.jpg"
    image_path.write_bytes(b"synthetic-image")
    respx.post(OLLAMA_CHAT_URL).mock(
        side_effect=httpx.ConnectError("connection refused")
    )

    with pytest.raises(OllamaError, match="接続できません"):
        extract_receipt(image_path)
