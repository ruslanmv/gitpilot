# AI provider setup in VS Code

Everything about provider configuration happens inside VS Code. There is no
browser step, no config file to hand-edit, and no API key that has to be
pasted into a terminal.

Open it with **`GitPilot: Settings`** from the command palette
(<kbd>Ctrl/Cmd</kbd>+<kbd>Shift</kbd>+<kbd>P</kbd>), then choose
**AI Providers**.

---

## The overview page

The first thing you see is a summary, not a form:

```
AI Providers

  ● GitPilot Server   Connected
    http://127.0.0.1:8000                    [Reconnect] [Change server]

  ACTIVE PROVIDER
    Claude (Anthropic)                            Active  ›
    claude-sonnet-4-5

  AVAILABLE PROVIDERS
    OllaBridge Cloud                                      ›
    Free hosted models — sign in, no API key needed
    Ollama (Local)                                        ›
    Run models locally on your computer
    ...
```

Click any provider to open its configuration page. Only one provider's form
is ever on screen. **‹ Back to AI Providers** returns to the overview without
leaving the settings tab.

A provider is only activated when you press **Save and activate**. Opening a
page and closing it changes nothing.

---

## How API keys are handled

Keys are stored by the GitPilot backend, never in VS Code settings and never
in your workspace.

The settings page never receives a stored key. It is told only that one
exists, and shown the last four characters:

```
API key configured: ••••A7X2. Leave the field empty to keep it.  Remove API key
```

Three rules follow from that:

- **An empty key field means "keep the current key."** You can change a model
  or a base URL without re-entering the secret.
- **Removing a key is a separate, confirmed action** — the *Remove API key*
  link, which asks before clearing.
- **Keys never appear in logs, notifications or error messages.** Error text
  is redacted before it is displayed.

If your GitPilot server is remote *and* reached over plain HTTP, GitPilot
warns before sending a key and lets you cancel.

---

## Providers

### OllaBridge Cloud

Free hosted models, and the default. Three connection methods, shown as tabs:

**Cloud Login** — click **Sign in with browser**. Your browser opens the
OllaBridge sign-in page and shows a pairing code; paste that code back into
VS Code and press **Pair device**. GitPilot never asks for your password. Once
paired the page shows your connection and a **Sign out** action.

**API Key** — for an OllaBridge endpoint you already have a token for. Set the
endpoint, the key, and a model.

**Local Gateway** — for a self-hosted OllaBridge. Defaults to
`http://127.0.0.1:11435`.

!!! warning "Not port 8000"
    `http://localhost:8000` is *GitPilot's own backend*, not an OllaBridge
    gateway. Pointing OllaBridge there makes model discovery query GitPilot
    about itself. GitPilot rejects that URL with an explanation, and repairs
    the value automatically if an older install stored it.

### Ollama (Local)

Set the Ollama URL — `http://127.0.0.1:11434` by default — and pick from the
models that machine has pulled. **Refresh** re-scans.

If Ollama is not running, the page says so plainly and offers **Retry** and
**Install Ollama** rather than leaving an empty dropdown.

Enter the instance root, not the API root. GitPilot talks to Ollama's
OpenAI-compatible surface and appends `/v1` itself; typing it yourself is
harmless — the URL is normalised, so `http://127.0.0.1:11434` and
`http://127.0.0.1:11434/v1` mean the same thing and neither becomes `/v1/v1`.
Model ids are passed through exactly as Ollama reports them, so anything
`ollama list` shows works, including `ollama create` names and
`hf.co/user/model` pulls.

### Claude (Anthropic)

Needs an Anthropic API key from
[console.anthropic.com](https://console.anthropic.com/settings/keys).

!!! note
    A Claude.ai subscription does **not** include API access. The API is
    billed separately and needs its own key.

Model and an optional custom base URL are configured on the same page.

### OpenAI

An API key from [platform.openai.com](https://platform.openai.com/api-keys),
a model, and an optional base URL — set the base URL for Azure OpenAI or a
proxy.

### IBM watsonx

Needs **both** an API key and a project ID; watsonx is not considered
configured with only one. The region base URL defaults to
`https://us-south.ml.cloud.ibm.com`.

### Open WebUI

Point GitPilot at your Open WebUI instance — `http://localhost:3000` by
default. Paste the **root URL**: GitPilot appends the OpenAI-compatible API
path itself, so `/api` or `/v1` on the end is unnecessary (and handled if you
include it anyway).

The API key is optional — an instance open to your local network needs none.
If yours requires one, create it in Open WebUI under
**Settings → Account → API keys**.

Models are discovered from the instance. If it does not answer, the page says
so instead of showing an empty list.

### Custom endpoint

Any OpenAI-compatible chat-completions gateway: self-hosted inference, a
corporate model gateway, a proxy in front of several vendors.

| Field | Notes |
|---|---|
| **Endpoint URL** | The OpenAI-compatible base URL. Pasting a full `/chat/completions` path also works — GitPilot trims it back to the root |
| **API key** | The token your endpoint issues |
| **Model** | The model id the endpoint expects |
| **Request headers** | Extra headers sent with every request |

**Request headers** exist because gateways routinely require attribution or
routing headers alongside the key — for example a header carrying your user
identity so usage is attributed correctly:

```
x-user            you@example.com
x-client-app-id   gitpilot
```

Add rows with **Add header**, remove them with **Remove**. The saved set is
exactly what the editor shows: a row you delete is deleted, and a row with a
blank name is ignored. Put the token in the **API key** field, not in a
header.

**Model discovery** is best-effort and tries the richer source first:

1. A published catalogue at the endpoint's origin
   (`/.well-known/opencode` or `/.well-known/models`)
2. The OpenAI-compatible `/models` listing

If neither is available — plenty of gateways serve chat-completions without a
catalogue — the page says so and you enter the model id by hand. That is a
working configuration, not an error.

---

## When the GitPilot server is not running

The settings page always opens. If the backend is unreachable you get
recovery actions rather than a dead-end dialog:

```
GitPilot server is not connected

Provider settings are stored by the GitPilot backend, so they
cannot be loaded right now.

[Start local server]  [Reconnect]  [Change server URL]  [Copy diagnostics]

Or start it yourself:
gitpilot serve --no-open
```

**Start local server** runs `gitpilot serve --no-open` for you and follows
the port it actually binds — GitPilot moves to the next free port when 8000
is taken, and the extension follows it rather than losing the connection.
Output goes to the **GitPilot** output channel.

For a **remote** server URL there is no *Start local server* button: starting
a process on your machine would not make someone else's server reachable.
You get Reconnect, Change server URL, and Copy diagnostics.

If `gitpilot` is not on your `PATH`, set `gitpilot.serverCommand` to its full
path.

---

## Performance

Provider pages avoid the slow endpoints deliberately:

| Call | Timeout | Notes |
|---|---|---|
| Health | 3s | Single attempt. A slow answer is the same as no answer |
| Settings | 10s | |
| Model discovery | 15s | Lazy — only for the provider you are configuring — and cached for 60s |
| Connection test | 30s | Performs a real round-trip |

`/api/status` is not called from these pages at all; it probes live providers
and can take upwards of 15 seconds.

---

## Settings reference

| Setting | Default | Purpose |
|---|---|---|
| `gitpilot.serverUrl` | `http://127.0.0.1:8000` | Where the GitPilot backend is |
| `gitpilot.serverCommand` | `gitpilot` | Command used to start a local server. Use a full path if it is not on `PATH` |
| `gitpilot.autoConnect` | `true` | Connect on startup |

Provider credentials are deliberately **not** in this table. They live on the
GitPilot server, not in VS Code configuration, so they are never written to
`settings.json` or synced by Settings Sync.

---

## Environment variables

Configuring in VS Code is the recommended path. For headless or scripted
installs, the backend also reads:

| Variable | Provider |
|---|---|
| `OPENAI_API_KEY`, `OPENAI_BASE_URL` | OpenAI |
| `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL` | Claude |
| `WATSONX_API_KEY`, `WATSONX_PROJECT_ID`, `WATSONX_BASE_URL` | watsonx |
| `OLLAMA_BASE_URL` | Ollama |
| `OLLABRIDGE_BASE_URL`, `OLLABRIDGE_API_KEY` | OllaBridge |
| `OPENWEBUI_BASE_URL`, `OPENWEBUI_API_KEY` | Open WebUI |
| `GITPILOT_CUSTOM_BASE_URL`, `GITPILOT_CUSTOM_API_KEY`, `GITPILOT_CUSTOM_MODEL` | Custom endpoint |

Environment values are merged in on every load, so operators can supply
credentials without them being written to disk. Custom request headers are
configured in the settings UI.

See [`.env.template`](https://github.com/ruslanmv/gitpilot/blob/master/.env.template)
for the annotated list.

---

## Troubleshooting

**"OLLABRIDGE API key not configured" on startup**
Fixed. OllaBridge needs no API key, and the startup check no longer claims
otherwise. Upgrade if you still see it.

**Model list is empty**
Discovery is lazy — press **Refresh**. If the provider cannot be reached the
page shows why. Every provider page accepts a manually typed model id.

**"Not connected" when the server is running**
Fixed. The connection state is re-probed rather than read from a cached flag
that refreshed on a 30-second timer.

**Changing the provider dropdown did nothing**
That dropdown is gone. Provider selection is a page with an explicit
**Save and activate**, which writes through the backend API.

**"Fallback to LiteLLM is not available" when a task produces a plan**
Fixed in 0.2.8, and it affected Ollama, OllaBridge, Open WebUI and custom
endpoints alike. Chat reaches these providers over plain HTTP, but planning
hands a model to CrewAI, which would only route a provider it recognised
natively — and on CrewAI 1.6 and earlier, none of these four qualified.
Upgrade GitPilot; if you cannot yet, `pip install litellm` unblocks it.
[Full explanation](troubleshooting.md#planning-is-the-path-that-still-goes-through-crewai).

---

## See also

- [Agent architecture and topologies](../agents.md)
- [Ports and defaults](../PORTS.md)
