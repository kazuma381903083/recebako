from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from recebako import cli
from recebako.config import CONFIG_ENV_VAR
from recebako.runtime import (
    InboxLock,
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
    assert len(output["phash"]) == 16
    assert captured.err == ""
    assert len(temporary_paths) == 1
    assert not temporary_paths[0].exists()


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
            "SELECT image_path FROM receipts ORDER BY id"
        ).fetchall()

    assert receipt_count is not None and receipt_count[0] == 2
    assert item_count is not None and item_count[0] == 2
    assert confirmed_count is not None and confirmed_count[0] == 1
    assert duplicate_row is not None
    assert duplicate_row[0] == first_output["receipt_id"]
    assert image_path.is_file()
    assert all(
        not Path(stored_path[0]).is_absolute()
        and ".." not in Path(stored_path[0]).parts
        and stored_path[0].startswith("unmanaged/")
        for stored_path in image_paths
    )


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
