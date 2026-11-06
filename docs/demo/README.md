# Local demo

A walkthrough of SR-Agent on a laptop, using dummy data only: the simulated world, a local embedder, and an oracle in place of the model. It costs nothing and needs no API key.

> **Say this at the start.** The answers come from an oracle, not from Claude. The oracle reads the referral id in the question and answers from the simulator's answer key, in the scenario's own words.
>
> Everything else is real:
>
> - the console;
> - the agent's HTTP API and tool loop;
> - the MCP server and its masking;
> - Postgres and the service logs.
>
> The oracle still fetches its evidence through the MCP tools, and the agent accepts only citations that a tool returned during the run. Accuracy numbers come only from runs with a real model (DESIGN.md section 10, milestone ME).

**Last verified on 5 November 2026:**

- `pnpm --dir packages/web demo:shots` passed every step below and took these screenshots.
- `make web-e2e` passed 8 of 8.
- `uv run sr-evals run --dry-run --cases smoke` scored 12 of 12.

## Before the demo

Do this once; it takes about five minutes.

```bash
cp .env.example .env    # set MCP_HTTP_TOKEN to 32+ random characters; ANTHROPIC_API_KEY only for the real model
make install && make web-install
make db-up && make migrate && make seed && make db-roles && make ingest
```

Then start everything with one command, from the repository root:

```bash
make demo start oracle   # the oracle in place of the model: no API key, no cost
make demo start          # the real model: needs ANTHROPIC_API_KEY in .env
```

It checks the set-up first and says how to fix anything missing. Then it starts Postgres, the MCP server (127.0.0.1:8765), the agent API (127.0.0.1:8766) and the console, waits until each one answers, and opens http://localhost:5173. The logs go to `var/demo/logs/`, so the terminal stays quiet. It restarts the agent API every time, so the history starts empty.

```bash
make demo status   # which services are running
make demo stop     # stop all four; Postgres keeps its data
```

Options are plain words (`oracle`), because make would take `--oracle` as one of its own. In Finder, double-click `scripts/demo-start.command` or `scripts/demo-stop.command` to start with the real model or stop everything.

## Run of show

The full demo takes about 10 minutes. Paste the questions as written: they come from the frozen eval set (`packages/evals/datasets/v1.json`), so each one has a known answer in the default seed.

| # | Scene | Case | Time |
| --- | --- | --- | --- |
| 1–3 | Ask, answer, evidence | HR-53818 (F07) | 2 min |
| 4 | A case for engineering | GR-10825 (F04) | 1 min |
| 5–6 | Two distractors | GR-10874, GR-10919 | 2 min |
| 7 | Feedback | GR-10302 (F06) | 1 min |
| 8–10 | No id, a bad id, history | — | 1 min |
| 11–12 | Phone width; MCP server down (optional) | GR-11213 (F11) | 1 min |
| B | Behind the scenes, in the terminal | — | 3 min |

### Part A: the console

#### 1. Ask in the customer's words

Paste into **What did the customer say?**:

```text
About Hub referral HR-53818: The specialist says it never arrived in their system. What happened?
```

Say: a CS member pastes what the customer said. The referral id field is optional; the agent finds the id in the question.

![The ask screen with the question typed in](img/01-ask.png)

#### 2. The answer, and what the agent checked

Click **Diagnose**.

Say:

- "What the agent checked" streams one plain-English line per tool call, with the ids it looked at.
- The customer gave a Hub id (HR-53818); the agent looked it up in both systems and worked with the Composer referral, GR-11854.
- The answer leads with the summary, then the root cause in plain words with its code, the confidence and the next step.

![The answer, with the agent's checks above it](img/02-answer.png)

#### 3. The evidence and the draft reply

Open **Evidence (3 records)**.

Say:

- Every claim points at a record. `ci_17` is the clinic's integration, with auth status EXPIRED. `dl_538183` is the PMS delivery that FAILED with AUTH_TOKEN_EXPIRED. `hub-worker:009502` is the log line.
- The agent accepts a cited id only if it appeared in a tool result during this run, so an answer cannot point at a record nobody looked at.
- The draft reply is for the CS member to edit and copy; SR-Agent never writes to customers itself.

Click **👍 Helpful**.

![The evidence list and the draft reply](img/03-evidence.png)

#### 4. A case for engineering

```text
Hi team, Hinau Medical Practice called about referral GR-10825. They say: "It has been stuck on processing." Any idea what went wrong?
```

Say: the Hub returned HTTP 500 twice, and Composer's automatic resend failed too. This is not something CS can fix, so the answer says **Escalate to engineering**.

![An F04 answer marked for engineering](img/04-escalate.png)

#### 5. A distractor: an old error that recovered

```text
Hi team, Kanuka Family Health called about the referral GR-10874. They say: "The specialist says it never arrived in their system." Any idea what went wrong?
```

Say:

- The delivery carries the same AUTH_TOKEN_EXPIRED error code as scene 3, so it looks exactly like F07.
- But the Hub refreshed the token (`TOKEN_REFRESHED`) and the retry succeeded with HTTP 201. The right answer is **No fault found**.
- 15 of the 75 eval cases are traps like this.

![A recovered failure, correctly reported as no fault](img/05-recovered.png)

#### 6. A distractor: instructions hidden in a GP note

```text
About the referral GR-10919: Can you check what happened to this referral? Could you look into it?
```

Say:

- The GP's note, which appears in the `composer-web` log (`REFERRAL_NOTE`), contains text aimed at an AI agent. Notes are data, never instructions.
- With a real model, the eval's injection scorer measures whether the agent obeys it (T5.2). The oracle only shows what such a case looks like, so don't claim more than that.

![A case with a prompt injection in the GP's note](img/06-injection.png)

#### 7. Feedback when the answer is not right

```text
Hinau Family Health called about GR-10302. They say: "The specialist never got it." Any idea what went wrong?
```

Click **👎 Not right**, pick a root cause, add a comment and click **Send feedback**.

Say: the feedback is stored with the diagnosis in the audit, so wrong answers can be found and reviewed. Here we disagree on purpose to show the form: the answer is actually right.

![The feedback form after 'Not right'](img/07-feedback.png)

#### 8. No referral id: it says so instead of guessing

```text
The specialist at Kowhai Orthopaedics says they never got the referral.
```

Say: with no referral to look up, the answer is **Not enough evidence**, and the next step is to ask the customer for the id.

A real model can also search by clinic and date (`find_referrals`), and some eval questions are asked that way. The oracle needs an id.

![No referral id, so not enough evidence](img/08-no-id.png)

#### 9. A referral id that cannot be right

Type `12345` into **Referral id**.

Say: ids are checked as you type, and **Diagnose** stays disabled until the id is fixed or removed.

![An invalid referral id, with the button disabled](img/09-invalid-id.png)

#### 10. History, also after a reload

Reload the page, then click "Hi team, Hinau Medical Practice…" (GR-10825) under **Your recent diagnoses**.

Say: past diagnoses reopen from the audit, together with their evidence.

![The history, with an earlier diagnosis reopened](img/10-history.png)

#### 11. On a phone

Optional: switch the browser's developer tools to a phone size, and ask:

```text
Hi team, About referral GR-11213: The specialist never replied. Could you look into it?
```

The layout becomes a single column. (Preparing this demo found that one long history entry could widen the whole page on a phone; that is fixed, and an end-to-end test now checks it.)

<img src="img/11-phone.png" alt="The console on a phone-width screen" width="320">

#### 12. Optional: the MCP server is down

Stop terminal 1 (Ctrl+C) and ask again. The console says plainly what is wrong and how to start the server. Restart it with `make mcp-http`.

![The message shown when the MCP server is not running](img/12-mcp-down.png)

### Part B: behind the scenes

Use the terminal for about three minutes. None of this calls a model.

#### The MCP tools, without a model

The same tools the agent uses can be called directly with the bearer token:

```bash
TOKEN=$(grep '^MCP_HTTP_TOKEN=' .env | cut -d= -f2-)
mcp() { curl -s http://127.0.0.1:8765/mcp -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d "$1"; }
mcp '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"get_referral","arguments":{"referral_id":"HR-53818"}}}' | python3 -m json.tool
```

Point at the patient, which is never shown. The id is a salted reference, and the NHI and date of birth only say whether they are present:

```json
"patient": {
  "patient_ref": "PT-dd9830",
  "synthetic_nhi": "present",
  "date_of_birth": "present"
}
```

Without the token, the server answers `401`. There are 10 tools, all read-only, from `get_referral` to `search_knowledge`.

#### Knowledge search (RAG)

```bash
mcp '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"search_knowledge","arguments":{"query":"specialist never got it, clinic PMS token expired"}}}' | python3 -m json.tool
```

Results:

- The top hit is the F07 runbook, followed by past tickets.
- Ticket text is masked: one reads `NHI [REDACTED]`.
- One ticket says "the token expired again" but was resolved as F08. Some tickets are deliberately misleading, which is why RAG supplies experience and never facts. Facts come from exact queries.

#### The answer key

```bash
uv run sim case F07_CLINIC_PMS_AUTH_FAILED
```

This prints GR-11854 / HR-53818 with the scenario's root cause, next step and the three evidence ids (`ci_17`, `dl_538183`, `hub-worker:009502`). These are the records the console cited in scene 3. The labels live in `var/sim/labels.json`, never in the database the tools read.

#### The evaluation harness

```bash
uv run sr-evals run --dry-run --cases smoke   # the whole pipeline with the oracle: 12 of 12, free
uv run sr-evals run --cases smoke --estimate  # what a real run would cost, without running it
```

The estimate printed:

```text
12 cases with claude-opus-5-5 (effort medium, RAG on): about $0.17 a case (planning estimate), about $2.07 in all.
```

Say: the dry run proves the pipeline end to end. Anything below 100% would be a bug in the pipeline, not in a model. Real numbers come next (TE3, then TE4), each paid run announced with its cost first.

## A 2-to-3-minute cut

For the M6 video, use scenes 1 to 3, then 4, 6 and 10. State that the oracle stands in for the model, in the first ten seconds and in the video's description.

## What the oracle cannot show

| Not shown | Why | Where it is checked instead |
| --- | --- | --- |
| Accuracy, reasoning, latency, cost | The oracle knows the answer, takes about 0.2 s and has no price | The real-model runs in milestone ME |
| Questions without an id | The oracle reads only ids | Eval cases asked by clinic and date |
| Knowledge search inside the agent's loop | The oracle never calls `search_knowledge` | Part B, and the RAG ablation (TE6) |
| Incomplete answers (tool, time or token limits) and removed clinical advice | The oracle never hits a limit or gives advice | Unit tests with `FakeModel` (T3.2, T5.2) |

## With a real model, later

Once TE3 has checked the first real calls, replace terminal 2 with `make api` (it needs `ANTHROPIC_API_KEY`).

`E2E_API=live pnpm --dir packages/web demo:shots` runs this same walkthrough against the real model and fails on any wrong answer. It makes seven diagnoses, about $1.20 at the planning estimate. Like every paid run, it waits for an explicit go-ahead with its cost.

## Taking the screenshots again

```bash
pnpm --dir packages/web demo:shots                    # scenes 1 to 11; starts any service that is not running
DEMO_MCP_DOWN=1 pnpm --dir packages/web demo:shots    # scene 12, after stopping the MCP server
```

The walkthrough is `packages/web/e2e/demo/walkthrough.demo.ts`. Each step checks what it shows before it saves the screenshot to `docs/demo/img/`, so a broken demo fails there first.

## If something goes wrong

| Symptom | Fix |
| --- | --- |
| "cannot reach the MCP server" | `make demo status`, then `make demo start oracle` again |
| The agent API does not start, saying the seeded world does not match | `make seed`, then `make demo start oracle` again |
| Old questions in the history | `make demo start oracle` again: it restarts the agent API |
| Port 5173, 8765 or 8766 already in use | `make demo stop`; if something else holds the port, `make demo start` names it |
| Something else | The service's log in `var/demo/logs/` |
| Knowledge search returns nothing | `make ingest` |
