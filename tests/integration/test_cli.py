from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from recebako import cli
from recebako.ai import OllamaTimeoutError
from recebako.config import CONFIG_ENV_VAR
from recebako.runtime import (
    InboxLock,
    RuntimeFileError,
    claim_inbox_file,
    initialize_runtime,
    scan_inbox,
)


def _write_config(tmp_path: Path, data_root: Path) -> Path:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
[data]
root = {json.dumps(str(data_root))}

[ollama]
base_url = "http://127.0.0.1:11434"
model = "qwen3-vl:8b"
temperature = 0

[review_ui]
host = "127.0.0.1"
port = 8765
""",
        encoding="utf-8",
    )
    return config_path


def _today() -> date:
    return datetime.now(UTC).astimezone().date()


def _ollama_payload(*, receipt_date: str, total: int = 100) -> str:
    return json.dumps(
        {
            "is_receipt": True,
            "store": "テスト商店",
            "date": receipt_date,
            "time": "12:34",
            "items": [{"name": "りんご", "qty": 1, "price": total}],
            "subtotal": total,
            "tax": 0,
            "total": total,
            "payment": "cash",
            "confidence": 0.99,
        },
        ensure_ascii=False,
    )


def _mixed_tax_payload(*, receipt_date: str) -> str:
    return json.dumps(
        {
            "is_receipt": True,
            "store": "テスト商店",
            "date": receipt_date,
            "time": "11:42",
            "items": [
                {
                    "name": "外税商品",
                    "qty": 1,
                    "price": 140,
                    "price_raw": 140,
                    "tax_rate": 8,
                    "tax_treatment": "excluded",
                },
                {
                    "name": "内税商品",
                    "qty": 1,
                    "price": 570,
                    "price_raw": 570,
                    "tax_rate": 10,
                    "tax_treatment": "included",
                },
            ],
            "subtotal": 710,
            "tax": 62,
            "tax_breakdowns": [
                {
                    "tax_rate": 8,
                    "taxable_amount": 140,
                    "tax_amount": 11,
                    "tax_treatment": "excluded",
                },
                {
                    "tax_rate": 10,
                    "taxable_amount": 570,
                    "tax_amount": 51,
                    "tax_treatment": "included",
                },
            ],
            "total": 721,
            "payment": "cash",
            "confidence": 0.99,
        },
        ensure_ascii=False,
    )


def _multiple_external_tax_payload(*, receipt_date: str) -> str:
    return json.dumps(
        {
            "is_receipt": True,
            "store": "テスト商店",
            "date": receipt_date,
            "time": "19:58",
            "items": [
                {
                    "name": "8%商品A",
                    "qty": 1,
                    "price": 600,
                    "price_raw": 600,
                    "tax_rate": None,
                    "tax_treatment": "excluded",
                },
                {
                    "name": "8%商品B",
                    "qty": 1,
                    "price": 148,
                    "price_raw": 148,
                    "tax_rate": None,
                    "tax_treatment": "excluded",
                },
                {
                    "name": "内税商品A",
                    "qty": 1,
                    "price": 5800,
                    "price_raw": 5800,
                    "tax_rate": None,
                    "tax_treatment": "included",
                },
                {
                    "name": "内税商品B",
                    "qty": 1,
                    "price": 5800,
                    "price_raw": 5800,
                    "tax_rate": None,
                    "tax_treatment": "included",
                },
                {
                    "name": "10%外税商品",
                    "qty": 1,
                    "price": 3,
                    "price_raw": 3,
                    "tax_rate": None,
                    "tax_treatment": "excluded",
                },
            ],
            "subtotal": 0,
            "tax": 0,
            "tax_breakdowns": [
                {
                    "tax_rate": 8,
                    "taxable_amount": 748,
                    "tax_amount": 59,
                    "tax_treatment": "excluded",
                },
                {
                    "tax_rate": 10,
                    "taxable_amount": 3,
                    "tax_amount": 3,
                    "tax_treatment": "excluded",
                },
            ],
            "total": 12410,
            "payment": "unknown",
            "confidence": 0.8,
        },
        ensure_ascii=False,
    )


def test_extract_command_prints_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    image_path = tmp_path / "receipt.jpg"
    with Image.new("RGB", (3000, 1000), "white") as image:
        image.save(image_path)
    temporary_paths: list[Path] = []
    today = _today()
    raw_date = f"{today.year}/{today.month}/{today.day}"

    def fake_request_receipt_extraction(path: Path) -> str:
        temporary_paths.append(path)
        assert path != image_path
        with Image.open(path) as image:
            assert image.mode == "RGB"
            assert max(image.size) == 2048
        return _ollama_payload(receipt_date=raw_date)

    monkeypatch.setattr(
        cli,
        "request_receipt_extraction",
        fake_request_receipt_extraction,
    )

    exit_code = cli.run(["extract", str(image_path)])

    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert exit_code == 0
    assert output["store"] == "テスト商店"
    assert output["date_raw"] == raw_date
    assert output["date"] == today.isoformat()
    assert output["status"] == "confirmed"
    assert output["validation_issues"] == []
    assert "is_receipt" not in output
    assert len(output["phash"]) == 16
    assert captured.err == ""
    assert len(temporary_paths) == 1
    assert not temporary_paths[0].exists()


def test_extract_retries_invalid_response_and_prints_only_accepted_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    image_path = tmp_path / "receipt.jpg"
    with Image.new("RGB", (120, 80), "white") as image:
        image.save(image_path)
    private_sentinel = "PRIVATE-DISCARDED-CLI-ATTEMPT"
    calls: list[str] = []

    def invalid_then_valid(path: Path) -> str:
        calls.append(path.name)
        if len(calls) == 1:
            return json.dumps({"store": private_sentinel})
        return _ollama_payload(receipt_date=_today().isoformat())

    monkeypatch.setattr(
        cli,
        "request_receipt_extraction",
        invalid_then_valid,
    )

    exit_code = cli.run(["extract", str(image_path)])

    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert exit_code == 0
    assert calls == [
        "variant-1-standard.jpg",
        "variant-2-rotated-clockwise-90.jpg",
    ]
    assert output["status"] == "confirmed"
    assert private_sentinel not in captured.out
    assert captured.err == ""


def test_extract_resolves_multiple_external_tax_rates_from_unique_subtotals(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    image_path = tmp_path / "multiple-tax-rates.jpg"
    with Image.new("RGB", (120, 80), "white") as image:
        image.save(image_path)
    monkeypatch.setattr(
        cli,
        "request_receipt_extraction",
        lambda path: _multiple_external_tax_payload(receipt_date="2022年05月14日"),
    )

    exit_code = cli.run(["extract", str(image_path), "--mode", "historical"])

    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert exit_code == 0
    assert output["status"] == "confirmed"
    assert output["validation_issues"] == []
    assert [item["price"] for item in output["items"]] == [
        647,
        160,
        5800,
        5800,
        3,
    ]
    assert [item["tax_adjustment"] for item in output["items"]] == [
        47,
        12,
        0,
        0,
        0,
    ]
    assert captured.err == ""


def test_extract_command_prints_failed_as_valid_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    image_path = tmp_path / "receipt.png"
    with Image.new("RGB", (120, 80), "white") as image:
        image.save(image_path)
    monkeypatch.setattr(
        cli,
        "request_receipt_extraction",
        lambda path: '{"store": "missing required fields"}',
    )

    exit_code = cli.run(["extract", str(image_path)])

    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert exit_code == 0
    assert output["receipt"] is None
    assert output["status"] == "failed"
    assert output["validation_issues"][0]["code"] == "structure.invalid"
    assert captured.err == ""


def test_extract_command_routes_non_receipt_without_exposing_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    image_path = tmp_path / "image.png"
    with Image.new("RGB", (120, 80), "white") as image:
        image.save(image_path)
    private_sentinel = "PRIVATE-NON-RECEIPT-CONTENT"
    monkeypatch.setattr(
        cli,
        "request_receipt_extraction",
        lambda path: json.dumps(
            {
                "is_receipt": False,
                "store": private_sentinel,
                "date": "not-a-date",
                "time": "",
                "items": [{"name": private_sentinel, "qty": 1, "price": 100}],
                "subtotal": 100,
                "tax": 0,
                "tax_breakdowns": [],
                "total": 100,
                "payment": "unknown",
                "confidence": 0.95,
            }
        ),
    )

    exit_code = cli.run(["extract", str(image_path)])

    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert exit_code == 0
    assert output["receipt"] is None
    assert output["status"] == "failed"
    assert output["validation_issues"] == [
        {
            "code": "receipt.not_receipt",
            "message": "画像は店舗のレシートではありません",
            "field": "is_receipt",
        }
    ]
    assert private_sentinel not in captured.out
    assert captured.err == ""


def test_extract_command_rejects_missing_image(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing_path = tmp_path / "missing.jpg"

    exit_code = cli.run(["extract", str(missing_path)])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert "画像ファイルが見つかりません" in captured.err


@pytest.mark.parametrize(
    ("mode", "expected_status", "expected_issue"),
    [
        ("regular", "review", "date.too_old"),
        ("historical", "confirmed", None),
    ],
)
def test_extract_mode_normalizes_old_date_without_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mode: str,
    expected_status: str,
    expected_issue: str | None,
) -> None:
    image_path = tmp_path / "old-receipt.png"
    with Image.new("RGB", (120, 80), "white") as image:
        image.save(image_path)
    data_root = tmp_path / "data"
    config_path = _write_config(tmp_path, data_root)
    monkeypatch.setenv(CONFIG_ENV_VAR, str(config_path))
    monkeypatch.setattr(
        cli,
        "request_receipt_extraction",
        lambda path: _ollama_payload(receipt_date="2020/1/1"),
    )

    exit_code = cli.run(["extract", str(image_path), "--mode", mode])

    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert exit_code == 0
    assert output["date_raw"] == "2020/1/1"
    assert output["date"] == "2020-01-01"
    assert output["status"] == expected_status
    assert [issue["code"] for issue in output["validation_issues"]] == (
        [expected_issue] if expected_issue is not None else []
    )
    assert not (data_root / "ledger.db").exists()
    assert captured.err == ""


def test_db_init_creates_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    data_root = tmp_path / "data"
    config_path = _write_config(tmp_path, data_root)
    monkeypatch.setenv(CONFIG_ENV_VAR, str(config_path))

    exit_code = cli.run(["db", "init"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert json.loads(captured.out) == {"database": "initialized"}
    assert (data_root / "ledger.db").is_file()
    assert captured.err == ""


def test_process_saves_receipt_and_second_run_as_duplicate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    image_path = tmp_path / "receipt.png"
    with Image.new("RGB", (120, 80), "white") as image:
        image.save(image_path)
    original_bytes = image_path.read_bytes()
    data_root = tmp_path / "data"
    config_path = _write_config(tmp_path, data_root)
    monkeypatch.setenv(CONFIG_ENV_VAR, str(config_path))

    def fake_process_extraction(path: Path, **kwargs: Any) -> str:
        assert path != image_path
        assert kwargs == {
            "base_url": "http://127.0.0.1:11434",
            "model": "qwen3-vl:8b",
            "temperature": 0,
        }
        return _ollama_payload(receipt_date=_today().isoformat())

    monkeypatch.setattr(
        "recebako.pipeline.process.request_receipt_extraction",
        fake_process_extraction,
    )

    first_exit_code = cli.run(["process", str(image_path)])
    first_output = json.loads(capsys.readouterr().out)
    second_exit_code = cli.run(["process", str(image_path)])
    second_captured = capsys.readouterr()
    second_output = json.loads(second_captured.out)

    assert first_exit_code == 0
    assert first_output["status"] == "confirmed"
    assert first_output["duplicate_of_id"] is None
    assert second_exit_code == 0
    assert second_output["status"] == "review"
    assert second_output["duplicate_of_id"] == first_output["receipt_id"]
    assert {issue["code"] for issue in second_output["validation_issues"]} == {
        "duplicate.suspected"
    }
    assert second_captured.err == ""

    with sqlite3.connect(data_root / "ledger.db") as connection:
        receipt_count = connection.execute("SELECT COUNT(*) FROM receipts").fetchone()
        item_count = connection.execute("SELECT COUNT(*) FROM items").fetchone()
        confirmed_count = connection.execute(
            "SELECT COUNT(*) FROM receipts WHERE status = 'confirmed'"
        ).fetchone()
        duplicate_row = connection.execute(
            """
            SELECT duplicate_of_id
            FROM receipts
            WHERE id = ?
            """,
            (second_output["receipt_id"],),
        ).fetchone()
        image_paths = connection.execute(
            "SELECT image_path, file_state FROM receipts ORDER BY id"
        ).fetchall()

    assert receipt_count is not None and receipt_count[0] == 2
    assert item_count is not None and item_count[0] == 2
    assert confirmed_count is not None and confirmed_count[0] == 1
    assert duplicate_row is not None
    assert duplicate_row[0] == first_output["receipt_id"]
    assert image_path.read_bytes() == original_bytes
    assert all(
        not Path(stored_path[0]).is_absolute()
        and ".." not in Path(stored_path[0]).parts
        and (data_root / stored_path[0]).is_file()
        and stored_path[1] == "finalized"
        for stored_path in image_paths
    )
    assert image_paths[0][0].startswith("archive/")
    assert image_paths[1][0].startswith("review/")


def test_process_final_move_failure_stays_pending_and_is_recoverable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    image_path = tmp_path / "receipt.png"
    with Image.new("RGB", (120, 80), "white") as image:
        image.save(image_path)
    original_bytes = image_path.read_bytes()
    data_root = tmp_path / "data"
    config_path = _write_config(tmp_path, data_root)
    monkeypatch.setenv(CONFIG_ENV_VAR, str(config_path))
    monkeypatch.setattr(
        "recebako.pipeline.process.request_receipt_extraction",
        lambda path, **kwargs: _ollama_payload(receipt_date=_today().isoformat()),
    )
    original_move_to_final = cli.move_to_final

    def fail_final_move(*args: Any, **kwargs: Any) -> Path:
        raise RuntimeFileError("forced final move failure")

    monkeypatch.setattr(cli, "move_to_final", fail_final_move)

    exit_code = cli.run(["process", str(image_path)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert image_path.read_bytes() == original_bytes
    with sqlite3.connect(data_root / "ledger.db") as connection:
        pending = connection.execute(
            "SELECT status, image_path, file_state FROM receipts WHERE id = 1"
        ).fetchone()
    assert pending is not None
    assert pending[0] == "confirmed"
    assert pending[1].startswith("processing/")
    assert pending[2] == "pending"
    assert (data_root / pending[1]).is_file()

    monkeypatch.setattr(cli, "move_to_final", original_move_to_final)
    recover_exit_code = cli.run(["runtime", "recover"])

    recover_output = json.loads(capsys.readouterr().out)
    assert recover_exit_code == 0
    assert recover_output["recovered"] == 1
    with sqlite3.connect(data_root / "ledger.db") as connection:
        finalized = connection.execute(
            "SELECT image_path, file_state FROM receipts WHERE id = 1"
        ).fetchone()
    assert finalized is not None
    assert finalized[0].startswith("archive/")
    assert finalized[1] == "finalized"
    assert (data_root / finalized[0]).is_file()


def test_process_historical_mode_saves_old_receipt_as_confirmed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    image_path = tmp_path / "historical.png"
    with Image.new("RGB", (120, 80), "white") as image:
        image.save(image_path)
    data_root = tmp_path / "data"
    config_path = _write_config(tmp_path, data_root)
    monkeypatch.setenv(CONFIG_ENV_VAR, str(config_path))
    monkeypatch.setattr(
        "recebako.pipeline.process.request_receipt_extraction",
        lambda path, **kwargs: _ollama_payload(receipt_date="2020/1/1"),
    )

    exit_code = cli.run(["process", str(image_path), "--mode", "historical"])

    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert exit_code == 0
    assert output["status"] == "confirmed"
    assert output["date_raw"] == "2020/1/1"
    assert output["date"] == "2020-01-01"
    assert output["receipt_id"] > 0
    assert captured.err == ""


def test_process_normalizes_mixed_tax_and_saves_audit_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    image_path = tmp_path / "mixed-tax.png"
    with Image.new("RGB", (120, 80), "white") as image:
        image.save(image_path)
    data_root = tmp_path / "data"
    config_path = _write_config(tmp_path, data_root)
    monkeypatch.setenv(CONFIG_ENV_VAR, str(config_path))
    monkeypatch.setattr(
        "recebako.pipeline.process.request_receipt_extraction",
        lambda path, **kwargs: _mixed_tax_payload(receipt_date=_today().isoformat()),
    )

    exit_code = cli.run(["process", str(image_path)])

    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert exit_code == 0
    assert output["status"] == "confirmed"
    assert output["validation_issues"] == []
    assert captured.err == ""

    with sqlite3.connect(data_root / "ledger.db") as connection:
        item_rows = connection.execute(
            """
            SELECT i.price, d.price_raw, d.tax_rate, d.tax_treatment,
                   d.tax_adjustment
            FROM items AS i
            JOIN item_tax_details AS d ON d.item_id = i.id
            ORDER BY i.id
            """
        ).fetchall()
        breakdown_rows = connection.execute(
            """
            SELECT tax_rate, taxable_amount, tax_amount, tax_treatment
            FROM receipt_tax_breakdowns
            ORDER BY id
            """
        ).fetchall()

    assert item_rows == [
        (151, 140, 8, "excluded", 11),
        (570, 570, 10, "included", 0),
    ]
    assert breakdown_rows == [
        (8, 140, 11, "excluded"),
        (10, 570, 51, "included"),
    ]


def test_runtime_init_command_prints_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    data_root = tmp_path / "data"
    config_path = _write_config(tmp_path, data_root)
    monkeypatch.setenv(CONFIG_ENV_VAR, str(config_path))

    exit_code = cli.run(["runtime", "init"])

    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert exit_code == 0
    assert output == {
        "data_root_initialized": True,
        "database_initialized": True,
        "directories": [
            "inbox",
            "processing",
            "archive",
            "review",
            "failed",
            "reports",
            "logs",
            "tmp",
        ],
    }
    assert captured.err == ""


def test_inbox_run_command_outputs_one_json_document(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    data_root = tmp_path / "data"
    config_path = _write_config(tmp_path, data_root)
    monkeypatch.setenv(CONFIG_ENV_VAR, str(config_path))
    image_path = data_root / "inbox" / "receipt.JPG"
    image_path.parent.mkdir(parents=True)
    with Image.new("RGB", (120, 80), "white") as image:
        image.save(image_path)
    monkeypatch.setattr(
        "recebako.pipeline.process.request_receipt_extraction",
        lambda path, **kwargs: _ollama_payload(receipt_date=_today().isoformat()),
    )

    exit_code = cli.run(["inbox", "run", "--limit", "1"])

    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert exit_code == 0
    assert output["scanned"] == 1
    assert output["processed"] == 1
    assert output["confirmed"] == 1
    assert output["results"][0]["source_name"] == "receipt.JPG"
    assert output["results"][0]["destination"].startswith("archive/")
    serialized = json.dumps(output, ensure_ascii=False)
    for private_field in ("store", "items", "total", "raw_payload"):
        assert private_field not in serialized
    assert captured.err == ""


def test_inbox_run_non_receipt_stdout_contains_only_safe_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    data_root = tmp_path / "data"
    config_path = _write_config(tmp_path, data_root)
    monkeypatch.setenv(CONFIG_ENV_VAR, str(config_path))
    image_path = data_root / "inbox" / "image.jpg"
    image_path.parent.mkdir(parents=True)
    with Image.new("RGB", (120, 80), "white") as image:
        image.save(image_path)
    private_sentinel = "PRIVATE-NON-RECEIPT-CONTENT"
    payload = json.loads(_ollama_payload(receipt_date="not-a-date"))
    payload["is_receipt"] = False
    payload["store"] = private_sentinel
    payload["items"] = [{"name": private_sentinel, "qty": 1, "price": 100}]
    monkeypatch.setattr(
        "recebako.pipeline.process.request_receipt_extraction",
        lambda path, **kwargs: json.dumps(payload),
    )

    exit_code = cli.run(["inbox", "run", "--limit", "1"])

    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert exit_code == 0
    assert output["failed"] == 1
    assert output["confirmed"] == 0
    assert output["review"] == 0
    assert output["results"][0]["status"] == "failed"
    assert output["results"][0]["destination"] == "failed/1_image.jpg"
    assert output["results"][0]["error_code"] is None
    assert private_sentinel not in captured.out
    assert private_sentinel not in captured.err
    assert captured.err == "recebako: warning: 1件の処理に失敗しました\n"


def test_inbox_run_with_no_files_is_successful_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    data_root = tmp_path / "data"
    config_path = _write_config(tmp_path, data_root)
    monkeypatch.setenv(CONFIG_ENV_VAR, str(config_path))

    exit_code = cli.run(["inbox", "run"])

    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert exit_code == 0
    assert output["scanned"] == 0
    assert output["results"] == []
    assert captured.err == ""


def test_runtime_recover_dry_run_prints_json_without_moving(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    data_root = tmp_path / "data"
    config_path = _write_config(tmp_path, data_root)
    monkeypatch.setenv(CONFIG_ENV_VAR, str(config_path))
    paths, _ = initialize_runtime(data_root)
    source = paths.inbox / "receipt.jpg"
    source.write_bytes(b"synthetic")
    work_path = claim_inbox_file(
        scan_inbox(paths).selected[0],
        paths,
        token="a" * 32,
    )

    exit_code = cli.run(["runtime", "recover", "--dry-run"])

    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert exit_code == 0
    assert output["dry_run"] is True
    assert output["recovered"] == 1
    assert output["results"][0]["action"] == "return_to_inbox"
    assert work_path.is_file()
    assert captured.err == ""


def test_inbox_run_rejects_second_process_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    data_root = tmp_path / "data"
    config_path = _write_config(tmp_path, data_root)
    monkeypatch.setenv(CONFIG_ENV_VAR, str(config_path))
    paths, _ = initialize_runtime(data_root)

    with InboxLock(paths):
        exit_code = cli.run(["inbox", "run"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert "すでに実行中" in captured.err


def test_inbox_run_failure_keeps_stdout_json_and_stderr_diagnostic_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    data_root = tmp_path / "data"
    config_path = _write_config(tmp_path, data_root)
    monkeypatch.setenv(CONFIG_ENV_VAR, str(config_path))
    image_path = data_root / "inbox" / "receipt.jpg"
    image_path.parent.mkdir(parents=True)
    with Image.new("RGB", (120, 80), "white") as image:
        image.save(image_path)
    monkeypatch.setattr(
        "recebako.pipeline.process.request_receipt_extraction",
        lambda path, **kwargs: (_ for _ in ()).throw(
            cli.OllamaError("秘密の商品 9999円 raw-response")
        ),
    )

    exit_code = cli.run(["inbox", "run"])

    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert exit_code == 0
    assert output["failed"] == 1
    assert output["results"][0]["status"] == "failed"
    assert "1件の処理に失敗" in captured.err
    for forbidden in ("秘密の商品", "9999", "raw-response"):
        assert forbidden not in captured.out
        assert forbidden not in captured.err


def test_evaluate_run_uses_default_models_and_isolated_ledgers_without_touching_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source_root = tmp_path / "evaluation-source"
    source_root.mkdir()
    source_image = source_root / "case-0001.jpeg"
    with Image.new("RGB", (120, 80), "white") as image:
        image.save(source_image)
    source_snapshot = (
        source_image.read_bytes(),
        source_image.stat().st_ino,
        source_image.stat().st_mtime_ns,
    )

    data_root = tmp_path / "normal-data"
    data_root.mkdir()
    sentinel = data_root / "keep.bin"
    sentinel.write_bytes(b"normal-data-must-remain-unchanged")
    sentinel_snapshot = (sentinel.read_bytes(), sentinel.stat().st_mtime_ns)
    config_path = _write_config(tmp_path, data_root)
    monkeypatch.setenv(CONFIG_ENV_VAR, str(config_path))

    raw_marker = "synthetic-sensitive-raw-marker"
    private_store = f"synthetic-sensitive-store-{raw_marker}"
    private_item = "synthetic-sensitive-item"
    private_total = 765_431
    calls: list[tuple[str, str, int, bytes]] = []

    def fake_request_receipt_extraction(path: Path, **kwargs: Any) -> str:
        calls.append(
            (
                kwargs["base_url"],
                kwargs["model"],
                kwargs["temperature"],
                path.read_bytes(),
            )
        )
        return json.dumps(
            {
                "is_receipt": True,
                "store": private_store,
                "date": "2026-07-25",
                "time": "12:34",
                "items": [{"name": private_item, "qty": 1, "price": private_total}],
                "subtotal": private_total,
                "tax": 0,
                "total": private_total,
                "payment": "cash",
                "confidence": 0.99,
            }
        )

    monkeypatch.setattr(
        "recebako.pipeline.process.request_receipt_extraction",
        fake_request_receipt_extraction,
    )
    output_root = tmp_path / "evaluation-output"

    exit_code = cli.run(
        [
            "evaluate",
            "run",
            str(source_root),
            "--output-root",
            str(output_root),
            "--reference-date",
            "2026-07-26",
        ]
    )

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert exit_code == 0
    assert captured.err == ""
    assert [
        (base_url, model, temperature) for base_url, model, temperature, _ in calls
    ] == [
        ("http://127.0.0.1:11434", "qwen3-vl:8b", 0),
        ("http://127.0.0.1:11434", "qwen3.5:9b", 0),
    ]
    assert calls[0][3] == calls[1][3]
    assert [model["model_name"] for model in report["models"]] == [
        "qwen3-vl:8b",
        "qwen3.5:9b",
    ]
    assert [
        [case["case_id"] for case in model["cases"]] for model in report["models"]
    ] == [["case-0001"], ["case-0001"]]
    for model in report["models"]:
        accuracy = model["accuracy"]
        assert accuracy["status"] == "unknown"
        assert accuracy["reason"] == "no_human_verified_ground_truth"
        assert accuracy["verified_case_count"] == 0
        for field_name in (
            "store",
            "date",
            "total",
            "receipt_status",
            "item_name",
            "item_quantity",
            "item_price",
        ):
            assert accuracy[field_name] == {
                "comparable_count": 0,
                "correct_count": 0,
                "accuracy_rate": None,
            }

    run_root = output_root / report["run_id"]
    ledger_paths = [
        run_root / "model-01" / "ledger.db",
        run_root / "model-02" / "ledger.db",
    ]
    assert all(path.is_file() for path in ledger_paths)
    assert len({(path.stat().st_dev, path.stat().st_ino) for path in ledger_paths}) == 2
    for ledger_path in ledger_paths:
        with sqlite3.connect(ledger_path) as connection:
            receipt_rows = connection.execute(
                "SELECT status FROM receipts ORDER BY id"
            ).fetchall()
        assert receipt_rows == [("confirmed",)]

    assert (
        source_image.read_bytes(),
        source_image.stat().st_ino,
        source_image.stat().st_mtime_ns,
    ) == source_snapshot
    assert (sentinel.read_bytes(), sentinel.stat().st_mtime_ns) == sentinel_snapshot
    assert not (data_root / "ledger.db").exists()
    for forbidden in (
        private_store,
        private_item,
        str(private_total),
        raw_marker,
        str(source_root),
        str(output_root),
        str(data_root),
        ".jpeg",
    ):
        assert forbidden not in captured.out


def test_evaluate_run_keeps_mixed_model_failures_in_safe_json_and_isolated_dbs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source_root = tmp_path / "evaluation-source"
    source_root.mkdir()
    for case_id, color in (("case-0001", "white"), ("case-0002", "gray")):
        with Image.new("RGB", (120, 80), color) as image:
            image.save(source_root / f"{case_id}.png")

    data_root = tmp_path / "normal-data"
    config_path = _write_config(tmp_path, data_root)
    monkeypatch.setenv(CONFIG_ENV_VAR, str(config_path))
    private_sentinel = "synthetic-sensitive-failure-998877"
    calls_by_model: dict[str, int] = {}

    def fake_request_receipt_extraction(path: Path, **kwargs: Any) -> str:
        model = str(kwargs["model"])
        calls_by_model[model] = calls_by_model.get(model, 0) + 1
        if calls_by_model[model] <= 3:
            raise OllamaTimeoutError(private_sentinel)
        return _ollama_payload(receipt_date="2026-07-25")

    monkeypatch.setattr(
        "recebako.pipeline.process.request_receipt_extraction",
        fake_request_receipt_extraction,
    )
    output_root = tmp_path / "evaluation-output"

    exit_code = cli.run(
        [
            "evaluate",
            "run",
            str(source_root),
            "--output-root",
            str(output_root),
            "--reference-date",
            "2026-07-26",
        ]
    )

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert exit_code == 0
    assert captured.err == ""
    assert calls_by_model == {"qwen3-vl:8b": 4, "qwen3.5:9b": 4}
    for model in report["models"]:
        assert [case["processing_success"] for case in model["cases"]] == [
            False,
            True,
        ]
        assert [case["status"] for case in model["cases"]] == [
            "failed",
            "confirmed",
        ]
        assert model["summary"]["processing_success_count"] == 1
        assert model["summary"]["processing_success_rate"] == 0.5
        assert model["summary"]["confirmed_rate"] == 0.5
        assert model["summary"]["failed_rate"] == 0.5
        assert model["summary"]["error_code_counts"] == {"ollama.timeout": 1}

    run_root = output_root / report["run_id"]
    for model_index in (1, 2):
        ledger_path = run_root / f"model-{model_index:02d}" / "ledger.db"
        with sqlite3.connect(ledger_path) as connection:
            stored_statuses = connection.execute(
                "SELECT status FROM receipts ORDER BY id"
            ).fetchall()
        assert stored_statuses == [("confirmed",)]
    for forbidden in (
        private_sentinel,
        str(source_root),
        str(output_root),
        "998877",
    ):
        assert forbidden not in captured.out


@pytest.mark.parametrize("dataset_kind", ["missing", "non_anonymous"])
def test_evaluate_run_rejects_invalid_dataset_with_fixed_private_safe_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    dataset_kind: str,
) -> None:
    data_root = tmp_path / "normal-data"
    config_path = _write_config(tmp_path, data_root)
    monkeypatch.setenv(CONFIG_ENV_VAR, str(config_path))
    private_name = "synthetic-sensitive-shop-998877.jpeg"
    source_root = tmp_path / "synthetic-sensitive-source"
    if dataset_kind == "non_anonymous":
        source_root.mkdir()
        with Image.new("RGB", (120, 80), "white") as image:
            image.save(source_root / private_name)

    exit_code = cli.run(
        [
            "evaluate",
            "run",
            str(source_root),
            "--output-root",
            str(tmp_path / "evaluation-output"),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert captured.err == "recebako: error: 評価を安全に実行できませんでした\n"
    for forbidden in (
        str(source_root),
        source_root.name,
        private_name,
        "synthetic-sensitive-shop",
        "998877",
    ):
        assert forbidden not in captured.err


def test_evaluate_run_reports_only_aggregate_accuracy_from_human_truth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source_root = tmp_path / "evaluation-source"
    source_root.mkdir()
    with Image.new("RGB", (120, 80), "white") as image:
        image.save(source_root / "case-0001.png")

    data_root = tmp_path / "normal-data"
    config_path = _write_config(tmp_path, data_root)
    monkeypatch.setenv(CONFIG_ENV_VAR, str(config_path))
    private_sentinel = "synthetic-private-sidecar-sentinel"
    private_store = f"{private_sentinel}-store"
    private_item = f"{private_sentinel}-item"
    private_total = 864_209

    def fake_request_receipt_extraction(path: Path, **kwargs: Any) -> str:
        return json.dumps(
            {
                "is_receipt": True,
                "store": private_store,
                "date": "2026-07-25",
                "time": "09:15",
                "items": [{"name": private_item, "qty": 2, "price": private_total}],
                "subtotal": private_total,
                "tax": 0,
                "total": private_total,
                "payment": "cash",
                "confidence": 0.99,
            }
        )

    monkeypatch.setattr(
        "recebako.pipeline.process.request_receipt_extraction",
        fake_request_receipt_extraction,
    )
    ground_truth_path = tmp_path / "human-ground-truth.csv"
    ground_truth_path.write_text(
        "case_id,human_verified,expected_store,expected_date,expected_total,"
        "expected_status,item_index,expected_item_name,expected_item_qty,"
        "expected_item_price\n"
        f"case-0001,true,{private_store},2026-07-25,{private_total},confirmed,"
        f"0,{private_item},2,{private_total}\n",
        encoding="utf-8",
    )
    output_root = tmp_path / "evaluation-output"

    exit_code = cli.run(
        [
            "evaluate",
            "run",
            str(source_root),
            "--output-root",
            str(output_root),
            "--ground-truth",
            str(ground_truth_path),
            "--model",
            "qwen3-vl:8b",
            "--reference-date",
            "2026-07-26",
        ]
    )

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert exit_code == 0
    assert captured.err == ""
    assert report["schema_version"] == 1
    assert set(report) == {"schema_version", "run_id", "models"}
    assert len(report["models"]) == 1
    model_report = report["models"][0]
    assert set(model_report) == {"model_name", "cases", "summary", "accuracy"}
    accuracy = model_report["accuracy"]
    assert accuracy["status"] == "measured"
    assert accuracy["reason"] is None
    assert accuracy["verified_case_count"] == 1
    for field_name in (
        "store",
        "date",
        "total",
        "receipt_status",
        "item_name",
        "item_quantity",
        "item_price",
    ):
        assert accuracy[field_name] == {
            "comparable_count": 1,
            "correct_count": 1,
            "accuracy_rate": 1.0,
        }

    run_root = output_root / report["run_id"]
    persisted_report_text = (run_root / "evaluation-report.json").read_text(
        encoding="utf-8"
    )
    persisted_report = json.loads(persisted_report_text)
    assert persisted_report == report
    assert persisted_report["schema_version"] == 1
    assert set(persisted_report["models"][0]) == {
        "model_name",
        "cases",
        "summary",
        "accuracy",
    }

    sidecar_text = (run_root / "quality-baseline-report.json").read_text(
        encoding="utf-8"
    )
    sidecar = json.loads(sidecar_text)
    assert sidecar["schema_version"] == 1
    assert sidecar["run_id"] == report["run_id"]
    assert set(sidecar) == {"schema_version", "run_id", "models"}
    assert len(sidecar["models"]) == 1
    sidecar_model = sidecar["models"][0]
    assert set(sidecar_model) == {
        "provenance",
        "summary",
        "accuracy",
        "quality",
    }
    assert sidecar_model["summary"] == model_report["summary"]
    assert sidecar_model["accuracy"] == accuracy
    provenance = sidecar_model["provenance"]
    assert provenance["metric_version"] == "quality-v1"
    assert provenance["model_name"] == "qwen3-vl:8b"
    assert len(provenance["prompt_sha256"]) == 64
    assert len(provenance["extraction_schema_sha256"]) == 64

    quality = sidecar_model["quality"]
    assert quality["metric_version"] == "quality-v1"
    assert quality["target_case_count"] == 1
    assert quality["verified_case_count"] == 1
    assert quality["golden_set_complete"] is False
    assert quality["total_accuracy"] == {
        "denominator_count": 1,
        "numerator_count": 1,
        "rate": 1.0,
    }
    assert quality["store_accuracy"] == {
        "denominator_count": 1,
        "numerator_count": 1,
        "rate": 1.0,
    }
    assert quality["date_accuracy"] == {
        "denominator_count": 1,
        "numerator_count": 1,
        "rate": 1.0,
    }
    assert quality["item_accuracy"] == {
        "denominator_count": 1,
        "numerator_count": 1,
        "rate": 1.0,
    }
    assert quality["false_confirmation_rate"] == {
        "denominator_count": 1,
        "numerator_count": 0,
        "rate": 0.0,
    }
    assert quality["review_rate"] == {
        "denominator_count": 1,
        "numerator_count": 0,
        "rate": 0.0,
    }
    for field_name in (
        "q1_total",
        "q2_store_and_date",
        "q3_items",
        "q4_false_confirmation",
        "q5_review",
    ):
        assert quality[field_name] == {
            "status": "unknown",
            "reason": "incomplete_golden_set",
        }

    for forbidden in (
        private_sentinel,
        private_store,
        private_item,
        str(private_total),
        str(ground_truth_path),
        str(source_root),
        str(output_root),
        str(data_root),
    ):
        assert forbidden not in captured.out
        assert forbidden not in persisted_report_text
        assert forbidden not in sidecar_text
    assert "case-0001" not in sidecar_text
