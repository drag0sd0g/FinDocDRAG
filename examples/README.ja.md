[English](README.md)

# FinDocRAG — API 使用例

このディレクトリには、FinDocRAG API を探索するための [Bruno](https://www.usebruno.com/) コレクションと、実際のエンドツーエンドのクエリレスポンスを示す具体的な注釈付きサンプルが含まれています。

## Bruno でコレクションを開く

1. Bruno をインストールする: https://www.usebruno.com/downloads
2. **Open Collection** → `examples/bruno/` フォルダを選択
3. 右上の環境ピッカーで **local** を選択
4. リクエストを送信する前にスタックを起動する (`make run` または `make run-remote`)

---

## エンドツーエンドのウォークスルー

### ステップ 1 — 有価証券報告書を取り込む

```bash
curl -X POST http://localhost:8001/v1/ingest \
  -H "Content-Type: application/json" \
  -d '{"tickers": ["AAPL"]}'
```

```json
{
  "status": "completed",
  "tickers_processed": ["AAPL"],
  "filings_published": 3,
  "filings_skipped": 0,
  "errors": []
}
```

埋め込みワーカーが Kafka メッセージを受け取り、各ファイリングを約 512 トークンのウィンドウにチャンク分割し、`sentence-transformers/all-MiniLM-L6-v2` で埋め込みを生成して、ベクトルを pgvector に書き込みます。CPU 上では数分かかります。

---

### ステップ 2 — ナレッジベースにクエリを送信する

```bash
curl -X POST http://localhost:8000/v1/query \
  -H "Content-Type: application/json" \
  -H "X-API-Key: dev-key-1" \
  -d '{
    "question": "What were Apple'\''s main risk factors related to supply chain in their 2024 10-K?",
    "ticker_filter": "AAPL",
    "top_k": 5
  }'
```

**注釈付きレスポンス:**

```jsonc
{
  // 取得したチャンクに基づいて LLM が生成した回答。
  // LLM が利用できない場合は null になる（下記「degraded」を参照）。
  "answer": "Apple's 2024 10-K identifies supply chain concentration as a primary risk. The Company sources components including advanced semiconductors, displays, and rare earth materials from a small number of suppliers, many located in Asia. A disruption — whether from geopolitical tension, natural disaster, or a single-supplier failure — could delay product launches and reduce gross margins [Source 1]. Apple also cites logistical dependency on third-party carriers for last-mile delivery, which creates exposure during peak demand periods [Source 2]. Additionally, the Company acknowledges that its contract manufacturers, primarily in China, are subject to local regulatory changes and labour cost inflation that it cannot fully control [Source 3].",

  // LLM プロンプトに注入されたチャンクのランク付きリスト。
  // コサイン類似度の降順（最も高いものが先頭）。
  "sources": [
    {
      "chunk_id": "a3f8c2d1e9b047f6a2c5d8e1f3b4a7c9",  // (accession + index) の SHA-256
      "ticker": "AAPL",
      "filing_date": "2024-11-01",
      "section": "Item 1A - Risk Factors",
      "relevance_score": 0.91,  // [0, 1] のコサイン類似度; 値が高いほど関連性が高い
      "text_preview": "The Company's operations and performance depend significantly on its ability to source components from a limited number of suppliers. The loss of a significant supplier could adversely affect..."
    },
    {
      "chunk_id": "b7e1d4c2a9f053e8c1d6a3f2e8b5c4d0",
      "ticker": "AAPL",
      "filing_date": "2024-11-01",
      "section": "Item 1A - Risk Factors",
      "relevance_score": 0.87,
      "text_preview": "The Company relies on third-party carriers for product delivery. Disruptions in logistics networks, including during peak demand periods, could impact product availability..."
    },
    {
      "chunk_id": "c9a5f3e7b2d018c4e7a2b9f1d3c6e8a1",
      "ticker": "AAPL",
      "filing_date": "2024-11-01",
      "section": "Item 1A - Risk Factors",
      "relevance_score": 0.83,
      "text_preview": "The Company's contract manufacturers are primarily located in China and are subject to local laws and regulations. Changes in labour costs, environmental regulations, or..."
    },
    {
      "chunk_id": "d2b8e4a6c1f079d5b3e8c2a4f7d1b9e3",
      "ticker": "AAPL",
      "filing_date": "2023-11-03",
      "section": "Item 1A - Risk Factors",
      "relevance_score": 0.79,
      "text_preview": "Global supply chain disruptions, including those resulting from geopolitical tensions, trade restrictions, or natural disasters, could adversely affect the availability..."
    },
    {
      "chunk_id": "e5c1a7d3b9f026e4c8b1a5d2f4e7c3b6",
      "ticker": "AAPL",
      "filing_date": "2024-11-01",
      "section": "Item 7 - MD&A",
      "relevance_score": 0.74,
      "text_preview": "Net sales decreased 1% or $3.6 billion during 2024 compared to 2023, reflecting supply constraints on certain products in the first half of the year..."
    }
  ],

  // この回答を生成した LLM バックエンド。
  "model": "mistral:7b",

  // 各ステージのレイテンシ内訳（ミリ秒単位）。
  "timing": {
    "embedding_ms": 11.3,    // sentence-transformers で質問を埋め込む時間
    "retrieval_ms": 42.7,    // 約 12,000 チャンクに対する pgvector コサイン類似度検索
    "generation_ms": 5840.0, // LLM 推論（CPU 上の Ollama は通常 3〜15 秒）
    "total_ms": 5894.0
  },

  // false = 完全な回答が返された。
  // true  = LLM が利用できなかった; "answer" は null だが "sources" は引き続き設定されており、
  //         生成された回答がなくても呼び出し元が取得したコンテキストを表示できる。
  "degraded": false,

  // X-Request-ID レスポンスヘッダーにもエコーバックされる; ログの相関付けに使用する。
  "request_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

---

### ステップ 3 — 取り込み済みのファイリングを確認する

```bash
curl http://localhost:8000/v1/documents?ticker=AAPL \
  -H "X-API-Key: dev-key-1"
```

```json
{
  "documents": [
    {
      "accession_number": "0000320193-24-000123",
      "ticker": "AAPL",
      "company_name": "Apple Inc.",
      "filing_date": "2024-11-01",
      "filing_type": "10-K",
      "chunk_count": 312,
      "ingested_at": "2024-11-15T09:32:14.000000"
    }
  ],
  "total": 1,
  "limit": 20,
  "offset": 0
}
```

---

## コレクションの構成

```
bruno/
  bruno.json                        コレクションのルート（Bruno 必須ファイル）
  environments/
    local.bru                       変数: query_api_url, ingestion_url,
                                          embedding_worker_url, api_key
  01-health/
    query-api-health.bru            GET /health
    ingestion-health.bru            GET /health
    embedding-worker-health.bru     GET /health
  02-ingest/
    ingest-tickers.bru              POST /v1/ingest  {"tickers": ["AAPL","MSFT"]}
    ingest-from-config.bru          POST /v1/ingest  (config/tickers.yml を読み込む)
  03-query/
    supply-chain-risk-aapl.bru      上記の注釈付きサンプル（アサション付き）
    revenue-comparison-msft-aapl.bru ticker_filter なしのクロスティッカー質問
    graceful-degradation.bru        LLM 停止時の degraded=true レスポンス形式を確認
  04-documents/
    list-all-documents.bru          GET /v1/documents
    list-documents-by-ticker.bru    GET /v1/documents?ticker=AAPL
```

## ヒント

- **認証:** Query API へのすべてのリクエストには `X-API-Key: dev-key-1` が必要（`local` 環境で設定済み）。Ingestion には認証が不要。
- **Degraded モード:** Ollama コンテナを停止し (`docker stop findoc-ollama`)、任意のクエリを送信すると、`"degraded": true` および `"answer": null` を含む HTTP 200 が返されるが、`sources` 配列には引き続きデータが設定される。
- **レート制限:** Query API はキーごとに 30 リクエスト/分を上限として適用する。コレクションをまとめて実行しすぎると、Bruno が HTTP 429 を受け取る。
- **`top_k` の範囲:** 1〜20。512 トークン/チャンクの場合、20 チャンク ≈ 10,000 トークンとなり、32,000 トークンのコンテキストウィンドウ内に収まる。
