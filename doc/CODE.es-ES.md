# Servidor MCP multi shell — Referencia del código

Este CODE también está disponible en:\
[en-GB](CODE.en-GB.md), [en-US](CODE.md), [es-AR](CODE.es-AR.md), es-ES

Todo está en `multi_shell_mcp_server.py`; las referencias `archivo:línea` apuntan a ese archivo.

## Índice

1. [Arquitectura y decisiones de diseño](#1-arquitectura-y-decisiones-de-diseño)
2. [Mapa de módulos](#2-mapa-de-módulos)
3. [Símbolos clave](#3-símbolos-clave)
4. [Flujos principales](#4-flujos-principales)
5. [Puntos de entrada y rutas](#5-puntos-de-entrada-y-rutas)
6. [Análisis de impacto](#6-análisis-de-impacto)
7. [Puntos de extensión](#7-puntos-de-extensión)

## 1. Arquitectura y decisiones de diseño

- **Un único archivo, sólo librería estándar**, para que funcione con `curl … | bash` (Debian, macOS) o
  `irm … | python -` (Windows) sin pip. Es una adaptación del servidor MCP de shell de moe-gguf-server
  (`backend/tools/server/server-shell-mcp.cpp`) sin las peticiones de contraseña de sudo, que dependen de la WebUI de
  mgs, y con soporte para Windows y macOS.
- **Cabecera políglota bash/Python** (líneas 3–5): bash ejecuta `:` y `exec python3 - "$@"`, que lee el resto de la
  tubería (bash lee las tuberías byte a byte, así que no consume nada más allá de la línea 4); Python ve esas líneas
  como una cadena, el docstring del módulo, así que `from __future__ import annotations` puede ir detrás.
- **Reglas de portabilidad**: el archivo es ASCII puro (Windows PowerShell 5.1 pasa el texto por tubería con
  codificación ASCII) y no usa sintaxis posterior a Python 3.8 (`from __future__ import annotations` evita evaluar las
  anotaciones `X | None`), para que funcione el `python3` que trae macOS.
- **MCP Streamable HTTP sin SSE ni sesiones**: cada POST lleva un mensaje JSON-RPC y recibe una respuesta JSON; GET y
  DELETE responden 405. Es el subconjunto que usan la WebUI de llama-server y la implementación de referencia.
- **`http.server.ThreadingHTTPServer`**: un hilo por conexión, para que un comando largo no bloquee otras llamadas.
  En Windows no se activa `SO_REUSEADDR`, porque permitiría a otro programa ocupar un puerto en uso.
- **Comandos**: `subprocess.Popen`; en POSIX, en una sesión nueva (grupo de procesos propio, sin terminal de control)
  que se mata con `killpg`; en Windows, en un grupo de procesos nuevo sin ventana de consola (`CREATE_NO_WINDOW`), que
  se mata como árbol con `taskkill /F /T`. stdout y stderr los leen dos hilos (`fReadPipe`) hacia una cola, porque en
  Windows no se puede sondear una tubería; el hilo de la petición consume la cola, así que las capturas y el registro
  de llamadas tienen un único escritor.
- **Líneas de comandos en Windows**: cmd recibe `"cmd" /d /s /c "<comando>"` (con `/s`, cmd ejecuta tal cual lo que hay
  entre las comillas exteriores); PowerShell recibe `-EncodedCommand` (base64 de UTF-16) con un prefijo que silencia
  las barras de progreso y pasa su salida a UTF-8. La salida que no está en UTF-8 se decodifica con la página de
  códigos OEM (`fDecode`).
- **Acceso desde el navegador**: CORS para los orígenes permitidos (la WebUI llama desde la página), validación de
  Origin como exige MCP y, sin `--token`, Host limitado a direcciones IP y `localhost` contra el DNS rebinding. Un host
  sólo es loopback (`fIsLoopback`) si es `localhost`, un subdominio suyo, `::1` o una dirección IP `127.x.x.x`: un
  nombre como `127.example.com` apunta adonde diga su DNS, así que no es loopback.
- **Documentación de la API**: `fOpenApi` construye el documento OpenAPI en cada petición a partir del servidor en
  marcha, así que los nombres de las herramientas y si `POST /api/mcp` exige el token son los de esta ejecución; los
  argumentos de las herramientas salen de `fToolInputSchema`, el mismo esquema que envía `tools/list`.
- **Convenciones**: indentación de dos espacios; prefijos `v`, `c`, `l`, `d`, `ld`, `s`, `a`, `f`, `m`, `p`; código
  en en-US.

## 2. Mapa de módulos

| Módulo | Ruta | Responsabilidad | Depende de | Usado por |
| --- | --- | --- | --- | --- |
| multi_shell_mcp_server | `multi_shell_mcp_server.py` | Todo el servidor: shells, ejecución, registro de llamadas, MCP, HTTP, línea de comandos | Librería estándar de Python | el usuario, los clientes MCP |

Secciones dentro del archivo, en orden: ajustes y estado, utilidades (nombre del sistema, decodificación), shells,
captura de salida, registro de llamadas, ejecución de comandos, métodos MCP, orígenes y hosts, documentación de la
API, manejador HTTP, liberación del puerto, arranque.

## 3. Símbolos clave

| Símbolo | Archivo:línea | Qué hace |
| --- | --- | --- |
| `lProtocolVersions` | `multi_shell_mcp_server.py:58` | Versiones de MCP que responde; la primera se ofrece a las desconocidas |
| `ldShellCandidates` | `multi_shell_mcp_server.py:96` | Shells que se buscan: herramienta, título, sintaxis, ejecutables, rutas de Windows, argumentos |
| `dState` | `multi_shell_mcp_server.py:130` | Shells encontrados, carpeta personal, usuario, host, sistema, token, orígenes permitidos, carpeta del registro, codificación de respaldo |
| `fOsName` | `multi_shell_mcp_server.py:159` | Nombre del sistema para las instrucciones (Debian, macOS, versión de Windows) |
| `fDecode` | `multi_shell_mcp_server.py:188` | UTF-8, o la página de códigos OEM en Windows |
| `fFindExecutable` | `multi_shell_mcp_server.py:199` | Busca un shell en el PATH y en las carpetas habituales de cada sistema |
| `fDetectShells` | `multi_shell_mcp_server.py:226` | Construye las herramientas a partir de los shells instalados |
| `fShellCommandLine` | `multi_shell_mcp_server.py:247` | Argumentos del proceso del shell (comillas de cmd y PowerShell en Windows) |
| `OutputCapture` | `multi_shell_mcp_server.py:278` | Conserva el principio y el final de un flujo, con corte seguro en UTF-8 |
| `CallLog` | `multi_shell_mcp_server.py:325` | Un archivo JSONL por llamada |
| `fKillGroup` | `multi_shell_mcp_server.py:396` | `killpg` en POSIX, `taskkill /F /T` en Windows |
| `fReadPipe` | `multi_shell_mcp_server.py:422` | Hilo lector de stdout o stderr |
| `fRunCommand` | `multi_shell_mcp_server.py:442` | Ejecuta un comando: arranque, tiempo límite, muerte del grupo, vaciado, estado de salida |
| `fInitializeResult` | `multi_shell_mcp_server.py:542` | Respuesta a `initialize`: versión, capacidades, serverInfo, instrucciones |
| `fToolInputSchema` | `multi_shell_mcp_server.py:571` | Argumentos de todas las herramientas (`command`, `cwd`, `timeout`), para `tools/list` y OpenAPI |
| `fToolsListResult` | `multi_shell_mcp_server.py:592` | Definiciones de las herramientas y su esquema de entrada |
| `fToolsCallResult` | `multi_shell_mcp_server.py:621` | Valida los argumentos, ejecuta, registra y construye el resultado |
| `fIsLoopback` | `multi_shell_mcp_server.py:739` | `localhost`, sus subdominios, `::1` y las direcciones IP `127.x.x.x` (nunca nombres) |
| `fOriginAllowed` | `multi_shell_mcp_server.py:756` | Comprobación de Origin de las peticiones del navegador |
| `fHostAllowed` | `multi_shell_mcp_server.py:773` | Comprobación contra DNS rebinding cuando no hay token |
| `fOpenApi` | `multi_shell_mcp_server.py:786` | Documento OpenAPI de `/api/`: todas las rutas, esquemas JSON-RPC y de las herramientas, token bearer con `--token` |
| `McpRequestHandler` | `multi_shell_mcp_server.py:1004` | Manejador HTTP: CORS, comprobaciones, despacho JSON-RPC |
| `McpRequestHandler.do_POST` | `multi_shell_mcp_server.py:1094` | Un mensaje JSON-RPC por petición |
| `fListeningPids` | `multi_shell_mcp_server.py:1168` | Procesos que escuchan en el puerto: `/proc`, `lsof` o `netstat` |
| `fFreePort` | `multi_shell_mcp_server.py:1233` | Los detiene antes de escuchar |
| `fMain` | `multi_shell_mcp_server.py:1287` | Argumentos, detección, escucha y parada |

## 4. Flujos principales

**Arranque.** `fMain` → `fParseArguments` → `fFallbackEncoding`, `fOsName`, `fDetectShells`, carpeta
personal/usuario/host en `dState` → `fFreePort` → `McpServer` escucha → `serve_forever`; Ctrl+C, SIGTERM o
Ctrl+Pausa (`fStopOnSignal`) → `server_close` → se matan los grupos en ejecución con `fKillGroup`.

**tools/call.** `do_POST` → `mRejected` (`fHostAllowed`, `fOriginAllowed`, token) → Content-Type y cuerpo → JSON-RPC
→ `fToolsCallResult` (una excepción inesperada → -32603): herramienta desconocida → `ValueError` → -32602;
`CallLog.mOpen`; comprobación de argumentos (rechazo → líneas de comando y de error, texto con `isError`); se
registra el comando → `fRunCommand(…, pShouldStop=fConnectionClosed(socket))`: `fShellCommandLine` → `Popen` → dos
hilos `fReadPipe` → cola → `OutputCapture` y `CallLog.mOutput` → línea de resultado → texto JSON +
`structuredContent` → `mSendJson`.

**Preflight del navegador.** `do_OPTIONS` → `fOriginAllowed` → 204 con `Access-Control-Allow-*`; todas las demás
respuestas llevan `Access-Control-Allow-Origin` para los orígenes permitidos (`mCorsHeaders`).

## 5. Puntos de entrada y rutas

| Ruta / comando | Manejador | Archivo |
| --- | --- | --- |
| `python3 multi_shell_mcp_server.py [opciones]`, `curl … \| bash`, `irm … \| python -` | `fMain` | `multi_shell_mcp_server.py` |
| `POST /api/mcp` | `McpRequestHandler.do_POST` | `multi_shell_mcp_server.py` |
| `GET`, `DELETE /api/mcp` | `do_GET`, `do_DELETE` (405) | `multi_shell_mcp_server.py` |
| `OPTIONS /api/mcp` | `do_OPTIONS` | `multi_shell_mcp_server.py` |
| `GET /api/doc` | `do_GET` (301 a `/api/doc/`) | `multi_shell_mcp_server.py` |
| `GET /api/doc/`, `/api/doc/openapi.json` | `do_GET` (`cSwaggerPage`, `fOpenApi`) | `multi_shell_mcp_server.py` |
| MCP `initialize`, `ping`, `tools/list`, `tools/call` | `fInitializeResult`, `{}`, `fToolsListResult`, `fToolsCallResult` | `multi_shell_mcp_server.py` |

## 6. Análisis de impacto

| Componente | Si lo cambias… |
| --- | --- |
| Líneas 3–5 (cabecera políglota) | `curl … \| bash` deja de funcionar si bash no puede ejecutarlas o Python no puede analizarlas |
| Cualquier carácter no ASCII, o sintaxis posterior a 3.8 | Pueden fallar `irm … \| python -` en Windows PowerShell 5.1 o el `python3` de macOS |
| `fRunCommand` / `fReadPipe` / `OutputCapture` | El resultado que ven todos los clientes, los tiempos límite y la muerte de los procesos en segundo plano en los tres sistemas |
| `fShellCommandLine` / `cPowerShellPrefix` | Cómo reciben los comandos cmd y PowerShell en Windows (comillas, codificación) |
| Objeto de resultado en `fToolsCallResult` | Lo que leen los modelos y lo que muestran los clientes; la línea `result` del registro de llamadas |
| `fIsLoopback` / `fOriginAllowed` / `fHostAllowed` / CORS | Si la WebUI de llama-server puede conectarse, y la protección frente a páginas web |
| `fToolInputSchema` | Los argumentos que envían todos los clientes, y el documento OpenAPI |
| Nombres de las herramientas (`fDetectShells`, `--tool-names`) | Los clientes que fijaron los nombres; las cajas que la WebUI de mgs dibuja para `bash.run` |
| Tratamiento JSON-RPC de `do_POST` | Todos los clientes MCP |

## 7. Puntos de extensión

- **Añadir un shell**: añade `{"tool", "title", "syntax", "names", "windows_paths", "arguments"}` a
  `ldShellCandidates` (`"windows_only": True` para los que sólo existen en Windows); si Windows necesita comillas
  especiales, añade una rama en `fShellCommandLine`.
- **Añadir un método MCP**: añade una rama en `do_POST`, declárala en `fInitializeResult` si es una capacidad y
  descríbela en `JsonRpcRequest` y `JsonRpcResponse` de `fOpenApi`.
- **Añadir una herramienta que no sea un shell**: añade su definición en `fToolsListResult`, despáchala por su nombre
  en `fToolsCallResult` antes de buscar el shell y añade su nombre y sus argumentos a `ToolsCallParams` en `fOpenApi`.
- **Añadir una ruta bajo `/api/`**: añádela en `do_GET`, `do_POST`... y en `paths` de `fOpenApi`.
- **Añadir una opción**: añádela en `fParseArguments`, guárdala en `dState` en `fMain` y documéntala en el README y el
  MANUAL (en los cuatro idiomas).
