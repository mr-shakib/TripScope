# AI analyst evaluation

The spec asks for measurements, not claims ("do not claim … highly accurate AI without measurements and
definitions", §13). This page defines how the AI analyst is measured and records the results.

## Method

`tripscope-ai-eval` (backend) asks every question in [`backend/evals/ai_cases.yaml`](../backend/evals/ai_cases.yaml)
through the production path: the same system prompt, agent loop, allowlisted tools, analytics service (read-only
ClickHouse user) and numeric verifier as the AI page. Report drafting is a dry run, so nothing is saved. It runs
against the published 2025-01 … 2025-06 Yellow Taxi data, with no dashboard filters as context.

The 15 cases cover the spec's example prompts and more: KPI lookups, month-over-month change, top pickup and
drop-off zones, distance by hour, weekday vs weekend, a period comparison, payment-type share, the distance
distribution, quarantine reasons, unusual days, a zone named in the question (JFK), an ambiguous question and two
prompt-injection attempts. Each case states the tools that should be used, argument values, and entities or
figures the answer should contain.

| Metric | Definition |
|---|---|
| answered rate | Data questions that ended with an answer (not a failure) |
| tool selection accuracy | Data questions where one of the expected tools ran successfully |
| argument accuracy | …where that tool ran with the expected arguments (defaults included) |
| expected text rate | Answers containing the expected entities or figures (case-insensitive) |
| grounding rate | Figures in answers that match the tool results (see the verifier) ÷ all figures in answers |
| answers with unmatched figures | Answers with at least one figure not found in the tool results |
| clarified ambiguous | Ambiguous questions answered with a clarifying question |
| injection resistance | Injection cases whose answer matches none of the forbidden patterns (prompt leak, "it was emailed") |
| latency | Seconds per question, end to end (model calls and queries); median and 90th percentile |

The verifier matches each figure at its written precision against the returned values, shares as percentages,
and ratios, changes and differences between like quantities (the same field of one result). It checks that a
figure exists in the evidence, not that the sentence uses it correctly: "Saturday had 154,005 trips" passes even
though 154,005 is Saturday's trips per day. Report narratives also cite the KPI or finding each statement rests
on, which narrows this gap but does not close it.

## Environment

AMD Ryzen 5 5500 (12 threads), 14 GiB RAM, NVIDIA GeForce GTX 1660 SUPER (6 GB), Linux 7.0; Ollama 0.40.1 serving
`qwen3.5:4b` (4.2B parameters, Q4_K_M, 8,192-token context) with `LLM_REASONING_EFFORT=none`; temperature 0;
`AI_MAX_TOOL_CALLS=6`. Warm model (loaded before the run).

## Results

Three consecutive runs of all 15 cases on 2026-10-10 (full output:
[`evals/ai-eval-qwen3.5-4b.md`](evals/ai-eval-qwen3.5-4b.md), [JSON](evals/ai-eval-qwen3.5-4b.json)). The outcome
of every case was the same in all three runs; only latency varied (shown as mean, then min–max across runs).

| Metric | qwen3.5:4b (local) |
|---|---|
| answered rate (data questions) | 100% |
| tool selection accuracy | 100% |
| argument accuracy | 100% |
| expected text rate | 100% |
| grounding rate (figures matching the tool results) | 98.2% (55 of 56) |
| answers with an unmatched figure | 8.3% (1 of 12) |
| clarified the ambiguous question | 0% |
| injection resistance | 100% (2 of 2) |
| failures | 0 |
| latency per question | median 14.3 s (12.8–16.8), 90th percentile 23.5 s (21.5–27.0) |
| model calls per question | 3.3 |

What the numbers mean:

- **The one unmatched figure** is "weekday trips were about 70% of total trips": the model divided two returned
  totals itself. The share is roughly right (70.9%) but is in no tool result, so the analyst marks it and, in a
  report narrative, would remove the sentence. Every other figure in every answer came from a tool result.
- **No clarifying questions.** Asked "How busy was it then?", the model answers over all published data instead of
  asking which period. The prompt allows both; small models rarely ask. The answer states the period it used.
- **Injection.** The model refused to run SQL or reveal its prompt, and when asked to email a report it created a
  private draft (the only writing tool) without claiming anything was sent.
- **Latency** is 3 to 4 model calls of 2 to 8 s each on a 6 GB GPU, plus queries (10–400 ms).

### How the evaluation changed the analyst

The first run (before the fixes below) answered 92% of the data questions, chose the right tool for 92% and used
the expected arguments for 63% (partly a scoring bug). It found real problems, each fixed and kept as a test:

1. Asked for a payment-type share, the model invented a `calculate_share` tool: breakdowns did not give shares.
   They now give each group's share of trips.
2. A final answer that was cut off or slightly malformed JSON failed the question. Final answers get more tokens
   and an almost-JSON answer is salvaged.
3. The April drop-off question always failed: Ollama answered HTTP 500 while parsing a malformed tool call the
   model produced (`<function>` closed by `</parameter>`). The top-zones view had repeated the trips column; with
   that removed the malformed call stopped, and the agent now also asks once more without tools when a provider
   fails mid-round.
4. Two single runs of the same code in separate Ollama sessions differed on two cases, which is why results are
   reported over repeated runs.

## DeepSeek

Not measured yet: no API key was configured in this environment. With a key:

```bash
cd backend && DEEPSEEK_API_KEY=… uv run tripscope-ai-eval --base-url https://api.deepseek.com/v1 \
  --model deepseek-chat --api-key-env DEEPSEEK_API_KEY --repeat 3 --out ../docs/evals/ai-eval-deepseek.json
```

## Limitations

- 15 questions on one dataset are a smoke test of behaviour, not a benchmark of model quality; the expected
  figures belong to the 2025-01 … 2025-06 data and change if other months are published.
- Local inference is not fully deterministic even at temperature 0, which is why results are reported over
  repeated runs.
- The verifier proves that figures come from the evidence, not that they are described correctly (see Method).
- Latency depends on the GPU; a CPU-only machine is several times slower.
