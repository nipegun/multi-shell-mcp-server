# Servidor MCP multi shell

Este README también está disponible en:\
[en-GB](README.en-GB.md), [en-US](../README.md), [es-AR](README.es-AR.md), es-ES

Un único archivo de Python, sólo con la librería estándar, que levanta un servidor MCP para que la WebUI de
llama-server (y otras aplicaciones MCP) ejecuten comandos en este ordenador. Funciona en Linux (Debian), macOS y
Windows y ofrece una herramienta por cada shell instalado: `bash.run`, `zsh.run`, `sh.run`, `psh.run` (PowerShell) y,
en Windows, `cmd.run`.

> **Aviso.** Quien pueda llamar a este servidor puede hacer todo lo que puede hacer el usuario que lo arrancó. Por
> defecto sólo escucha en `127.0.0.1`; si lo abres a la red, usa `--token`.

## Ejecutarlo directamente desde GitHub

Debian y macOS (terminal):

```bash
curl -fsSL https://raw.githubusercontent.com/nipegun/multi-shell-mcp-server/refs/heads/main/multi_shell_mcp_server.py | bash
curl -fsSL https://raw.githubusercontent.com/nipegun/multi-shell-mcp-server/refs/heads/main/multi_shell_mcp_server.py | bash -s -- --host 0.0.0.0 --token MiSecreto
```

Windows (PowerShell):

```powershell
irm https://raw.githubusercontent.com/nipegun/multi-shell-mcp-server/refs/heads/main/multi_shell_mcp_server.py | python -
irm https://raw.githubusercontent.com/nipegun/multi-shell-mcp-server/refs/heads/main/multi_shell_mcp_server.py | python - --host 0.0.0.0 --token MiSecreto
```

Descargado, en cualquier sistema: `python3 multi_shell_mcp_server.py` (`python` o `py` en Windows).

Sólo necesita Python 3 (3.8 o posterior); no hay que instalar nada con pip:

| Sistema | Cómo conseguir Python |
| --- | --- |
| Debian | `apt install python3` |
| macOS | Viene con las Command Line Tools (el primer `python3` ofrece instalarlas), o `brew install python` |
| Windows | `winget install Python.Python.3.13`, o el instalador de python.org con *Add python.exe to PATH* |

## Conectar la WebUI de llama-server

En la WebUI, abre la configuración de servidores MCP y añade:

- URL: `http://127.0.0.1:44444/api/mcp`
- Cabecera (sólo si arrancaste el servidor con `--token`): `Authorization: Bearer MiSecreto`

Si la WebUI se abre desde otro ordenador, arranca el servidor con `--host 0.0.0.0` y usa la dirección IP de este
ordenador en la URL (por ejemplo `http://192.168.1.10:44444/api/mcp`). Cualquier otra aplicación MCP que hable
Streamable HTTP se conecta con la misma URL.

## Opciones

| Opción | Por defecto | Descripción |
| --- | --- | --- |
| `--host` | `127.0.0.1` | Dirección en la que escucha (`0.0.0.0` para todas las interfaces) |
| `--port` | `44444` | Puerto |
| `--token` | `$MULTI_SHELL_MCP_TOKEN` o ninguno | Exige `Authorization: Bearer <token>` |
| `--allow-origin` | — | Origen de navegador permitido además de este mismo host (repetible, `*` para cualquiera) |
| `--tool-names` | `dot` | `underscore` da `bash_run`... para aplicaciones que rechazan puntos en los nombres de herramienta |
| `--no-call-log` | — | No guarda un archivo por llamada en `~/.multi-shell-mcp-server` |

## Documentación

- [Manual de uso](MANUAL.es-ES.md) ([en-GB](MANUAL.en-GB.md), [en-US](MANUAL.md), [es-AR](MANUAL.es-AR.md))
- [Referencia del código](CODE.es-ES.md) ([en-GB](CODE.en-GB.md), [en-US](CODE.md), [es-AR](CODE.es-AR.md))
