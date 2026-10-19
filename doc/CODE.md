# Multi shell MCP server — Code reference

This CODE is also available in:\
[en-GB](CODE.en-GB.md), en-US, [es-AR](CODE.es-AR.md), [es-ES](CODE.es-ES.md)

Everything lives in `multi_shell_mcp_server.py`; `file:line` references point into it.

## Index

1. [Architecture and design decisions](#1-architecture-and-design-decisions)
2. [Module map](#2-module-map)
3. [Key symbols](#3-key-symbols)
4. [Main flows](#4-main-flows)
5. [Entry points and routes](#5-entry-points-and-routes)
6. [Impact analysis](#6-impact-analysis)
7. [Extension points](#7-extension-points)

## 1. Architecture and design decisions

- **One file, standard library only**, so it runs with `curl … | bash` (Debian, macOS) or `irm … | python -`
  (Windows) without pip. It ports the shell MCP server of moe-gguf-server (`backend/tools/server/server-shell-mcp.cpp`)
  minus the sudo prompts, which depend on the mgs WebUI, and adds Windows and macOS.
- **Bash/Python polyglot header** (lines 3–5): bash runs `:` and `exec python3 - "$@"`, which reads the rest of the
  pipe (bash reads pipes byte by byte, so nothing past line 4 is consumed); Python sees those lines as a string, the
  module docstring, so `from __future__ import annotations` can follow it.
- **Portability rules**: the file is plain ASCII (Windows PowerShell 5.1 pipes text with an ASCII encoding) and uses
  no syntax newer than Python 3.8 (`from __future__ import annotations` keeps `X | None` annotations unevaluated), so
  the `python3` that macOS ships works.
- **MCP Streamable HTTP without SSE or sessions**: every POST carries one JSON-RPC message and gets a JSON answer;
  GET and DELETE answer 405. This is the subset the WebUI of llama-server and the reference implementation use.
- **`http.server.ThreadingHTTPServer`**: one thread per connection, so a long command does not block other calls.
  `SO_REUSEADDR` is not set on Windows, where it would let a second program take a port in use.
- **Commands**: `subprocess.Popen`; on POSIX in a new session (own process group, no controlling terminal) killed
  with `killpg`; on Windows in a new process group without console window (`CREATE_NO_WINDOW`), killed as a tree with
  `taskkill /F /T`. stdout and stderr are read by two threads (`fReadPipe`) into a queue, because Windows pipes cannot
  be polled; the request thread consumes the queue, so captures and the call log have a single writer.
- **Windows command lines**: cmd gets `"cmd" /d /s /c "<command>"` (with `/s`, cmd runs what is between the outer
  quotes as it is); PowerShell gets `-EncodedCommand` (base64 of UTF-16) with a prefix that silences progress records
  and switches its output to UTF-8. Output that is not UTF-8 is decoded with the OEM code page (`fDecode`).
- **Browser access**: CORS for allowed origins (the WebUI calls from the page), Origin validation as MCP requires,
  and, without `--token`, Host restricted to IP addresses and `localhost` against DNS rebinding. A host is loopback
  (`fIsLoopback`) only when it is `localhost`, a subdomain of it, `::1` or a `127.x.x.x` IP address: a name such as
  `127.example.com` points wherever its DNS says, so it is not loopback.
- **API documentation**: `fOpenApi` builds the OpenAPI document on every request from the running server, so the tool
  names and whether `POST /api/mcp` needs the token are those of this run; the tool arguments come from
  `fToolInputSchema`, the same schema `tools/list` sends.
- **Conventions**: two-space indentation; prefixes `v`, `c`, `l`, `d`, `ld`, `s`, `a`, `f`, `m`, `p`; code in en-US.

## 2. Module map

| Module | Path | Responsibility | Depends on | Used by |
| --- | --- | --- | --- | --- |
| multi_shell_mcp_server | `multi_shell_mcp_server.py` | Whole server: shells, execution, call log, MCP, HTTP, CLI | Python standard library | the user, MCP clients |

Sections inside the file, in order: settings and state, helpers (system name, decoding), shells, output capture, call
log, command execution, MCP methods, origins and hosts, API documentation, HTTP handler, port freeing, start.

## 3. Key symbols

| Symbol | File:line | What it does |
| --- | --- | --- |
| `lProtocolVersions` | `multi_shell_mcp_server.py:58` | MCP versions answered; the first is offered for unknown ones |
| `ldShellCandidates` | `multi_shell_mcp_server.py:96` | Shells searched for: tool, title, syntax, executables, Windows paths, arguments |
| `dState` | `multi_shell_mcp_server.py:130` | Shells found, home, user, host, system, token, allowed origins, call log folder, fallback encoding |
| `fOsName` | `multi_shell_mcp_server.py:159` | System name for the instructions (Debian, macOS, Windows version) |
| `fDecode` | `multi_shell_mcp_server.py:188` | UTF-8, or the OEM code page on Windows |
| `fFindExecutable` | `multi_shell_mcp_server.py:199` | Looks a shell up in PATH and the usual folders of each system |
| `fDetectShells` | `multi_shell_mcp_server.py:226` | Builds the tools from the shells installed |
| `fShellCommandLine` | `multi_shell_mcp_server.py:247` | Arguments of the shell process (cmd and PowerShell quoting on Windows) |
| `OutputCapture` | `multi_shell_mcp_server.py:278` | Keeps the head and the tail of a stream, UTF-8-safe cut |
| `CallLog` | `multi_shell_mcp_server.py:325` | One JSONL file per call |
| `fKillGroup` | `multi_shell_mcp_server.py:396` | `killpg` on POSIX, `taskkill /F /T` on Windows |
| `fReadPipe` | `multi_shell_mcp_server.py:422` | Reader thread of stdout or stderr |
| `fRunCommand` | `multi_shell_mcp_server.py:442` | Runs a command: spawn, deadline, kill group, drain, exit status |
| `fInitializeResult` | `multi_shell_mcp_server.py:542` | `initialize` answer: version, capabilities, serverInfo, instructions |
| `fToolInputSchema` | `multi_shell_mcp_server.py:571` | Arguments of every tool (`command`, `cwd`, `timeout`), for `tools/list` and OpenAPI |
| `fToolsListResult` | `multi_shell_mcp_server.py:592` | Tool definitions and input schema |
| `fToolsCallResult` | `multi_shell_mcp_server.py:621` | Validates arguments, runs, logs and builds the result |
| `fIsLoopback` | `multi_shell_mcp_server.py:739` | `localhost`, its subdomains, `::1` and `127.x.x.x` IP addresses (never names) |
| `fOriginAllowed` | `multi_shell_mcp_server.py:756` | Origin check of browser requests |
| `fHostAllowed` | `multi_shell_mcp_server.py:773` | DNS rebinding check when there is no token |
| `fOpenApi` | `multi_shell_mcp_server.py:786` | OpenAPI document of `/api/`: every route, JSON-RPC and tool schemas, bearer token when `--token` |
| `McpRequestHandler` | `multi_shell_mcp_server.py:1004` | HTTP handler: CORS, checks, JSON-RPC dispatch |
| `McpRequestHandler.do_POST` | `multi_shell_mcp_server.py:1094` | One JSON-RPC message per request |
| `fListeningPids` | `multi_shell_mcp_server.py:1168` | Processes listening on the port: `/proc`, `lsof` or `netstat` |
| `fFreePort` | `multi_shell_mcp_server.py:1233` | Stops them before listening |
| `fMain` | `multi_shell_mcp_server.py:1287` | Arguments, detection, listening, shutdown |

## 4. Main flows

**Start.** `fMain` → `fParseArguments` → `fFallbackEncoding`, `fOsName`, `fDetectShells`, home/user/host into
`dState` → `fFreePort` → `McpServer` listens → `serve_forever`; Ctrl+C, SIGTERM or Ctrl+Break (`fStopOnSignal`) →
`server_close` → running groups killed with `fKillGroup`.

**tools/call.** `do_POST` → `mRejected` (`fHostAllowed`, `fOriginAllowed`, token) → Content-Type and body → JSON-RPC
→ `fToolsCallResult` (an unexpected exception → -32603): unknown tool → `ValueError` → -32602; `CallLog.mOpen`;
argument checks (rejection → command and error lines, `isError` text); command line logged → `fRunCommand(…,
pShouldStop=fConnectionClosed(socket))`: `fShellCommandLine` → `Popen` → two `fReadPipe` threads → queue →
`OutputCapture` and `CallLog.mOutput` → result line → JSON text + `structuredContent` → `mSendJson`.

**Browser preflight.** `do_OPTIONS` → `fOriginAllowed` → 204 with `Access-Control-Allow-*`; every other answer carries
`Access-Control-Allow-Origin` for allowed origins (`mCorsHeaders`).

## 5. Entry points and routes

| Route / command | Handler | File |
| --- | --- | --- |
| `python3 multi_shell_mcp_server.py [options]`, `curl … \| bash`, `irm … \| python -` | `fMain` | `multi_shell_mcp_server.py` |
| `POST /api/mcp` | `McpRequestHandler.do_POST` | `multi_shell_mcp_server.py` |
| `GET`, `DELETE /api/mcp` | `do_GET`, `do_DELETE` (405) | `multi_shell_mcp_server.py` |
| `OPTIONS /api/mcp` | `do_OPTIONS` | `multi_shell_mcp_server.py` |
| `GET /api/doc` | `do_GET` (301 to `/api/doc/`) | `multi_shell_mcp_server.py` |
| `GET /api/doc/`, `/api/doc/openapi.json` | `do_GET` (`cSwaggerPage`, `fOpenApi`) | `multi_shell_mcp_server.py` |
| MCP `initialize`, `ping`, `tools/list`, `tools/call` | `fInitializeResult`, `{}`, `fToolsListResult`, `fToolsCallResult` | `multi_shell_mcp_server.py` |

## 6. Impact analysis

| Component | If you change it… |
| --- | --- |
| Lines 3–5 (polyglot header) | `curl … \| bash` stops working if bash cannot run them or Python cannot parse them |
| Any character outside ASCII, or syntax newer than 3.8 | `irm … \| python -` on Windows PowerShell 5.1, or the `python3` of macOS, may break |
| `fRunCommand` / `fReadPipe` / `OutputCapture` | The result every client sees, timeouts and the kill of background processes on the three systems |
| `fShellCommandLine` / `cPowerShellPrefix` | How cmd and PowerShell receive commands on Windows (quotes, encoding) |
| Result object in `fToolsCallResult` | What models read and what clients render; the `result` line of the call log |
| `fIsLoopback` / `fOriginAllowed` / `fHostAllowed` / CORS | Whether the WebUI of llama-server can connect, and the protection against web pages |
| `fToolInputSchema` | The arguments every client sends, and the OpenAPI document |
| Tool names (`fDetectShells`, `--tool-names`) | Clients that pinned the names; the boxes that the mgs WebUI draws for `bash.run` |
| `do_POST` JSON-RPC handling | Every MCP client |

## 7. Extension points

- **Add a shell**: append `{"tool", "title", "syntax", "names", "windows_paths", "arguments"}` to
  `ldShellCandidates` (`"windows_only": True` for Windows-only shells); if Windows needs special quoting, add a
  branch in `fShellCommandLine`.
- **Add an MCP method**: add a branch in `do_POST`, declare it in `fInitializeResult` if it is a capability, and
  describe it in `JsonRpcRequest` and `JsonRpcResponse` of `fOpenApi`.
- **Add a tool that is not a shell**: add its definition in `fToolsListResult`, dispatch on its name in
  `fToolsCallResult` before the shell lookup, and add its name and arguments to `ToolsCallParams` in `fOpenApi`.
- **Add a route under `/api/`**: add it in `do_GET`, `do_POST`... and in `paths` of `fOpenApi`.
- **Add an option**: add it in `fParseArguments`, store it in `dState` in `fMain`, document it in README and MANUAL
  (four languages each).
