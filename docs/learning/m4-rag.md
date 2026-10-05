# M4 — RAG over tickets and runbooks: learning note

> Draft by Claude, from the M4 work. The agent eval with and without RAG is **PENDING** a run against the model API; the commands are given in place.

Milestone M4 · Tasks T4.1–T4.4 · October 2026

## What I built

| Task | Result |
| --- | --- |
| T4.1 Ingestion | `Embedder` interface (local `bge-small` by default, `minilm`, and a hashing embedder for tests); the `knowledge_chunk` table (migration 0006); idempotent `sr-ingest` |
| T4.2 Search | Hybrid search (vector and full text, fused by rank); the `search_knowledge` and `get_runbook` tools |
| T4.3 Labelled queries | 40 hand-written queries; recall@5, MRR and runbook@5 |
| T4.4 Experiments | Every embedder, chunk size, search mode and filter, each on a scratch database |

## How retrieval works, in one paragraph

`sr-ingest` reads tickets and runbooks, masks patient identifiers, and cuts them into chunks: one per ticket, and runbook sections merged up to about 500 tokens. It embeds each chunk with a small open model on the laptop and stores the vector with a full-text index. A search ranks chunks twice, by vector similarity and by word match, then fuses the two rankings by rank (reciprocal rank fusion), so their scores never need calibrating. The agent sees at most five chunks per search, as context, never as evidence.

## What I learned

### Hybrid search earns its place by finding runbooks

On the 40 labelled queries (`bge-small`, 2,000-character chunks):

| Mode | recall@5 | MRR | runbook@5 |
| --- | --- | --- | --- |
| Vector | 0.900 | 0.875 | 0.425 |
| Keyword | 0.900 | 0.772 | 0.825 |
| **Hybrid** | **0.900** | **0.875** | **0.725** |

- **Vector search finds tickets:** they are written the way CS asks.
- **Keyword search finds runbooks:** they use the technical terms the query contains ("401", "manual", "timeout").
- **Hybrid keeps the best of both.** "Recall" alone would have hidden the difference, so define the measure before running the experiment. For a single-label query with about 30 relevant tickets, recall@5 means "found the right kind of knowledge" (a relevant hit in the top five); runbook@5 asks for the authoritative source.

### Smaller chunks are not automatically better

At 400 characters, runbooks split into sections and runbook@5 falls from 0.725 to 0.600. A runbook split into sections loses the context that made it relevant. Today's runbooks are about 1,300 characters, so the default chunk is a whole runbook.

### Retrieval reproduces the traps

All four misses are F00 "it recovered" queries. "The first push got a 401 but the token refreshed" retrieves F07 tickets; "the first hand-off failed but the resend worked" retrieves F04. Text about a failure sits close to text about the same failure that recovered.

Searching by clinic name is imprecise too: "Kea Children's Specialists" brings back other "Children's Specialists" clinics. That is exactly the `misleading_ticket` distractor. So the tool descriptions and the prompt say knowledge is context, never evidence about this referral, and that a ticket about the same clinic may describe a different cause.

### A filter is only as good as the guess behind it

Filtering by the labelled code takes recall@5 to 1.000. That is an upper bound: with a wrong guess, the filter would hide the knowledge that corrects it. The agent is not told to filter.

### Privacy at ingestion, proven at the tool

- **Masking comes before chunking,** so identifiers never reach the embedder, the vector store or a search result. On the default seed, 51 tickets carry a synthetic NHI and no chunk does.
- **The leak test now searches real ingested tickets** that carried identifiers, and checks they come back masked.

### Small things that would have bitten later

- **Only compare like with like.** Vector search compares only chunks embedded by the query's own model; a server configured with another embedder finds nothing rather than nonsense.
- **Query safety.** Only `[a-z0-9]` words reach `to_tsquery`, so a query cannot inject operators.
- **Idempotent ingestion.** A content hash per chunk covers the text and the embedder, so a re-run takes 0.1 s, and a new embedder or chunk size re-embeds everything.
- **The model downloads once** (about 64 MB), loads on the first search (about 1.5 s), then answers in 20 to 40 ms. CI caches it.
- **Experiments on scratch databases.** They never touch the development data, and all 24 configurations take about 35 s.

## What I measured

| Measure | Value |
| --- | --- |
| Chunks | 394 (377 tickets, 17 runbooks) at 2,000 characters; 425 at 400 |
| Ingest | about 13 s with `bge-small` (after a one-time download); a re-run takes 0.1 s |
| recall@5 / MRR / runbook@5 (default) | 0.900 / 0.875 / 0.725 (target recall@5 ≥ 0.8) |
| `minilm` instead (hybrid) | 0.900 / 0.833 / 0.675 |
| Agent accuracy with RAG against without | **PENDING**: `uv run sr-evals run --label rag` against the `--no-rag` baseline |
| Hosted embedder (Voyage AI) | Not run: it needs its own key and budget |

## What RAG changed for the agent

**PENDING.** Compare the two reports (the second is compared with the first automatically when they share a case set).

- **The questions to answer:**
  - Did accuracy on distractors fall? Look at `misleading_ticket` and the recovered cases.
  - How many of the eight tool calls did search use?
  - Did cost and latency rise?
- `TODO(me)`: your own view of whether RAG is worth it for this agent.

## Interview talking points

- "I defined the retrieval measures before running experiments. Classic recall would have hidden that vector search almost never surfaces the runbook; hybrid search fixed that."
- "Retrieval reproduced the very traps the agent must avoid: 'it recovered' queries retrieve the failure they resemble. So knowledge is labelled as context, never evidence, in the tool description and the prompt."
- "Patient identifiers are masked before chunking, and the leak test searches real tickets that carried them."
