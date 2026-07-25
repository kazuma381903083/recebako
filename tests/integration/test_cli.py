from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from PIL import Image

from recebako import cli


def test_extract_command_prints_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    image_path = tmp_path / "receipt.jpg"
    with Image.new("RGB", (3000, 1000), "white") as image:
        image.save(image_path)
    temporary_paths: list[Path] = []

    def fake_request_receipt_extraction(path: Path) -> str:
        temporary_paths.append(path)
        assert path != image_path
        with Image.open(path) as image:
            assert image.mode == "RGB"
            assert max(image.size) == 2048
        return json.dumps(
            {
                "store": "テスト商店",
                "date": datetime.now(timezone.utc).astimezone().date().isoformat(),
                "time": "12:34",
                "items": [{"name": "りんご", "qty": 1, "price": 100}],
                "subtotal": 91,
                "tax": 9,
                "total": 100,
                "payment": "cash",
                "confidence": 0.99,
            },
            ensure_ascii=False,
        )

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
    assert output["status"] == "confirmed"
    assert output["validation_issues"] == []
    assert len(output["phash"]) == 16
    assert captured.err == ""
    assert len(temporary_paths) == 1
    assert not temporary_paths[0].exists()


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
