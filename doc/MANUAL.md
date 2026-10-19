# Multi shell MCP server — User manual

This MANUAL is also available in:\
[en-GB](MANUAL.en-GB.md), en-US, [es-AR](MANUAL.es-AR.md), [es-ES](MANUAL.es-ES.md)

## Contents

1. [Starting the server](#1-starting-the-server)
2. [Connecting apps](#2-connecting-apps)
3. [The tools](#3-the-tools)
4. [History of calls](#4-history-of-calls)
5. [Security](#5-security)
6. [Troubleshooting](#6-troubleshooting)

## 1. Starting the server

The server is the single file `multi_shell_mcp_server.py`. It runs on Linux (Debian), macOS and Windows and only
needs Python 3.8 or newer; nothing has to be installed with pip.

Debian and macOS:

```bash
curl -fsSL https://raw.githubusercontent.com/nipegun/multi-shell-mcp-server/refs/heads/main/multi_shell_mcp_server.py | bash
curl -fsSL <same URL> | bash -s -- --host 0.0.0.0 --token MySecret    # with options
python3 multi_shell_mcp_server.py                                      # downloaded
```

Windows (PowerShell; in cmd, `curl -fsSL <URL> | python -` works the same):

```powershell
irm https://raw.githubusercontent.com/nipegun/multi-shell-mcp-server/refs/heads/main/multi_shell_mcp_server.py | python -
irm <same URL> | python - --host 0.0.0.0 --token MySecret              # with options
python multi_shell_mcp_server.py                                       # downloaded (or: py multi_shell_mcp_server.py)
```

The terminal shows the system, the user, the MCP address (`http://127.0.0.1:44444/api/mcp`), the tools found,
whether a token is needed and the folder of the call log. Stop it with `Ctrl+C`; the commands still running are
killed. If the port is already taken by a previous run (or another program of the same user), the server stops that
program before listening.

| Option | Default | Description |
| --- | --- | --- |
| `--host` | `127.0.0.1` | Address to listen on (`0.0.0.0` for every interface) |
| `--port` | `44444` | Port |
| `--token` | `$MULTI_SHELL_MCP_TOKEN` or none | Require `Authorization: Bearer <token>` |
| `--allow-origin` | — | Browser origin allowed besides this same host (repeatable, `*` for any) |
| `--tool-names` | `dot` | `underscore` gives `bash_run`... for apps that reject dots in tool names |
| `--no-call-log` | — | Do not keep a file per call |

The API documentation (Swagger) is at `http://127.0.0.1:44444/api/doc/`: every route, the JSON-RPC messages, and the
arguments and answer of the tools, with examples you can try (*Try it out*). If the server runs with `--token`, press
*Authorize* and paste the token first. With `--host 0.0.0.0`, the firewall of Windows or macOS may ask whether Python
can accept connections.

## 2. Connecting apps

**WebUI of llama-server.** In the MCP servers settings, add the URL `http://127.0.0.1:44444/api/mcp` and, if the
server runs with `--token`, the header `Authorization` with the value `Bearer <token>`. The WebUI calls the server
from the browser: pages of the same computer (any port, such as `:8080`) are allowed. If the WebUI is opened from
another computer, start the server with `--host 0.0.0.0` and use the IP address of this computer in the URL; if the
WebUI is served from another host, add its address with `--allow-origin http://<host>:<port>`.

**Other apps.** Any MCP client with Streamable HTTP uses the same URL and, if needed, the same header. The server
answers plain JSON, without SSE stream and without session, as the shell server of moe-gguf-server does.

## 3. The tools

One tool per shell found when the server starts:

| Tool | Shell | Where |
| --- | --- | --- |
| `bash.run` | Bash | Linux and macOS; on Windows, the one of Git for Windows (or WSL) |
| `zsh.run` | Zsh | macOS (default shell) and Linux if installed |
| `sh.run` | sh | Linux and macOS; on Windows, the one of Git for Windows |
| `psh.run` | PowerShell | Windows (`pwsh` 7 if installed, otherwise Windows PowerShell); Linux and macOS with `pwsh` |
| `cmd.run` | cmd.exe | Windows only |

Each tool takes:

| Argument | Description |
| --- | --- |
| `command` (required) | Command or script to run, in the syntax of that shell |
| `cwd` | Working directory; default the home folder of the user. Relative paths start from the home folder; `~` works |
| `timeout` | Seconds before every process started by the command is killed; 60 by default, 3600 at most |

The answer is a JSON object, as text and as `structuredContent`: `exit_code`, `stdout`, `stderr`, `signal`,
`timed_out`, `cancelled`, `timeout`, `seconds`, `shell` and `cwd`. Long outputs keep the first and the last 8 KiB of
stdout (4 KiB of stderr), with `[... N bytes omitted ...]` in between. Output that is not UTF-8 (cmd writes in the
OEM code page of Windows) is converted.

Every call is a new process without a terminal (on Windows, without a console window) and with empty input, run as
the user that started the server: `cd` and variables do not carry over to the next call, and interactive programs
cannot be used. When the timeout expires or the app closes the connection, every process the command started is
killed (its process group, or its process tree on Windows). Processes left in the background must redirect their
output, because the call returns when the shell exits.

## 4. History of calls

Every call leaves a JSONL file in `~/.multi-shell-mcp-server/` (`%USERPROFILE%\.multi-shell-mcp-server\` on Windows),
named after the local time of the call down to the millisecond (for example `y2026m10d18h09m30s15ms042.jsonl`). The
first line is the command; then the whole output, piece by piece (`stdout` and `stderr` events); last the result, or
the reason the command could not start. On Linux and macOS only the user that runs the server can read them.
`--no-call-log` turns this off.

## 5. Security

- By default the server only listens on `127.0.0.1`. With `--host 0.0.0.0`, anyone on the network can reach it: use
  `--token`.
- Browser pages of other sites cannot call the server: only pages of the same host (any port), loopback pages when
  the server listens on loopback, and the origins of `--allow-origin`. A loopback page is one served from
  `localhost`, `127.x.x.x` or `[::1]`; a domain whose name merely starts with `127.` is not.
- Without `--token`, the server only accepts requests addressed to an IP address or `localhost`, so a web page of a
  domain that points to this computer (DNS rebinding) cannot use it. With `--token`, names work too.

## 6. Troubleshooting

| Problem | What to do |
| --- | --- |
| `python3` or `python` is not found | Install Python (README); on Windows, tick *Add python.exe to PATH* or use `py` |
| The WebUI says the connection failed | Check the URL (`/api/mcp`), that the server is running and, from another computer, `--host 0.0.0.0` and the firewall |
| The terminal says *rejected a request from origin* | Start the server with `--allow-origin` and the origin it prints |
| The terminal says *rejected a request for host* | Use the IP address in the URL, or start the server with `--token` |
| 401 *Missing or wrong token* | Add the header `Authorization: Bearer <token>` in the app |
| An app rejects the tool names | Start the server with `--tool-names underscore` |
| `bash.run` is missing on Windows | Install Git for Windows (it brings `bash.exe` and `sh.exe`) and restart the server |
| A command never ends | It may wait for input or keep its output open: pass a `timeout` or redirect background output |
