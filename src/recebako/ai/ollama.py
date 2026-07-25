from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from recebako.domain import ReceiptExtraction

OLLAMA_CHAT_URL = "http://127.0.0.1:11434/api/chat"
OLLAMA_MODEL = "qwen3-vl:8b"
REQUEST_TIMEOUT_SECONDS = 60.0

EXTRACTION_PROMPT = """\
これは日本の店舗のレシート画像です。記載内容を読み取ってください。
ルール:
1. 画像に書かれている情報だけを使う。推測で補完しない
2. 読み取れない項目は空文字または0にする
3. 金額は税込の整数(円)。カンマは除去
4. 値引き行はpriceを負の値にする
5. 全体の読み取りに対する自信をconfidenceに0.0-1.0で正直に申告する
"""


class OllamaError(RuntimeError):
    """Ollamaとの通信または応答検証に失敗したことを表す。"""


class _OllamaMessage(BaseModel):
    content: str


class _OllamaChatResponse(BaseModel):
    message: _OllamaMessage


def _request_payload(image_bytes: bytes) -> dict[str, Any]:
    return {
        "model": OLLAMA_MODEL,
        "stream": False,
        "options": {"temperature": 0},
        "format": ReceiptExtraction.model_json_schema(),
        "messages": [
            {
                "role": "user",
                "images": [base64.b64encode(image_bytes).decode("ascii")],
                "content": EXTRACTION_PROMPT,
            }
        ],
    }


def request_receipt_extraction(image_path: Path) -> str:
    image_bytes = image_path.read_bytes()

    try:
        with httpx.Client(
            timeout=REQUEST_TIMEOUT_SECONDS,
            trust_env=False,
        ) as client:
            response = client.post(
                OLLAMA_CHAT_URL,
                json=_request_payload(image_bytes),
            )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise OllamaError(
            f"OllamaがHTTP {exc.response.status_code}を返しました"
        ) from exc
    except httpx.RequestError as exc:
        raise OllamaError(
            "Ollamaへ接続できませんでした (http://127.0.0.1:11434 を確認してください)"
        ) from exc

    try:
        chat_response = _OllamaChatResponse.model_validate(response.json())
    except (ValueError, ValidationError) as exc:
        raise OllamaError("Ollamaから無効な応答を受信しました") from exc

    return chat_response.message.content


def extract_receipt(image_path: Path) -> ReceiptExtraction:
    try:
        return ReceiptExtraction.model_validate_json(
            request_receipt_extraction(image_path)
        )
    except ValidationError as exc:
        raise OllamaError("Ollamaから無効な構造化応答を受信しました") from exc
