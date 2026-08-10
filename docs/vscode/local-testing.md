# Testing the extension locally

How to build the VS Code extension, install it into your own editor, and check
that the settings pages actually work.

---

## Quick version

```bash
make extension-test    # run the automated suites
make extension-dev     # package + install into your VS Code
```

Then in VS Code: **Ctrl/Cmd+Shift+P** → *Developer: Reload Window* →
**Ctrl/Cmd+Shift+P** → *GitPilot: Settings*.

---

## The targets

| Target | What it does |
|---|---|
| `make extension-install` | Install npm dependencies only |
| `make extension-compile` | TypeScript → JavaScript, and copy the webview assets into `out/` |
| `make extension-test` | Compile, then run the automated suites |
| `make extension-package` | Build a `.vsix` |
| `make extension-dev` | Package **and** install into your local VS Code |
| `make extension-uninstall` | Remove the locally installed extension |

`extension-dev` needs the `code` command on your `PATH`. If it is not there:
**Ctrl/Cmd+Shift+P** → *Shell Command: Install 'code' command in PATH*. Failing
that, install the `.vsix` by hand from the Extensions view → `...` menu →
*Install from VSIX...*.

!!! warning "Reload after installing"
    VS Code keeps the previous copy loaded until the window reloads. If a
    change appears to have done nothing, this is almost always why.

---

## The automated suites

```bash
make extension-test
# or, from extensions/vscode:
npm test
```

Ten suites, in separate processes so a crash in one still reports the others:

**Connection procedure** (`test/connection.test.js`) drives the real client
against a real HTTP server whose answer speed the test controls, because the
bug it covers was entirely about timing: a backend answering 200 in ten seconds
was reported as Offline by a 3-second probe. It asserts the escalating
deadline, that a refused connection still fails fast, that "slow" and
"not there" are told apart, and that the watcher reconnects on its own once the
server appears.

**Diagnostics** (`test/diagnostics.test.js`) covers the reporting that exists
because failures kept being invisible: a backend from a different build is
called out unprompted and only once, every request is recorded with its timing
and reason, and the report still works when the backend cannot answer — which
is exactly when it is needed.

**Navigation sidebar** (`test/navView.test.js`) asserts the sidebar navigates
and nothing more — no composer, no message list, no quick actions, no provider
dropdown — plus one primary status that never contradicts itself, and one-click
task opening.

**Landing page** (`test/landing.test.js`) asserts the landing and the
conversation are the same surface at two moments: exactly one composer on the
whole page, the landing gives way to the transcript on the first message and
comes back when it is cleared, status is stated once, and every animation the
panel had is still present.

**Session commands** (`test/chatSession.test.js`) covers what "New Chat did
nothing" was really about: the conversation the panel renders is cleared before
the network round-trip, the task state goes with it, and resuming replays a
session's history into the surface the user can actually see.

**Chat panel presentation** (`test/chatPanel.test.js`) pins the visual decisions
that are easy to undo by accident — a message is text and not a card, the
transcript uses the height it has, the mode selector states a permission model
— and asserts that every animation the panel ever had is still present.

**Composer and transcript** (`test/composer.test.js`) drives the chat the way a
user does: context chips appear from a selection and can be taken back, `@` and
`/` complete inline, a message typed mid-run is queued rather than interrupting,
tool calls collapse into a block that survives a re-render with its open state
intact, and a diff is summarised before it is listed.

**Command wiring** (`test/commandWiring.test.js`) checks the joins between the
webview, `package.json` and the commands the extension actually registers. It
exists because three buttons — Revert, Rewind and Approve & Execute — dispatched
commands nobody had registered, so they failed silently while every piece in
isolation looked fine.

**Settings webview** (`test/settingsWebview.test.js`) loads the real template
into jsdom, plays the extension host's side of the message protocol, and drives
the pages like a user — clicking provider rows, switching OllaBridge tabs,
toggling MCP tools. It catches navigation and rendering bugs.

**Settings panel host** (`test/settingsPanel.test.js`) loads the compiled panel
with a stubbed `vscode` module and calls its message handler directly. It
catches what a UI test cannot see: that no API key ever appears in a message
bound for the webview, that a save writes the config before activating the
provider, and that destructive actions ask first.

Both load from `out/`, so they test what actually ships. `npm test` compiles
first; `npm run test:only` skips that when you have already built.

### Backend suites

```bash
pytest tests/test_provider_setup.py       # provider rules, URL normalisation
pytest tests/test_agent_topologies.py     # agent roster, pipeline sequences
pytest tests/test_mcp_catalog_remote.py   # MCP registry search, catalogue packaging
pytest tests/test_checkpoints.py          # snapshot, mirror-restore, size limit
pytest tests/test_checkpoint_api.py       # checkpoint/rewind routes, auto-snapshot gate
pytest tests/test_direct_chat.py          # chat over plain HTTP, and the probe that must not block
pytest tests/test_diagnostics_api.py      # /api/health version, /api/diagnostics
```

---

## Running against a live backend

The extension is a client; most pages need `gitpilot serve` running.

```bash
pip install -e .        # or: pip install gitcopilot
gitpilot serve --no-open
```

You do **not** have to start it by hand — that is one of the things worth
testing. See the offline checklist below.

---

## Manual checklist

Work through this after `make extension-dev` and a window reload. Each item
names what to do and what you should see.

### The sidebar

1. Open the GitPilot icon in the activity bar. ✅ The only view is **GitPilot** —
   a status line, **New Task**, **Recent Tasks**, **Settings**. No message
   list, no composer, no quick actions, no provider dropdown.
2. ✅ Quick actions are on the landing page, not here.
3. Click a session. ✅ It opens the chat in one action — you should not have
   to click a second "Open Chat" button.
4. Hover a session row. ✅ A `⋯` menu fades in. Click it. ✅ A menu opens and
   the session does **not** switch underneath you.
5. Stop the backend. ✅ The sidebar shows **Start server** / **Reconnect** in
   place rather than going blank.

### One surface

See [The interface](interface.md) for what each surface is for.

1. Open the GitPilot sidebar. ✅ There is **no chat** in it — no composer, no
   message list, no quick actions. Status, **New Task**, **Recent Tasks**,
   **Settings**, and nothing else.
2. ✅ There is no **GitPilot Workspace** view under it any more.
3. Reload the window with no files open. ✅ One editor tab, titled **GitPilot**,
   showing the landing page. Reload again *with* a file open. ✅ It does not
   appear — it never opens on top of work in progress
   (`gitpilot.showHomeOnStartup` turns it off entirely).
4. Click **New Task**. ✅ The same one tab opens or comes forward. There is
   never a second GitPilot tab.
5. Type a task and press <kbd>Enter</kbd>. ✅ The landing page gives way to the
   conversation **in the same tab**. ✅ The composer is still exactly where it
   was — it never moves or duplicates.
6. Click a recent task in the sidebar. ✅ The tab shows that conversation, in
   one click.

### New Task is a new task

1. Run a task that changes a file, so the panel has a transcript, a collapsed
   `⌄ ✓ Investigated N steps` block, and a **Proposed Changes** section.
2. Pin a file with `+`, and type half a sentence without sending it.
3. Click **New Task** in the sidebar. ✅ The panel shows the **landing page** —
   `What are we building?` — with:
   - no transcript,
   - **no leftover activity block**,
   - no plan, scope or changed-files section,
   - an empty composer and no context chips.
4. ✅ The previous conversation is in **Recent Tasks**. Click it. ✅ Its history
   comes back, and *not* underneath the new task's leftovers.
5. Start a long task, and click **New Task** while it is still streaming.
   ✅ The old run stops rather than continuing to write into the new
   conversation.
6. Queue a message during a run (type + <kbd>Enter</kbd>), then click
   **New Task**. ✅ The queued message is dropped — it was meant for the
   conversation you just replaced.

### Status is stated once

1. Stop the backend. ✅ The sidebar reads **● Offline** with
   *GitPilot server isn't running* underneath, and **Start server** /
   **Reconnect** directly below that. ✅ There is no second card repeating it,
   and the words *Ready* and *Disconnected* never appear together.
2. Start the backend but clear the provider. ✅ The sidebar reads
   **● Needs setup** / *No provider configured* — not "Ready".
3. While reconnecting. ✅ **● Connecting…** with a pulsing dot; no recovery
   buttons, because nothing has failed yet.
4. Configure a provider. ✅ **● Ready**, model name beside it, no reason line.
5. ✅ The landing page's footer says the same thing once:
   `● Ready · llama3:8b · gitpilot repository · Changes require approval`.

### GitPilot Chat — composer and transcript

1. Select a few lines in a source file. ✅ A chip appears **above** the chat
   composer reading `◉ <path>:42-58`. Deselect. ✅ It disappears rather than
   stacking a second one.
2. Type `@` then part of a filename. ✅ A dropdown opens **above the composer**
   with matches — no modal. <kbd>↑</kbd>/<kbd>↓</kbd> move, <kbd>Enter</kbd>
   accepts. ✅ The file becomes a chip and the `@query` text is consumed.
3. Type `someone@example.com`. ✅ No dropdown — an `@` inside a word is not a
   trigger. Same for `/` mid-sentence.
4. Type `/` at the start of a message. ✅ The command list opens.
5. Send with chips attached. ✅ The transcript shows **only what you typed**, and
   the pinned file chips are gone (consumed). The selection chip stays, because
   the selection is still there.
6. While GitPilot is working, type and press <kbd>Enter</kbd>. ✅ The status line
   reads `1 queued` and the run is **not** cancelled. When the run ends,
   ✅ the queued message is sent.
7. Press <kbd>Esc</kbd> mid-run. ✅ The run stops and the queue is dropped.
8. Watch a task that uses tools. ✅ They collapse into one line in the
   transcript — `⌄ ✓ Investigated 3 steps`. Click it. ✅ It expands to
   `Read <path>`, `Searched <term>` and so on. Keep it open while the task
   continues. ✅ It stays open.
9. Hover a message. ✅ The timestamp fades in; it is invisible otherwise.
10. After a change is proposed, look at **Proposed Changes**. ✅ The header reads
    `✓ Updated user.ts` with `+8 −2` on the right, and the file rows are still
    inside.

### Undo — checkpoints and rewind

1. In **Agent** mode, ask GitPilot to change a file. ✅ When it finishes, click
   **Rewind…** in the chat panel. A picker lists checkpoints newest first, each
   labelled `Before write_file · <path>`.
2. Pick the one before the change and confirm. ✅ The file is back, ✅ the
   conversation is truncated to that point, and ✅ any file GitPilot created is
   gone — not merely overwritten.
3. Check your own repository. ✅ `git status` is unchanged by the rewind itself,
   and `node_modules` / `.venv` are untouched.
4. Click **Revert** after a change has been applied. ✅ It undoes the most recent
   change. (Before this release that button did nothing at all.)
5. Approve a plan with **Approve & Execute**. ✅ It actually runs. (It used to
   set the status to *generating* and stop.)
6. `GitPilot: Rewind to a Checkpoint` from the palette. ✅ Same picker.

### AI Providers — the original bug

1. **Ctrl/Cmd+Shift+P** → *GitPilot: Settings* → **AI Providers**.
2. The page shows a server badge, one **Active Provider**, and a list of the
   rest. ✅ No "Not connected to GitPilot server" dialog, even if the server
   was started a moment ago.
3. Click **Claude (Anthropic)**. ✅ Only Claude's form appears. No OllaBridge
   tabs, no Ollama fields.
4. ✅ The API key box is **empty**, with the placeholder *"Leave empty to keep
   the current key"*. If a key is stored, the hint below reads
   `API key configured: ••••XXXX`.
5. Type a model change but leave the key box empty → **Save and activate**.
   ✅ Succeeds, and the stored key still works.
6. **‹ Back to AI Providers**. ✅ Returns to the list, still inside the
   Settings tab.

### The offline recovery path

1. Stop the backend (Ctrl+C in the `gitpilot serve` terminal).
2. Reopen **AI Providers**. ✅ The page opens. It shows *"GitPilot server is
   not connected"* with **Start local server**, **Reconnect**, **Change server
   URL** and **Copy diagnostics** — not a dead-end dialog.
3. Click **Start local server**. ✅ A progress notification appears, the
   **GitPilot** output channel shows the command, and the page reconnects.
4. Set `gitpilot.serverUrl` to a remote address and stop the server again.
   ✅ **Start local server** is *hidden* — starting a process here would not
   make someone else's server reachable.

### OllaBridge, Open WebUI, custom endpoint

1. Open **OllaBridge Cloud**. ✅ Three tabs: *Cloud Login*, *API Key*,
   *Local Gateway*. Exactly one panel visible.
2. *Cloud Login* → **Sign in with browser**. ✅ Your browser opens the sign-in
   page; VS Code never asks for a password.
3. *Local Gateway*. ✅ The URL defaults to `http://127.0.0.1:11435` —
   **not** `:8000`, which is GitPilot's own backend.
4. Open **Open WebUI**. ✅ Asks for the instance root; the hint says GitPilot
   appends the API path itself.
5. Open **Custom endpoint**. ✅ URL, key, model, and an editable
   **Request headers** list with *Add header* / *Remove*.

### Agent topologies

1. **Agent** section. ✅ Topologies render as cards showing their full agent
   sequence — Feature Builder reads
   *Explorer → Planner → Coder → Reviewer → PR Manager*.
2. ✅ There is **no free-text topology box**.
3. Click one. ✅ It becomes Active, and the choice persists across a reload —
   it is saved on the backend, not just in VS Code.
4. Confirm from the server:
   ```bash
   curl http://127.0.0.1:8000/api/settings/topology
   ```
5. Pick **Automatic (recommended)**. ✅ The same call now returns
   `{"topology": null}`.

### MCP servers

1. **MCP Servers** section. ✅ A gateway card, attached servers, and a catalogue.
2. With no gateway running: ✅ **Install MCP Context Forge** is visible. With
   one running: ✅ it is hidden.
3. Click **Attach** on a bundled server. ✅ It appears under *Attached servers*
   marked **Disabled** — attaching is not enabling.
4. Open it. ✅ Endpoint, the *name* of its token env var (never a value), a
   master switch, and every tool with a risk badge and a "Used by" line.
5. Toggle a high-risk tool (e.g. one containing `drop`) **on**. ✅ A modal asks
   first. Decline → ✅ it stays off.
6. Toggle it **off** again. ✅ No prompt — removing a capability is not risky.
7. Search the registry for something. ✅ Results appear under the bundled
   entries, or a *"Registry unavailable"* line if it cannot be reached —
   either way the page still works.
8. **Add manually**. ✅ The third prompt asks for an environment variable
   **name** and says not to paste the token.

### Checking the agents actually gained the tools

The point of attaching a server is what the agents can do. After enabling one:

```bash
curl http://127.0.0.1:8000/api/mcp/status
```

✅ `tools_advertised` rises. Disable the server, call again, ✅ it falls back.

---

## Debugging

**Output channel** — View → Output → **GitPilot** in the dropdown. Server
startup, MCP installer commands and their output all land here.

**Webview devtools** — with the settings tab focused: **Ctrl/Cmd+Shift+P** →
*Developer: Open Webview Developer Tools*. The Console shows webview errors,
and Network is empty by design: the webview makes no requests of its own.

**Extension host log** — **Ctrl/Cmd+Shift+P** → *Developer: Show Logs...* →
*Extension Host*.

### Faster loop: the Extension Development Host

Packaging on every change is slow. For iterating:

1. Open `extensions/vscode` as the VS Code workspace folder.
2. Press **F5**. A second window opens with the extension loaded from source.
3. After an edit, run `npm run compile` (or `npm run watch` in a terminal),
   then **Ctrl/Cmd+R** in that window.

!!! note
    The settings panel reads its template from `out/`, so a template edit
    needs `npm run compile` — the copy step is what moves it. `npm run watch`
    only watches TypeScript, so re-run `npm run compile` after touching the
    HTML.

---

## Common problems

**Changes do not appear**
Reload the window. If it still looks stale, `make extension-uninstall`, reload,
then `make extension-dev` again.

**"Could not load settings template"**
`out/ui/webview/gitpilotSettingsTemplate.html` is missing — run
`npm run compile`, which copies it.

**`make extension-dev` says `code` is not on your PATH**
VS Code → **Ctrl/Cmd+Shift+P** → *Shell Command: Install 'code' command in
PATH*, or install the `.vsix` from the Extensions view.

**`vsce` fails to package**
Run `npm install` first. `make extension-package` does this via
`extension-compile`.

**Tests fail with "Not compiled yet"**
Run `npm run compile`. `npm test` does it for you; `npm run test:only` does not.

---

## See also

- [AI provider setup](ai-providers.md)
- [Agent topologies](agent-topologies.md)
- [MCP servers](mcp-servers.md)
