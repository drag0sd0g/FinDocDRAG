# 金融ドキュメント・インテリジェンス・プラットフォーム — 技術設計書

## 目次

1. [用語集](#1-用語集)
2. [背景](#2-背景)
3. [機能要件](#3-機能要件)
4. [非機能要件](#4-非機能要件)
5. [主要提案](#5-主要提案)
6. [スケーラビリティ](#6-スケーラビリティ)
7. [レジリエンス](#7-レジリエンス)
8. [オブザーバビリティ](#8-オブザーバビリティ)
9. [セキュリティ](#9-セキュリティ)
10. [デプロイメント](#10-デプロイメント)

---

## 1. 用語集

| 用語                        | 定義                                                                                                                                                      |
| --------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **RAG**                     | Retrieval-Augmented Generation（検索拡張生成）— LLM の応答を検索したソースドキュメントに基づかせることでハルシネーションを低減し、引用を提供する技術。            |
| **Embedding**               | ニューラルエンコーダモデルが生成する、テキストチャンクの固定長密ベクトル表現。意味的類似性検索に使用される。                                                   |
| **Vector Store**            | 高次元の埋め込みベクトルを格納し、近似最近傍（ANN）検索でクエリするために最適化されたデータベースまたはインデックス。                                          |
| **pgvector**                | ベクトル列型と ANN インデックスサポート（IVFFlat、HNSW）を標準 PostgreSQL に追加するオープンソースの PostgreSQL 拡張機能。                                    |
| **Chunk**                   | モデルのコンテキスト制限に収まるよう、論理的な境界（セクション、段落）でオリジナルテキストを分割して生成したソースドキュメントのセグメント。                     |
| **10-K Filing**             | 上場企業が SEC に提出する年次包括報告書。監査済み財務諸表と事業内容の開示が含まれる。                                                                        |
| **SEC EDGAR**               | 米国証券取引委員会（SEC）の電子データ収集・分析・検索システム — 企業の開示書類へのアクセスを提供する無料の公開 API。                                           |
| **LLM**                     | Large Language Model（大規模言語モデル）— 大量のテキストコーパスで訓練された、プロンプトが与えられると人間のようなテキストを生成できるニューラルネットワーク。   |
| **Ollama**                  | LLM をスタンドアロンサーバーとしてローカルで実行するためのオープンソースツール。REST API を提供する。                                                         |
| **HNSW**                    | Hierarchical Navigable Small World — pgvector が高速ベクトル検索に使用するグラフベースの近似最近傍インデックスアルゴリズム。                                   |
| **Cosine Similarity**       | 2 つのベクトル間の角度のコサインを測定する指標。クエリ埋め込みに対する関連性によってドキュメントチャンクをランク付けするために使用される。                      |
| **Token**                   | LLM が処理するテキストの最小単位（英語では概ね単語の 3/4 程度）。モデルには入力と出力を合わせた固定のトークン制限がある。                                      |
| **Context Window**          | LLM が単一リクエストで処理できる最大トークン数（プロンプトと補完の合計）。                                                                                    |
| **Hallucination**           | LLM がソースデータに基づかない、もっともらしく聞こえるが事実として誤った情報を生成すること。                                                                  |
| **CDC**                     | Change Data Capture（変更データキャプチャ）— データ変更を検出して伝播するパターン。ここでは取り込みパイプラインのイベント駆動設計のコンセプトとして使用される。  |
| **Drift (Embedding Drift)** | 時間の経過とともに埋め込みの統計的分布が変化すること。埋め込みモデルやドキュメントの特性が変化した場合、検索品質が低下する可能性がある。                        |
| **ragas**                   | RAG パイプラインを評価するためのオープンソースフレームワーク。忠実度、回答の関連性、コンテキスト精度などの指標を提供する。                                      |
| **Helm**                    | テンプレート化された YAML チャートを使用してアプリケーションスタックを定義、バージョン管理、デプロイする Kubernetes 用パッケージマネージャー。                    |
| **HPA**                     | Horizontal Pod Autoscaler — 観測されたメトリクスに基づいてポッドレプリカ数を自動スケールする Kubernetes リソース。                                            |
| **Pydantic**                | FastAPI がスキーマ生成に使用する、リクエスト/レスポンスモデルの型契約を強制する Python データバリデーションライブラリ。                                        |
| **FastAPI**                 | 自動 OpenAPI ドキュメント生成と非同期サポートを備えた、API 構築のためのモダンで高パフォーマンスな Python Web フレームワーク。                                   |
| **sentence-transformers**   | HuggingFace Transformers 上に構築された、文やテキストの埋め込みを生成する事前学習済みモデルを提供する Python ライブラリ。                                      |

---

## 2. 背景

### 2.1 問題の定義

金融の専門家（アナリスト、コンプライアンス担当者、ポートフォリオマネージャー）は、大量の規制書類や決算資料から特定の情報を定常的に抽出する必要がある。現状のアプローチは次の通りである。

- **手動検索**: SEC EDGAR から PDF または HTML の書類をダウンロードし、Ctrl+F で検索するか線形に読む。
- **キーワードベースの検索**: 従来の全文検索インデックスはドキュメントを返すが回答は返さない。ユーザーは依然として何ページものコンテキストを読む必要がある。
- **アナリストによる要約**: 人間が作成するため大幅な遅延が生じ、コストが高く、カバー範囲も限られる。

これらのアプローチのいずれも、ユーザーが _「Apple の 2024 年 10-K でサプライチェーンに関連する主なリスク要因は何か？」_ のような自然言語の質問をして、ソースの書類に基づいた直接的で引用付きの回答を受け取ることを可能にしない。

### 2.2 提案する解決策

**FinDoc RAG** は、次の機能を備えたエンドツーエンドの Retrieval-Augmented Generation プラットフォームである。

1. イベント駆動パイプラインを通じて公開財務ドキュメント（SEC の 10-K 書類）を取り込む。
2. ドキュメントをチャンクに分割し、埋め込みを生成してベクトルストアにインデックスする。
3. 自然言語クエリを受け付け、意味的類似性によって関連するドキュメントチャンクを検索し、大規模言語モデルを使用してソース引用付きの根拠ある回答を生成する REST API を公開する。
4. プロダクション品質のオブザーバビリティ、評価、デプロイメントインフラを提供する。

### 2.3 目標

| ID  | 目標                                                                                |
| --- | ----------------------------------------------------------------------------------- |
| G-1 | ドキュメントの取り込みから引用付き回答生成までのエンドツーエンド RAG システムを実証する。 |
| G-2 | 取り込みパイプラインにイベント駆動アーキテクチャ（Kafka）を使用する。                   |
| G-3 | OpenAPI ドキュメントを備えたプロダクションスタイルの REST API としてプラットフォームを提供する。 |
| G-4 | 検索および回答品質の定量的な評価を提供する。                                           |
| G-5 | オブザーバビリティを含む Helm を使用した Kubernetes 上にフルスタックをデプロイする。    |
| G-6 | 明確な根拠とトレードオフを伴うすべてのアーキテクチャ上の決定を文書化する。              |

### 2.4 対象外事項

| ID   | 対象外事項                                                                                                   |
| ---- | ------------------------------------------------------------------------------------------------------------ |
| NG-1 | プロダクション用マルチテナント SaaS 製品の構築 — これはポートフォリオ/デモプロジェクトである。                   |
| NG-2 | カスタム LLM のファインチューニングや訓練 — Ollama、OpenAI API、または Anthropic API を通じて事前学習済みモデルを使用する。 |
| NG-3 | リアルタイムストリーミングクエリのサポート（WebSocket/SSE）— API はリクエスト/レスポンス方式である。            |
| NG-4 | 初期バージョンでの非 SEC ドキュメントソースの取り込み（例: ニュース、独自調査）。                               |
| NG-5 | フロントエンド UI の構築 — インターフェースは REST API（自動生成 Swagger ドキュメント付き）である。             |

### 2.5 システムコンテキスト図

```
┌──────────────────────────────────────────────────────────────────────┐
│                         FinDoc RAG System                            │
│                                                                      │
│  ┌────────────┐   ┌──────────────┐   ┌────────────┐   ┌──────────┐ │
│  │ Ingestion  │──▶│  Embedding   │──▶│  Query     │──▶│   LLM    │ │
│  │ Service    │   │  Worker      │   │  API       │   │  (LLM)   │ │
│  └─────┬──────┘   └──────┬───────┘   └─────┬──────┘   └──────────┘ │
│        │                 │                  │                         │
│        │           ┌─────▼──────┐           │                        │
│        │           │ PostgreSQL │◀──────────┘                        │
│        │           │ + pgvector │                                    │
│   ┌────▼────┐      └────────────┘                                   │
│   │  Kafka  │                                                        │
│   └─────────┘                                                        │
└──────────────────────────────────────────────────────────────────────┘
       ▲                                            ▲
       │                                            │
┌──────┴──────┐                              ┌──────┴──────┐
│  SEC EDGAR  │                              │  API Client │
│  (Public)   │                              │  (User)     │
└─────────────┘                              └─────────────┘
```

---

## 3. 機能要件

### 3.1 ドキュメント取り込み

| ID   | 要件                                                                                                                                                            |
| ---- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| FR-1 | システムは、企業のティッカーシンボルによって SEC EDGAR の EFTS 全文検索 API（`efts.sec.gov/LATEST/search-index`）から 10-K 書類を取得しなければならない（SHALL）。 |
| FR-2 | システムは、どの企業を取り込むかを定義する設定ファイル（`config/tickers.yml`）を通じてティッカーシンボルのリストを受け付けなければならない（SHALL）。              |
| FR-3 | システムは、取得した各書類を Kafka トピック（`filings.raw`）へのメッセージとして、ティッカー、書類提出日、アクセッション番号、および生テキストのメタデータとともに公開しなければならない（SHALL）。 |
| FR-4 | システムは、再取り込み時にべき等でなければならない（SHALL）: 同じアクセッション番号を持つ書類がベクトルストアに既に存在する場合、それはスキップされなければならない（SHALL）。 |
| FR-5 | システムは、解析に失敗した書類をログに記録してスキップし、残りの書類の取り込みを中断することなく継続しなければならない（SHALL）。                                 |

### 3.2 チャンキングと埋め込み

| ID    | 要件                                                                                                                                                                                                                                                                                     |
| ----- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| FR-6  | 埋め込みワーカーサービスは、`filings.raw` Kafka トピックからメッセージを消費しなければならない（SHALL）。                                                                                                                                                                               |
| FR-7  | ワーカーは、セクション認識分割を使用して各書類をチャンクに分割しなければならない（SHALL）: まず 10-K のアイテムセクション（Item 1、Item 1A、Item 7 など）で分割し、次にセクション内の段落で分割する。目標チャンクサイズは 512 トークンで、連続するチャンク間に 64 トークンのオーバーラップを設ける。 |
| FR-8  | 各チャンクは、ソースティッカー、書類提出日、アクセッション番号、セクション名、チャンクシーケンスインデックスのメタデータを保持しなければならない（SHALL）。                                                                                                                              |
| FR-9  | ワーカーは、`sentence-transformers/all-MiniLM-L6-v2` モデルを使用して各チャンクの 384 次元の埋め込みベクトルを生成しなければならない（SHALL）。                                                                                                                                          |
| FR-10 | ワーカーは、各チャンク（テキスト + メタデータ + 埋め込みベクトル）を pgvector を使用した PostgreSQL の `document_chunks` テーブルに保存しなければならない（SHALL）。                                                                                                                    |
| FR-11 | ワーカーは、正常に処理されたチャンク ID を下流のオブザーバビリティのために `filings.embedded` Kafka トピックに公開しなければならない（SHALL）。                                                                                                                                         |

### 3.3 クエリと回答生成

| ID    | 要件                                                                                                                                                                                                                       |
| ----- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| FR-12 | Query API は、次のフィールドを持つ JSON ボディを受け付ける `POST /v1/query` エンドポイントを公開しなければならない（SHALL）: `question`（文字列、必須）、`ticker_filter`（文字列、任意）、`top_k`（整数、任意、デフォルト 5、最大 20）。 |
| FR-13 | Query API は、同じ埋め込みモデル（`all-MiniLM-L6-v2`）を使用してユーザーの質問を埋め込み、コサイン距離でオプションのティッカーシンボルフィルタリングを行いながら pgvector から `min(top_k × 4, 100)` 個の候補チャンクを取得し、Maximal Marginal Relevance（MMR、λ=0.5）リランキングを適用して、関連性と多様性のバランスが最も優れた最終的な `top_k` チャンクを選択しなければならない（SHALL）。 |
| FR-14 | Query API は、取得したチャンクをコンテキストとして含み、ユーザーの質問と、提供されたコンテキストのみから回答してソースを引用するよう指示するプロンプトを構築しなければならない（SHALL）。                                  |
| FR-15 | プロンプトテンプレートは以下の構造に従わなければならない（SHALL）:                                                                                                                                                         |

```
You are a financial document analyst. Answer the user's question using ONLY
the provided context from SEC filings. If the context does not contain enough
information to answer, say "I don't have enough information to answer this."

For every claim in your answer, cite the source using [Source N] notation,
where N corresponds to the context chunk number.

Context:
{numbered_chunks_with_metadata}

Question: {user_question}

Answer:
```

| ID    | 要件                                                                                                                                                                                                                                                                                                            |
| ----- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| FR-16 | Query API は、構築したプロンプトを設定済みの LLM バックエンド（Ollama ローカルまたは OpenAI API）に送信し、生成された回答を返さなければならない（SHALL）。                                                                                                                                                        |
| FR-17 | API レスポンスには以下が含まれなければならない（SHALL）: `answer`（文字列または null）、`sources`（`chunk_id`、`ticker`、`filing_date`、`section`、`relevance_score`、および `text_preview` — チャンクの最初の 200 文字 — を持つオブジェクトの配列）、`model`（文字列 — 使用された LLM）、`timing`（`embedding_ms`、`retrieval_ms`、`generation_ms`、および `total_ms` を持つオブジェクト）、`degraded`（ブール値 — LLM が利用不可の場合は true）、`request_id`（UUID）。 |
| FR-18 | Query API は、環境変数（`LLM_BACKEND`）で選択可能な 3 つの LLM バックエンドをサポートしなければならない（SHALL）: `ollama`（デフォルト、`mistral:7b` モデル使用）、`openai`（`gpt-4o-mini` モデル使用）、および `claude`（`claude-opus-4-6` モデル使用、`CLAUDE_MODEL` 環境変数で上書き可能）。                  |

### 3.4 ドキュメント一覧

| ID    | 要件                                                                                                                                                   |
| ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| FR-19 | Query API は、取り込まれたすべての書類のリストを、ティッカー、書類提出日、アクセッション番号、チャンク数、および取り込みタイムスタンプのメタデータとともに返す `GET /v1/documents` エンドポイントを公開しなければならない（SHALL）。 |
| FR-20 | `GET /v1/documents` エンドポイントは、オプションのクエリパラメータをサポートしなければならない（SHALL）: `ticker`（ティッカーシンボルでフィルタリング）および `limit`/`offset`（ページネーション、デフォルトの制限は 20、最大 100）。 |

### 3.5 ヘルスとレディネス

| ID    | 要件                                                                                                                                                                                      |
| ----- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| FR-21 | 各サービス（Ingestion Service、Embedding Worker、Query API）は、サービスが実行中でその依存関係（データベース、Kafka、LLM）が到達可能な場合に HTTP 200 で `{"status": "healthy"}` を返す `GET /health` エンドポイントを公開しなければならない（SHALL）。 |
| FR-22 | 各サービスは、サービスが完全に初期化されてリクエストを処理する準備ができた場合（例: 埋め込みモデルのロード完了、データベース接続プールの確立完了）にのみ HTTP 200 を返す `GET /ready` エンドポイントを公開しなければならない（SHALL）。 |

---

## 4. 非機能要件

### 4.1 パフォーマンス

| ID    | 要件                                                                                                                                              |
| ----- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| NFR-1 | ベクトル類似性検索（LLM 生成を除く検索のみ）は、最大 500,000 チャンクのコーパスに対して p99 で 200ms 以内に完了しなければならない（SHALL）。         |
| NFR-2 | エンドツーエンドのクエリレイテンシ（検索 + LLM 生成）は、少なくとも 16 GB RAM を搭載したマシンで Ollama の `mistral:7b` を使用する場合、p95 で 10 秒以内に完了しなければならない（SHALL）。 |
| NFR-3 | 埋め込みワーカーは、単一のワーカーインスタンスで少なくとも毎秒 50 チャンクを処理して保存しなければならない（SHALL）。                              |
| NFR-4 | 取り込みサービスは、SEC の 1 秒あたり 10 リクエストのレート制限を遵守しながら、SEC EDGAR から少なくとも毎分 10 件の書類を取得して公開しなければならない（SHALL）。 |

> **注記:** NFR-1 から NFR-4 は、想定されるデプロイメントプロファイル（RAM 16 GB 以上、CPU のみの埋め込み）に合わせたデザインターゲットである。ベンチマーク測定はまだ行われていない。実際の測定値に対する検証は、最初の完全な評価実行（評価ハードウェアが利用可能になる 2026 年 4 月頃に予定）で計画されている。実際のデータが収集された後、数値が修正される可能性がある。

### 4.2 キャパシティ

| ID    | 要件                                                                                        |
| ----- | ------------------------------------------------------------------------------------------- |
| NFR-5 | システムは、最大 1,000 件の 10-K 書類（約 500,000 チャンク）の取り込みとクエリをサポートしなければならない（SHALL）。 |
| NFR-6 | ベクトルストアは、HNSW インデックスによる最大 500,000 個の 384 次元ベクトルをサポートしなければならない（SHALL）。 |

### 4.3 品質と評価

| ID    | 要件                                                                                                                                                                                                                          |
| ----- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| NFR-7 | プロジェクトには、少なくとも 5 社にわたる少なくとも 30 の質問と回答のペアのキュレートされたデータセットに対して実行される評価ハーネスを含めなければならない（SHALL）。                                                       |
| NFR-8 | 評価ハーネスは、`ragas` ライブラリを使用して以下のメトリクスを計算して報告しなければならない（SHALL）: **Context Precision**（取得したチャンクは関連性があるか？）、**Faithfulness**（回答は取得したコンテキストに基づいているか？）、**Answer Relevancy**（回答は質問に答えているか？）。 |
| NFR-9 | 評価結果は JSON レポートとして保存され、質問ごとのスコアと集計平均のテーブルとともに `docs/evaluation-results.md` に要約されなければならない（SHALL）。                                                                      |

### 4.4 開発者体験

| ID     | 要件                                                                                              |
| ------ | ------------------------------------------------------------------------------------------------- |
| NFR-10 | スタック全体が単一の `docker compose up` コマンドでローカルに実行可能でなければならない（SHALL）。 |
| NFR-11 | プロジェクトには、次のターゲットを持つ `Makefile` を含めなければならない（SHALL）: `setup`、`test`、`lint`、`run`、`eval`、`docker-build`、`helm-deploy`。 |
| NFR-12 | すべての Python コードは、エラーゼロで `ruff` リンティングと `mypy` 型チェックをパスしなければならない（SHALL）。 |
| NFR-13 | 各サービスは、`pytest-cov` で計測して少なくとも 80% の行カバレッジを持つユニットテストを備えなければならない（SHALL）。 |

---

## 5. 主要提案

### 5.1 高レベルアーキテクチャ

システムは、Kafka と共有 PostgreSQL データベースで接続された 4 つの独立してデプロイ可能なサービスで構成される。

```
┌──────────────┐     ┌─────────┐     ┌───────────────────┐     ┌─────────────┐
│  SEC EDGAR   │────▶│Ingestion│────▶│   Kafka            │────▶│  Embedding  │
│  (External)  │     │ Service │     │                     │     │  Worker     │
└──────────────┘     └─────────┘     │  filings.raw        │     └──────┬──────┘
                                     │  filings.embedded    │            │
                                     │  filings.dlq         │            │
                                     └───────────────────────┘            │
                                                                         ▼
┌──────────────┐     ┌─────────┐                            ┌───────────────────┐
│  API Client  │◀───▶│ Query   │◀──────────────────────────▶│   PostgreSQL      │
│  (User)      │     │ API     │                            │   + pgvector      │
└──────────────┘     └────┬────┘                            │                   │
                          │                                 │  document_chunks  │
                          ▼                                 │  ingestion_log    │
                     ┌─────────┐                            └───────────────────┘
                     │  Ollama │
                     │  (LLM)  │
                     └─────────┘
```

### 5.2 サービスの説明

#### 5.2.1 Ingestion Service

| プロパティ      | 値                                                                     |
| ------------- | ---------------------------------------------------------------------- |
| **言語**      | Python 3.12                                                            |
| **フレームワーク** | FastAPI（ヘルス/レディネスエンドポイント + `POST /v1/ingest` トリガーを公開） |
| **役割**      | SEC EDGAR から 10-K 書類を取得し、生テキストを Kafka に公開する           |

**取り込みフロー:**

```
┌──────────┐      ┌──────────────────┐      ┌──────────────┐      ┌────────┐
│  Trigger │─────▶│  Fetch filing    │─────▶│  Deduplicate │─────▶│Publish │
│ POST /v1 │      │  from EDGAR API  │      │  (check by   │      │to Kafka│
│ /ingest  │      │                  │      │  accession#) │      │        │
└──────────┘      └──────────────────┘      └──────────────┘      └────────┘
```

- ボディ `{"tickers": ["AAPL", "MSFT"]}` で `POST /v1/ingest` を受け付けるか、ボディが提供されない場合は `config/tickers.yml` から読み込む。
- 各ティッカーについて、SEC EDGAR EFTS API に 10-K 書類を問い合わせる。
- 各書類について、`ingestion_log` テーブルを確認し、アクセッション番号が既に存在する場合はスキップする。
- 生の書類テキストとメタデータを `filings.raw` Kafka トピックに公開する。
- `ingestion_log` にステータス `PUBLISHED` とともにアクセッション番号を記録する。

**Kafka メッセージスキーマ（`filings.raw`）:**

```json
{
  "accession_number": "0000320193-24-000123",
  "ticker": "AAPL",
  "filing_date": "2024-11-01",
  "filing_type": "10-K",
  "company_name": "Apple Inc.",
  "raw_text": "...",
  "source_url": "https://www.sec.gov/Archives/edgar/data/...",
  "published_at": "2026-03-10T12:00:00Z"
}
```

#### 5.2.2 Embedding Worker

| プロパティ      | 値                                                                               |
| ------------- | -------------------------------------------------------------------------------- |
| **言語**      | Python 3.12                                                                      |
| **フレームワーク** | スタンドアロン Kafka コンシューマ（`confluent-kafka` を使用）+ ヘルス用 FastAPI サイドカー |
| **役割**      | 生の書類を消費し、テキストをチャンク化し、埋め込みを生成して pgvector に保存する      |

**処理フロー:**

```
┌──────────┐    ┌───────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
│ Consume  │───▶│  Section  │───▶│  Chunk   │───▶│ Embed    │───▶│  Store   │
│ from     │    │  Split    │    │  by size │    │ via      │    │  in      │
│ Kafka    │    │ (10-K     │    │ (512 tok │    │ sentence │    │ pgvector │
│          │    │  items)   │    │  64 over)│    │ transf.  │    │          │
└──────────┘    └───────────┘    └──────────┘    └──────────┘    └──────────┘
                                                                       │
                                                                       ▼
                                                                 ┌──────────┐
                                                                 │ Publish  │
                                                                 │ to       │
                                                                 │filings.  │
                                                                 │embedded  │
                                                                 └──────────┘
```

**チャンキング戦略:**

1. **セクション分割**: 10-K 構造を解析し、既知のアイテム（Item 1、1A、1B、2、3、4、5、6、7、7A、8、9、9A、9B、10、11、12、13、14、15）で分割する。セクション名はメタデータとして保存される。
2. **段落分割**: 各セクション内で、二重改行で分割する。
3. **トークンベースのウィンドウ化**: 段落が 512 トークンを超える場合、64 トークンのオーバーラップを持つ 512 トークンのウィンドウに分割する。トークンカウントは `cl100k_base` エンコーディングで `tiktoken` ライブラリを使用する。
4. 各結果チャンクには、決定論的な `chunk_id` が割り当てられる: `SHA256(accession_number + section_name + chunk_sequence_index)`。以下のフィールドが `document_chunks` テーブルのチャンクごとに保存される:

| フィールド          | 型            | 説明                                                                     |
| ------------------ | ------------- | ------------------------------------------------------------------------ |
| `chunk_id`         | `VARCHAR(64)` | アクセッション + セクション + インデックスの決定論的 SHA256 ハッシュ        |
| `accession_number` | `VARCHAR(30)` | SEC EDGAR アクセッション番号（`ingestion_log` への外部キー）              |
| `ticker`           | `VARCHAR(10)` | ティッカーシンボル（例: `AAPL`）                                           |
| `filing_date`      | `DATE`        | 10-K が提出された日付                                                     |
| `section_name`     | `VARCHAR(100)`| 10-K のアイテム名（例: `Item 1A - Risk Factors`、解析不能の場合は `Unknown`）|
| `chunk_index`      | `INTEGER`     | 書類内のゼロベースのシーケンスインデックス                                  |
| `token_count`      | `INTEGER`     | このチャンクのトークン数（tiktoken `cl100k_base`）                         |
| `embedding`        | `vector(384)` | `all-MiniLM-L6-v2` からの密な埋め込みベクトル                              |

**埋め込み:**

- モデル: `sentence-transformers/all-MiniLM-L6-v2`（384 次元、約 80 MB、CPU で動作）。
- バッチ埋め込み: スループット向上のため、チャンクを 64 個のバッチで埋め込む。
- モデルはワーカー起動時に一度ロードされてメモリに保持される。

#### 5.2.3 Query API

| プロパティ      | 値                                                                       |
| ------------- | ------------------------------------------------------------------------ |
| **言語**      | Python 3.12                                                              |
| **フレームワーク** | FastAPI                                                                  |
| **役割**      | ユーザークエリを受け付け、関連するチャンクを検索し、引用付きの回答を生成する |

**クエリフロー:**

```
┌──────────┐   ┌──────────┐   ┌───────────┐   ┌──────────┐   ┌──────────┐
│ Receive  │──▶│  Embed   │──▶│  Retrieve │──▶│ Build    │──▶│  Call    │
│ POST     │   │  query   │   │  top-k    │   │ prompt   │   │  LLM    │
│ /v1/query│   │  vector  │   │  chunks   │   │ with     │   │         │
│          │   │          │   │  from     │   │ context  │   │         │
└──────────┘   └──────────┘   │  pgvector │   └──────────┘   └────┬─────┘
                              └───────────┘                       │
                                                                  ▼
                                                            ┌──────────┐
                                                            │ Return   │
                                                            │ answer + │
                                                            │ sources  │
                                                            │ + timing │
                                                            └──────────┘
```

**API コントラクト:**

リクエスト（`POST /v1/query`）:

```json
{
  "question": "What were Apple's main risk factors related to supply chain in their 2024 10-K?",
  "ticker_filter": "AAPL",
  "top_k": 5
}
```

レスポンス:

```json
{
  "answer": "According to Apple's 2024 10-K filing, the main supply chain risk factors include... [Source 1] ... concentration of suppliers in specific regions... [Source 2] ...",
  "sources": [
    {
      "chunk_id": "a1b2c3d4...",
      "ticker": "AAPL",
      "filing_date": "2024-11-01",
      "section": "Item 1A - Risk Factors",
      "relevance_score": 0.87,
      "text_preview": "The Company's business, reputation, results of operations, financial condition..."
    }
  ],
  "model": "mistral:7b",
  "timing": {
    "embedding_ms": 12,
    "retrieval_ms": 45,
    "generation_ms": 3200,
    "total_ms": 3257
  },
  "degraded": false,
  "request_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

**LLM バックエンド抽象化:**

```python
# Simplified interface — all backends implement this
class LLMBackend(Protocol):
    async def generate(self, prompt: str, max_tokens: int = 1024) -> LLMResponse: ...

class OllamaBackend(LLMBackend):
    # Calls local Ollama server at http://ollama:11434/api/generate
    # Model: mistral:7b (default); started via --profile local-llm
    ...

class OpenAIBackend(LLMBackend):
    # Calls OpenAI API with model: gpt-4o-mini
    # Requires OPENAI_API_KEY environment variable
    ...

class AnthropicBackend(LLMBackend):
    # Calls Anthropic Messages API with model: claude-opus-4-6 (default)
    # Requires ANTHROPIC_API_KEY environment variable
    # Model overridable via CLAUDE_MODEL environment variable
    ...
```

アクティブなバックエンドは `LLM_BACKEND` 環境変数（`ollama`、`openai`、または `claude`）によって選択される。

#### 5.2.4 評価ハーネス

| プロパティ      | 値                                                                       |
| ------------- | ------------------------------------------------------------------------ |
| **言語**      | Python 3.12                                                              |
| **フレームワーク** | `ragas` ライブラリを使用したスタンドアロンスクリプト                         |
| **役割**      | キュレートされたテストデータセットに対して検索と生成の品質を計測する           |

**評価データセット**（`services/eval/eval_dataset.json`）:

少なくとも 30 の質問・回答・コンテキストのトリプルを含む手動でキュレートされた JSON ファイル:

```json
[
  {
    "question": "What was Apple's total revenue in fiscal year 2024?",
    "ground_truth": "Apple's total net revenue for fiscal year 2024 was $391.0 billion.",
    "ticker": "AAPL"
  }
]
```

**計算されるメトリクス:**

| メトリクス         | ライブラリ | 説明                                                                             |
| ----------------- | ------- | -------------------------------------------------------------------------------- |
| Context Precision | `ragas` | 質問に答えるために関連する取得チャンクの割合                                        |
| Faithfulness      | `ragas` | 取得したコンテキストによって裏付けられた生成回答内の主張の割合                       |
| Answer Relevancy  | `ragas` | 質問と生成された回答との意味的類似度                                                |

**出力:**

- `services/eval/results/eval_report_{timestamp}.json` への JSON レポート
- `docs/evaluation-results.md` に追記されるサマリーテーブル

### 5.3 データモデル

#### PostgreSQL スキーマ

```sql
-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Ingestion tracking
CREATE TABLE ingestion_log (
    accession_number VARCHAR(30) PRIMARY KEY,
    ticker           VARCHAR(10)  NOT NULL,
    company_name     VARCHAR(200) NOT NULL,
    filing_date      DATE         NOT NULL,
    filing_type      VARCHAR(10)  NOT NULL DEFAULT '10-K',
    source_url       TEXT         NOT NULL,
    status           VARCHAR(20)  NOT NULL DEFAULT 'PUBLISHED',
    chunk_count      INTEGER,
    ingested_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    completed_at     TIMESTAMPTZ
);

CREATE INDEX idx_ingestion_ticker ON ingestion_log(ticker);
CREATE INDEX idx_ingestion_status ON ingestion_log(status);

-- Document chunks with embeddings
CREATE TABLE document_chunks (
    chunk_id         VARCHAR(64)  PRIMARY KEY,  -- SHA256 hash
    accession_number VARCHAR(30)  NOT NULL REFERENCES ingestion_log(accession_number),
    ticker           VARCHAR(10)  NOT NULL,
    filing_date      DATE         NOT NULL,
    section_name     VARCHAR(100) NOT NULL,
    chunk_index      INTEGER      NOT NULL,
    chunk_text       TEXT         NOT NULL,
    token_count      INTEGER      NOT NULL,
    embedding        vector(384)  NOT NULL,     -- all-MiniLM-L6-v2 output dimension
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- HNSW index for fast approximate nearest neighbor search
CREATE INDEX idx_chunks_embedding ON document_chunks
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 200);

CREATE INDEX idx_chunks_ticker ON document_chunks(ticker);
CREATE INDEX idx_chunks_accession ON document_chunks(accession_number);
```

#### Kafka トピック

| トピック            | キー               | 値スキーマ           | パーティション数 | 保持期間  |
| ------------------ | ---------------- | ---------------- | ---------- | --------- |
| `filings.raw`      | accession_number | 書類 JSON         | 6          | 7 日間    |
| `filings.embedded` | accession_number | チャンク ID 配列   | 6          | 7 日間    |
| `filings.dlq`      | accession_number | エラー + 元データ  | 3          | 30 日間   |

> **トピックの作成:** `filings.raw` は最初の公開時に Kafka によって自動作成される（`KAFKA_AUTO_CREATE_TOPICS_ENABLE=true`、6 パーティションと 7 日間保持のクラスターデフォルトを継承）。`filings.embedded` と `filings.dlq` は、コンシューマが起動する前に正しいパーティション数と保持期間を確保するために Docker Compose の初期化スクリプト（`kafka-setup` サービス）によって明示的に作成される。

### 5.4 技術選択と根拠

| 決定事項              | 選択肢                  | 根拠                                                                                                                                                             |
| ----------------------- | ---------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| ベクトルストア          | PostgreSQL + pgvector  | 別個のベクトルデータベース（Pinecone、Weaviate）の導入を避ける。PostgreSQL は実績があり、pgvector の HNSW インデックスは当プロジェクトのスケール（500K ベクトル）に十分である。運用するインフラコンポーネントが一つ減る。 |
| 埋め込みモデル          | all-MiniLM-L6-v2       | 384 次元、約 80 MB のモデル、CPU で動作、検索タスクに対して良好な品質。GPU 不要、API コストなし。MTEB リーダーボードで十分にベンチマーク済み。                     |
| LLM（デフォルト）       | Ollama と mistral:7b   | ローカルで動作、API コストなし、良好な指示追従でドキュメント QA に十分な品質。Ollama はシンプルな REST API を提供し、コンテナ化が容易。Docker Compose の `--profile local-llm` で起動。 |
| LLM（代替）             | OpenAI gpt-4o-mini     | より高品質な回答、評価比較に有用だが、API キーが必要でコストがかかる。デフォルトではなく代替バックエンドとして提供。                                               |
| LLM（代替）             | Claude（Anthropic）    | Anthropic Messages API を通じた高品質な回答。ベーススタック以外にローカル GPU や RAM 不要。モデルのデフォルトは `claude-opus-4-6`、`CLAUDE_MODEL` で上書き可能。`ANTHROPIC_API_KEY` が必要。 |
| メッセージキュー        | Apache Kafka           | 取り込みと埋め込みの間のイベント駆動分離。耐久性、リプレイ可能性、並列処理のためのコンシューマーグループセマンティクスを提供する。                                  |
| API フレームワーク      | FastAPI                | 非同期ネイティブ、自動 OpenAPI ドキュメント生成、Pydantic バリデーション、高パフォーマンス。Python ML/AI エコシステムで優勢。                                       |
| プログラミング言語      | Python 3.12            | ML/AI ライブラリエコシステム（sentence-transformers、ragas、LangChain）に必要。安全性のための型ヒント + mypy。                                                     |
| チャンキングのトークンカウント | tiktoken (cl100k_base) | 高速で決定論的なトークンカウント。cl100k_base はモダンな OpenAI モデルが使用するエンコーディングで、他のモデルのトークン化の合理的なプロキシである。              |

---

## 6. スケーラビリティ

### 6.1 スケーリング戦略の概要

```
                    ┌─────────────────────────────────────────┐
                    │           Load Balancer (K8s Service)    │
                    └───────────────┬─────────────────────────┘
                                    │
                    ┌───────────────▼─────────────────────────┐
                    │         Query API (HPA: 2-8 replicas)    │
                    │    Scales on: CPU > 70% or                │
                    │    request latency p95 > 5s               │
                    └───────────────┬─────────────────────────┘
                                    │
            ┌───────────────────────┼───────────────────────┐
            ▼                       ▼                       ▼
   ┌────────────────┐    ┌──────────────────┐    ┌─────────────────┐
   │  PostgreSQL    │    │  Ollama LLM      │    │  Embedding      │
   │  + pgvector    │    │  (1 replica,     │    │  Model          │
   │  (1 primary)   │    │   GPU optional)  │    │  (in-process)   │
   └────────────────┘    └──────────────────┘    └─────────────────┘
```

### 6.2 ボトルネック分析

| コンポーネント      | ボトルネック                        | 緩和策                                                                                                                             |
| ---------------- | --------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| Query API        | CPU バウンドの埋め込み生成           | 埋め込みモデルはレプリカごとにロードされる。HPA がレプリカを水平スケールする。                                                        |
| Query API        | LLM 生成レイテンシ                  | LLM 呼び出しは非同期でノンブロッキング。複数の並行クエリを同時に処理できる。                                                         |
| Embedding Worker | CPU バウンドのバッチ埋め込み         | Kafka コンシューマーグループにより、より多くのワーカーレプリカを追加できる。パーティションが負荷を分散する。                           |
| PostgreSQL       | 大規模テーブルでのベクトル検索       | チューニングされた `ef_search` パラメータを持つ HNSW インデックス。500K ベクトルでは、単一の PostgreSQL インスタンスで十分である。インデックスのビルドは `CREATE INDEX CONCURRENTLY` を使用するため、既存のクエリはブロックされない。ビルド時間は汎用ハードウェアで 500K ベクトルの場合、数分と推定される（未検証 — 参考数値は [pgvector ベンチマーク](https://github.com/pgvector/pgvector#benchmarks) を参照）。 |
| Ollama           | CPU での逐次生成                    | シングルリクエストのボトルネック。緩和策: GPU が利用可能な場合は GPU を使用するか、並行ワークロードでは OpenAI API にフォールバックする。 |
| Kafka            | このスケールではボトルネックになりにくい | トピックあたり 6 パーティションで最大 6 つの並行コンシューマをサポートする。                                                         |

### 6.3 スケーリングの限界としきい値

| シナリオ               | 予想される限界                   | スケーリングアクション                                                                      |
| ----------------------- | ------------------------- | ------------------------------------------------------------------------------------- |
| 100K チャンク未満       | Query API シングルレプリカ   | スケーリング不要                                                                          |
| 100K〜500K チャンク     | Query API 2〜4 レプリカ     | HPA が起動。HNSW インデックスの `ef_search` を増加させる                                    |
| 500K チャンク超         | プロジェクトスコープ外       | 必要なもの: ティッカーによる pgvector パーティショニング、リードレプリカ、または専用ベクトル DB |
| 10 同時クエリ超         | Ollama がボトルネックになる  | OpenAI バックエンドに切り替えるか、Ollama に GPU を追加する                                  |

---

## 7. レジリエンス

### 7.1 障害モードと緩和策

```
┌─────────────────────────────────────────────────────────────────────┐
│                     Failure Handling Overview                        │
│                                                                     │
│  SEC EDGAR ──X──▶ Ingestion Service                                │
│                   └─▶ Retry with exponential backoff (3 attempts)   │
│                   └─▶ Log error, skip filing, continue              │
│                                                                     │
│  Kafka ──X──▶ Ingestion Service / Embedding Worker                 │
│               └─▶ confluent-kafka auto-reconnect                    │
│               └─▶ Health endpoint returns unhealthy                 │
│               └─▶ K8s liveness probe restarts pod                   │
│                                                                     │
│  Embedding Worker ──X──▶ Processing fails                          │
│                         └─▶ Publish to filings.dlq (dead letter)   │
│                         └─▶ Log error with accession number        │
│                         └─▶ Commit offset, continue                │
│                                                                     │
│  PostgreSQL ──X──▶ Query API / Embedding Worker                    │
│                    └─▶ Connection pool retries (3 attempts)         │
│                    └─▶ Health endpoint returns unhealthy            │
│                    └─▶ Query API returns HTTP 503                   │
│                                                                     │
│  Ollama ──X──▶ Query API                                           │
│               └─▶ Timeout after 30 seconds                          │
│                   (aiohttp.ClientTimeout configured in              │
│                    OllamaBackend — applies to Ollama only)          │
│               └─▶ Return HTTP 200 with degraded: true               │
│                   (retrieved sources without LLM answer)            │
│                                                                     │
│  OpenAI / Claude API ──X──▶ Query API                              │
│                    └─▶ Retry with exponential backoff               │
│                        (max_retries=2 → 3 total attempts)           │
│                        SDK default timeouts apply (~600s)           │
│                    └─▶ If still failing, return HTTP 200 degraded   │
└─────────────────────────────────────────────────────────────────────┘
```

### 7.2 べき等性

| コンポーネント      | べき等性のメカニズム                                                                                                                              |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Ingestion Service | Kafka に公開する前に `ingestion_log` で既存のアクセッション番号を確認する。重複した取り込みトリガーは安全に処理される。                               |
| Embedding Worker  | `chunk_id` は決定論的な SHA256 ハッシュである。アップサート（`INSERT ... ON CONFLICT DO NOTHING`）により、同じメッセージを再処理しても重複が生じない。 |
| Kafka Consumer    | データベース書き込み成功後に手動でオフセットをコミットする。ワーカーが処理中にクラッシュした場合、メッセージは再配信され、アップサートが重複排除を処理する。 |

### 7.3 デッドレターキュー

3 回の再試行後に埋め込みワーカーで処理に失敗したメッセージは、`filings.dlq` トピックに以下の形式で公開される:

```json
{
  "original_message": { ... },
  "error": "Section parsing failed: unexpected format in Item 7A",
  "failed_at": "2026-03-10T14:30:00Z",
  "retry_count": 3
}
```

DLQ トピックは 30 日間の保持期間を持ち、手動での検査とリプレイが可能である。

**DLQ メッセージの検査:**

```bash
# Docker Compose (local)
docker compose exec kafka \
  kafka-console-consumer.sh \
  --bootstrap-server localhost:9092 \
  --topic filings.dlq \
  --from-beginning \
  --max-messages 10

# Kubernetes
kubectl exec -n findoc-rag deploy/findoc-rag-kafka -- \
  kafka-console-consumer.sh \
  --bootstrap-server localhost:9092 \
  --topic filings.dlq \
  --from-beginning
```

**失敗した書類のリプレイ:** DLQ ペイロードから `original_message` フィールドを抽出し、`filings.raw` に再公開する。埋め込みワーカーのべき等アップサートにより、安全な再処理が保証される（セクション 7.2）。

**アラート:** `findoc_embedding_dlq_messages_total` Prometheus カウンター（Grafana パイプラインダッシュボードに表示）は、DLQ への書き込みごとにインクリメントされる。このカウンターのレートが許容しきい値を超えた場合のアラートを設定する（例: `rate(findoc_embedding_dlq_messages_total[5m]) > 0`）。

### 7.4 グレースフルデグラデーション

| 障害                            | デグレード動作                                                                                                                           |
| ----------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| LLM が利用不可                  | Query API は生成された回答なしに関連性スコア付きの取得ソースを返す。HTTP 200 と `"answer": null` および `"degraded": true`。               |
| 埋め込みモデルのロード失敗       | サービスのヘルスエンドポイントが unhealthy を返す。K8s はトラフィックをルーティングしない。ポッドが再起動する。                             |
| Kafka が利用不可               | 取り込みサービスが HTTP 503 を返す。埋め込みワーカーが一時停止する（Kafka コンシューマーのポーリングループがブロックする）。Kafka が復旧すると両方が自動的に回復する。 |

---

## 8. オブザーバビリティ

### 8.1 メトリクス

すべてのサービスは `/metrics`（ポート 9090）で Prometheus メトリクスを公開する。

#### 8.1.1 Ingestion Service のメトリクス

| メトリクス名                              | タイプ    | ラベル             | 説明                                               |
| ----------------------------------------- | --------- | ------------------ | -------------------------------------------------- |
| `findoc_ingestion_filings_fetched_total`  | Counter   | `ticker`, `status` | 取得した書類の合計（成功/エラー/スキップ）          |
| `findoc_ingestion_edgar_request_duration` | Histogram | `ticker`           | SEC EDGAR API リクエストレイテンシ                  |
| `findoc_ingestion_kafka_publish_total`    | Counter   | `topic`, `status`  | Kafka に公開されたメッセージ（成功/エラー）         |
| `findoc_ingestion_filing_size_bytes`      | Histogram | `ticker`           | 生の書類テキストサイズ（バイト）                    |

#### 8.1.2 Embedding Worker のメトリクス

| メトリクス名                              | タイプ    | ラベル             | 説明                                          |
| ----------------------------------------- | --------- | ------------------ | --------------------------------------------- |
| `findoc_embedding_chunks_processed_total` | Counter   | `ticker`, `status` | 埋め込みと保存が完了したチャンク（成功/エラー） |
| `findoc_embedding_batch_duration`         | Histogram | —                  | チャンクのバッチを埋め込む時間                  |
| `findoc_embedding_chunk_tokens`           | Histogram | —                  | チャンクあたりのトークン数分布                  |
| `findoc_embedding_dlq_messages_total`     | Counter   | —                  | デッドレターキューに送られたメッセージ数        |
| `findoc_embedding_kafka_lag`              | Gauge     | `partition`        | パーティションごとのコンシューマーラグ          |
| `findoc_embedding_chunks_per_filing`      | Histogram | `ticker`           | 書類ごとに生成されたチャンク数                  |

#### 8.1.3 Query API のメトリクス

| メトリクス名                             | タイプ    | ラベル              | 説明                                                |
| --------------------------------------- | --------- | ------------------- | --------------------------------------------------- |
| `findoc_query_requests_total`           | Counter   | `endpoint`,`status` | エンドポイントと HTTP ステータス別の総 API リクエスト |
| `findoc_query_embedding_duration`       | Histogram | —                   | ユーザークエリの埋め込みにかかる時間                  |
| `findoc_query_retrieval_duration`       | Histogram | —                   | pgvector 類似性検索にかかる時間                      |
| `findoc_query_llm_duration`             | Histogram | `backend`           | LLM 生成にかかる時間（ollama/openai）                |
| `findoc_query_total_duration`           | Histogram | —                   | エンドツーエンドのクエリレイテンシ合計                |
| `findoc_query_retrieval_score`          | Histogram | —                   | トップ 1 の関連性スコアの分布                        |
| `findoc_query_llm_tokens_used`          | Counter   | `backend`, `type`   | 消費されたトークン（プロンプト/補完）                 |
| `findoc_query_degraded_responses_total` | Counter   | `reason`            | デグレードモードで返されたレスポンス                  |

### 8.2 Grafana ダッシュボード

プロジェクトには `monitoring/grafana/dashboards/` にある 2 つの事前構築済み Grafana ダッシュボードが JSON ファイルとして含まれている:

**ダッシュボード 1: 取り込みと埋め込みパイプライン**

- ティッカー別の書類取り込みレート（書類/分）
- 埋め込みワーカーのスループット（チャンク/秒）
- パーティション別の Kafka コンシューマーラグ
- DLQ メッセージレート
- チャンクのトークン数分布

**ダッシュボード 2: クエリパフォーマンス**

- クエリリクエストレートとエラーレート
- レイテンシ内訳: 埋め込み → 検索 → LLM 生成（積み上げ）
- p50 / p95 / p99 の総クエリレイテンシ
- トップ 1 の検索関連性スコア分布
- LLM トークン消費レート
- デグレードレスポンスレート

### 8.3 構造化ロギング

すべてのサービスは JSON 出力で Python の `structlog` ライブラリを使用する:

```json
{
  "timestamp": "2026-03-10T12:00:00Z",
  "level": "info",
  "service": "query-api",
  "event": "query_completed",
  "sources": 5,
  "degraded": false,
  "total_ms": 3257,
  "request_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
}
```

すべてのログエントリには、ログエントリ全体でリクエストをトレースするための `request_id`（UUID）が含まれる。リクエスト ID は `X-Request-ID` ヘッダーが提供されている場合はそこから設定され、そうでない場合は API によって生成される。

### 8.4 埋め込みドリフト検出

スケジュールジョブ（Kubernetes CronJob、週次）は、過去 7 日間に取り込まれたすべてのチャンクの平均埋め込みベクトルを計算し、コーパス全体の平均と（コサイン距離で）比較する。ドリフトが設定可能なしきい値（デフォルト: 0.15）を超えた場合、警告メトリクスが発行される:

| メトリクス名                    | タイプ | 説明                                                   |
| ------------------------------ | ----- | ------------------------------------------------------ |
| `findoc_embedding_drift_score` | Gauge | 最近の埋め込みとコーパス平均埋め込みのコサイン距離       |
| `findoc_embedding_drift_alert` | Gauge | ドリフトがしきい値を超えた場合は 1、それ以外は 0         |

---

## 9. セキュリティ

### 9.1 認証と認可

| メカニズム         | 実装                                                                                                                                                               |
| ----------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| API Key 認証      | Query API は `X-API-Key` ヘッダーを要求する。有効なキーはカンマ区切りの環境変数（`API_KEYS`）として保存される。有効なキーなしのリクエストは HTTP 401 を受け取る。 |
| 内部サービス      | Ingestion Service と Embedding Worker は外部に公開されない。Kubernetes クラスターネットワーク内で Kafka と PostgreSQL を通じてのみ通信する。                       |
| Ollama            | 外部への露出なしにクラスター内部サービスとして動作する。内部 K8s ネットワーク経由で Query API からアクセスされる。                                                  |

### 9.2 データセキュリティ

| 懸念事項             | 緩和策                                                                                                                                                                                                                                                                                        |
| -------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 保存データ           | すべてのデータ（書類、埋め込み）は公開 SEC データである。PII や機密情報は含まれない。                                                                                                                                                                                          |
| 転送中データ         | すべての Kubernetes 内部通信はクラスターネットワーキングを使用する。外部 API アクセスは HTTPS を使用する（K8s Ingress TLS 終端経由）。                                                                                                                                          |
| API キー             | Kubernetes Secret として保存され、環境変数としてマウントされる。ログに記録されず、API レスポンスにも含まれない。                                                                                                                                                                |
| OpenAI API キー      | Kubernetes Secret として保存される。Query API ポッドのみがアクセス可能。ログに記録されない。                                                                                                                                                                                    |
| LLM プロンプトインジェクション | システムプロンプトはハードコードされている（ユーザーによる変更不可）。プロンプトには構造的にラベル付けされた 3 つのセクションがある: `Context:`（検索からの `[Source N]` 番号付きブロック）、`Question:`（そのままのユーザー入力、最後に配置）、`Answer:`（LLM がここから補完）。区切りは位置的かつ構造的なもので、ユーザー入力や取得テキストのエスケープはない。質問またはチャンクのいずれかの敵対的コンテンツがモデルの出力に影響を与える可能性がある。これは多層防御の手段であり、保証ではない — プロンプトインジェクションは未解決の研究課題である。 |

### 9.3 レート制限

| 制限       | 値                     | 実装                                                        |
| --------- | ---------------------- | ----------------------------------------------------------- |
| Query API | 30 リクエスト/分/キー   | `slowapi` ライブラリ（API キーごとのトークンバケット）        |
| SEC EDGAR | 10 リクエスト/秒        | 取り込みサービスのセマフォリミッターを使用した `aiohttp`      |

### 9.4 依存関係管理

- すべての Python 依存関係は `requirements.txt` に正確なバージョンで固定されている（`pip-compile` で生成）。
- サービスの Docker イメージ（`ingestion`、`embedding-worker`、`query-api`）はベースイメージとして `python:3.12-slim` を使用する。サードパーティのインフライメージ（Prometheus、Grafana、Ollama）は `latest` タグを使用している。これらを特定のダイジェストに固定することは、プロダクション向けの推奨ハードニング手順である。
- GitHub Actions ワークフローには、依存関係の既知の脆弱性を確認するための `pip-audit` ステップが含まれている。

---

## 10. デプロイメント

### 10.1 リポジトリ構造

```
findoc-rag/
├── README.md
├── Makefile
├── docker-compose.yml
├── config/
│   └── tickers.yml
├── .github/
│   └── workflows/
│       ├── ci.yml
│       └── docker-publish.yml
├── services/
│   ├── ingestion/
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   ├── src/
│   │   │   ├── main.py
│   │   │   ├── edgar_client.py
│   │   │   ├── kafka_producer.py
│   │   │   ├── config.py
│   │   │   ├── db.py
│   │   │   └── metrics.py
│   │   └── tests/
│   │       └── test_edgar_client.py
│   ├── embedding-worker/
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   ├── src/
│   │   │   ├── main.py
│   │   │   ├── chunker.py
│   │   │   ├── embedder.py
│   │   │   ├── store.py
│   │   │   ├── metrics.py
│   │   │   └── drift_detector.py
│   │   └── tests/
│   │       ├── test_main.py
│   │       ├── test_chunker.py
│   │       ├── test_embedder.py
│   │       ├── test_store.py
│   │       └── test_drift_detector.py
│   ├── query-api/
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   ├── src/
│   │   │   ├── main.py
│   │   │   ├── metrics.py
│   │   │   ├── rag/
│   │   │   │   ├── retriever.py
│   │   │   │   ├── generator.py
│   │   │   │   └── prompts.py
│   │   │   ├── llm/
│   │   │   │   ├── backend.py
│   │   │   │   ├── ollama_backend.py
│   │   │   │   ├── openai_backend.py
│   │   │   │   └── anthropic_backend.py
│   │   │   ├── auth.py
│   │   │   └── models.py
│   │   └── tests/
│   │       ├── test_main.py
│   │       ├── test_main_app.py
│   │       ├── test_retriever.py
│   │       ├── test_generator.py
│   │       └── test_llm_backends.py
│   └── eval/
│       ├── requirements.txt
│       ├── eval_dataset.json
│       └── run_eval.py
├── helm/
│   └── findoc-rag/
│       ├── Chart.yaml
│       ├── values.yaml
│       ├── dashboards/
│       │   └── findoc-overview.json
│       └── templates/
│           ├── ingestion-deployment.yaml
│           ├── embedding-worker-deployment.yaml
│           ├── query-api-deployment.yaml
│           ├── ollama-deployment.yaml
│           ├── postgresql-statefulset.yaml
│           ├── kafka-statefulset.yaml
│           ├── prometheus-deployment.yaml
│           ├── grafana-deployment.yaml
│           ├── grafana-provisioning-configmap.yaml
│           ├── grafana-dashboards-configmap.yaml
│           ├── configmaps.yaml
│           ├── secrets.yaml
│           ├── services.yaml
│           ├── hpa.yaml
│           ├── ingress.yaml
│           └── cronjob-drift-detection.yaml
├── monitoring/
│   ├── prometheus.yml
│   └── grafana/
│       └── dashboards/
│           ├── pipeline-dashboard.json
│           └── query-dashboard.json
├── db/
│   └── migrations/
│       └── 001_initial_schema.sql
└── docs/
    ├── technical-design-document.md
    ├── design-decisions.md
    └── evaluation-results.md
```

### 10.2 ローカル開発（Docker Compose）

単一の `docker compose up` でフルスタックをローカルで起動できる:

```yaml
# Simplified overview of docker-compose.yml services
services:
  postgres: # PostgreSQL 16 with pgvector extension
  kafka: # Apache Kafka (KRaft mode, no Zookeeper)
  ollama: # Ollama with mistral:7b model pre-pulled
  ingestion: # Ingestion Service
  embedding-worker: # Embedding Worker
  query-api: # Query API (exposed on port 8000)
  prometheus: # Prometheus (scrapes all services)
  grafana: # Grafana (pre-configured dashboards, port 3000)
```

起動順序は `depends_on` とヘルスチェックで管理される:

1. PostgreSQL + Kafka が最初に起動する
2. データベースマイグレーションが実行される（init コンテナ）
3. Ollama が起動してモデルをプルする
4. Ingestion Service、Embedding Worker、Query API が起動する
5. Prometheus と Grafana が最後に起動する

### 10.3 CI/CD パイプライン（GitHub Actions）

#### ワークフロー 1: `ci.yml`（すべてのプッシュと PR で実行）

```
┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
│  Lint    │───▶│  Type    │───▶│  Unit    │───▶│  Audit   │
│  (ruff)  │    │  Check   │    │  Tests   │    │  (pip-   │
│          │    │  (mypy)  │    │ (pytest) │    │  audit)  │
└──────────┘    └──────────┘    └──────────┘    └──────────┘
```

- `[ingestion, embedding-worker, query-api]` のマトリクスで各サービスに対して実行される
- Python 3.12
- カバレッジレポートはアーティファクトとしてアップロードされる

#### ワークフロー 2: `docker-publish.yml`（`main` へのプッシュ時）

```
┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│  Build       │───▶│  Push to     │───▶│  Update      │
│  Docker      │    │  GitHub      │    │  Helm        │
│  Images      │    │  Container   │    │  image tags  │
│              │    │  Registry    │    │              │
└──────────────┘    └──────────────┘    └──────────────┘
```

- 各サービスのマルチアーキテクチャイメージ（amd64/arm64）をビルド
- GitHub Container Registry（`ghcr.io/drag0sd0g/findoc-rag/*`）にプッシュ
- タグ: `latest` + git SHA

### 10.4 Kubernetes デプロイメント（Helm）

#### リソース割り当て

| コンポーネント      | レプリカ数          | CPU リクエスト | CPU 制限  | メモリリクエスト | メモリ制限   |
| ----------------- | ------------ | ----------- | --------- | -------------- | ------------ |
| Query API         | 2（HPA: 2-8）| 500m        | 2000m     | 1Gi            | 2Gi          |
| Embedding Worker  | 2            | 1000m       | 2000m     | 2Gi            | 4Gi          |
| Ingestion Service | 1            | 250m        | 500m      | 512Mi          | 1Gi          |
| Ollama            | 1            | 2000m       | 4000m     | 8Gi            | 12Gi         |
| PostgreSQL        | 1            | 500m        | 2000m     | 1Gi            | 4Gi          |
| Kafka（KRaft）    | 1            | 500m        | 1000m     | 1Gi            | 2Gi          |
| Prometheus        | 1            | 250m        | 500m      | 512Mi          | 1Gi          |
| Grafana           | 1            | 100m        | 250m      | 256Mi          | 512Mi        |

#### Horizontal Pod Autoscaler（Query API）

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: query-api-hpa
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: query-api
  minReplicas: 2
  maxReplicas: 8
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
```

#### Kubernetes アーキテクチャ

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Kubernetes Cluster                           │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  Namespace: findoc-rag                                       │   │
│  │                                                              │   │
│  │  ┌────────────┐  ┌────────────┐  ┌────────────────────────┐ │   │
│  │  │ Ingress    │─▶│ Query API  │─▶│ Ollama                 │ │   │o
│  │  │ (TLS)     │  │ (2-8 pods) │  │ (1 pod, 8Gi+ RAM)     │ │   │
│  │  └────────────┘  └─────┬──────┘  └────────────────────────┘ │   │
│  │                        │                                     │   │
│  │                        ▼                                     │   │
│  │               ┌────────────────┐                             │   │
│  │               │  PostgreSQL    │                             │   │
│  │               │  + pgvector    │                             │   │
│  │               │  (StatefulSet) │                             │   │
│  │               └────────────────┘                             │   │
│  │                        ▲                                     │   │
│  │                        │                                     │   │
│  │  ┌────────────┐  ┌────┴───────┐  ┌────────────────────────┐ │   │
│  │  │ Ingestion  │─▶│   Kafka    │◀─│ Embedding Worker       │ │   │
│  │  │ (1 pod)    │  │  (KRaft)   │  │ (2 pods)               │ │   │
│  │  └────────────┘  └────────────┘  └────────────────────────┘ │   │
│  │                                                              │   │
│  │  ┌────────────┐  ┌────────────┐  ┌────────────────────────┐ │   │
│  │  │ Prometheus │─▶│  Grafana   │  │ Drift CronJob (weekly) │ │   │
│  │  │            │  │            │  │                         │ │   │
│  │  └────────────┘  └────────────┘  └────────────────────────┘ │   │
│  └──────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

### 10.5 Makefile ターゲット

```makefile
setup:          # Create virtual environments, install dependencies
test:           # Run pytest for all services
lint:           # Run ruff + mypy for all services
run:            # docker compose up (full local stack)
eval:           # Run RAG evaluation harness
docker-build:   # Build Docker images for all services
helm-deploy:    # Deploy to Kubernetes via Helm
helm-teardown:  # Remove Helm release
migrate:        # Run database migrations
```
