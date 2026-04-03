# Multi-Repo Context Feature — Implementation Plan

> GitPilot: Claude-like repo chips for multi-repo workspace context

## Overview

Replace the single `repo` state in App.jsx with a **context set** of repos
displayed as chips. One repo is always the **active write target**. Agent
execution stays single-repo (the active one), but the user can see and switch
between multiple repos attached to their workspace.

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│  ContextBar (chips)                                 │
│  [ owner/repo-a  main ✕ ]  [ repo-b  feat ✕ ]  [+] │
│      ^active                   ^secondary           │
├────────────────────┬────────────────────────────────┤
│  Sidebar           │  Workspace                     │
│  ┌──────────────┐  │  ┌────────────┬───────────────┐│
│  │ CONTEXT (2)  │  │  │ Context    │ Chat          ││
│  │ active: A    │  │  │ Panel (A)  │ Panel (A)     ││
│  │ [Manage]     │  │  │            │               ││
│  └──────────────┘  │  └────────────┴───────────────┘│
│  Sessions          │                                │
│  User              │                                │
└────────────────────┴────────────────────────────────┘
```

Downstream components (`ChatPanel`, `ProjectContextPanel`, `FileTree`, etc.)
continue receiving a single `repo` prop — they don't know about multi-repo.

---

## Phase 1: Frontend State Refactor (App.jsx)

### Current state

```js
const [repo, setRepo] = useState(null);  // single repo object
```

### New state

```js
const [contextRepos, setContextRepos] = useState([]);
//  Each entry: { repoKey: "owner/repo", repo: {...}, branch: "main" }

const [activeRepoKey, setActiveRepoKey] = useState(null);
//  Which repo is the write target
```

### Derived `repo` (backwards-compatible)

```js
const repo = useMemo(() => {
  const entry = contextRepos.find(r => makeRepoKey(r.repo) === activeRepoKey);
  return entry?.repo || null;
}, [contextRepos, activeRepoKey]);
```

Every consumer that currently reads `repo` continues to work unchanged.

### Key operations

| Action       | Implementation                                                |
|--------------|---------------------------------------------------------------|
| Add repo     | Push to `contextRepos`, set as `activeRepoKey` if first       |
| Remove repo  | Filter from `contextRepos`, reassign active to first or null  |
| Switch active| Set `activeRepoKey` to clicked chip's key                     |
| Clear all    | Set both to `[]` / `null`                                     |

### `repoStateByKey` — no changes needed

The existing `repoStateByKey` dictionary already supports N repos as keys.
Each repo in `contextRepos` already has its own per-repo state entry
(branch, sessionBranches, chatByBranch, etc.).

---

## Phase 2: ContextBar Component

**New file:** `frontend/components/ContextBar.jsx` (~150 LOC)

A horizontal bar rendered above the workspace grid.

### Props

```js
ContextBar({
  contextRepos,       // [{repoKey, repo, branch}]
  activeRepoKey,      // string | null
  onActivate,         // (repoKey) => void
  onRemove,           // (repoKey) => void
  onAdd,              // () => void — opens AddRepoModal
  onBranchChange,     // (repoKey, newBranch) => void
})
```

### Chip anatomy

```
┌──────────────────────────────────┐
│ ⎇  owner/repo-name  main    ✕   │
│ ^branch icon  ^name  ^branch ^rm │
└──────────────────────────────────┘
```

- **Active chip:** Blue left border (`#3B82F6`), slightly brighter text
- **Inactive chip:** Muted border (`#27272A`), dimmed text (`#71717A`)
- **Click chip body** → set as active write target
- **Click ✕** → remove from context (confirm if active + only remaining)
- **"+" button** → opens AddRepoModal

### Styling

- Dark theme: `#131316` background, `#27272A` borders
- Horizontal scroll with `overflow-x: auto` if many chips
- Fixed height: ~40px
- Monospace font for repo/branch names

---

## Phase 3: AddRepoModal

**New file:** `frontend/components/AddRepoModal.jsx` (~40 LOC)

A portal modal wrapping the existing `RepoSelector` component.

```js
AddRepoModal({ isOpen, onSelect, onClose, excludeKeys })
```

- `excludeKeys`: Array of repoKeys already in context (prevents duplicates)
- Reuses `RepoSelector` entirely — no new search/fetch logic
- On select: calls `onSelect(repo)`, modal auto-closes
- Portal to `document.body` (consistent with other modals)

---

## Phase 4: Sidebar Context Card Update

Replace the current single-repo card with a compact multi-repo summary:

### Current

```
CURRENT CONTEXT                     [✕]
  owner/repo-name
  main · Private
  [Switch repo]  [Project settings]
```

### New

```
CURRENT CONTEXT                     [✕]
  2 repos · active: repo-name
  main · Private
  [Manage context]  [Project settings]
```

- **"Manage context"** button scrolls/focuses ContextBar
- **✕ close** clears ALL context repos (with confirm)
- Remove "Switch repo" button (replaced by ContextBar chip interaction)
- If only 1 repo, show current format (no "N repos" counter)

---

## Phase 5: Per-Chip Branch Picker

Each chip in ContextBar can show a branch dropdown. Reuses existing
`BranchPicker` (already portaled to `document.body`):

```jsx
<BranchPicker
  repo={chip.repo}
  currentBranch={chip.branch}
  defaultBranch={chip.repo.default_branch}
  sessionBranches={repoStateByKey[chip.repoKey]?.sessionBranches || []}
  onBranchChange={(newBranch) => onBranchChange(chip.repoKey, newBranch)}
/>
```

Branch changes update both:
- `contextRepos[i].branch`
- `repoStateByKey[key].currentBranch`

---

## Phase 6: Backend — Session Model

**File:** `gitpilot/session.py`

### Extend Session dataclass

```python
@dataclass
class Session:
    # ... existing fields kept for backwards compat ...
    repo_full_name: Optional[str] = None
    branch: Optional[str] = None

    # NEW: multi-repo context
    repos: List[Dict[str, Any]] = field(default_factory=list)
    #  Each: {"full_name": "owner/repo", "branch": "main", "mode": "read"|"write"}
    active_repo: Optional[str] = None  # full_name of write-target
```

### Backwards-compatible migration

In `from_dict()`:
```python
if not data.get("repos") and data.get("repo_full_name"):
    data["repos"] = [{
        "full_name": data["repo_full_name"],
        "branch": data.get("branch", "main"),
        "mode": "write",
    }]
    data["active_repo"] = data["repo_full_name"]
```

Existing sessions auto-migrate on load. No data loss.

---

## Phase 7: Backend — API Updates

**File:** `gitpilot/api.py`

### Update `POST /api/sessions`

Accept either legacy or multi-repo format:
```json
// Legacy (still works)
{ "repo_full_name": "owner/repo", "branch": "main" }

// New
{
  "repos": [
    {"full_name": "owner/repo-a", "branch": "main", "mode": "write"},
    {"full_name": "owner/repo-b", "branch": "feat-x", "mode": "read"}
  ],
  "active_repo": "owner/repo-a"
}
```

### New endpoint: `PATCH /api/sessions/{id}/context`

```json
// Add repo to context
{ "action": "add", "repo_full_name": "owner/repo", "branch": "main" }

// Remove repo from context
{ "action": "remove", "repo_full_name": "owner/repo" }

// Set active write target
{ "action": "set_active", "repo_full_name": "owner/repo" }
```

### Update `GET /api/sessions/{id}`

Response includes `repos` and `active_repo` fields.

---

## Phase 8: Integration

Wire everything together:

1. **App.jsx:** Replace `repo`/`setRepo` with `contextRepos`/`activeRepoKey`
   + derived `repo`
2. **Workspace layout:** Insert `<ContextBar>` between sidebar and
   workspace-grid (or above workspace-grid)
3. **Sidebar:** Update context card to show multi-repo summary
4. **RepoSelector `onSelect`:** Push to `contextRepos` instead of `setRepo`
5. **Session create/load:** Persist `repos[]` and `active_repo`

---

## File Change Summary

| File | Change | Effort |
|------|--------|--------|
| `frontend/App.jsx` | State refactor: `repo` → `contextRepos` + `activeRepoKey` + derived | Medium |
| `frontend/components/ContextBar.jsx` | **New** — chip bar | ~150 LOC |
| `frontend/components/AddRepoModal.jsx` | **New** — thin RepoSelector wrapper | ~40 LOC |
| `frontend/styles.css` | ContextBar + updated context card styles | Small |
| `gitpilot/session.py` | Add `repos`, `active_repo` + migration | Small |
| `gitpilot/api.py` | New `PATCH` endpoint, update create/load | Small |

### Files with ZERO changes

- `ChatPanel.jsx` — receives derived single `repo`
- `ProjectContextPanel.jsx` — receives derived single `repo`
- `FileTree.jsx` — receives props from ProjectContextPanel
- `BranchPicker.jsx` — already generic, reused per-chip
- `FlowViewer.jsx`, `LlmSettings.jsx` — no repo dependency
- `EnvironmentSelector.jsx`, `EnvironmentEditor.jsx` — no repo dependency

---

## Risks & Trade-offs

1. **Agent execution stays single-repo.** The active write target is the
   only repo sent to agent operations. Cross-repo tasks (e.g., "update
   repo A based on repo B") require the user to switch active target.
   Consistent with how Claude Code works today.

2. **No cross-repo diff views.** Diffing between repos is a future feature.
   This plan only covers context switching.

3. **Session persistence is additive.** Existing sessions with single
   `repo_full_name` auto-migrate. No destructive schema changes.

4. **Chip count cap.** UX recommendation: soft-cap at 5 repos. Beyond that,
   horizontal scroll + a "N more" overflow indicator.

---

## Implementation Order

```
Phase 1-3 (Frontend only, no backend changes)
  ├── 1. ContextBar.jsx + AddRepoModal.jsx (standalone)
  ├── 2. App.jsx state refactor
  └── 3. Sidebar context card update

Phase 4-5 (Backend persistence)
  ├── 4. session.py model extension
  └── 5. api.py endpoint updates

Phase 6 (Wire)
  └── 6. Connect frontend ↔ backend session persistence
```

Phases 1-3 give the full UX with client-side-only state.
Phases 4-6 add server persistence across sessions.
