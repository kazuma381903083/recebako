# Recebako Autopilot Status

更新日: 2026-07-26
作業ブランチ: `issue/7-item-name-normalization`

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
- Pull Request [#64](https://github.com/kazuma381903083/recebako/pull/64)はmainへmerge済み
- Issue #42はユーザーが22/30 verifiedの現行baselineを受け入れたためclose済み
- Pull Request [#65](https://github.com/kazuma381903083/recebako/pull/65)、
  [#66](https://github.com/kazuma381903083/recebako/pull/66)、
  [#67](https://github.com/kazuma381903083/recebako/pull/67)、
  [#68](https://github.com/kazuma381903083/recebako/pull/68)は依存順にmainへmerge済み
- Issue #7の作業ブランチは最新mainのmerge commit `c9fcd39`へfast-forward済み。
  Issue #7だけを独立commitにし、自動mergeしない
- Issue #11は仕様内のserver要否とloopback通信の位置付けが明確になるまでblocked。
  試行した実装は保持・commitしておらず、Issue #7とは混在させない

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
  測定と文書化を完了。Pull Request #64をmergeし、Issue #42をclose済み
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
- 30 target（22 human verified）のprivate E2Eでsidecar型、legacy集計一致、
  provenance、0600権限、private allowlist境界を確認済み
- 残る8件は現行baselineの完了条件に含めない。追加入力が必要になってもAI出力から
  ground truthを生成・変更しない

## Phase 2 non-receipt safety

- 状態: Issue #3の実装、回帰test、仕様更新、全検査を作業ブランチで完了
- 対象Issue: `#3`
- schema: top-level必須のstrict boolean `is_receipt`をOllama formatへ追加。欠落、
  文字列、数値、nullはcoerceせず`structure.invalid`でfailed
- 判定: `is_receipt=false`はschema validのまま`receipt.not_receipt`で即failed。
  日付、税、重複は評価せず、normalized extractionを後段へ渡さない
- 状態遷移: 既存transactionでfailed rowをpending保存し、画像を`failed/`へ移動して
  finalized。中断後は再抽出せず同じrowを回復する
- 互換性: 正常レシートのconfirmed/review、既存CLI成功JSON、評価report schemaを
  維持。raw extraction schemaとpromptのhashは意図どおり更新
- private境界: private画像を使用せず、合成画像とmock Ollama応答だけで検証。
  固定issue code以外の入力内容をresult、audit、stdoutへ出さない
- DB migration、新規依存、外部AI、モデル既定値、confidence閾値の変更なし
- 自動検査: private-file scan、ruff、format、mypy、358 tests、diff checks成功

## Phase 2 extraction variant retry

- 状態: Issue #4の実装、仕様更新、回帰test、全検査を作業ブランチで完了
- 対象Issue: `#4`
- 試行順: standard、時計回り90度、standardの2倍拡大（長辺2048px上限）。
  最大3回は初回を含む総試行数
- 再試行: 不正JSON、schema-invalid、Ollama timeoutだけを次variantへ進める。
  schema-validな非レシートとreviewは即停止し、接続、HTTP/API応答、画像、DB、
  ファイル例外は再試行しない
- 設定: 全試行で同じlocalhost endpoint、model、temperatureを使用し、model切替や
  動的推測を行わない
- 後段処理: 採用payloadだけを税正規化、重複判定、DB transactionへ一度渡す。
  3回schema-invalidの場合は最終payloadだけをfailed 1行として保存する
- 画像: 元画像を変更せず、既存互換pHashを全variantで共有。runtime処理では`tmp`
  配下、単体extractではprocess所有のOS一時領域に遅延生成し、成功、失敗、例外の
  すべてで削除する
- 回復: 途中成功後のDB rollback、最終ファイル移動失敗、次回起動時の再抽出なし
  1行回復を自動testで確認
- private境界: private画像を使用せず、合成画像とmock応答だけで検証。破棄した応答、
  variant path、試行履歴をresult、audit、stdout、DBへ追加しない
- DB migration、新規依存、外部AI、モデル既定値、timeout、confidence閾値の変更なし
- 自動検査: private-file scan、ruff、format、mypy、388 tests、diff checks成功

## Phase 2 unified Ollama configuration

- 状態: Issue #5の実装、仕様更新、回帰test、全検査を作業ブランチで完了
- 対象Issue: `#5`
- source of truth: endpoint、model、temperatureの中央既定値と検証を
  frozen `OllamaConfig`へ集約。AI層の既存定数は中央値の互換aliasとした
- 通信境界: HTTPの`127.0.0.1`または`localhost`だけを受理し、portを保った数値
  loopbackへ正規化。不正endpointは画像読込・runtime初期化・通信より前に拒否し、
  環境proxyを無効化してredirectを追従しない
- 設定解決: process/inboxは`AppConfig`の同一objectを全試行へ渡す。standalone
  extractは明示設定、既定設定ファイル、そのファイルが存在しない場合だけ中央builtin
  defaultの順で解決し、不正設定をfallbackで隠さない
- 評価: 品質比較の既定2modelと`--model`はmodelだけの明示例外。endpointと
  temperatureをbase configから継承した新しい検証済みobjectをmodelごとに作り、
  production設定を変更しない
- 互換性: 設定なしのstandalone extract、既存CLI JSON、評価report、公開Python
  scalar APIを維持。scalar APIも通信前に中央validatorへ通す
- private境界: private画像を使用せず、合成入力とmock通信だけで検証。secret、
  endpoint入力、絶対pathをerrorや成果物へ追加しない
- DB migration、新規依存、外部endpoint、LiteLLM、既定model、timeout、抽出schema、
  prompt、検証閾値の変更なし
- 自動検査: private-file scan、ruff、format、mypy、467 tests、diff checks成功

## Phase 2 raw item name normalization contract

- 状態: Issue #7のdomain validation、非破壊carry、repository round-trip、回帰test、
  仕様更新を作業ブランチで実装
- 対象Issue: `#7`
- raw契約: 既存の`items.name`がcanonicalなraw品目名。`name_norm`の有無や内容で
  上書き・変換せず、保存前後で同じ値を維持
- normalized契約: `name_norm`は独立した任意field。欠落と`null`は未設定として
  受理し、既存response・既存DB行との後方互換性を維持。未設定値はJSON出力で省略
- validation: 空文字、前後空白、制御文字、不可視format文字、surrogate、文字列以外は
  schema-invalidとして`confirmed`へ進めない。受理したUnicode文字列は変換せず保持
- 保存: 税正規化を通してrawとnormalizedの両方を独立してcarryし、既存
  `items.name_norm`列へtransaction内で保存・復元。既存NULL行を推測で補完しない
- 互換性: 既存のraw品目名によるquality/accuracy照合、`name_norm`未設定・`null`の
  CLI旧JSON形、`StoredItem`読取契約を維持。非null時は独立fieldとして出力する。
  抽出schemaのprovenance hashだけはfield追加に伴い更新
- 対象外: 抽出promptによる正規化名生成、カテゴリ分類、検索・集計・report、
  review UI、model既定値・閾値変更、private精度評価
- DB migration、新規依存、外部AI、privateデータ利用なし。prompt hashは変更しない
- §6-4全体はLLM生成と集計・表示が未実装のため`partially_implemented`を維持
- 自動検査: private-file scan、ruff、format、mypy、491 tests、diff checks成功

## Remaining

1. Issue #7を独立commitとしてpushしてmain向けPull Requestを作成する
2. Issue #7のPull Requestは自動mergeせずreviewを待つ
3. Issue #11は仕様のserver要否とloopback通信の扱いが明確になるまでblockedを維持
4. 22/30 baselineは現状の評価として維持し、confirmed率をaccuracyと表現しない
