# Reading large files

GitPilot's agents read files the way Claude Code does: whole when a file is
small, in bounded pages when it is large. One big file cannot fill the
model's context, and the agent always knows how much is left and where to
continue.

Implemented in `gitpilot/large_read.py` and used by every read path:
`read_file` / `read_file_window` (repository tools), `read_local_file` (local
workspace) and `fs.read` (tool registry).

## What an agent gets back

| File | Result |
| --- | --- |
| Up to 2000 lines and 100k characters, no line over 2000 characters | The whole file, framed exactly as before (`Content of a.py:` … `---`). |
| Larger | A line-numbered first page and a footer: `3000 more lines. Continue with the window tool: offset=2000, limit=2000.` |
| A line over 2000 characters (minified code) | The line is shortened: `… [48210 more characters on this line]`, counted in the footer. |
| Binary | `(binary file, N bytes, not shown)` |

Pages are numbered like `cat -n`:

```
Content of src/big.py (lines 2001-4000 of 5000):
---
  2001	def handler(event):
  2002	    ...
---
1000 more lines. Continue with the window tool: offset=4000, limit=2000.
```

## Paging

- **Tool registry (`fs.read`)**: pass `offset` (0-indexed line) and `limit`
  (lines, default 2000).
- **CrewAI agents**: `read_file_window(file_path, offset, limit)`. The default
  explorer tool list stays `read_file` only, so small models keep a one-argument
  read tool; it now returns a bounded first page instead of a whole large file.

| Constant | Default |
| --- | --- |
| `READ_PAGE_LINES` | 2000 |
| `READ_PAGE_CHARS` | 100 000 |
| `LINE_MAX_CHARS` | 2000 |

## Conversation compaction

Bounding each tool result is the first line of defence. When a session still
grows past 70 % of the model's context window, `gitpilot/auto_compact.py`
folds older turns into one summary (system instructions, tool definitions,
`AGENTS.md` and the latest turns are kept), and `gitpilot/context_budget.py`
drops oversized tool outputs first. Each compaction is recorded in the task
trace.
