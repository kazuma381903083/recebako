from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from recebako.domain import ReceiptExtraction

OLLAMA_CHAT_URL = "http://127.0.0.1:11434/api/chat"
OLLAMA_BASE_URL = "http://127.0.0.1:11434"
OLLAMA_MODEL = "qwen3-vl:8b"
CONNECT_TIMEOUT_SECONDS = 5.0
READ_TIMEOUT_SECONDS = 180.0
WRITE_TIMEOUT_SECONDS = 30.0
POOL_TIMEOUT_SECONDS = 5.0

EXTRACTION_PROMPT = """\
入力画像が日本の店舗のレシートかを判定し、レシートなら記載内容を読み取ってください。
ルール:
1. 店舗のレシートならis_receipt=true、それ以外の画像ならis_receipt=falseにする
2. is_receipt=falseの場合、文字列は空文字、数値は0、itemsとtax_breakdownsは
   空配列、paymentはunknownにし、画像内容を推測してレシート情報を作らない
3. 画像に書かれている情報だけを使う。推測で補完しない
4. 読み取れない項目は空文字または0にする
5. items[].priceは数量を含む税込の行合計(円)。カンマは除去する
6. items[].price_rawには商品行へ実際に印字された行合計をそのまま入れる
7. 商品行や税率別集計に「内」「込」とあればincluded、税が後から加算
   される税抜表示ならexcluded、判断できなければunknownにする
8. 軽減税率記号や税率別集計から読める場合だけitems[].tax_rateを設定する
9. tax_breakdownsにはレシートに明記された税率、対象金額、税額、内税・外税
   区分を入れる。推測した内訳は追加しない
10. 小計、消費税、合計、預り、釣銭などの集計行はitemsへ含めない
11. 値引き行はpriceとprice_rawを負の値にする
12. 判定と読み取りに対する自信をconfidenceに0.0-1.0で正直に申告する
"""


class OllamaError(RuntimeError):
    """Ollamaとの通信または応答検証に失敗したことを表す。"""


class OllamaTimeoutError(OllamaError):
    """Ollamaの推論が制限時間内に完了しなかったことを表す。"""


class OllamaConnectionError(OllamaError):
    """localhostのOllamaへ接続できなかったことを表す。"""


class OllamaResponseError(OllamaError):
    """Ollamaが処理不能なHTTPまたは応答内容を返したことを表す。"""


class _OllamaMessage(BaseModel):
    content: str
    thinking: str = ""


class _OllamaChatResponse(BaseModel):
    message: _OllamaMessage


def _request_payload(
    image_bytes: bytes,
    *,
    model: str,
    temperature: int,
) -> dict[str, Any]:
    return {
        "model": model,
        "stream": False,
        "think": False,
        "options": {"temperature": temperature},
        "format": ReceiptExtraction.model_json_schema(),
        "messages": [
            {
                "role": "user",
                "images": [base64.b64encode(image_bytes).decode("ascii")],
                "content": EXTRACTION_PROMPT,
            }
        ],
    }


def request_receipt_extraction(
    image_path: Path,
    *,
    base_url: str = OLLAMA_BASE_URL,
    model: str = OLLAMA_MODEL,
    temperature: int = 0,
) -> str:
    if base_url != OLLAMA_BASE_URL:
        raise OllamaError("Ollama接続先は127.0.0.1:11434のみ指定できます")

    image_bytes = image_path.read_bytes()

    try:
        with httpx.Client(
            timeout=httpx.Timeout(
                connect=CONNECT_TIMEOUT_SECONDS,
                read=READ_TIMEOUT_SECONDS,
                write=WRITE_TIMEOUT_SECONDS,
                pool=POOL_TIMEOUT_SECONDS,
            ),
            trust_env=False,
        ) as client:
            response = client.post(
                f"{base_url}/api/chat",
                json=_request_payload(
                    image_bytes,
                    model=model,
                    temperature=temperature,
                ),
            )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise OllamaResponseError(
            f"OllamaがHTTP {exc.response.status_code}を返しました"
        ) from exc
    except httpx.ReadTimeout as exc:
        raise OllamaTimeoutError(
            f"Ollamaの推論が{int(READ_TIMEOUT_SECONDS)}秒以内に完了しませんでした"
        ) from exc
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        raise OllamaConnectionError(
            "Ollamaへ接続できませんでした (http://127.0.0.1:11434 を確認してください)"
        ) from exc
    except httpx.RequestError as exc:
        raise OllamaConnectionError("Ollamaとのローカル通信に失敗しました") from exc

    try:
        chat_response = _OllamaChatResponse.model_validate(response.json())
    except (ValueError, ValidationError) as exc:
        raise OllamaResponseError("Ollamaから無効な応答を受信しました") from exc

    if chat_response.message.content.strip():
        return chat_response.message.content
    return chat_response.message.thinking


def extract_receipt(
    image_path: Path,
    *,
    base_url: str = OLLAMA_BASE_URL,
    model: str = OLLAMA_MODEL,
    temperature: int = 0,
) -> ReceiptExtraction:
    try:
        return ReceiptExtraction.model_validate_json(
            request_receipt_extraction(
                image_path,
                base_url=base_url,
                model=model,
                temperature=temperature,
            )
        )
    except ValidationError as exc:
        raise OllamaResponseError("Ollamaから無効な構造化応答を受信しました") from exc
