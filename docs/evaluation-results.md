# Evaluation Results

RAG quality metrics produced by the `ragas` evaluation harness (`services/eval/run_eval.py`).
Each run appends a new section below.

## How to Run

```bash
# 1. Start the stack — choose one:
make run          # includes Ollama (needs ~6 GB RAM + model download)
make run-remote   # without Ollama; set LLM_BACKEND=claude or openai first

# 2. Export credentials for the ragas judge (pick one; priority: Anthropic > OpenAI > Ollama)
export ANTHROPIC_API_KEY=sk-ant-…   # preferred judge: claude-haiku-4-5
export OPENAI_API_KEY=sk-…          # alternative judge (if no Anthropic key)
# No key set → falls back to Ollama running locally

# 3. Optionally configure the Query API backend
export LLM_BACKEND=claude           # or: openai / ollama

# 4. Install eval dependencies
cd services/eval && pip install -r requirements.txt

# 5. Run
make eval
# or directly: python services/eval/run_eval.py
```

## Metrics Explained

| Metric | What it measures | Needs LLM? |
|---|---|---|
| `answer_relevancy` | How relevant the generated answer is to the question | Yes |
| `faithfulness` | Whether all claims in the answer are grounded in the retrieved context | Yes |
| `context_precision` | Whether the retrieved chunks ranked highest are the most relevant | Yes |

All three metrics are computed by `ragas` using an LLM as judge (see ADR-009). The judge is selected automatically: `ANTHROPIC_API_KEY` → `claude-haiku-4-5`; `OPENAI_API_KEY` → OpenAI; neither → Ollama local fallback.
Scores range from 0 to 1; higher is better. Target thresholds: ≥ 0.70 for all three (NFR-8).

---
