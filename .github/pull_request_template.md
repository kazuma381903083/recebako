## 関連Issue

- Closes #

## 変更概要

- <!-- 記載する -->

## 実装範囲

- <!-- 記載する -->

## 対象外

- <!-- 記載する -->

## Source of truth

- 関連仕様:
- 関連ADR:
- 後方互換条件となる既存テスト:

## 影響

### DB

- [ ] 変更なし
- [ ] Migrationあり

既存データへの影響とrollback／復旧方法:

- <!-- 記載する -->

### 公開API・CLI・設定

- [ ] 変更なし
- [ ] 互換性を保つ変更あり
- [ ] 破壊的変更あり。明示承認を得ている

理由と互換策:

- <!-- 記載する -->

### 本番依存関係

- [ ] 追加なし
- [ ] 追加あり

必要性、代替案、ライセンス、保守・セキュリティ影響:

- <!-- 記載する -->

## 検証結果

- [ ] `uv run ruff check .`
- [ ] `uv run ruff format --check .`
- [ ] `uv run mypy src`
- [ ] `uv run pytest`
- [ ] `git diff --check`
- [ ] `git diff --cached --check`
- [ ] privateデータ検査（追跡済み、staged、未追跡）

追加の自動テスト／private E2E／手動確認:

- <!-- 記載する -->

## セキュリティ・安全性レビュー

- [ ] 実レシート、DB、個人レポート、runtimeログを含まない
- [ ] secrets、token、認証情報を含まない
- [ ] 外部AI・外部OCRへの通信を追加していない
- [ ] レシート内容やLLM生レスポンスをログへ追加していない
- [ ] path traversal、symlink、既存ファイル上書きを確認した
- [ ] DB書き込みのtransactionとファイル状態の整合性を確認した
- [ ] `review`／`failed`をunsafeに`confirmed`へ変更しない
- [ ] stdoutのJSON契約へ診断出力を混入させていない

## 自己レビュー

- [ ] Issueで指定された範囲だけを変更した
- [ ] 正常系と失敗系をテストした
- [ ] bug fixにはregression testを追加した
- [ ] 不必要な依存関係や過剰実装がない
- [ ] 1 Issueに対応する独立コミットとしてrollbackできる
- [ ] `main`へ直接commit／pushしていない
- [ ] force push、履歴書き換え、`--no-verify`を使っていない

## 依存関係とBlocked事項

- Depends on:
- Blocks:
- Blocked:

## 残るリスクと手動確認事項

- <!-- 記載する -->

## マージ方針

- [ ] 自動マージしない
- [ ] ユーザーの明示承認なしに`main`へマージしない
