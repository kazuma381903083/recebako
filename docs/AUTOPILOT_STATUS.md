# Recebako Autopilot Status

更新日: 2026-07-26
作業ブランチ: `issue/42-quality-baseline`

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
- Pull Request [#63](https://github.com/kazuma381903083/recebako/pull/63)はmainへmerge済み
- Issue #42の起点mainはPR #63のmerge commit `8d6f9ee`

## Phase 2 private evaluation

- 状態: 実装・自動検査・private E2E・独立commit・push完了
- 対象Issue: `#58`
- 確定コミット: `2e4d605`
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

## Phase 2 quality baseline

- 状態: `quality-v1`定義、aggregate-only sidecar実装、22/30 verifiedのbaseline
  測定と文書化を実施。Issue #42の30/30 private受入は未完了
- 対象Issue: `#42`
- 評価条件: historical mode、reference date固定、temperature 0、同一入力順、
  model別DB
- 使用件数: 30件
- human verified: 22件。残る8件はaccuracy分母へ含めていない
- model: `qwen3-vl:8b`、`qwen3.5:9b`
- 互換性: stdoutと`evaluation-report.json`のschema version 1を維持し、
  実装後のrunでは`quality-baseline-report.json` schema version 1を独立生成
- 指標: 店名だけをNFKC・case folding・Unicode空白除去で比較し、品目は
  raw名・数量・税込line priceの完全一致tupleを順序保持LCSで評価
- target 30件に対してverified 22件のため、観測rateは記録するがQ1〜Q5の
  assessmentは全modelで`unknown`
- `qwen3-vl:8b`: processing/schema 100.0%、confirmed/review/failed
  33.3%/66.7%/0.0%、平均38.1秒/件
- `qwen3.5:9b`: processing/schema 93.3%/93.3%、confirmed/review/failed
  10.0%/83.3%/6.7%、平均46.3秒/件
- 品質観測値と計算方法: `docs/BASELINE_REPORT.md`
- confirmed率をaccuracyとして扱わず、model既定値、prompt、検証閾値は変更して
  いない
- private境界: 評価source不変、model別DB分離、通常利用DB非変更、Git記録は
  aggregate値だけ
- 22/30 private E2Eでsidecar型、legacy集計一致、provenance、0600権限、
  private allowlist境界を確認済み

## Remaining

1. 残る8件のground truthを人間が確認し、30/30 verifiedでQ1〜Q5を再判定して
   Issue #42のprivate受入を完了する
2. 品質改善は完全なbaseline取得後にIssue #60の範囲で判断し、Issue #42では
   model既定値を変更しない
3. Phase 3以降はIssueの依存関係・優先順位に従うwaiting／blocked状態を維持する
