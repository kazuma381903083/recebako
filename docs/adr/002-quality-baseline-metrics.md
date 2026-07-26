# ADR 002: 品質baseline指標と目標判定

## Status

Accepted

## Context

評価ハーネスは、標準出力と`RUN_ID/evaluation-report.json`にschema version 1の
評価reportを返す。この契約にはcase単位の安全な処理結果と、人間が確認した
正解との従来のフィールド別`accuracy`が含まれており、既存consumerとの
後方互換性を維持する必要がある。

一方、仕様のNFR-Q1〜Q5を継続的に判定するには、次の点を固定したversionedな
集計指標が必要である。

- 不完全な評価集合を30件のgolden setと同等に扱わないこと
- 店名の表記差を決定論的かつ保守的に正規化すること
- 品目の挿入、欠落、重複、順序を考慮して一致率を計算すること
- `confirmed`率と、誤った`confirmed`の割合を区別すること
- モデルだけでなくprompt、抽出schema、指標定義も追跡できること
- privateなexpected値とactual値を新しい成果物へ含めないこと

評価にはGit管理外のprivateデータを使用する。正解をAI出力から生成、補完、
変更したり、比較対象の値をreportへ露出したりしてはならない。

## Decision

### Versioning and compatibility

- 標準出力と`RUN_ID/evaluation-report.json`はschema version 1のまま変更しない。
- NFR-Q1〜Q5とprovenanceは、aggregate-onlyの独立sidecar
  `RUN_ID/quality-baseline-report.json`へ記録する。
- sidecarのreport schema versionは`1`、指標定義のmetric versionは
  `quality-v1`とする。この2つは別々にversion管理する。
- 既存の`accuracy` blockはフィールド、計算方法、意味を変更しない。従来値と
  `quality-v1`の値を同じ指標として扱わない。
- sidecarにはcase単位のentryを設けず、modelごとの集計値だけを記録する。
- sidecarは`schema_version`、`run_id`、modelごとの`provenance`、既存の
  aggregate `summary`、既存のaggregate `accuracy`、新規`quality`で構成する。
- sidecarのschemaを変更する場合はreport schema versionを、指標の分母、
  比較方法、または閾値を変更する場合はmetric versionを上げる。

### Common comparison rules

正解との比較には`human_verified=true`のcaseだけを使用する。
`human_verified=false`のcaseと正解がないcaseはQ1〜Q4の分母へ含めない。
処理失敗やschema失敗によりactual値が得られないverified caseは、比較対象から
除外せず不一致として扱う。

店名はexpectedとactualの双方へ次の順序で比較用正規化を適用する。

1. Unicode NFKC正規化
2. Unicode case folding
3. すべてのUnicode空白文字の除去

この正規化は`quality-v1`での店名比較だけに使い、別名、支店名、法人表記などの
意味的な同一性は推測しない。DBの`name_norm`も使用しない。

日付は正規化後のISO `YYYY-MM-DD`、合計・数量・税込line priceは整数値を
完全一致で比較する。品目名には店名用の正規化を適用せず、保存されたraw品目名と
人手入力されたraw品目名を文字列として完全一致で比較する。

### Metrics and thresholds

| 指標 | 定義 | 目標 |
|---|---|---:|
| Q1 合計一致率 | 合計が完全一致したverified case数 / verified case数 | 0.98以上 |
| Q2 店名一致率 | 比較用正規化後の店名が一致したverified case数 / verified case数 | 0.95以上 |
| Q2 日付一致率 | ISO日付が完全一致したverified case数 / verified case数 | 0.95以上 |
| Q3 品目一致率 | 順序保持LCS長の合計 / caseごとの`max(expected件数, actual件数)`の合計 | 0.80以上 |
| Q4 誤確定率 | 合計が不一致のactual `confirmed`数 / verified case内のactual `confirmed`数 | 0.02以下 |
| Q5 review率 | actual `review`数 / 全target case数 | 0.30以下 |

Q2は店名一致率と日付一致率の両方が閾値以上の場合だけ達成とする。欠損、不正、
またはISO値の不一致は日付不一致とする。

Q3では各品目を次のtupleとして比較する。

```text
(raw品目名, qty, 税込line price)
```

tupleの3要素がすべて完全一致する場合だけ同一品目とみなす。expectedとactualの
品目順を保持したlongest common subsequence（LCS）の長さをcaseごとの分子とし、
`max(expected品目数, actual品目数)`をcaseごとの分母とする。runの品目一致率は、
全verified caseの分子の合計を分母の合計で割る。全caseを合計した分母が0の場合、
rateとassessmentは`unknown`とする。

Q4は自動確定の安全性を測る指標であり、`confirmed`率そのものではない。
verified case内にactual `confirmed`が1件もない場合は、誤確定率を0とせず、
rateとassessmentを`unknown`とする。Q4は合計一致だけを判定対象とし、
`expected_status`との一致率ではない。

Q5は正解の有無にかかわらず、当該runの全target caseを分母にする。target caseが
0件の場合は、rateとassessmentを`unknown`とする。

### Golden-set assessment

rateを算出できる場合は、評価集合が不完全でも観測値としてsidecarへ記録できる。
ただし、Q1〜Q5を目標に対して`met`または`not_met`と判定するのは、同一runで
次の条件をすべて満たす場合だけとする。

1. `target_case_count == 30`
2. `verified_case_count == 30`
3. 当該指標の分母が0でない

件数不足、未検証case、正解欠損、または0分母がある指標のassessmentは
`unknown`とする。測定済みrateが存在しても、このassessmentを`met`または
`not_met`へ読み替えない。

### Report provenance

sidecar schema version 1には、少なくとも次のprovenanceを含める。

- 実際に使用したmodel name
- modelへ送信したpromptのSHA-256
- modelへ強制したJSON schemaのSHA-256
- metric version `quality-v1`

promptはmodelへ送信する文字列のUTF-8 bytesをhashする。抽出schemaはmodelへ
送信するPydantic JSON schemaをkey順でsortし、不要な空白を含まないUTF-8 JSONへ
serializeしてhashする。SHA-256はrepository管理下の抽出契約だけを対象とし、
private入力、ground truth、AI応答、評価DBを対象にしない。

### Private-data boundary

- ground truthは人間だけが作成、修正し、AI出力から生成または補完しない。
- private画像、ground truth、評価DBはGit管理外に置く。
- sidecarには集計したcount、rate、assessment、固定閾値、provenanceだけを
  記録する。
- 店名、品目名、金額、画像名、絶対path、raw Ollama response、case ID、
  個別caseのexpected/actual値をsidecar、文書、Issue、PR、commit messageへ
  記録しない。
- prompt/schemaのprovenance hashへprivateデータを混入させない。

## Rationale

独立sidecarにすると既存の標準出力、schema version 1 report、従来accuracyを
変えずに、NFR判定を再現可能にできる。30件すべてを人間確認済みに限定して
assessmentすることで、容易なcaseだけを含む部分集合や正解欠損による見かけ上の
達成を防ぐ。

店名の比較用正規化は、文字幅、大文字小文字、空白だけの差を吸収しつつ、
意味を推測しない。raw品目tupleのLCSは1品目の挿入・欠落で後続すべてが位置ずれ
する問題を避け、同時に余分な品目、欠落、重複、順序変更を分母と順序制約へ
反映する。

Q4の分母をactual `confirmed`に限定することで、自動確定したcaseのうち合計を
誤った割合を直接測れる。0件を誤確定率0%としないため、安全性を確認できて
いないrunが目標達成になることもない。Q5は正解データに依存しない運用品質なので、
全target caseを分母にする。

model nameに加えてprompt、抽出schema、metric versionを残すことで、baseline差が
モデル差、抽出契約差、指標定義差のどれによるものかを追跡できる。

## Alternatives considered

### Extend or replace the existing evaluation report

schema version 1のconsumerとの互換性を壊すか、既存のcase単位reportへ異なる目的の
NFR判定を混在させるため採用しない。

### Assess a partial or partially verified set

rateの探索的利用は認めるが、golden set全体の品質目標を満たした証拠にはならない。
そのためrateとassessmentを分離する。

### Normalize item names

表記ゆれを吸収するとraw抽出品質と業務上の意味的正規化が混在するため採用しない。
品目名正規化の業務仕様は別Issueで扱う。

### Compare items only by array index

1品目の挿入または欠落で後続品目が連鎖的に不一致になるため採用しない。

### Compare items as an unordered set

重複品目とレシート上の順序を失い、余分な品目や欠落の説明力が低下するため
採用しない。

### Use confirmed rate as accuracy

`confirmed`は処理結果の状態であり、人間確認済み正解との一致を示さないため
採用しない。

### Generate or repair ground truth from AI output

評価対象自身の出力を正解へ混入させ、独立したaccuracy測定が成立しなくなるため
採用しない。

## Consequences

- 既存の標準出力、`evaluation-report.json`、schema version 1 consumerは影響を
  受けない。
- NFR consumerは独立した`quality-baseline-report.json`を明示的に読む必要がある。
- 既存`accuracy`と`quality-v1`は計算規則が異なるため、名称とversionを区別して
  比較する必要がある。
- 30件未満または一部未検証のrunではrateが表示されても、Q1〜Q5 assessmentは
  `unknown`になる。
- Q4はactual `confirmed`がないrunで`unknown`となり、review中心のモデルを
  誤って安全性目標達成とは判定しない。
- baseline比較ではmodel、prompt、抽出schema、metric versionが同一かを確認できる。
- このADRはbaseline結果だけを根拠とするmodel既定値の変更を決定しない。
