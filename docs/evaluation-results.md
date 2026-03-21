# Evaluation Results

RAG quality metrics produced by the `ragas` evaluation harness (`services/eval/run_eval.py`).
Each run appends a new section below.

## How to Run

```bash
# 1. Start the stack (stop ollama to save RAM if using OpenAI backend)
docker compose up -d
docker compose stop ollama          # optional — frees ~6 GB on 8 GB machines

# 2. Export credentials
export OPENAI_API_KEY=sk-...        # required for ragas LLM judge
export LLM_BACKEND=openai           # optional: use OpenAI for Query API too

# 3. Install eval dependencies
cd services/eval && pip install -r requirements.txt

# 4. Run
make eval
# or directly: python services/eval/run_eval.py
```

## Metrics Explained

| Metric | What it measures | Needs LLM? |
|---|---|---|
| `answer_relevancy` | How relevant the generated answer is to the question | Yes |
| `faithfulness` | Whether all claims in the answer are grounded in the retrieved context | Yes |
| `context_precision` | Whether the retrieved chunks ranked highest are the most relevant | Yes |

All three metrics are computed by `ragas` using an OpenAI LLM as judge (see ADR-009).
Scores range from 0 to 1; higher is better. Target thresholds: ≥ 0.70 for all three (NFR-8).

---
