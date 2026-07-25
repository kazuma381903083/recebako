# Recebako 開発ワークフロー

## 目的

この文書は、1つのGitHub Issueを安全に調査、実装、検証し、Pull Requestとして
引き渡すまでの標準手順を定める。機能要求は仕様書、設計判断はADR、後方互換条件は
既存テストをsource of truthとする。

## 1. Issueの確認

着手前にIssueの次の項目を確認する。

- 背景と目的
- 実装範囲と対象外
- 受入条件と必須テスト
- 関連仕様と関連ADR
- 依存Issueとblocked理由
- DB、公開API、公開CLI、設定形式への影響
- 新しい本番依存関係の有無
- private E2Eと手動確認の要否

目的が複数の主要コンポーネントにまたがる場合は、独立して検証、commit、
rollbackできるIssueへ分割する。仕様にない挙動を補って実装範囲を広げない。

## 2. Gitと作業ツリーの事前確認

作業用ブランチであることを確認し、`main`上では実装しない。次を読み取り専用で
確認する。

```bash
git branch --show-current
git remote -v
git status --short
git diff
git diff --cached
git ls-files --others --exclude-standard
```

既存の変更は所有者が不明でも保持する。無関係な変更をreset、checkout、削除、
stageしない。実画像、DB、ログ、生成レポート、秘密情報が追跡済み、staged、
未追跡へ混入していないことを確認する。

## 3. ベースライン検査

`scripts/check.sh`が存在する場合はそれを実行する。存在しない場合は次を実行する。

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
git diff --check
```

`scripts/check-private-files.sh`が存在する場合は、privateデータ検査にも使用する。
開始時点の失敗は、今回のIssueによる失敗と混同しないよう記録する。

## 4. 設計と影響確認

実装前に次を明確にする。

- 受入条件を満たす最小の変更
- 正常系、失敗系、regression test
- transaction境界とDB・ファイル間の回復方法
- idempotencyと安全な再試行
- path traversal、symlink、上書き、競合への対策
- LLM出力を不正入力として拒否する境界
- `review`、`failed`、`confirmed`の遷移条件
- runtimeデータとGit管理データの境界

新しい本番依存関係、DB migration、公開API、公開CLI、設定形式の変更には、
必要性、代替案、互換性、rollback、セキュリティへの影響を記録する。

## 5. 実装

Issueで指定された主要コンポーネントに変更を限定する。既存の安全条件を緩和せず、
LLM通信はAI層、SQLite書き込みはtransaction、runtimeデータはリポジトリ外という
境界を維持する。

実レシート、個人DB、個人レポート、runtimeログをソースツリーへコピーしない。
実データを使う必要がある場合はGit管理外の隔離環境で匿名case IDだけを使用する。

## 6. テスト

変更に最も近いunit testから実行し、次にintegration test、最後に全検査を行う。
bug fixでは修正前に失敗し修正後に成功するregression testを追加する。

Ollama応答はCIではmockする。ローカルモデル試験は`ollama` marker、実レシート試験は
`private` markerを使用し、いずれも通常のCIから分離する。
Git管理外の匿名caseを一括評価する手順と正解CSV契約は
[`PRIVATE_EVALUATION.md`](PRIVATE_EVALUATION.md)に従う。

## 7. 自己レビュー

未コミット差分を次の観点で確認する。

- Issueと仕様への適合
- データ損失、上書き、rollback
- privateデータと秘密情報の漏えい
- path traversalとsymlink
- DB transactionとファイル状態の整合性
- unsafeな`confirmed`判定
- 既存CLI、API、DB、設定との互換性
- 回帰と失敗系の不足
- 不必要な依存関係
- 過剰実装
- ログへのレシート内容混入
- stdoutのJSON契約

問題を修正した場合は、対象テストだけで終了せず全検査を再実行する。

## 8. 完了検査

次をすべて成功させる。

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
git diff --check
```

privateデータ検査は追跡済み、staged、未追跡のすべてを対象にする。

## 9. stageとcommit

今回のIssueに属するファイルだけを明示的にstageする。commit前に次を確認する。

```bash
git status --short
git diff --cached --check
git diff --cached --name-status
git diff --cached
```

検査結果と差分に問題がない場合だけ、Issue単位の独立コミットを作る。
`--no-verify`、force push、公開済み履歴の書き換えを行わない。

## 10. PushとPull Request

作業ブランチをremoteへpushし、`main`向けPull Requestを作る。Pull Requestには
次を記載する。

- 関連Issue
- 変更概要と対象外
- 関連仕様とADR
- DB、API、CLI、依存関係への影響
- 検証結果
- privateデータ検査結果
- 残るリスクと手動確認
- 依存Issueとblocked事項

自動マージせず、ユーザーの承認なしに`main`へマージしない。

## 停止条件

次の場合は安全に進められる範囲の調査を終えたうえで停止し、選択肢、推奨案、
影響範囲を報告する。

- 仕様、ADR、既存テストが矛盾する
- 実データ原本の変更が必要
- privateデータがGit履歴へ入っている
- 破壊的なDB変更や公開互換性の破壊が必要
- 新しい本番依存関係が必要
- 外部AI、外部OCR、外部クラウド、有料サービスが必要
- `main`へのマージ、履歴書き換え、force pushが必要
- 複数案で安全性、データ互換性、利用体験が大きく異なる
- 正解データなしでは安全な品質判断ができない
- 全検査を成功させられず、安全な範囲で原因を解決できない
