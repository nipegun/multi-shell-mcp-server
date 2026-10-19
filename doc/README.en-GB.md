# Multi shell MCP server

This README is also available in:\
en-GB, [en-US](../README.md), [es-AR](README.es-AR.md), [es-ES](README.es-ES.md)

A single Python file, with nothing but the standard library, that starts an MCP server so the WebUI of llama-server
(and other MCP apps) can run commands on this computer. It works on Linux (Debian), macOS and Windows and offers one
tool per shell installed: `bash.run`, `zsh.run`, `sh.run`, `psh.run` (PowerShell) and, on Windows, `cmd.run`.

> **Warning.** Whoever can call this server can do anything the user that started it can do. By default it only
> listens on `127.0.0.1`; if you open it to the network, use `--token`.

## Run it straight from GitHub

Debian and macOS (terminal):

```bash
curl -fsSL https://raw.githubusercontent.com/nipegun/multi-shell-mcp-server/refs/heads/main/multi_shell_mcp_server.py | bash
curl -fsSL https://raw.githubusercontent.com/nipegun/multi-shell-mcp-server/refs/heads/main/multi_shell_mcp_server.py | bash -s -- --host 0.0.0.0 --token MySecret
```

Windows (PowerShell):

```powershell
irm https://raw.githubusercontent.com/nipegun/multi-shell-mcp-server/refs/heads/main/multi_shell_mcp_server.py | python -
irm https://raw.githubusercontent.com/nipegun/multi-shell-mcp-server/refs/heads/main/multi_shell_mcp_server.py | python - --host 0.0.0.0 --token MySecret
```

Downloaded, on any system: `python3 multi_shell_mcp_server.py` (`python` or `py` on Windows).

The only requirement is Python 3 (3.8 or newer); nothing has to be installed with pip:

| System | How to get Python |
| --- | --- |
| Debian | `apt install python3` |
| macOS | It comes with the Command Line Tools (the first `python3` offers to install them), or `brew install python` |
| Windows | `winget install Python.Python.3.13`, or the installer of python.org with *Add python.exe to PATH* |

## Connect the WebUI of llama-server

In the WebUI, open the MCP servers settings and add:

- URL: `http://127.0.0.1:44444/api/mcp`
- Header (only if you started the server with `--token`): `Authorization: Bearer MySecret`

If the WebUI is opened from another computer, start the server with `--host 0.0.0.0` and use the IP address of this
computer in the URL (for example `http://192.168.1.10:44444/api/mcp`). Any other MCP app that speaks Streamable HTTP
connects with the same URL.

## Options

| Option | Default | Description |
| --- | --- | --- |
| `--host` | `127.0.0.1` | Address to listen on (`0.0.0.0` for every interface) |
| `--port` | `44444` | Port |
| `--token` | `$MULTI_SHELL_MCP_TOKEN` or none | Require `Authorization: Bearer <token>` |
| `--allow-origin` | — | Browser origin allowed besides this same host (repeatable, `*` for any) |
| `--tool-names` | `dot` | `underscore` gives `bash_run`... for apps that reject dots in tool names |
| `--no-call-log` | — | Do not keep a file per call in `~/.multi-shell-mcp-server` |

## Documentation

- [User manual](MANUAL.en-GB.md) ([en-US](MANUAL.md), [es-AR](MANUAL.es-AR.md), [es-ES](MANUAL.es-ES.md))
- [Code reference](CODE.en-GB.md) ([en-US](CODE.md), [es-AR](CODE.es-AR.md), [es-ES](CODE.es-ES.md))
