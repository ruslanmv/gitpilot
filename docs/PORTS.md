# Which port GitPilot serves on

GitPilot defaults to `:8000` — a popular address. Another backend already sitting
there should not stop you from starting GitPilot, and it should not silently move
GitPilot somewhere a proxy, a container port map, or a teammate's bookmark isn't
looking. So the rule depends on **where the port came from**:

| You ran | Port 8000 free | Port 8000 taken |
|---|---|---|
| `gitpilot serve` | serves on 8000 | serves on 8001, and says so |
| `gitpilot serve --port 9000` | serves on 9000 | **fails** — 9000 is a promise to something |
| `GITPILOT_PORT=9000 gitpilot serve` | serves on 9000 | **fails**, same reason |
| `… --no-strict-port` | — | drifts to the next free port |
| `… --strict-port` | — | fails, even on the default |

The drift is bounded: 20 consecutive ports are tried, then the run fails with the
range it searched. Twenty busy ports in a row is a problem scanning won't solve.

## For scripts and wrappers

Two things make the moving port safe to build on.

```bash
gitpilot free-port --port 8000       # prints 8000, or the next free one
```

Prints only the number, so it drops into a shell substitution. Use it to pick a
port *before* starting anything — a dev proxy target, a printed URL, a health
poll.

```bash
gitpilot serve --port 8000 --no-strict-port --port-file /tmp/gitpilot.port
```

`--port-file` records the port actually bound, written before the server boots.
Because the server re-checks availability at bind time, a port that was free a
moment ago and got taken in between still resolves — and the file, not the
guess, is what a wrapper should poll. `make run` does exactly this, and passes
the result to the Vite dev server as `GITPILOT_PORT` so the API proxy follows
the backend.

## Stopping it again

`make stop` and `make stop-soft` sweep the whole window (`PORT` …
`PORT + PORT_WINDOW - 1`, 8000–8019 by default), so a backend that drifted is
still stoppable. Override with `make stop PORT=9000 PORT_WINDOW=5`.

## Why binding, not connecting

Availability is decided by attempting a `bind()`, not by connecting to the port.
A connect probe asks "is a server reachable here", which is the wrong question:
it misses a listener bound to a different interface of the same host, and it
cannot see a socket held open without accepting connections. Binding asks
exactly what uvicorn is about to ask, on every address the host resolves to, so
a port reported free is a port the server can actually take.
