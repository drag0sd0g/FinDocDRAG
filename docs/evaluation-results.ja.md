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
