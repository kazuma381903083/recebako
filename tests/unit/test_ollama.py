from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx
import pytest
import respx

from recebako.ai.ollama import (
    OLLAMA_CHAT_URL,
    OLLAMA_MODEL,
    OllamaError,
    extract_receipt,
)
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
