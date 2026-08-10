# When GitPilot cannot answer

## "Error processing message: …" / "Fallback to LiteLLM is not available"

**Symptom.** The server starts, the banner says `LLM Provider ✅ OLLAMA`, the
extension shows **Ready** — and the first message comes back as an error.

**Cause.** Every chat path used to go through CrewAI, which pulls in LiteLLM
and ~180 other packages. That is the right machinery for a multi-agent run
against a GitHub repository, and the wrong machinery for answering a question
about a local folder. If the agent runtime was not installed, a perfectly
configured and reachable Ollama could not answer anything.

**Fixed in 0.2.8.** Five of the seven providers speak the OpenAI
`/v1/chat/completions` shape, so GitPilot now talks to them directly over HTTP
— no CrewAI, no LiteLLM, nothing to install:

| Provider | Chat path | Needs the agent runtime |
| --- | --- | --- |
| Ollama | direct HTTP | no |
| OllaBridge | direct HTTP | no |
| Open WebUI | direct HTTP | no |
| OpenAI (and compatible proxies) | direct HTTP | no |
| Custom endpoint | direct HTTP | no |
| Claude | CrewAI | yes |
| watsonx | CrewAI | yes |

If you use Claude or watsonx and see a message about the agent runtime:

```bash
pip install 'gitcopilot[agents]'
```

Or switch to any provider in the top half of the table, which need nothing.

### Planning is the path that still goes through CrewAI

The table above is about *chat*. Planning is different: `/api/chat/plan`
hands a model object to CrewAI Agents, so it cannot bypass CrewAI the way
chat does. That is why the same error kept appearing on Ollama after chat
was fixed, but only when a task produced a plan:

```
POST /api/chat/plan HTTP/1.1" 500 Internal Server Error
ImportError: Fallback to LiteLLM is not available
```

**Cause.** CrewAI routes a call natively only when the provider is in its
`SUPPORTED_NATIVE_PROVIDERS` list; everything else goes to its optional
LiteLLM fallback, which fails outright when LiteLLM is not installed. On
CrewAI 1.6 — and every release before about 1.10 — that list is:

```
openai, anthropic, claude, azure, azure_openai, google, gemini, bedrock, aws
```

Ollama is not in it. Neither spelling of the obvious fix works there:
`provider="ollama"` names a provider CrewAI will not route, and
`model="ollama/llama3:8b"` (or `model="openai/qwen2.5:1.5b"`, which is how
OllaBridge, Open WebUI and custom endpoints were spelled) is validated
against CrewAI's own model constants, which no locally served model
satisfies. Both land in the LiteLLM fallback.

**Fixed in 0.2.8.** All four of these endpoints speak the OpenAI
chat-completions API, and `openai` has been natively routable for as long as
that list has existed — so GitPilot now asks for it by name, passing the
endpoint's own base URL and model id untouched:

```python
LLM(model="llama3:8b", provider="openai", base_url="http://localhost:11434/v1", api_key="ollama")
```

This needs no LiteLLM and works on both old and new CrewAI. If you are on a
GitPilot older than 0.2.8 and cannot upgrade yet, either of these unblocks
planning:

```bash
pip install -U gitcopilot          # preferred — no LiteLLM needed at all
pip install litellm                # installs the fallback CrewAI asked for
```

### The banner tells the truth now

`gitpilot serve` used to validate provider *configuration* only, so it printed
`✅ OLLAMA` and then failed on an import it had never checked. It now also
checks that the runtime that will actually answer is present:

```
❌ CLAUDE cannot answer yet
  claude needs the agent runtime, which is not installed.
  Install it with: pip install 'gitcopilot[agents]'
```

## The web app answers and VS Code times out

**Symptom.** The same backend, the same moment: the web app at `:5173`
replies normally, and VS Code shows

```
Chat Error: Error: Request to /api/chat/send timed out after 20000ms
```

The server log is the giveaway — it *succeeded*:

```
WARNING:gitpilot._api_app:[HTTP] 🐢 POST /api/chat/send took 21.26s (status=200)
```

**Cause.** Nothing was broken. Inference had been given the deadline meant
for an ordinary HTTP request. A local Ollama answering with repository
context routinely takes 20–60s; the extension gave up at 20s, while the web
app had always allowed five minutes for the same call.

The retry budget made it worse. A timeout carries no HTTP status, so it slipped
past the "don't retry these" list and the default two retries fired — three
runs of a call the server was still executing, queued behind each other on a
single-threaded Ollama. That is why the log shows the same request at 21s,
23s and 35s: each attempt was slower than the one it was sent to rescue.

**Fixed in 0.2.8.** Chat, plan and execute now get a five-minute deadline,
matching the web app, and are never retried — a timeout there means the
answer is lost, not duplicated. If a large model on CPU still gets cut off,
raise it:

```jsonc
// settings.json
"gitpilot.llmTimeoutSeconds": 900
```

Lite Mode (`gitpilot.liteMode`) or a smaller model will answer faster instead.

## Where a VS Code chat message actually goes

Worth knowing before debugging anything below, because two different
pipelines answer depending on whether your session has a GitHub repository.

```
Chat input
  └─ sendChatToBackend()            status → "planning"  (spinner starts)
       ├─ POST /api/v2/chat/stream  (SSE, always tried first)
       │    ├─ session HAS a repo   → CrewAI planner + executor
       │    │                         emits text_delta … done
       │    │                         ⇒ answer streams in, CrewAI trace in server log
       │    └─ session has NO repo  → closes immediately, no text
       │                              ⇒ handover, not an error
       └─ empty stream ⇒ POST /api/chat/send   status → "generating"
            ├─ repo session    → agent pipeline (CrewAI)
            └─ folder session  → one direct call to the provider, no agents
```

The server log now names the branch it took:

```
[chat] session=… repo=owner/name → agent pipeline (CrewAI; verbose trace follows)
[chat] session=… folder-only → direct ollama call, model=deepseek-r1:latest
       (single completion, no agents — CrewAI is not involved …)
[chat] session=… direct call returned 1841 chars in 22.4s
```

### The server prints uvicorn's INFO lines but none of GitPilot's

`uvicorn.run(log_level="info")` reads as "log at info" and configures
uvicorn's three loggers. GitPilot's records propagate to a root logger with
no handler, so Python falls back to `logging.lastResort`, whose level is
WARNING. That produced a log like this — uvicorn INFO, GitPilot WARNING,
and nothing in between:

```
INFO:     127.0.0.1:59698 - "GET /api/health HTTP/1.1" 200 OK
WARNING:gitpilot._api_app:[HTTP] 🐢 POST /api/chat/send took 33.52s
```

Every route decision, provider call and timing was being written and
discarded. Fixed in 0.2.8 — GitPilot logs at INFO by default. To change it:

```bash
gitpilot serve --log-level DEBUG      # or -l DEBUG
GITPILOT_LOG_LEVEL=DEBUG gitpilot serve
```

DEBUG adds the agent internals. `--reload` re-imports the app in a child
process, so the level travels as `GITPILOT_LOG_LEVEL` rather than in memory.

### Agent (CrewAI) verbosity

The crews narrate themselves to the server console — agent banners, task
status, final answer. The Lite paths used to run silently, which meant the
exact configuration a small local model needs was also the one with no
visible trace. They are verbose now. To quiet them:

```bash
GITPILOT_AGENT_VERBOSE=0 gitpilot serve
```

### "I see CrewAI logs in the web app but not in VS Code"

Nothing is being hidden. The web app plans against a **selected repository**,
so it runs the CrewAI pipeline and CrewAI prints its trace to the server
console. A VS Code session with only a folder open runs the direct path,
which has no agents to trace — one provider call, no crew, no boxes.

To get the agent trace in VS Code, select a GitHub repository for the
session. The `[chat]` lines above tell you which pipeline ran, so you never
have to guess.

### Turning on the extension's own log

`View → Output → GitPilot` shows the client half of the picture:

```
[GitPilot] stream → POST /api/v2/chat/stream session=… intent=… chars=2411
[GitPilot] stream → empty after 0.3s (done=1); backend handed off → batch
[GitPilot] batch → POST /api/chat/send
[GitPilot] batch ✓ 1841 chars in 22.4s (plan=no edits=0) intent=explain session=…
```

A failing stream now says *why* — unreachable, an HTTP status, or empty —
instead of the single "streaming unavailable" line that covered all three.

## `gitpilot serve` and `make run` are not the same GitPilot

**Symptom.** A bug you already upgraded past comes back when you start the
server a different way — most often the LiteLLM error above.

**Cause.** Two installs, two versions. Check the banner each one prints:

```
│ GitPilot v0.2.8 │   ← make run: the repo checkout, via uv
│ GitPilot v0.2.7 │   ← gitpilot serve: whatever pip installed, on PATH
```

`make run` runs the working tree. `gitpilot serve` runs the `gitpilot` on
your PATH — typically an older release under
`~/.local/lib/python3.11/site-packages/gitpilot/`, which has none of your
local changes and its own dependency set. A fix in the checkout does nothing
for it until it is installed.

Note that `make install` does **not** fix this on its own, and is not meant
to: it prepares `.venv` for `make run`, and installing a command onto your
PATH is something to ask for rather than have done to you. It does now tell
you when the two disagree, at the end of its output.

**Fix.** Point the command at the checkout:

```bash
make install-cli     # editable, so it tracks the tree from here on
```

**Check at any time:**

```bash
make check-cli
```

which prints one of:

```
✓ gitpilot v0.2.8 → this checkout
⚠  'gitpilot' on PATH is NOT this checkout.
     on PATH:  v0.2.7      ~/.local/lib/python3.11/site-packages/gitpilot
   this repo:  /mnt/c/workspace/gitpilot/gitpilot
```

If it still shows the old version afterwards, the shell has cached the old
path — open a new terminal, and make sure uv's tool directory is on PATH
(`uv tool update-shell`).

A traceback tells you the same thing: a path under `site-packages/gitpilot/`
is the installed copy, not your checkout.

## Every request takes about ten seconds

**Symptom.** The log is full of lines like:

```
[HTTP] 🐢 GET /api/health took 10.02s (status=200)
[HTTP] 🐢 GET /api/status took 10.02s (status=200)
[HTTP] 🐢 GET /api/settings took 10.03s (status=200)
```

Always ~10.02s. A constant like that is a timeout, not slowness.

**Cause.** GitPilot probes Ollama *and* OllaBridge to auto-pick a local model,
five seconds of socket timeout each. It ran inline inside `async def` request
handlers, so it blocked the event loop — every concurrent request finished
together, ten seconds later, and it repeated every 20 seconds.

It only bites when a port **hangs** rather than refusing. On Linux and macOS an
unbound local port refuses instantly, so the probe costs milliseconds; under
**WSL2** it frequently hangs to the full timeout. That is why this shows up
almost exclusively on Windows.

**Fixed in 0.2.8.** A stale probe now refreshes in a background thread and the
request gets the last known settings immediately. Only one refresh runs at a
time, however many requests arrive. `force=True` still probes inline for the
explicit bootstrap paths that need a fresh answer.

## "Offline — Could not reach http://127.0.0.1:8000" while the server is running

**Symptom.** `gitpilot serve` is up and its log shows `GET /api/health … 200 OK`,
but the sidebar says **Offline**. Clicking **Reconnect** changes nothing.

**Cause.** Two things compounding.

The server was answering — in ten seconds, for the reason above. The
extension's entire connect procedure was one probe with a **3-second**
deadline, so it gave up first and reported a working backend as down. Reconnect
repeated the identical 3-second probe against the identical slow server, which
is why the button appeared to do nothing.

And the message was wrong in a way that mattered: the server *was* reached. It
said "Could not reach", which points at the one thing that was already working.

**Fixed in 0.2.8.**

- **The deadline grows.** 3s, then 10s, then 25s. A server still importing its
  way to readiness is waited for; one that is genuinely absent still fails in
  about three seconds, so **Offline** appears promptly when it is true.
- **Refused and slow are told apart.** Nothing listening reads
  `Could not reach <url>`; something listening but slow reads
  `<url> is not answering yet — still trying`. The **Start server** button
  hides in the second case, because starting a server that is already running
  only fails on a port in use.
- **It reconnects on its own.** While offline, the extension probes in the
  background with backoff (2s, doubling, capped at a minute) and connects the
  moment the server answers. Starting `gitpilot serve` in a terminal is enough;
  there is no trip back to the sidebar. The panel says *Retrying
  automatically…* so it does not look dead.
- **Start server actually starts it.** That button used to open the Settings
  page, which has no way to start anything either.
- **A live server that is briefly busy stays connected.** The periodic health
  check gets a real deadline and one retry, instead of blinking the whole UI to
  Offline on one slow moment.

## The answer arrives but nothing streamed

Expected, for now. Streaming runs through the multi-agent executor, which needs
a GitHub repository (`owner/repo`). A **folder** or **local git** session has
no such name, so `/api/v2/chat/stream` closes immediately and the extension
falls back to `/api/chat/send`, which is the correct path for those sessions.
You get the whole answer at once instead of token by token.

## First stop for anything else: GitPilot: Diagnostics

**Ctrl/Cmd+Shift+P → *GitPilot: Diagnostics*** opens a report in a new document,
ready to read or paste into an issue. It answers, in one place, the questions
that previously needed a terminal and several guesses:

```
Versions
  extension        0.2.8
  backend          0.2.7
  ⚠ MISMATCH — the two halves are from different builds.
    `make extension-dev` rebuilds only the extension.
    Reinstall the backend:  pip install -e . --no-deps

Connection
  server url       http://127.0.0.1:8000
  state            connected
  last probe       reachable after 41ms
  retrying         no

Chat
  path             direct
  can answer       yes

Provider
  name             ollama
  model            llama3:8b
  endpoint         http://localhost:11434/v1
  configured       yes

Backend environment
  python           3.11.9
  interpreter      /home/x/.venv/bin/python
  package path     /home/x/.venv/lib/python3.11/site-packages/gitpilot
  runtimes         crewai=no  litellm=no  httpx=yes

Recent requests (14)
     ✗   3001ms  POST   /api/chat/send  — timed out after 3000ms
        41ms  GET    /api/health (200)
```

**`package path` is the one to read first when a change you made had no
effect.** `gitpilot` is a console script, so it imports from site-packages
rather than the directory you are standing in — if that path points anywhere
other than your checkout, you are running a different copy of the code and
`git pull` will not change it.

### The version mismatch is now caught for you

Two places, so it is hard to miss:

- **On connect.** The extension reads the backend version from `/api/health`
  and warns once per session if it differs from its own.
- **At build time.** `make extension-dev` prints all three versions and flags a
  mismatch, because that target builds the extension and never touches Python:

```
🧩 Versions
   extension (built)    0.2.8
   backend  (repo)      0.2.8
   backend  (installed) 0.2.7

   ⚠️  The installed backend is 0.2.7 but this checkout is 0.2.8.
      Nothing here touches Python. Reinstall the backend:
        pip install -e . --no-deps
```

Run it on its own any time with `make version-check`.

### The Output channel is now a trace

**View → Output → GitPilot** carries one line per request:

```
[conn] connecting
[conn] connected
[version] extension 0.2.8, backend 0.2.8
[api] GET /api/health 41ms (200)
[api] 🐢 GET /api/settings 10031ms (200)
[api] ✗ POST /api/chat/send 3001ms — timed out after 3000ms
```

`🐢` marks anything over 3s and `✗` marks a failure with its reason, so a slow
or failing backend is visible from inside VS Code rather than only in the
terminal running the server.

## Checking what GitPilot thinks it can do

```python
python -c "from gitpilot.direct_chat import describe_runtime; print(describe_runtime())"
```

```
{'provider': 'ollama', 'path': 'direct', 'ready': True,
 'detail': '', 'endpoint': 'http://localhost:11434/v1'}
```

`path` is `direct` when the provider is answered over plain HTTP, and `crewai`
when it needs the agent runtime. `ready` is what the startup banner reports.
