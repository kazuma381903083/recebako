from __future__ import annotations

import json
from pathlib import Path

import pytest

from recebako import cli
from recebako.domain import ReceiptExtraction


def test_extract_command_prints_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    image_path = tmp_path / "receipt.jpg"
    image_path.write_bytes(b"synthetic-image")
    expected = ReceiptExtraction(
        store="テスト商店",
        date="2026-07-25",
        time="12:34",
        items=[{"name": "りんご", "qty": 1, "price": 100}],
        subtotal=91,
        tax=9,
        total=100,
        payment="cash",
        confidence=0.99,
    )

    def fake_extract_receipt(path: Path) -> ReceiptExtraction:
        assert path == image_path
        return expected

    monkeypatch.setattr(cli, "extract_receipt", fake_extract_receipt)

    exit_code = cli.run(["extract", str(image_path)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert json.loads(captured.out) == expected.model_dump(mode="json")
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
