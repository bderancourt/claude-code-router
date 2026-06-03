# claude-code-router

Minimal stdlib-only routing proxy that lets a single Claude Code session
talk to **either** Anthropic's hosted models **or** a local vLLM/llama.cpp
server, chosen per-request by the model name.

## Why

The official Claude Code CLI talks to one backend per process (set via
`ANTHROPIC_BASE_URL`). To switch between a local model and Anthropic during
the same session, a small router is needed in front. Existing routers
(claude-code-router by musistudio, LiteLLM, etc.) are large TypeScript /
Python projects with many dependencies — overkill, and an audit burden when
you care about not leaking your Claude Pro OAuth token.

This proxy is **one file, stdlib only**, ~250 lines. Audit it yourself.

## Routing

Default behaviour (no `CCR_LOCAL_MODELS` set):

| `model` field in the request | Destination |
|---|---|
| starts with `claude-` | `https://api.anthropic.com` (your normal Pro/API auth) |
| anything else (or no model) | local backend |

With `CCR_LOCAL_MODELS=haiku` (pattern-based routing):

| `model` field in the request | Destination |
|---|---|
| contains `haiku` (e.g. `claude-haiku-4-5`) | local backend |
| other `claude-*` (sonnet, opus…) | `https://api.anthropic.com` |
| anything else | local backend |

This lets you use the **VS Code extension's model picker** to switch backends:
select Haiku → local model; select Sonnet/Opus → Anthropic.

## Configuration

All via environment variables (defaults shown):

| Var | Default | Purpose |
|---|---|---|
| `CCR_LISTEN_HOST` | `127.0.0.1` | bind address |
| `CCR_LISTEN_PORT` | `8082` | bind port |
| `CCR_LOCAL_HOST` | `127.0.0.1` | local backend host |
| `CCR_LOCAL_PORT` | `8080` | local backend port |
| `CCR_LOCAL_MODEL` | _(unset)_ | rewrite model name sent to local backend |
| `CCR_LOCAL_MODELS` | _(unset)_ | comma-separated substrings that trigger local routing |
| `CCR_ANTHROPIC_HOST` | `api.anthropic.com` | upstream Anthropic host |
| `CCR_ANTHROPIC_PORT` | `443` | upstream Anthropic port |

### `CCR_LOCAL_MODEL` — model name rewriting

When set, any request routed to the local backend has its `model` field
rewritten to this value. Useful when the client (e.g. the VS Code extension)
sends a hardcoded `claude-*` name but your local server only knows its own
model identifier:

```
CCR_LOCAL_MODEL=natfii/Qwen3.6-27B-VLM-NVFP4-MTP
```

### `CCR_LOCAL_MODELS` — pattern-based routing

Comma-separated list of substrings. Any `claude-*` model whose name contains
one of these patterns is routed to the local backend instead of Anthropic.
Other `claude-*` models continue to go to Anthropic.

```
CCR_LOCAL_MODELS=haiku
```

Leave unset to use the default behaviour (all `claude-*` → Anthropic).

## Usage

Start the proxy:

```sh
python3 proxy.py
```

Point Claude Code at it:

```sh
ANTHROPIC_BASE_URL=http://127.0.0.1:8082 claude
```

**CLI:** use `/model <name>` to switch:

- `/model claude-sonnet-4-6` → goes to Anthropic
- `/model claude-haiku-4-5` → goes to local (if `CCR_LOCAL_MODELS=haiku`)

**VS Code extension:** set `ANTHROPIC_BASE_URL` via `claudeCode.environmentVariables`
in your settings, then use the model picker normally. Haiku → local, Sonnet/Opus → Anthropic.

```json
"claudeCode.environmentVariables": [
  { "name": "ANTHROPIC_BASE_URL", "value": "http://127.0.0.1:8082" }
]
```

## System message extraction (vLLM)

Claude Code sends the system prompt as a `messages[0]` entry with
`role: "system"`. vLLM's Anthropic-compatible `/v1/messages` endpoint
expects it as a top-level `system` field instead.

The proxy automatically extracts and promotes system messages when routing
to the local backend, so no vLLM configuration is needed on your end.

## Cross-provider switching: thinking blocks

When you switch from a local model to an Anthropic model mid-session,
Claude Code resends the full conversation history. Anthropic validates
the cryptographic signature of any `thinking` block in that history;
blocks produced by another provider fail validation and the request is
rejected:

```
API Error: 400 messages.N.content.0: Invalid `signature` in `thinking` block
```

To make in-session switching usable, the proxy **strips `thinking` and
`redacted_thinking` blocks from message history before forwarding to
Anthropic**. The user-visible text of prior assistant turns is preserved;
only the reasoning trace is dropped (it could not have transferred
meaningfully across providers anyway).

## Security properties

- **Bind:** only `127.0.0.1` by default (never accept external connections).
- **No dependencies:** stdlib only. `python3 proxy.py` works on any 3.10+.
- **Auth pass-through:** the `Authorization` header (your Pro OAuth Bearer)
  is forwarded **transparently** to whichever backend the model dictates.
  The proxy never reads, parses, or stores it.
- **No body logging:** request and response bodies are never written to
  disk or stderr. Only metadata is logged: method, path, target label,
  and the model name extracted from the body (which the user typed).
- **Body modifications** are limited to three documented cases:
  1. Anthropic-bound requests: `thinking`/`redacted_thinking` blocks stripped.
  2. Local-bound requests: `system`-role messages extracted to top-level field.
  3. Local-bound requests: `model` field rewritten if `CCR_LOCAL_MODEL` is set.
- **No telemetry, no phone-home.** The proxy talks only to the two
  destinations you configured.
- **Hop-by-hop headers stripped** per RFC 7230 (`Connection`, `Keep-Alive`,
  `Proxy-Authorization`, `TE`, `Trailers`, `Transfer-Encoding`, `Upgrade`).
  `Host` is rewritten per target.

## Threat model & caveats

- This proxy trusts whatever runs on `127.0.0.1`. If another user on this
  machine can bind low ports or read your loopback traffic, that's a
  pre-existing problem.
- The proxy uses Python's default SSL context for TLS to Anthropic
  (system root CAs, verification enabled). Don't disable it.
- If you mistype a model name that doesn't start with `claude-`, your
  request goes to the local backend — including any Bearer token that
  was attached. The local backend ignores the auth header, but be aware
  of this routing default.

## License

GPL-3 (see LICENSE).
