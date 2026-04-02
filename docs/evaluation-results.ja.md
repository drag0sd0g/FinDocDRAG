[English](evaluation-results.md)

# 評価結果

`ragas` 評価ハーネス（`services/eval/run_eval.py`）が生成する RAG 品質メトリクスです。
実行のたびに新しいセクションが以下に追記されます。

## 実行方法

```bash
# 1. スタックを起動する — どちらか一方を選択:
make run          # Ollama を含む（約 6 GB の RAM とモデルのダウンロードが必要）
make run-remote   # Ollama なし; 事前に LLM_BACKEND=claude または openai を設定すること

# 2. ragas ジャッジ用の認証情報をエクスポートする（いずれか一つ; 優先順位: Anthropic > OpenAI > Ollama）
export ANTHROPIC_API_KEY=sk-ant-…   # 推奨ジャッジ: claude-haiku-4-5
export OPENAI_API_KEY=sk-…          # 代替ジャッジ（Anthropic キーがない場合）
# キーが未設定の場合 → ローカルで動作中の Ollama にフォールバック

# 3. 任意で Query API バックエンドを設定する
export LLM_BACKEND=claude           # または: openai / ollama

# 4. eval の依存関係をインストールする
cd services/eval && pip install -r requirements.txt

# 5. 実行する
make eval
# または直接実行: python services/eval/run_eval.py
```

## メトリクスの説明

| メトリクス | 測定内容 | LLM が必要か |
|---|---|---|
| `answer_relevancy` | 生成された回答が質問に対してどの程度関連しているか | はい |
| `faithfulness` | 回答中のすべての主張が取得されたコンテキストに基づいているか | はい |
| `context_precision` | 最上位にランク付けされた取得チャンクが最も関連性の高いものであるか | はい |

3 つのメトリクスはいずれも `ragas` が LLM をジャッジとして使用して算出します（ADR-009 参照）。ジャッジは自動的に選択されます: `ANTHROPIC_API_KEY` → `claude-haiku-4-5`; `OPENAI_API_KEY` → OpenAI; いずれも未設定 → Ollama ローカルフォールバック。
スコアは 0 から 1 の範囲で、高いほど良好です。目標しきい値: 3 つすべてで ≥ 0.70（NFR-8）。

---

## 評価実行 — 2026-04-02 13:23:39 UTC

**評価サンプル数:** 24 / 30  
**詳細レポート:** `services/eval/results/eval_report_2026-04-02_13-23-39_UTC.json`

| 質問 | ティッカー | Answer Relevancy | Faithfulness | Context Precision |
| --- | --- | --- | --- | --- |
| What was Apple's total net revenue for fiscal year 2024? | AAPL | 0.000 | 0.500 | — |
| What are Apple's primary supply chain risk factors disc… | AAPL | 0.819 | 0.400 | — |
| What was Apple's Services segment revenue in fiscal yea… | AAPL | 0.000 | 0.000 | — |
| How much did Apple spend on research and development in… | AAPL | 0.000 | 1.000 | — |
| Which geographic markets contribute most to Apple's net… | AAPL | 0.744 | 0.000 | — |
| What competitive risks does Apple identify in its annua… | AAPL | 0.759 | 0.900 | — |
| What was Microsoft's total revenue for fiscal year 2024? | MSFT | 0.000 | 1.000 | — |
| What was the revenue from Microsoft's Intelligent Cloud… | MSFT | 0.000 | 1.000 | 0.867 |
| What are the key competition-related risk factors Micro… | MSFT | 0.792 | 1.000 | — |
| How much did Microsoft spend on research and developmen… | MSFT | 0.000 | 0.500 | — |
| How does Microsoft describe its approach to returning c… | MSFT | 0.000 | — | — |
| What are Microsoft's three main business segments as de… | MSFT | 0.651 | 0.000 | 0.679 |
| What was Alphabet's total revenue for fiscal year 2024? | GOOGL | 0.000 | 1.000 | 1.000 |
| What was Google Cloud's revenue for fiscal year 2024? | GOOGL | 0.000 | 1.000 | 0.500 |
| What regulatory and legal risks does Alphabet disclose … | GOOGL | 0.000 | 0.250 | — |
| What is Alphabet's primary source of revenue according … | GOOGL | 0.000 | 1.000 | — |
| How much did Alphabet spend on research and development… | GOOGL | 0.000 | 1.000 | — |
| How does Alphabet describe its artificial intelligence … | GOOGL | 0.000 | 1.000 | 0.000 |
| What was Amazon's total net sales for fiscal year 2024? | AMZN | 0.000 | 1.000 | 0.583 |
| What was Amazon Web Services net sales for fiscal year … | AMZN | 0.000 | 1.000 | 0.950 |
| What competition-related risks does Amazon disclose in … | AMZN | 0.000 | 0.500 | 0.750 |
| How does Amazon describe its fulfilment and logistics n… | AMZN | 0.000 | 1.000 | 0.583 |
| What risks does Amazon identify related to its internat… | AMZN | 0.679 | 0.625 | 1.000 |
| What was Amazon's operating income for fiscal year 2024? | AMZN | 0.000 | 0.333 | 0.750 |
| **平均** |  | **0.185** | **0.696** | **0.697** |
