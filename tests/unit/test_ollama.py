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
    assert payload["options"] == {"temperature": 0}
    assert payload["format"] == ReceiptExtraction.model_json_schema()
    assert payload["messages"][0]["images"] == [
        base64.b64encode(image_bytes).decode("ascii")
    ]


@respx.mock
def test_extract_receipt_rejects_invalid_structured_output(tmp_path: Path) -> None:
    image_path = tmp_path / "receipt.jpg"
    image_path.write_bytes(b"synthetic-image")
    respx.post(OLLAMA_CHAT_URL).mock(
        return_value=_ollama_response(
            {
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
def test_extract_receipt_reports_ollama_http_error(tmp_path: Path) -> None:
    image_path = tmp_path / "receipt.jpg"
    image_path.write_bytes(b"synthetic-image")
    respx.post(OLLAMA_CHAT_URL).mock(return_value=httpx.Response(500))

    with pytest.raises(OllamaError, match="HTTP 500"):
        extract_receipt(image_path)
