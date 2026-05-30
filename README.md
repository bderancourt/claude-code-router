# claude-code-router

Minimal stdlib-only routing proxy that lets a single Claude Code session
talk to **either** Anthropic's hosted models **or** a local llama.cpp server,
chosen per-request by the model name.

## Why

The official Claude Code CLI talks to one backend per process (set via
`ANTHROPIC_BASE_URL`). To switch between a local model and Anthropic during
the same session, a small router is needed in front. Existing routers
(claude-code-router by musistudio, LiteLLM, etc.) are large TypeScript /
Python projects with many dependencies — overkill, and an audit burden when
you care about not leaking your Claude Pro OAuth token.

This proxy is **one file, stdlib only**, ~230 lines. Audit it yourself.

## Routing

| `model` field in the request | Destination |
|---|---|
| starts with `claude-` | `https://api.anthropic.com` (your normal Pro/API auth) |
| anything else (or no model) | `http://127.0.0.1:8080` (local llama.cpp) |

## Configuration

All via environment variables (defaults shown):

| Var | Default | Purpose |
|---|---|---|
| `CCR_LISTEN_HOST` | `127.0.0.1` | bind address |
| `CCR_LISTEN_PORT` | `8082` | bind port |
| `CCR_LOCAL_HOST` | `127.0.0.1` | local backend host |
| `CCR_LOCAL_PORT` | `8080` | local backend port (llama.cpp default) |
| `CCR_ANTHROPIC_HOST` | `api.anthropic.com` | upstream Anthropic host |
| `CCR_ANTHROPIC_PORT` | `443` | upstream Anthropic port |

## Usage

Start the proxy:

```sh
python3 proxy.py
```

Point Claude Code at it:

```sh
ANTHROPIC_BASE_URL=http://127.0.0.1:8082 claude
```

Inside Claude Code, use `/model <name>` to switch:

- `/model claude-sonnet-4-6` → goes to Anthropic
- `/model qwen3.6-35b-a3b-UD-Q5_K_XL-ctx200k` → goes to llama.cpp

## Cross-provider switching: thinking blocks

When you switch from a local model to an Anthropic model mid-session,
Claude Code resends the full conversation history. Anthropic validates
the cryptographic signature of any `thinking` block in that history;
blocks produced by another provider (llama.cpp emits them for reasoning
models like qwen3) fail validation and the request is rejected:

```
API Error: 400 messages.N.content.0: Invalid `signature` in `thinking` block
```

To make in-session switching usable, the proxy **strips `thinking` and
`redacted_thinking` blocks from message history before forwarding to
Anthropic**. The user-visible text of prior assistant turns is preserved;
only the reasoning trace is dropped (it could not have transferred
meaningfully across providers anyway).

This is the **only** body modification the proxy performs. Requests bound
for the local backend — and Anthropic-bound requests that don't contain
foreign thinking blocks — are byte-for-byte pass-through.

## Security properties

- **Bind:** only `127.0.0.1` by default (never accept external connections).
- **No dependencies:** stdlib only. `python3 proxy.py` works on any 3.10+.
- **Auth pass-through:** the `Authorization` header (your Pro OAuth Bearer)
  is forwarded **transparently** to whichever backend the model dictates.
  The proxy never reads, parses, or stores it.
- **No body logging:** request and response bodies are never written to
  disk or stderr. Only metadata is logged: method, path, target label,
  and the model name extracted from the body (which the user typed).
- **Bodies are not modified**, with one documented exception: for
  requests routed to Anthropic, `thinking` / `redacted_thinking` content
  blocks are removed from `messages[].content[]` (see "Cross-provider
  switching" above). No other field is touched.
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
  was attached. The local backend (llama.cpp) ignores the auth header,
  but be aware of this routing default.

## License

GPL-3 (see LICENSE).
