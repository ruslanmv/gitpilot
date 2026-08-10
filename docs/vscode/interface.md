# The GitPilot interface

GitPilot has **two** surfaces, and each answers one question.

| Surface | Question | What lives there |
| --- | --- | --- |
| **Sidebar** | *Which task?* | Status, **New Task**, recent tasks, quick actions, Settings |
| **Editor tab** | *Work on that task* | The landing page, then the conversation |

```
┌───────────────┬──────────────────────────────────────────┐
│ GITPILOT      │ GitPilot                                 │
│               │                                          │
│ ● Ready       │          What are we building?           │
│ gitpilot/main │                                          │
│               │      ┌───────────────────────────┐       │
│ + New Task    │      │ Describe a task...        │       │
│               │      │ + @ /        Ask ▾     ↑  │       │
│ Recent Tasks  │      └───────────────────────────┘       │
│  Auth refactor│                                          │
│  Fix CI       │   Review  Find bugs  Tests  Explain      │
│               │                                          │
│ ⚙ Settings    │   ● Ready · llama3:8b · gitpilot repo    │
└───────────────┴──────────────────────────────────────────┘
```

**There is one composer.** The sidebar used to contain a complete second chat —
its own prompt box, its own quick actions, its own send flow — sitting under a
landing page that had all the same things. The first question anyone asked was
"which one am I supposed to use?", which is not a question a finished product
asks of anyone.

## The sidebar

Navigation, and nothing else. It answers four things:

1. **Is GitPilot ready?** — one status line.
2. **What project am I in?** — repository and branch.
3. **What was I working on?** — recent tasks.
4. **How do I start?** — **New Task**.

### One status, stated once

There used to be four status concepts on screen at the same time — `Offline`,
`Disconnected`, `Provider not set`, `No model` — and two of them could
contradict each other, with a *Disconnected* pill sitting beside a *Ready* one.

There is now a single primary state, with the reason underneath only when there
is one worth giving:

| State | Means | Reason shown |
| --- | --- | --- |
| **● Ready** | You can start work | — |
| **● Connecting…** | In flight. The only state that animates | — |
| **● Needs setup** | Server reachable, but unusable | `No provider configured` |
| **● Offline** | Server unreachable | `GitPilot server isn't running` |

*Needs setup* exists because a server you can reach but cannot use is not
ready — saying "Ready" there sends people into a task that cannot start.
When offline, **Start server** and **Reconnect** appear directly under the
reason, not in a second card that repeats it.

### Recent tasks, not sessions

`session` is the right word in the code. In the interface these are engineering
jobs — *Add OAuth*, *Fix CI*, *Refactor authentication* — so they are **tasks**.
GitPilot is a collection of development tasks, not a collection of chats.

Clicking a task opens it. One click, one result — no select-then-open.

### New Task starts a task

**New Task** creates a new session and gives you a clean panel: the landing
page, an empty composer, no transcript, no plan, no changed files. The previous
conversation is not lost — it moves into **Recent Tasks**, one click away.

It used to only reveal the editor tab, on the theory that a session should be
created once there was something to create it for. In practice that landed you
in the previous conversation with everything still on screen, so the one
control named for starting fresh was the one that never did.

Clearing the store is not enough on its own, which is why this needed more than
a one-line fix:

- **Tool-activity blocks** live in the transcript's DOM and carry no message
  key, so a state change leaves them — and their presence keeps the transcript
  on screen instead of the landing page.
- **The composer has its own memory**: a queued message, pinned files, and
  whatever you had half-typed. All three belong to the task being replaced.
- **A run still streaming** would carry on writing into the new conversation,
  because the panel opens a fresh streaming node the moment a chunk arrives
  with none open. New Task cuts the stream first.

Resuming a task from **Recent Tasks** clears the same things before replaying
that task's history, so a resumed conversation never appears underneath another
task's steps.

### Quick actions

Five shortcuts, below Recent Tasks. They open the GitPilot tab and run there,
so the answer always lands in the conversation.

Recent Tasks comes first on purpose: once GitPilot is in daily use, resuming
work is more frequent than starting a canned action.

### What is deliberately not there

- **No composer.** There is one, in the editor.
- **No brand row.** VS Code already draws `GITPILOT` above the view; a second
  one put the same word on screen twice.
- **No second status card**, no repository line repeated, no pills.

## The editor tab

One tab, titled **GitPilot**. Empty, it is the landing page; send a message and
the same tab is the conversation. They are the same surface at two moments,
which is why there is never a question about which composer is real.

```
                      GP  GitPilot

                 What are we building?

     Ask GitPilot to investigate, explain, change, review, or test your code.

    ┌──────────────────────────────────────────────────────────┐
    │  Describe the task, ask a question, or request a change… │
    │                                                          │
    │  [+] [@] [/]                          Ask ⌄        [ ↑ ] │
    └──────────────────────────────────────────────────────────┘
     Enter to send · Shift+Enter for a new line          Ready

     [ Review code ] [ Find bugs ] [ Write tests ] [ Explain project ]

        ● Ready · llama3:8b · gitpilot repository · Changes require approval
```

The order is deliberate: the question, then the box to answer it in, then the
shortcuts. Putting the shortcuts above the composer pushed the one thing to do
off the fold.

The composer is the one framed object on the page — everything else sits on the
editor background, so what to do next is unmistakable. The four suggestions are
neutral outlines rather than accent pills on purpose: four bright buttons
compete with the send button and turn a landing page into a toolbar.

`Send` is a small arrow rather than a full-width bar, for the same reason. Enter
is what people actually press.

### The mode picker

`Ask ⌄` opens a menu naming all three modes and what each will and will not do:

| | |
| --- | --- |
| **Ask** | Proposes changes; you approve each change |
| **Plan** | Read-only. Investigates and plans; nothing is written |
| **Agent** | Edits files and runs tools without prompting |

It replaced a three-segment control that ate the width the composer needed and
could only explain itself in a tooltip. The stored value is unchanged.

The column is capped at a readable width — this is an editor tab, and on a wide
monitor an uncapped composer stretches to 2000px.

### It knows what you are looking at

Selecting code in the editor attaches it to the composer automatically. See
**Context chips** below.

### When the server is not reachable

The status line says `Offline`, and the sidebar carries **Start server** /
**Reconnect**. Recovery lives in one place, not two.

## The conversation

### Context chips

What GitPilot will look at is stated **above** the composer, before you send —
not guessed afterwards.

```
 ┌────────────────────────────────────────────────────┐
 │ ◉ src/api/user.ts:42-58 ×    @ src/db.ts ×         │
 │                                                    │
 │ add error handling                                 │
 │                                                    │
 │ [+] [@] [/]                    Ask ⌄        [ ↑ ]  │
 └────────────────────────────────────────────────────┘
```

- **Selecting code in the editor attaches a chip automatically** — file and
  line range, with the selected code travelling to the model, capped at 4000
  characters. Clearing the selection removes the chip; it never stacks.
- **`+` or `@`** pins a file. Pinned files are consumed by the send, so they are
  not silently re-sent with every later message. The live selection chip stays,
  because the selection is still there.
- **Every chip has an `×`.** Context you cannot see or take back is context you
  cannot trust.

### `@` and `/` completion

Both complete **inline, in a dropdown above the composer** — not in a modal
quick pick — so the sentence you are part-way through writing stays on screen.

| Key | Opens | Notes |
| --- | --- | --- |
| `@` | Workspace files | Accepting one adds a **chip**, not inline text |
| `/` | `/explain` `/review` `/fix` `/test` `/plan` `/security` | Only at the start of a message |

<kbd>↑</kbd>/<kbd>↓</kbd> move, <kbd>Enter</kbd> or <kbd>Tab</kbd> accepts,
<kbd>Esc</kbd> closes. A `@` inside a word (`someone@example.com`) and a `/`
mid-sentence (`http://x/rev`) are not triggers.

### Typing while GitPilot works

Thinking of the next thing to say while an agent runs is normal, so:

- <kbd>Enter</kbd> **queues** the message. The status line reads `2 queued`, and
  the queue is sent in order as the run ends.
- <kbd>Enter</kbd> never cancels. Losing a task to a reflex is not a trade
  anyone would choose.
- <kbd>Esc</kbd>, or the **Stop** button, cancels the run *and* drops the queue —
  firing it would restart the work you just stopped.

### Tool activity is part of the conversation

Consecutive tool calls collapse into one line **in the transcript**, and stay
there once the task is over:

```
 ⌄ ✓ Investigated 3 steps
     ✓ Read src/api/user.ts
     ✓ Read src/api/auth.ts
     ✓ Searched handleAuth
```

Click to expand. A failed step says so in the summary — `1 of 4 steps failed` —
and turns the marker red. Tool names are rendered as verbs, because
`read_file · completed` is a log and `Read src/api/user.ts` is an explanation.

Blocks you expand stay expanded: the transcript is patched on each update
rather than rebuilt, so nothing closes underneath you.

### Reading a change

Proposed changes are stated as one line before they are listed:

```
 ⌄ ✓ Updated user.ts                                   +8 −2
```

Counts come from the backend when it supplies them, and are otherwise read off
the diff preview. The per-file rows, with **Open** and **Diff**, are still
underneath.

### Everything else in the transcript

- **Timestamps appear on hover.** A column of times down the right-hand edge is
  noise you scan past; on hover it is exactly as available as before.
- **A message is text, not a card.** The role is carried by a 2px rule. Cards
  are reserved for things you can act on — approvals, proposed changes.
- **Code blocks have a Copy button**, and diffs open in VS Code's native diff
  viewer.

## Undo: checkpoints and rewind

GitPilot snapshots the workspace **and** the conversation before every change
it makes. That is what turns Agent mode from a one-way door into a choice: let
it run, and rewind if it went wrong.

### What a checkpoint is

Three things captured together, before a mutating tool runs:

1. The workspace, committed to a **shadow git repository** at
   `~/.gitpilot/history/<workspace-hash>/`. Your own `.git`, index and
   uncommitted work are never touched.
2. The conversation up to that point.
3. The tool call that was about to run — which is where
   `Before write_file · src/api/user.ts` comes from.

Git stores one copy of an unchanged file however many checkpoints reference
it, which is what makes snapshotting before *every* tool call affordable.

### Rewinding

| How | What it does |
| --- | --- |
| **Rewind…** in the chat panel | Pick any checkpoint in this session |
| **Revert** | Undo the most recent change |
| *GitPilot: Rewind to a Checkpoint* | The same picker, from the palette |

A rewind restores **both** halves. Files alone would leave the model reasoning
about edits that no longer exist, so the transcript is truncated to the same
point and everything after it is discarded. You are asked to confirm first, and
the dialog says which of the two it can actually do.

Restoring is a **mirror, not an overlay** — a file the agent invented is
removed, not just overwritten. Ignored directories (`.git`, `node_modules`,
virtualenvs, build output) are never touched in either direction.

### When the workspace is too large

Past 256 MB of snapshottable files, a checkpoint records the conversation and
the tool call but not the files, and marks itself `has_files: false`. The
picker then offers *"Conversation only — the workspace was too large to
snapshot"* and rewinding leaves your files alone. Half a checkpoint that is
honest about being half beats a rewind that cannot happen.

### Ask, Plan and Agent all get it

Checkpoints are taken at the one place all three permission modes converge, so
an approved write in Ask mode is snapshotted exactly like an unattended write
in Agent mode. Plan mode writes nothing, so it records nothing.

A checkpoint that fails is logged and the tool runs anyway — a safety net that
stops the show is worse than one with a hole in it.

### API

| Route | Purpose |
| --- | --- |
| `POST /api/sessions/{id}/checkpoint` | Take one now |
| `GET /api/sessions/{id}/checkpoints` | List them, newest first |
| `POST /api/sessions/{id}/rewind` | Restore files + conversation |

## The permission model

The mode is stated in three places — the composer, the trust footer, and the
chat's own mode selector — because it decides whether GitPilot may write to your
repository.

| Mode | What GitPilot may do |
| --- | --- |
| **Ask** | Proposes changes; you approve each one. This is the default |
| **Plan** | Read-only. Investigates and plans; nothing is written |
| **Agent** | Edits files and runs tools without prompting |

`Agent` is a label, not a new setting — the stored value is still `auto`, so
existing configuration and the `gitpilot.permissionMode` setting are unchanged.

## The trust footer

The quiet line at the bottom of Home answers the questions an enterprise user
asks before typing anything:

```
● Ready · llama3:8b · gitpilot repository · Changes require approval
```

Readiness, which model is answering, which repository is in scope, and what
GitPilot is allowed to do. The model and the permission mode are buttons.

## Commands and settings

| Command | Title |
| --- | --- |
| `gitpilot.openHome` | **GitPilot: Home** |
| `gitpilot.openChatTab` | **GitPilot: Open Chat in an Editor Tab** |
| `gitpilot.openChat` | **GitPilot: Open GitPilot Workspace** (<kbd>Ctrl/Cmd</kbd>+<kbd>Shift</kbd>+<kbd>G</kbd>) |
| `gitpilot.newSession` | **GitPilot: New Session** |

| Setting | Default | Effect |
| --- | --- | --- |
| `gitpilot.showHomeOnStartup` | `true` | Open Home when a window starts with no files open. Home is never opened on top of work already in progress |

## Motion

Every animation the extension had is still present — message entrance, thinking
pulse and sweep, plan collapse, success flash, error shake, caret blink, spinner
and activity fade — and Home adds a single page entrance plus the connecting
pulse. All of it is disabled automatically when the operating system reports
`prefers-reduced-motion`.
