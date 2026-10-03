# 0002. Local-first runtime; AWS is optional

- Status: Accepted
- Date: 2026-10-03
- Deciders: @Ming

## Context

Version 0.1 of the design deployed every part to AWS from the first milestone: CDK stacks, VPC, Aurora, Lambda, Cognito and CloudFront. It used Amazon Bedrock for both the model and the embeddings.

The primary goal is learning MCP, RAG and agents. Cloud infrastructure has its own learning value, but in v0.1 it competed with that goal from week one. The review of v0.1 also found running costs that do not scale to zero: VPC interface endpoints are billed hourly, and Aurora resume adds latency.

## Decision

- The whole system runs on a laptop:
  - `docker compose up` starts Postgres 16 with pgvector;
  - the simulator writes JSONL logs to local files;
  - one model API key is the only external dependency.
- The model is Claude through the Anthropic API by default, behind a `ModelClient` interface. Amazon Bedrock is a second implementation of that interface.
- Embeddings use a local open model by default, behind an `Embedder` interface, so ingestion runs offline at no cost. Hosted embedders (Voyage AI, Bedrock Titan) are alternatives, compared in T4.4.
- AWS deployment is an optional final milestone (DESIGN.md sections 9 and 10, M7). It starts only after the local system meets its targets.

## Alternatives considered

| Option | Why not chosen |
| --- | --- |
| AWS from day one (v0.1) | Slow feedback loop, ongoing cost, and infrastructure work in the critical path of every milestone |
| Bedrock as the only model provider | Needs an AWS account and model access before any agent code can run. The Anthropic API is a single key. |
| Hosted embeddings only | Every re-ingest during chunking experiments costs money and needs network access |

## Consequences

- **Easier:**
  - fast iteration;
  - zero idle cost;
  - anyone can clone and run the project;
  - unit tests and most evaluations run without cloud credentials.
- **Harder:** cloud-specific behaviour, such as CloudWatch Logs limits, Lambda cold starts and Bedrock regional availability, is only discovered in M7. The adapter interfaces (`LogSource`, `ModelClient`, `Embedder`) keep that late discovery contained.
- **Revisit if:** a learning goal needs a managed AWS feature early, for example Bedrock Guardrails or AgentCore. That feature can be added behind its interface without moving the rest of the system to AWS.
