# 品質baseline report

更新日: 2026-07-26
対象: Issue #42
metric version: `quality-v1`

## 目的とデータ境界

Git管理外の匿名評価画像と、人間が入力したground truthを使い、同一条件で
ローカルモデルを比較した品質改善前baselineである。本文には集計値だけを記録し、
privateな入力、正解値、抽出値、識別子、path、生応答を含めない。

評価sourceは読み取り専用として扱い、modelごとに評価runtimeとSQLiteを分離した。
通常利用の台帳DBは使用も変更もしていない。ground truthの作成・修正にAI出力は
使用していない。

## 評価条件

| 条件 | 値 |
|---|---|
| 実施日・reference date | 2026-07-26 |
| ingest mode | `historical` |
| target件数 | 30 |
| `human_verified=true` | 22 |
| accuracy対象外 | 8 |
| temperature | 0 |
| model順 | `qwen3-vl:8b`、`qwen3.5:9b` |
| DB隔離 | modelごとに専用SQLite |
| prompt・抽出schema | 両modelで同一 |

両modelは同じ入力を同じ順序で処理した。実装後runの
`quality-baseline-report.json`へ、承認された`quality-v1`のaggregateだけと、
model名、prompt、抽出schemaのprovenanceを記録した。既存reportとの集計一致、
sidecarの型、private allowlist境界、保存権限を再検証している。
対応形式の匿名画像30件をprivateなstaging copyから評価し、source自体は変更して
いない。

## Accuracy計算方法

Accuracyは`human_verified=true`の22件だけを対象とする。正解なし、または
`human_verified=false`の8件は分母へ含めない。verified caseの処理失敗・schema失敗は
actual欠損による不一致として扱い、分母から除外しない。`confirmed`率は処理状態の
割合でありaccuracyではない。

`quality-v1`の比較規則は次のとおり。

- Q1: 合計の整数完全一致数をverified件数で割る。
- Q2店名: expectedとactualをUnicode NFKC、case folding、Unicode空白除去の順で
  正規化し、完全一致した件数をverified件数で割る。意味的な別名統合はしない。
- Q2日付: 正規化後のISO日付が完全一致した件数をverified件数で割る。
- Q3: `(raw品目名, qty, 税込line price)`がすべて完全一致するtupleを、
  順序保持LCSで対応付ける。分子はLCS長の合計、分母はcaseごとの
  `max(expected品目数, actual品目数)`の合計とする。
- Q4: verified case内でactualが`confirmed`の件数を分母とし、そのうち合計が
  不一致の件数を分子とする。分母0は0%ではなく`unknown`とする。
- Q5: 全target caseを分母とする`review`率であり、accuracyではない。

目標assessmentは、同一runがtarget 30件かつhuman verified 30件の場合だけ行う。
今回は30件中22件だけがverifiedのため、観測rateの値にかかわらずQ1〜Q5はすべて
`unknown`である。

## 処理結果

| 指標 | `qwen3-vl:8b` | `qwen3.5:9b` |
|---|---:|---:|
| processing成功 | 30/30（100.0%） | 28/30（93.3%） |
| schema成功 | 30/30（100.0%） | 28/30（93.3%） |
| confirmed | 10/30（33.3%） | 3/30（10.0%） |
| review | 20/30（66.7%） | 25/30（83.3%） |
| failed | 0/30（0.0%） | 2/30（6.7%） |
| 税正規化 採用 / 拒否 | 3 / 18 | 0 / 22 |
| 日付 正規化 / 拒否 / 無変更 / 未評価 | 26 / 2 / 2 / 0 | 20 / 8 / 0 / 2 |
| 重複 none / 未評価 | 30 / 0 | 28 / 2 |
| 処理時間 最短 / 平均 / 最長 | 15.3 / 38.1 / 76.9秒/件 | 12.5 / 46.3 / 180.0秒/件 |

税正規化の採用数・拒否数は処理結果の集計であり、税情報のaccuracyではない。
重複結果には、このrunで`identity`または`phash`と判定されたものはなかった。
平均処理時間は成功・失敗を含む全30件のelapsed timeから算出している。

## `quality-v1` human-verified結果

| 指標 | `qwen3-vl:8b` | `qwen3.5:9b` | assessment |
|---|---:|---:|---|
| Q1 合計一致率 | 18/22（81.8%） | 16/22（72.7%） | 両方`unknown` |
| Q2 店名一致率 | 5/22（22.7%） | 2/22（9.1%） | 両方`unknown` |
| Q2 日付一致率 | 20/22（90.9%） | 15/22（68.2%） | 両方`unknown` |
| Q3 品目tuple一致率 | 14/126（11.1%） | 8/120（6.7%） | 両方`unknown` |
| Q4 観測誤確定率 | 0/8（0.0%） | 0/3（0.0%） | 両方`unknown` |
| Q5 review率 | 20/30（66.7%） | 25/30（83.3%） | 両方`unknown` |

Q4の0%は、今回観測したactual `confirmed`のうち合計不一致がなかったという意味に
限られる。golden setが未完成なので、安全性目標を達成したとは判定しない。

## 従来schema version 1 accuracy（参考）

既存`evaluation-report.json`のフィールド別accuracyは後方互換のため維持する。
品目はindex位置ごとに各フィールドを別々に比較するため、tupleをLCSで比較する
`quality-v1` Q3とは定義が異なる。

| フィールド | `qwen3-vl:8b` | `qwen3.5:9b` |
|---|---:|---:|
| 店名 | 5/22（22.7%） | 2/22（9.1%） |
| 日付 | 20/22（90.9%） | 15/22（68.2%） |
| 合計 | 18/22（81.8%） | 16/22（72.7%） |
| receipt status | 10/22（45.5%） | 7/22（31.8%） |
| 品目名 | 21/126（16.7%） | 16/120（13.3%） |
| 品目数量 | 89/126（70.6%） | 73/120（60.8%） |
| 品目価格 | 55/126（43.7%） | 32/120（26.7%） |

## 発見した弱点

- 22件だけの人手確認ではgolden set全体の目標判定ができない。
- 両modelとも店名とraw品目tupleの一致が低く、合計もQ1目標の観測値には届いて
  いない。
- review率は両modelともQ5上限を上回る観測値で、特に`qwen3.5:9b`で高い。
- `qwen3.5:9b`ではprocessing・schema失敗があり、平均処理時間も長い。
- 税正規化は拒否が採用を大きく上回る。拒否理由はprivate値を公開せず、
  aggregateなreason code単位で追加分析する必要がある。
- `qwen3-vl:8b`は今回の主要な観測値で相対的に高いが、部分verified baselineだけで
  model既定値の変更または品質目標達成を判断できない。

## 次の改善候補

1. 22 verified caseの現行baselineを固定し、変更前後を同じmetric version、
   評価集合、実行条件で比較してprovenanceの差を記録する。
2. 店名、日付、合計、raw品目tuple、税正規化拒否のaggregate reasonを分けて
   調査し、Issue #60でpromptまたは検証規則の最小変更候補を評価する。
3. 変更候補ごとに同じmodel順、temperature、mode、reference date、分離DBで
   回帰評価し、confirmed率だけで優劣を決めない。
4. 将来verified caseを追加する場合も人間だけがground truthを入力し、現行baselineと
   母集団が異なることをverified件数、評価集合、実行条件とともに文書へ明示する。

Issue #42ではmodel、prompt、検証閾値、model既定値を変更しない。
今回のprivate E2Eは30 target（22 human verified）でsidecar生成まで確認し、
この範囲をIssue #42のbaselineとしてユーザーが受け入れた。golden setは未完成のため
Q1〜Q5のassessmentは`unknown`を維持し、目標達成とは表現しない。
