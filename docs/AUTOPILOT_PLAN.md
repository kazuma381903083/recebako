# recebako v1.1 自走開発計画

## 1. この文書の役割

この文書は、`docs/spec/recebako-spec-v1.1.html` の要求と現在の実装との差分を
追跡する。機能要求の正本は仕様書、設計判断の正本は `docs/adr/`、後方互換条件は
既存テストである。この文書だけを根拠に新しい仕様を追加しない。

監査基準は Phase 1C 修正コミット `e55e1e9` とする。

状態は次の5種類だけを使う。

- `implemented`: 要求された挙動と自動テストが存在する
- `partially_implemented`: 要求の一部だけが実装済み、または受入確認が残る
- `not_implemented`: v1.1の要求だが実装がない
- `out_of_scope`: 仕様書自身がv1対象外またはPhase 4任意としている
- `requires_user_decision`: 人間の正解入力、物理端末設定、または仕様判断が必要

`implemented` はコードが存在するだけでなく、仕様上の受入条件を自動テストで確認
できる場合に限る。数値NFRは実装状態と達成判定を混同せず、測定境界または
human-verifiedな正解がない場合は達成済みとしない。

## 2. スコープと機能要求

| 要求 | 状態 | 現在の根拠 | 残作業 |
|---|---|---|---|
| §1-1 撮影から月次レポートまでの自動家計簿 | `partially_implemented` | `pipeline/process.py`、`runtime/inbox.py` で取込・抽出・検証・保存まで実装 | 分類、レポート、レビュー、運用自動化 |
| §1-3 紙レシートの取込・抽出・分類・蓄積・レポート・レビュー・検索 | `partially_implemented` | 取込からSQLite蓄積まで実装 | FR-05、FR-07〜09 |
| §1-3 電子レシート、メール、クレカCSV連携、銀行API、複数ユーザー、確定申告 | `out_of_scope` | 仕様書がv1対象外と明記 | Issue化しない |
| §2-2 Pixel、Syncthing送信専用、Google Photos除外 | `requires_user_decision` | アプリは `inbox/` のみを消費しSyncthingへ直接依存しない | 物理端末の設定と手動確認 |
| FR-01 画像自動検知、起動時一括処理 | `partially_implemented` | `recebako inbox run`、排他、順序、limit、自動回復を実装 | watcher、launchd |
| FR-02 EXIF回転、長辺2048px、pHash | `implemented` | `imaging/preprocess.py` と `test_preprocess.py` | private撮影条件での受入確認のみ |
| FR-03 定義済みJSONによる抽出 | `implemented` | Pydantic schemaをOllama `format` に指定、temperature=0 | `name_norm` と `is_receipt` は別要求 |
| FR-04 合計、日付、重複、三値判定 | `partially_implemented` | 検証、pHash/identity重複、confirmed/review/failedを実装 | 最大3回の画像variant再試行 |
| FR-05 費目分類と店名マスタ学習 | `not_implemented` | DB列と `store_master` テーブルのみ存在し保存値は未設定 | G01、G19、G23〜G25 |
| FR-06 SQLite保存、原画像相対パス、撮影年月archive | `partially_implemented` | transaction、`file_state`、排他的移動、回復、実在相対パスをテスト。現在の年月は抽出日（不正時は実行日）で決まり撮影年月ではない | G55〜G57 |
| FR-07 自己完結月次HTML | `not_implemented` | `reports/` と外観サンプルのみ | G27〜G33、G50、G51 |
| FR-08 ローカルレビューUI | `not_implemented` | localhost設定検証のみ | G11〜G22、G53、G54 |
| FR-09 期間・店・費目・品目検索 | `not_implemented` | query/CLIなし | G26 |
| FR-10 Takeoutフォルダ一括投入 | `partially_implemented` | historical modeとinbox一括は実装 | G08のsource非変更な安全入口 |

## 3. データ設計とAI処理

| 要求 | 状態 | 現在の根拠 | 残作業 |
|---|---|---|---|
| §5-1 JSON Schema強制、必須値検証 | `implemented` | `domain/receipt.py`、`ai/ollama.py`、Ollama mock tests | なし |
| §5-1、ADR-001 税情報、raw/normalized価格、安全な補正 | `implemented` | migration 002、`normalization/tax.py`、監査issue code | なし |
| §5-2 receipts/items/store_master | `partially_implemented` | テーブルは存在 | category、name_norm、master挙動 |
| §5-3 inbox→processing→archive/review/failed | `partially_implemented` | 排他的移動、pending/finalized、起動時回復、dry-run | 抽出3回失敗後という規則（G04） |
| §6-1 推測禁止、負値、schema、temperature=0 | `implemented` | `ai/ollama.py` | なし |
| §6-2 合計・日付・confidence・重複のreview判定 | `implemented` | `validation/receipt.py`、`storage/duplicates.py` | なし |
| §6-2 回転・拡大を伴う最大3回再試行 | `not_implemented` | Ollama呼出しは1回 | G04 |
| §6-3 店名マスタ優先、未知店だけLLM、修正学習 | `not_implemented` | 実装なし | G01、G19、G23〜G25 |
| §6-4 raw品目名と `name_norm` の分離 | `not_implemented` | DB列のみ、domain/promptに未追加 | G07 |
| §6-4 confirmed限定のレポート集計 | `not_implemented` | query layerなし | G27 |
| §11 `is_receipt=false` をfailedへ隔離 | `not_implemented` | schema/prompt/validationに項目なし | G03 |
| 費目をreceipt単位かitem単位か | `requires_user_decision` | §6-3と§6-4の集計粒度が一意でない | G01でADR化 |
| 真の重複を集計から除外する永続表現 | `requires_user_decision` | 仕様は「重複疑いをreview」まで | G02でADR化 |

## 4. 非機能要求

| 要求 | 状態 | 現在の根拠 | 残作業 |
|---|---|---|---|
| NFR-P1 1枚60秒、VLM30秒 | `requires_user_decision` | 「撮影→DB」「Pixel→report」「Mac側処理」という測定境界が仕様内で一致せず、安全な計測値もない | G49、G06、G43 |
| NFR-P2 50枚30分 | `partially_implemented` | 逐次batch処理は可能 | G43の50件benchmark |
| NFR-P3 常駐7GB以下、idle unload | `not_implemented` | lifecycle制御と測定なし | G44、G52 |
| NFR-Q1 合計98%以上 | `partially_implemented` | `quality-v1`でhuman verifiedのみを分母に測定 | 30件すべての人手確認後に目標判定 |
| NFR-Q2 店名・日付95%以上 | `partially_implemented` | versionedな店名正規化と日付完全一致を測定 | 30件すべての人手確認後に目標判定 |
| NFR-Q3 品目80%以上 | `partially_implemented` | 品目tupleの順序保持LCSで測定 | 30件すべての人手確認後に目標判定 |
| NFR-Q4 誤確定2%以下 | `partially_implemented` | verified内のactual confirmedを分母に測定し、0分母をunknown化 | 30件すべての人手確認後に目標判定 |
| NFR-Q5 review率30%以下 | `partially_implemented` | 全target caseを分母に継続測定 | 30件すべての人手確認後に目標判定 |
| NFR-S1 外部送信なし | `partially_implemented` | Ollama URL固定、`trust_env=False`、実行経路はlocalhostのみ | G37、G47 |
| NFR-S2 Google Photos除外 | `requires_user_decision` | Pixel側の物理設定 | G37 |
| NFR-S3 ログ衛生 | `partially_implemented` | inbox/failedは内容を出さず、CLI機能出力と分離 | G06の安全なtiming telemetryと運用時redirect方針 |
| NFR-S4 FileVault、local backup | `requires_user_decision` | OS設定と方式が未確認 | G40、G41 |
| NFR-S5 review UIは127.0.0.1のみ | `partially_implemented` | config validatorは実装、serverなし | G11以降 |
| NFR-S6、§8 依存最小、5 package以内、四半期監査、model provenance | `partially_implemented` | lockfileは存在するが、本番依存は仕様の予算を超え、定期audit・license/provenance確認がない | G10 |
| §7-4 crash再開、二重処理防止 | `partially_implemented` | pending/finalized、自動回復、排他、重複reviewをunit/integrationで確認 | G46のforced-crash subprocess E2E |
| §7-4 model/endpoint設定1箇所 | `partially_implemented` | process/inboxはconfig、extractはmodule既定値 | G05 |
| §7-4 CSV撤退経路 | `requires_user_decision` | export契約未定義 | G38、G39 |

## 5. テスト計画と受入基準

| 要求 | 状態 | 現在の根拠 | 残作業 |
|---|---|---|---|
| §9-1 30件golden setと人間入力CSV | `partially_implemented` | 30 caseのprivate CSVがあり22 caseを人間確認済み | 残る8 caseの人手確認と業態・撮影条件の人手確認 |
| §9-2 Q1〜Q3精度試験 | `partially_implemented` | Issue #42でversioned baselineを実装・測定 | 30/30 verifiedで再実行 |
| 壊れた画像、白紙、ぼけ、非レシートをconfirmedにしない | `partially_implemented` | 空・破損・構造不正のunit testあり | G03、G45 |
| 同一画像・再撮影の重複review | `partially_implemented` | identity/pHash unitと同一入力integrationあり | G59のprivate再撮影E2E |
| Pixel撮影からレポートまで60秒 | `requires_user_decision` | Pixel、watcher、report、timingが未実装で、仕様内の終点もDB/reportで一致しない | G49、G33、G36、G37、G43 |
| 強制終了後に欠損・二重登録なし | `partially_implemented` | 中断点回復unit/integrationあり | G46 |
| 外向き通信はSyncthing以外ゼロ | `not_implemented` | 監視受入試験なし | G47 |
| §10 Phase 4横展開・Pixel on-device | `out_of_scope` | 仕様書が任意発展と明記 | 今回Issue化しない |

## 6. Issue分割と依存順

GitHub Issue作成時は各Issueに、背景、目的、範囲、対象外、受入条件、必須テスト、
関連仕様、関連ADR、DB変更、依存関係、private E2E、セキュリティ境界、blocked理由を
記載する。`Gxx` は作成前の安定キーであり、作成後にGitHub Issue番号を併記する。

G09の正解データは仕様書§9-1どおり人間が入力するCSVとする。30件の業態内訳
（10/5/5/5/5）、長い・薄い・しわのある対象、明暗・角度・影の撮影条件は人間が
選定し、Codexやモデル出力を正解として転記しない。

GitHub Issueは
[`kazuma381903083/recebako#1`〜`#62`](https://github.com/kazuma381903083/recebako/issues)
として作成済みであり、この表の `Gnn` は同じ番号の `#nn` に対応する。

| Key | 優先度 | 目的 | 依存 | 現在状態 |
|---|---:|---|---|---|
| G01 | P0 | 費目粒度とstore照合規則のADR | なし | blocked: user decision |
| G02 | P0 | 重複除外の永続状態と集計規則のADR | なし | blocked: user decision |
| G03 | P0 | 非レシート構造化判定 | なし | waiting |
| G04 | P0 | 最大3回の抽出variant再試行 | G03推奨（hard dependencyではない） | waiting |
| G05 | P1 | 全抽出CLIのmodel/endpoint設定統一 | なし | waiting |
| G06 | P1 | 機微情報を含まない段階別処理時間計測 | G49 | blocked: G49 |
| G07 | P1 | raw品目名とname_normの分離 | なし | waiting |
| G08 | P2 | Takeoutローカルフォルダ安全投入 | なし | waiting |
| G09 | P0 | 30件human-verified正解CSV | G58 | partially implemented: 22/30 human verified |
| G10 | P1 | 依存数・脆弱性・license・model provenance監査 | なし | waiting |
| G11 | P0 | localhost review server shell | なし | waiting |
| G12 | P1 | review一覧read-only API | G11 | waiting |
| G13 | P1 | receipt詳細read-only API | G11 | waiting |
| G14 | P1 | 原画像の安全なlocalhost API | G11、G13 | waiting |
| G15 | P1 | 修正履歴schema/repository | なし | waiting |
| G16 | P1 | レシート項目修正API | G11、G13、G15 | waiting |
| G17 | P1 | 再検証付きconfirmed遷移 | G16 | waiting |
| G18 | P1 | 重複除外mutation | G02、G15、G17 | blocked: G02 |
| G19 | P1 | review修正のstore_master学習 | G01、G16、G17、G25 | blocked: G01 |
| G20 | P2 | review一覧画面 | G12 | waiting |
| G21 | P2 | receipt詳細・画像並記画面 | G13、G14 | waiting |
| G22 | P2 | receipt編集・保存control | G16、G21 | waiting |
| G23 | P1 | store_master優先resolver | G01 | blocked: G01 |
| G24 | P1 | 未知店だけlocal LLM resolver | G01、G23 | blocked: G01 |
| G25 | P1 | 費目resolverのpipeline統合 | G01、G23、G24 | blocked: G01 |
| G26 | P2 | 期間・店・費目・品目検索CLI | G01、G07、G25 | blocked: G01 |
| G27 | P1 | confirmed限定月次集計query | G01、G02、G07、G25 | blocked: G01/G02 |
| G28 | P2 | 月次HTML KPI section | G27 | waiting |
| G29 | P2 | 日別費目stacked SVG | G27、G28 | blocked: G01 |
| G30 | P2 | 費目内name_norm TOP品目section | G07、G27、G28 | blocked: G01 |
| G31 | P2 | local LLM月次所感 | G27、G28 | waiting |
| G32 | P2 | 月次report CLIとatomic保存 | G28〜G31、G50、G51 | waiting |
| G33 | P2 | 月初report launchd job | G32 | waiting |
| G34 | P1 | inbox watcher | なし | waiting |
| G35 | P2 | failed時macOS通知 | G34 | waiting |
| G36 | P2 | watcher/startup batch launchd agent | G34 | blocked: macOS smoke |
| G37 | P1 | Pixel/Syncthing/Photos除外runbook | なし | blocked: device setup |
| G38 | P1 | CSV export契約ADR | G01、G02 | blocked: user decision |
| G39 | P2 | CSV export CLI | G18、G25、G38 | blocked: G38 |
| G40 | P1 | local backup/restore方式ADR | なし | blocked: user decision |
| G41 | P2 | local-only OS backup設定とrestore検証 | G40 | blocked: G40/manual |
| G42 | P1 | Q1〜Q5判定と回帰baseline | G09、G58 | partially implemented: `quality-v1`実装・22/30 baseline測定済み、30/30 private受入待ち |
| G43 | P1 | 1件・50件性能benchmark | G06、G49、G58 | blocked: G49 |
| G44 | P1 | VLM常駐memory測定 | G43 | blocked: local measurement |
| G45 | P1 | degraded/nonreceipt安全受入suite | G03、G04、G58 | waiting |
| G46 | P1 | forced-crash recovery E2E | なし | waiting |
| G47 | P1 | 全Ollama経路を含む外向き通信受入手順 | G34、G37 | blocked: OS permission |
| G48 | P3 | v1.0 release準備 | 必須v1 Issue群、G62 | blocked: user approval |
| G49 | P0 | 60秒要件の測定開始・終了点ADR | なし | blocked: user decision |
| G50 | P2 | 月次HTML費目別summary section | G27、G28 | blocked: G01 |
| G51 | P2 | 月次HTML店舗別ranking section | G27、G28 | waiting |
| G52 | P1 | Ollama idle時model unload | G05、G44 | blocked: local measurement |
| G53 | P2 | confirmed遷移control | G17、G21 | waiting |
| G54 | P2 | 重複解決control | G18、G21 | blocked: G02 |
| G55 | P0 | 撮影年月とfallback規則のADR | なし | blocked: user decision |
| G56 | P1 | 撮影年月によるarchive配置 | G55 | blocked: G55 |
| G57 | P1 | legacy image_path棚卸しと非破壊移行方針 | なし | blocked: existing data audit |
| G58 | P0 | Git管理外private評価ハーネス | なし | implemented: PR #63でmainへmerge済み |
| G59 | P1 | 再撮影版の重複private受入試験 | G58 | blocked: private capture |
| G60 | P1 | 評価結果に基づくprompt・検証閾値調整判断 | G09、G42 | blocked: verified metrics |
| G61 | P2 | Qwen3-VL 4B fallback比較と採否判断 | G42、G43 | blocked: verified metrics |
| G62 | P2 | Phase 3一週間ノーメンテsoak | Phase 3必須Issue群 | blocked: manual soak |

## 7. 今回の実装境界

Issue #42では既存のstdoutと評価report schema version 1を維持したまま、
aggregate-only sidecarへversionedなQ1〜Q5指標、目標判定、provenanceを追加し、
同一条件の回帰baselineを記録する。prompt、検証閾値、モデル既定値、品目名の
業務正規化、費目分類、レビューUI、検索、月次レポート、watcher、launchd、
macOS通知、Pixel連携、CSV export、backup運用は変更しない。

正解データがない段階のconfirmed率は精度ではない。human verifiedな正解が未入力なら、
評価結果は必ず「精度不明」と表現する。
