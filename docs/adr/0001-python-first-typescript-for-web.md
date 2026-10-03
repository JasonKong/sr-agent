# 0001. Python for the backend, TypeScript for the web console

- Status: Accepted
- Date: 2026-10-03
- Deciders: @Ming

## Context

SR-Agent has six parts: a data simulator, an MCP server, a knowledge retrieval layer (RAG), a diagnosis agent, an evaluation harness and a web console. Version 0.1 of the design used TypeScript for the MCP server and the infrastructure, and Python for the agent, the simulator and the evaluations. That meant two toolchains, two sets of lint, type-check and test tools, and shared schemas generated for both languages.

The primary goal of the project is learning MCP, RAG and agents (DESIGN.md section 1). Every hour spent maintaining a second toolchain is an hour not spent on that.

Two facts about LLM applications shape the decision:

1. **The model runs remotely.** The application mostly sends HTTP requests, handles JSON, streams output and orchestrates tool calls. Language performance hardly matters. What matters is the ecosystem and where the code runs.
2. **The mainstream ecosystem has two halves.**
   - Python owns the data and model side: embeddings, vector tooling, evaluation libraries, data generation and notebooks. This comes from a decade of machine-learning research.
   - TypeScript owns the product side: browsers, web and desktop apps, serverless and edge runtimes, and streaming UIs. It also has the largest developer population.
   - Model providers and MCP ship first-class SDKs for both. The MCP specification's schema is itself written in TypeScript.

## Decision

- **Python 3.12** for the simulator, MCP server (official MCP Python SDK), RAG layer, agent and evaluations, in one uv workspace.
- **TypeScript** only for the web console (React, Vite), because it runs in the browser. Later, if the optional AWS milestone happens, also for the CDK app, because CDK's TypeScript support is the most complete.
- **Components talk only through protocols:** MCP between the agent and the MCP server, and HTTP with JSON schemas between the web console and the agent API. No component imports another component's code across the language boundary.
- **Every model boundary is validated at runtime**, with Pydantic in Python and Zod in TypeScript. Model output is untrusted input, and static types cannot check it.

## Alternatives considered

| Option | Why not chosen |
| --- | --- |
| TypeScript MCP server, Python agent and evals (v0.1) | Two toolchains for the core of the system. The simulator and the MCP server would share database schemas across languages, which needs code generation. The learning benefit of two MCP SDKs does not justify that cost. |
| TypeScript everywhere | Embeddings, data generation and evaluation tooling are weaker. Local embedding models and data analysis are far easier in Python. |
| Python everywhere, including the UI (for example Streamlit or Gradio) | Fast for demos, but a poor fit for a polished, accessible CS console. It would also skip a realistic frontend-to-API boundary. |
| Another language (Go, Java, C#, PHP) | All can build MCP servers and call model APIs, but their AI tooling and examples lag behind. That slows learning. |

## Consequences

- **Easier:** one toolchain for the backend (uv, Ruff, mypy, pytest); Pydantic models shared across the simulator, MCP server, agent and evals; direct access to Python embedding and evaluation libraries.
- **Harder:** the web console must keep its own TypeScript types for the agent API. They are generated from the API's OpenAPI schema rather than hand-written.
- **Reversible:** because components meet only at MCP and HTTP, any one of them could be rewritten in another language without touching the others. For example, an MCP server inside an existing PHP application could replace the Python one through the same protocol.
- **Revisit if:** the MCP Python SDK falls behind the specification in a way that blocks a feature this project needs, or the project is adopted by a team whose main language is different (DESIGN.md section 11).

## Notes for explaining this decision

> In an LLM application the model runs remotely, so the app is mostly I/O and JSON orchestration. The language choice is driven by ecosystem and deployment target, not performance.
>
> Python dominates the data and ML side, TypeScript the product side, and both have first-class SDKs for the model providers and MCP. I used Python for the simulator, MCP server, agent and evals, and TypeScript for the browser console.
>
> Because the parts meet only at MCP and HTTP with schemas, each language choice is independent and reversible. The one rule that applies in both languages is runtime validation at every model boundary.
