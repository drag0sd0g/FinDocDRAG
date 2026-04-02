[日本語](evaluation-results.ja.md)

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

## Evaluation Run — 2026-04-02 13:23:39 UTC

**Samples evaluated:** 24 / 30  
**Full report:** `services/eval/results/eval_report_2026-04-02_13-23-39_UTC.json`

| Question | Ticker | Answer Relevancy | Faithfulness | Context Precision |
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
| **Mean** |  | **0.185** | **0.696** | **0.697** |
