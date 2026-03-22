#!/usr/bin/env python3
"""RAG evaluation harness using the ragas library.

Calls the live Query API for every sample in eval_dataset.json, collects
answers and retrieved contexts, then computes ragas metrics and persists
the results as JSON and Markdown.

Usage (with the full Docker Compose stack running):

    python run_eval.py

    # Override defaults:
    python run_eval.py \\
        --api-url http://localhost:8000 \\
        --api-key dev-key-1 \\
        --top-k 5

Environment variables:

    Judge LLM (priority order — first one set wins):

    ANTHROPIC_API_KEY  Use Claude as the ragas judge LLM (preferred).
                       Model defaults to claude-haiku-4-5; override with
                       CLAUDE_JUDGE_MODEL.

    OPENAI_API_KEY     Use OpenAI as the ragas judge LLM. ragas picks
                       this up automatically — no extra configuration needed.

                       MEMORY NOTE: On a memory-constrained machine (e.g. 8 GB
                       M1) set OPENAI_API_KEY so that LLM compute is offloaded
                       to the OpenAI API rather than running Ollama locally.
                       You can then stop the ollama container before evaluating:
                           docker compose stop ollama

    (neither set)      Falls back to Ollama running locally. Override the
                       Ollama base URL and model with EVAL_OLLAMA_URL and
                       EVAL_OLLAMA_MODEL.

    CLAUDE_JUDGE_MODEL  Claude model name for judging (default: claude-haiku-4-5).
                        Only used when ANTHROPIC_API_KEY is set.
    EVAL_OLLAMA_URL     Ollama base URL for the judge (default: http://localhost:11434).
                        Only used when neither API key is set.
    EVAL_OLLAMA_MODEL   Ollama model for the judge (default: mistral:7b).
                        Only used when neither API key is set.

    EVAL_API_URL     Override the Query API base URL (default: http://localhost:8000)
    EVAL_API_KEY     Override the X-API-Key header value (default: dev-key-1)

References:
    TDD NFR-7  — evaluation dataset >= 30 Q&A pairs across >= 5 companies
    TDD NFR-8  — ragas metrics: context_precision, faithfulness, answer_relevancy
    TDD NFR-9  — results persisted as JSON + Markdown
    TDD Section 5.2.4 — Evaluation Harness design
    ADR-009    — rationale for choosing ragas
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiohttp

# ── Paths ─────────────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).parent
DATASET_PATH = SCRIPT_DIR / "eval_dataset.json"
RESULTS_DIR = SCRIPT_DIR / "results"
EVAL_RESULTS_MD = SCRIPT_DIR.parent.parent / "docs" / "evaluation-results.md"

# ── Defaults ──────────────────────────────────────────────────────────────────

DEFAULT_API_URL = os.getenv("EVAL_API_URL", "http://localhost:8000")
DEFAULT_API_KEY = os.getenv("EVAL_API_KEY", "dev-key-1")
DEFAULT_TOP_K = 5

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ── Query API client ──────────────────────────────────────────────────────────


async def _call_query_api(
    session: aiohttp.ClientSession,
    question: str,
    ticker: str | None,
    api_url: str,
    api_key: str,
    top_k: int,
) -> dict[str, Any]:
    """POST /v1/query and return the parsed response body."""
    payload: dict[str, Any] = {"question": question, "top_k": top_k}
    if ticker:
        payload["ticker_filter"] = ticker

    async with session.post(
        f"{api_url}/v1/query",
        json=payload,
        headers={"X-API-Key": api_key, "Content-Type": "application/json"},
        timeout=aiohttp.ClientTimeout(total=120),
    ) as resp:
        resp.raise_for_status()
        return await resp.json()  # type: ignore[no-any-return]


async def _check_api_health(api_url: str) -> bool:
    """Return True if the Query API /health endpoint is reachable."""
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.get(
                f"{api_url}/health",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp,
        ):
            return resp.status == 200
    except Exception:
        return False


async def collect_responses(
    dataset: list[dict[str, Any]],
    api_url: str,
    api_key: str,
    top_k: int,
) -> list[dict[str, Any]]:
    """Call the Query API for each sample and return enriched records."""
    enriched: list[dict[str, Any]] = []

    async with aiohttp.ClientSession() as session:
        for idx, sample in enumerate(dataset, 1):
            question = sample["question"]
            ticker = sample.get("ticker")
            logger.info("[%d/%d] %s", idx, len(dataset), question[:72])

            try:
                resp = await _call_query_api(
                    session, question, ticker, api_url, api_key, top_k
                )
                # text_preview (200 chars) is what the API exposes per FR-17;
                # it is sufficient for ragas context scoring.
                contexts = [
                    s.get("text_preview", "") for s in resp.get("sources", [])
                ]
                enriched.append(
                    {
                        "question": question,
                        "ground_truth": sample["ground_truth"],
                        "ticker": ticker,
                        "answer": resp.get("answer") or "",
                        "contexts": contexts,
                        "sources": resp.get("sources", []),
                        "model": resp.get("model", ""),
                        "timing": resp.get("timing", {}),
                        "degraded": resp.get("degraded", False),
                    }
                )
            except Exception as exc:
                logger.warning("Query failed for '%s…': %s", question[:50], exc)
                enriched.append(
                    {
                        "question": question,
                        "ground_truth": sample["ground_truth"],
                        "ticker": ticker,
                        "answer": "",
                        "contexts": [],
                        "sources": [],
                        "model": "",
                        "timing": {},
                        "degraded": True,
                        "error": str(exc),
                    }
                )

    return enriched


# ── ragas evaluation ──────────────────────────────────────────────────────────


def _resolve_ragas_llm() -> tuple[Any, str]:
    """Return (ragas_llm, judge_label) based on available API keys.

    Priority: ANTHROPIC_API_KEY → OPENAI_API_KEY → Ollama (local fallback).
    Returns (None, "OpenAI (auto)") when OPENAI_API_KEY is set, since ragas
    picks that up automatically without an explicit LLM object.
    Raises ImportError (with a logger.error) when no option is available.
    """
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    openai_key = os.getenv("OPENAI_API_KEY")
    ollama_url = os.getenv("EVAL_OLLAMA_URL", "http://localhost:11434")
    ollama_model = os.getenv("EVAL_OLLAMA_MODEL", "mistral:7b")

    if anthropic_key:
        try:
            from langchain_anthropic import ChatAnthropic
            from ragas.llms import LangchainLLMWrapper

            claude_judge_model = os.getenv("CLAUDE_JUDGE_MODEL", "claude-haiku-4-5")
            ragas_llm = LangchainLLMWrapper(
                ChatAnthropic(model=claude_judge_model, api_key=anthropic_key)
            )
            return ragas_llm, f"Anthropic/{claude_judge_model}"
        except ImportError:
            logger.warning(
                "langchain-anthropic not installed — trying next option. "
                "Run: pip install langchain-anthropic"
            )

    if openai_key:
        # ragas picks up OPENAI_API_KEY automatically; no explicit LLM object needed
        return None, "OpenAI (auto)"

    # Fallback: local Ollama (heavy on RAM — stop other services first)
    try:
        from langchain_community.chat_models import ChatOllama
        from ragas.llms import LangchainLLMWrapper

        ragas_llm = LangchainLLMWrapper(
            ChatOllama(model=ollama_model, base_url=ollama_url)
        )
        logger.warning(
            "No API key found — falling back to local Ollama (%s) for ragas judge. "
            "RAM usage will be high. Set ANTHROPIC_API_KEY for a lighter alternative.",
            ollama_model,
        )
        return ragas_llm, f"Ollama/{ollama_model} (local)"
    except ImportError:
        logger.error(
            "langchain-community not installed and no API key found. "
            "Run: pip install langchain-community  OR  export ANTHROPIC_API_KEY=sk-ant-..."
        )
        raise


def _build_ragas_dataset(
    valid: list[dict[str, Any]],
    ragas_llm: Any,
    judge_label: str,
) -> tuple[Any, list[Any], list[str]]:
    """Build a ragas EvaluationDataset and metric list from valid responses.

    Returns (dataset, metrics, metric_names).
    """
    from ragas import EvaluationDataset, SingleTurnSample
    from ragas.metrics import AnswerRelevancy, ContextPrecision, Faithfulness

    logger.info(
        "Building ragas EvaluationDataset from %d valid samples (judge: %s) …",
        len(valid),
        judge_label,
    )
    samples = [
        SingleTurnSample(
            user_input=r["question"],
            retrieved_contexts=(
                r["contexts"] if r["contexts"] else ["No context retrieved."]
            ),
            response=r["answer"],
            reference=r["ground_truth"],
        )
        for r in valid
    ]
    dataset = EvaluationDataset(samples=samples)

    if ragas_llm is not None:
        metrics: list[Any] = [
            AnswerRelevancy(llm=ragas_llm),
            Faithfulness(llm=ragas_llm),
            ContextPrecision(llm=ragas_llm),
        ]
    else:
        # OpenAI path: ragas reads OPENAI_API_KEY from the environment automatically
        metrics = [AnswerRelevancy(), Faithfulness(), ContextPrecision()]

    metric_names = ["answer_relevancy", "faithfulness", "context_precision"]
    return dataset, metrics, metric_names


def _extract_metric_scores(
    result: Any,
    metric_names: list[str],
) -> dict[str, list[float]]:
    """Extract per-sample scores from a ragas Result object into a plain dict."""
    result_df = result.to_pandas()
    scores: dict[str, list[float]] = {}
    for name in metric_names:
        if name in result_df.columns:
            scores[name] = result_df[name].tolist()
        else:
            logger.warning("Metric '%s' not found in ragas output.", name)
    return scores


def compute_ragas_metrics(
    responses: list[dict[str, Any]],
) -> tuple[dict[str, list[float]], list[str]]:
    """Compute ragas metrics and return (per-sample scores dict, metric names).

    Selects a judge LLM via _resolve_ragas_llm() (Anthropic → OpenAI → Ollama),
    builds the EvaluationDataset, runs ragas evaluate(), and returns per-sample
    scores alongside the metric name list.
    """
    try:
        from ragas import evaluate
    except ImportError as exc:
        logger.error("Cannot import ragas: %s", exc)
        logger.error("Run:  pip install -r requirements.txt")
        return {}, []

    valid = [r for r in responses if r.get("answer") and not r.get("error")]
    if not valid:
        logger.error(
            "No successful Query API responses to evaluate. "
            "Ensure the stack is running and filings have been ingested."
        )
        return {}, []

    try:
        ragas_llm, judge_label = _resolve_ragas_llm()
    except ImportError:
        return {}, []

    dataset, metrics, metric_names = _build_ragas_dataset(valid, ragas_llm, judge_label)

    logger.info("Running ragas evaluation (metrics: %s) …", ", ".join(metric_names))
    try:
        result = evaluate(dataset=dataset, metrics=metrics)
    except Exception as exc:
        logger.error("ragas evaluate() failed: %s", exc)
        return {}, []

    return _extract_metric_scores(result, metric_names), metric_names


# ── Result persistence ────────────────────────────────────────────────────────


def _mean(values: list[float]) -> float:
    finite = [v for v in values if not math.isnan(v)]
    return sum(finite) / len(finite) if finite else float("nan")


def save_json_report(
    responses: list[dict[str, Any]],
    scores: dict[str, list[float]],
    metric_names: list[str],
    run_at: str,
) -> Path:
    """Write the full per-sample report to results/eval_report_<ts>.json (NFR-9)."""
    RESULTS_DIR.mkdir(exist_ok=True)
    ts = run_at.replace(":", "-").replace(" ", "_")
    report_path = RESULTS_DIR / f"eval_report_{ts}.json"

    valid = [r for r in responses if r.get("answer") and not r.get("error")]

    report: dict[str, Any] = {
        "run_at": run_at,
        "total_samples": len(responses),
        "evaluated_samples": len(valid),
        "aggregate_metrics": {
            name: {
                "scores": scores.get(name, []),
                "mean": _mean(scores.get(name, [])),
            }
            for name in metric_names
        },
        "samples": [
            {
                "question": r["question"],
                "ticker": r.get("ticker"),
                "ground_truth": r["ground_truth"],
                "answer": r.get("answer", ""),
                "contexts_count": len(r.get("contexts", [])),
                "model": r.get("model", ""),
                "timing": r.get("timing", {}),
                "degraded": r.get("degraded", False),
                **{
                    name: (
                        scores[name][i]
                        if i < len(scores.get(name, []))
                        else None
                    )
                    for name in metric_names
                },
            }
            for i, r in enumerate(valid)
        ],
    }

    report_path.write_text(json.dumps(report, indent=2))
    logger.info("JSON report → %s", report_path)
    return report_path


def append_markdown_summary(
    scores: dict[str, list[float]],
    metric_names: list[str],
    responses: list[dict[str, Any]],
    run_at: str,
    report_path: Path,
) -> None:
    """Append a results table to docs/evaluation-results.md (NFR-9)."""
    valid = [r for r in responses if r.get("answer") and not r.get("error")]

    lines: list[str] = [
        f"\n## Evaluation Run — {run_at}\n",
        f"**Samples evaluated:** {len(valid)} / {len(responses)}  ",
        f"**Full report:** `{report_path.relative_to(SCRIPT_DIR.parent.parent)}`\n",
    ]

    if metric_names and valid and scores:
        col_headers = ["Question", "Ticker"] + [
            m.replace("_", " ").title() for m in metric_names
        ]
        lines.append("| " + " | ".join(col_headers) + " |")
        lines.append("| " + " | ".join(["---"] * len(col_headers)) + " |")

        for i, r in enumerate(valid):
            q = r["question"]
            q_cell = (q[:55] + "…") if len(q) > 56 else q
            row = [q_cell, r.get("ticker") or "—"]
            for name in metric_names:
                val = scores.get(name, [])[i] if i < len(scores.get(name, [])) else None
                row.append(
                    f"{val:.3f}" if (val is not None and val == val) else "—"
                )
            lines.append("| " + " | ".join(row) + " |")

        mean_row = ["**Mean**", ""] + [
            f"**{_mean(scores.get(name, [])):.3f}**" for name in metric_names
        ]
        lines.append("| " + " | ".join(mean_row) + " |")
    else:
        lines.append(
            "_No metrics computed. "
            "Check that OPENAI_API_KEY is set and the Query API returned answers._\n"
        )

    EVAL_RESULTS_MD.parent.mkdir(parents=True, exist_ok=True)
    if not EVAL_RESULTS_MD.exists():
        EVAL_RESULTS_MD.write_text(
            "# Evaluation Results\n\n"
            "RAG quality metrics produced by the `ragas` evaluation harness "
            "(`services/eval/run_eval.py`).\n"
            "Each run appends a new section below.\n"
        )

    with EVAL_RESULTS_MD.open("a") as fh:
        fh.write("\n".join(lines) + "\n")

    logger.info("Markdown summary → %s", EVAL_RESULTS_MD)


# ── CLI ───────────────────────────────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--api-url",
        default=DEFAULT_API_URL,
        help="Query API base URL (default: %(default)s)",
    )
    parser.add_argument(
        "--api-key",
        default=DEFAULT_API_KEY,
        help="X-API-Key header value (default: %(default)s)",
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DATASET_PATH,
        help="Path to eval_dataset.json (default: %(default)s)",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=DEFAULT_TOP_K,
        help="Number of chunks to retrieve per query (default: %(default)s)",
    )
    return parser.parse_args()


async def _main(args: argparse.Namespace) -> int:
    # ── 1. Load dataset ───────────────────────────────────────────────────────
    if not args.dataset.exists():
        logger.error("Dataset not found: %s", args.dataset)
        return 1

    dataset: list[dict[str, Any]] = json.loads(args.dataset.read_text())
    logger.info("Loaded %d samples from %s", len(dataset), args.dataset.name)

    # ── 2. Health-check the Query API ─────────────────────────────────────────
    logger.info("Checking Query API at %s …", args.api_url)
    if not await _check_api_health(args.api_url):
        logger.error(
            "Query API is not reachable at %s/health.\n"
            "Start the stack first:  docker compose up -d",
            args.api_url,
        )
        return 1

    # ── 3. Collect Query API responses ────────────────────────────────────────
    responses = await collect_responses(
        dataset, args.api_url, args.api_key, args.top_k
    )
    answered = sum(1 for r in responses if r.get("answer") and not r.get("error"))
    logger.info("Received answers for %d / %d questions.", answered, len(responses))

    if answered == 0:
        logger.error(
            "All queries failed or returned empty answers. "
            "Have filings been ingested?  Try:  curl -X POST %s/v1/ingest",
            args.api_url,
        )
        return 1

    # ── 4. Compute ragas metrics ──────────────────────────────────────────────
    scores, metric_names = compute_ragas_metrics(responses)

    # ── 5. Print aggregate scores to stdout ───────────────────────────────────
    if metric_names and scores:
        print("\n── Aggregate Scores " + "─" * 40)
        for name in metric_names:
            print(f"  {name:<25} {_mean(scores.get(name, [])):.4f}")
        print()

    # ── 6. Persist results ────────────────────────────────────────────────────
    run_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    report_path = save_json_report(responses, scores, metric_names, run_at)
    append_markdown_summary(scores, metric_names, responses, run_at, report_path)

    return 0


def main() -> None:
    args = _parse_args()
    sys.exit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
