# Servidor MCP multi shell — Manual de uso

Este MANUAL también está disponible en:\
[en-GB](MANUAL.en-GB.md), [en-US](MANUAL.md), [es-AR](MANUAL.es-AR.md), es-ES

## Contenido

1. [Arrancar el servidor](#1-arrancar-el-servidor)
2. [Conectar aplicaciones](#2-conectar-aplicaciones)
3. [Las herramientas](#3-las-herramientas)
4. [Historial de llamadas](#4-historial-de-llamadas)
5. [Seguridad](#5-seguridad)
6. [Solución de problemas](#6-solución-de-problemas)

## 1. Arrancar el servidor

El servidor es el único archivo `multi_shell_mcp_server.py`. Funciona en Linux (Debian), macOS y Windows y sólo
necesita Python 3.8 o posterior; no hay que instalar nada con pip.

Debian y macOS:

```bash
curl -fsSL https://raw.githubusercontent.com/nipegun/multi-shell-mcp-server/refs/heads/main/multi_shell_mcp_server.py | bash
curl -fsSL <misma URL> | bash -s -- --host 0.0.0.0 --token MiSecreto    # con opciones
python3 multi_shell_mcp_server.py                                        # descargado
```

Windows (PowerShell; en cmd, `curl -fsSL <URL> | python -` funciona igual):

```powershell
irm https://raw.githubusercontent.com/nipegun/multi-shell-mcp-server/refs/heads/main/multi_shell_mcp_server.py | python -
irm <misma URL> | python - --host 0.0.0.0 --token MiSecreto              # con opciones
python multi_shell_mcp_server.py                                         # descargado (o: py multi_shell_mcp_server.py)
```

La terminal muestra el sistema, el usuario, la dirección MCP (`http://127.0.0.1:44444/api/mcp`), las herramientas
encontradas, si hace falta token y la carpeta del registro de llamadas. Se detiene con `Ctrl+C`; los comandos que
sigan en ejecución se matan. Si el puerto ya está ocupado por una ejecución anterior (u otro programa del mismo
usuario), el servidor detiene ese programa antes de escuchar.

| Opción | Por defecto | Descripción |
| --- | --- | --- |
| `--host` | `127.0.0.1` | Dirección en la que escucha (`0.0.0.0` para todas las interfaces) |
| `--port` | `44444` | Puerto |
| `--token` | `$MULTI_SHELL_MCP_TOKEN` o ninguno | Exige `Authorization: Bearer <token>` |
| `--allow-origin` | — | Origen de navegador permitido además de este mismo host (repetible, `*` para cualquiera) |
| `--tool-names` | `dot` | `underscore` da `bash_run`... para aplicaciones que rechazan puntos en los nombres de herramienta |
| `--no-call-log` | — | No guarda un archivo por llamada |

La documentación de la API (Swagger) está en `http://127.0.0.1:44444/api/doc/`: todas las rutas, los mensajes
JSON-RPC y los argumentos y la respuesta de las herramientas, con ejemplos que puedes probar (*Try it out*). Si el
servidor se ejecuta con `--token`, pulsa antes *Authorize* y pega el token. Con `--host 0.0.0.0`, el cortafuegos de
Windows o de macOS puede preguntar si Python puede aceptar conexiones.

## 2. Conectar aplicaciones

**WebUI de llama-server.** En la configuración de servidores MCP, añade la URL `http://127.0.0.1:44444/api/mcp` y, si
el servidor se ejecuta con `--token`, la cabecera `Authorization` con el valor `Bearer <token>`. La WebUI llama al
servidor desde el navegador: las páginas del mismo ordenador (cualquier puerto, como `:8080`) están permitidas. Si la
WebUI se abre desde otro ordenador, arranca el servidor con `--host 0.0.0.0` y usa la dirección IP de este ordenador
en la URL; si la WebUI se sirve desde otro host, añade su dirección con `--allow-origin http://<host>:<puerto>`.

**Otras aplicaciones.** Cualquier cliente MCP con Streamable HTTP usa la misma URL y, si hace falta, la misma
cabecera. El servidor responde JSON simple, sin flujo SSE y sin sesión, igual que el servidor de shell de
moe-gguf-server.

## 3. Las herramientas

Una herramienta por cada shell encontrado al arrancar el servidor:

| Herramienta | Shell | Dónde |
| --- | --- | --- |
| `bash.run` | Bash | Linux y macOS; en Windows, el de Git for Windows (o WSL) |
| `zsh.run` | Zsh | macOS (shell por defecto) y Linux si está instalado |
| `sh.run` | sh | Linux y macOS; en Windows, el de Git for Windows |
| `psh.run` | PowerShell | Windows (`pwsh` 7 si está instalado; si no, Windows PowerShell); Linux y macOS con `pwsh` |
| `cmd.run` | cmd.exe | Sólo Windows |

Cada herramienta recibe:

| Argumento | Descripción |
| --- | --- |
| `command` (obligatorio) | Comando o script que se ejecuta, con la sintaxis de ese shell |
| `cwd` | Directorio de trabajo; por defecto, la carpeta personal del usuario. Las rutas relativas parten de la carpeta personal; `~` funciona |
| `timeout` | Segundos antes de matar todos los procesos que lanzó el comando; 60 por defecto, 3600 como máximo |

La respuesta es un objeto JSON, como texto y como `structuredContent`: `exit_code`, `stdout`, `stderr`, `signal`,
`timed_out`, `cancelled`, `timeout`, `seconds`, `shell` y `cwd`. Las salidas largas conservan los primeros y los
últimos 8 KiB de stdout (4 KiB de stderr), con `[... N bytes omitted ...]` en medio. La salida que no está en UTF-8
(cmd escribe en la página de códigos OEM de Windows) se convierte.

Cada llamada es un proceso nuevo sin terminal (en Windows, sin ventana de consola) y con la entrada vacía, ejecutado
con el usuario que arrancó el servidor: `cd` y las variables no pasan a la llamada siguiente, y no se pueden usar
programas interactivos. Cuando vence el tiempo límite o la aplicación cierra la conexión, se matan todos los procesos
que lanzó el comando (su grupo de procesos, o su árbol de procesos en Windows). Los procesos que se dejen en segundo
plano deben redirigir su salida, porque la llamada termina cuando termina el shell.

## 4. Historial de llamadas

Cada llamada deja un archivo JSONL en `~/.multi-shell-mcp-server/` (`%USERPROFILE%\.multi-shell-mcp-server\` en
Windows), con el nombre de la hora local de la llamada hasta el milisegundo (por ejemplo
`y2026m10d18h09m30s15ms042.jsonl`). La primera línea es el comando; después, la salida completa, trozo a trozo
(eventos `stdout` y `stderr`); y al final, el resultado, o el motivo por el que el comando no pudo arrancar. En Linux
y macOS sólo puede leerlos el usuario que ejecuta el servidor. `--no-call-log` lo desactiva.

## 5. Seguridad

- Por defecto el servidor sólo escucha en `127.0.0.1`. Con `--host 0.0.0.0` cualquiera de la red puede llegar a él:
  usa `--token`.
- Las páginas de otros sitios no pueden llamar al servidor: sólo las páginas del mismo host (cualquier puerto), las
  páginas de loopback cuando el servidor escucha en loopback y los orígenes de `--allow-origin`. Una página de
  loopback es la que se sirve desde `localhost`, `127.x.x.x` o `[::1]`; un dominio cuyo nombre sólo empiece por `127.`
  no lo es.
- Sin `--token`, el servidor sólo acepta peticiones dirigidas a una dirección IP o a `localhost`, para que una página
  web de un dominio que apunte a este ordenador (DNS rebinding) no pueda usarlo. Con `--token`, los nombres también
  funcionan.

## 6. Solución de problemas

| Problema | Qué hacer |
| --- | --- |
| No se encuentra `python3` o `python` | Instala Python (README); en Windows, marca *Add python.exe to PATH* o usa `py` |
| La WebUI dice que falló la conexión | Comprueba la URL (`/api/mcp`), que el servidor esté en marcha y, desde otro ordenador, `--host 0.0.0.0` y el cortafuegos |
| La terminal dice *rejected a request from origin* | Arranca el servidor con `--allow-origin` y el origen que muestra |
| La terminal dice *rejected a request for host* | Usa la dirección IP en la URL, o arranca el servidor con `--token` |
| 401 *Missing or wrong token* | Añade la cabecera `Authorization: Bearer <token>` en la aplicación |
| Una aplicación rechaza los nombres de las herramientas | Arranca el servidor con `--tool-names underscore` |
| Falta `bash.run` en Windows | Instala Git for Windows (trae `bash.exe` y `sh.exe`) y reinicia el servidor |
| Un comando no termina nunca | Puede estar esperando entrada o mantener abierta su salida: pasa un `timeout` o redirige la salida de los procesos en segundo plano |
