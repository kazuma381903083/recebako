# Recebako 開発指示

## 適用範囲とsource of truth

- 個別のGitHub Issueを1回の作業範囲とし、現在指定されたIssueだけを実装する。
- 機能要求のsource of truthは
  `docs/spec/recebako-spec-v1.1.html`とする。
- レポートの外観は`docs/spec/monthly-report-sample.html`を参照する。ただし、
  機能要求やセキュリティ要件より優先しない。
- 設計判断のsource of truthは`docs/adr/`とする。
- 既存テストは後方互換条件である。削除、無効化、期待値の安易な緩和で変更を
  通してはならない。
- 仕様、ADR、既存テストが矛盾する場合は実装を止め、矛盾箇所と選択肢を報告する。
- 仕様にない要求を推測で追加しない。

## 作業単位とGit

- 1つのIssueは、1つの明確な目的と原則1つの主要コンポーネントに限定する。
- 各Issueは自動テスト可能で、既存機能を壊さずロールバックできる範囲にする。
- 各Issueを独立したコミットにし、無関係な変更を混ぜない。
- `main`へ直接commitまたはpushしない。作業用ブランチからPull Requestを作る。
- 自動マージ、force push、公開済み履歴のrebaseや書き換えを行わない。
- `--no-verify`を使わない。
- 全検査が成功した変更だけをcommitする。
- mainへのマージ、破壊的変更、履歴書き換えはユーザーの明示承認なしに行わない。

## 標準コマンド

- Install/sync: `uv sync`
- Test: `uv run pytest`
- Lint: `uv run ruff check .`
- Format check: `uv run ruff format --check .`
- Type check: `uv run mypy src`
- Diff check: `git diff --check`

`scripts/check.sh`が存在する場合は、個別コマンドの代わりにそれを使用する。
作業完了前と各コミット前に全検査を実行する。

privateデータ検査では、`scripts/check-private-files.sh`が存在する場合はそれを
使用し、追跡済み、staged、未追跡のすべてを対象にする。

各コミット前に次も確認する。

```bash
git status --short
git diff --cached --check
git diff --cached --name-status
```

## アーキテクチャ規則

- runtimeデータはGitリポジトリの外に置く。
- アプリケーションはinboxへ置かれたファイルを処理し、Syncthingへ直接依存
  しない。
- Ollama通信は`src/recebako/ai/`内に閉じ込める。
- すべてのLLM出力を信頼できない入力として扱う。
- 構造化出力はPydanticと業務検証規則の両方で検証する。
- レシート記載の生の品目名と正規化した品目名を別々に保持する。
- SQLiteへの書き込みはtransactionを使用する。
- 処理はidempotentかつ安全に再試行可能にする。
- DBとファイルの状態が途中で分離しても、安全に回復できる設計を維持する。
- review UIは`127.0.0.1`にだけbindする。

## セキュリティとprivateデータ

- 実レシート、帳簿DB、生成された個人レポート、runtimeログをGitへ含めない。
- privateデータをdocs、Issue、Pull Request、コミットメッセージ、テストfixtureへ
  転記しない。
- レシート内容、店名、品目、金額、LLMの生レスポンスをアプリケーションログへ
  出力しない。
- secrets、token、認証情報をソース、ログ、文書へ記録しない。
- 外部AI、外部OCR、外部Webサービスへレシート画像や抽出データを送信しない。
- runtimeで許可するAI通信はlocalhostのOllamaだけとする。
- 実レシートを使う検査はGit管理外で行い、原本を変更しない。
- symlink、path traversal、root外参照、既存ファイルの上書きを拒否する。
- CLIがJSONをstdoutへ出す契約では、診断や進捗をstdoutへ混入させない。

## 状態判定の安全性

- `review`または`failed`を、根拠なく`confirmed`へ変更しない。
- `confirmed`への遷移前に、構造、業務規則、重複、DBとファイルの整合性を
  再検証する。
- LLMのconfidenceだけを自動確定の根拠にしない。
- 税正規化は関連ADRの採用条件を満たす場合だけ適用し、曖昧な場合は元データを
  保持して`review`にする。

## 依存関係、DB、公開インターフェース

- 新しい本番依存関係を追加する場合は、必要性、代替案、ライセンス、保守と
  セキュリティへの影響をIssueとPull Requestへ記載する。
- DB変更にはmigration、既存データへの影響、rollbackまたは復旧方法を記載する。
- 破壊的なDB変更はユーザーの明示承認なしに行わない。
- 公開CLI、公開API、設定形式、DB互換性を変更する場合は、理由、利用者への影響、
  互換策を記載する。
- 公開済みの互換性を破る必要がある場合は実装を止め、選択肢を報告する。

## テスト規則

- CIテストはOllamaを必要としないようにする。
- unitおよびintegration testではOllama HTTP応答をmockする。
- ローカルモデルを使うテストには`ollama` markerを付ける。
- 実レシートを使うテストには`private` markerを付け、Git管理外に置く。
- bug fixには必ずregression testを追加する。
- 正常系だけでなく、入力不正、通信失敗、ファイル操作失敗、DB rollbackなどの
  失敗系を試験する。
- テストを削除またはskipして成功状態を作らない。

## コミット前の自己レビュー

- 仕様適合とIssue範囲
- データ損失とrollback可能性
- privateデータ、ログ、stdoutへの漏えい
- path traversalとsymlink
- DB transactionとファイル状態の整合性
- unsafeな`confirmed`判定
- 後方互換性と回帰
- 不必要な依存関係と過剰実装

指摘を修正した後は、全検査を最初から再実行する。

## 完了条件

作業は次のすべてを満たした場合だけ完了とする。

1. Issueで要求された範囲だけが実装されている。
2. 正常系と失敗系、およびbug fixのregression testがある。
3. lint、format、type check、test、diff checkが成功している。
4. privateデータ検査が追跡済み、staged、未追跡のすべてで成功している。
5. 無関係なファイルが変更されていない。
6. 変更ファイルと検証結果を最終報告に列挙している。
7. 独立コミットとして安全にrollbackできる。
