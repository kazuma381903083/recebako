# Recebako Autopilot Status

更新日: 2026-07-26
作業ブランチ: `autopilot/recebako-v1`

## Phase 1C

- 状態: 監査・修正・確定済み
- 確定コミット: `e55e1e9`
- 共通検査: 200 tests passed
- private E2E: 匿名case 3件を処理し、`review=3`、`failed=0`
- DBの画像パス: 全caseでfinalized、相対パス、参照先の存在を確認
- recover dry-run: 回復対象・不整合なし
- 履歴上の削除済みレシートは、ユーザーが明示した適用除外として扱う。名称や内容は本台帳へ記録しない

## Git / GitHub

- ソース側の `.git` は書き込み保護されているため、Gitメタデータの更新にはGit管理専用の外部runtime controlを使用する
- Git remoteへのpushは利用可能
- GitHub CLIは未認証だが、既存credentialをメモリ内だけで利用するGitHub API経路は利用可能
- 自走開発基盤、v1.1ギャップ分析、Issue linkは独立commitでpush済み
- G01〜G62をGitHub Issue `#1`〜`#62`として作成済み
- 各Issueへ優先度、依存関係、受入条件、private E2E要否、security境界、waiting／blocked理由を記録済み

## Phase 2 private evaluation

- 状態: 実装・自動検査・private E2E完了、コミット前
- 対象Issue: `#58`
- CLI: 匿名caseだけを受け付け、既定で`qwen3-vl:8b`と`qwen3.5:9b`を同一条件で評価
- 分離: modelごとに専用runtimeとSQLiteを作成し、通常利用の`data.root`を変更しない
- report: case ID、安全なoutcome、集計値、処理時間だけを出力し、画像、原本名、path、店名、品目、金額、生応答を含めない
- human ground truth: Git管理外CSVを任意指定可能。人間確認済みcaseがなければaccuracyは`unknown`
- 自動検査: 296 tests passed
- private E2E: 匿名case 3件を両modelで処理。両方とも`processing=3/3`、`schema=3/3`、`review=3`、`failed=0`
- 平均処理時間: model 1は約47.3秒／件、model 2は約57.0秒／件
- accuracy: 人間確認済み正解を使用していないため`unknown`。confirmed率をaccuracyとして扱わない
- 境界確認: 匿名source copyと通常data領域は不変、model別DBは分離、report禁止項目なし、runtime権限検査成功
- 残余リスク: 同一OS accountで同時実行される敵対processは既にprivate runtimeと同等権限を持つため、評価の隔離境界外。信頼済みlocal sessionで実行する

## Remaining

1. Phase 2差分の最終自己レビューと共通検査を行う
2. Phase 2を独立commitし、作業branchをpushする
3. main向けPull Requestを作成し、未解決リスクと手動確認事項を記録する
4. Phase 3以降はIssueの依存関係・優先順位に従うwaiting／blocked状態を維持し、今回実装しない
