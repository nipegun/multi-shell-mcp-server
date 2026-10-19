#!/usr/bin/env -S PYTHONDONTWRITEBYTECODE=1 python3

""":"
exec python3 - "$@"
":"""

# Multi shell MCP server: a single file, with nothing but the Python standard library, for Linux, macOS and Windows.
#
# It answers MCP Streamable HTTP at http://127.0.0.1:44444/api/mcp (plain JSON answers, no SSE stream, no session),
# so the WebUI of llama-server and other MCP clients can run commands on this computer. It exposes one tool per shell
# found: bash.run, zsh.run, sh.run, psh.run (PowerShell) and, on Windows, cmd.run. Each call runs as the user that
# started the server, without a terminal and with empty input, in its own process group, so a timeout or a closed
# connection kills every process the command started.
#
# Usage:
#   Linux and macOS:  curl -fsSL <raw URL of this file> | bash
#                     curl -fsSL <raw URL of this file> | bash -s -- --host 0.0.0.0 --token SECRET
#   Windows:          irm <raw URL of this file> | python -
#                     irm <raw URL of this file> | python - --host 0.0.0.0 --token SECRET
#   Anywhere:         python3 multi_shell_mcp_server.py [--host 0.0.0.0] [--port 44444] [--token SECRET]
#
# The three lines after the shebang let bash run this file from a pipe: bash runs ":" and then hands the rest of its
# input to Python, which reads those lines as a string and ignores them. The file is plain ASCII, so Windows
# PowerShell can pipe it to Python without changing any character, and it avoids syntax newer than Python 3.8, so the
# python3 that macOS ships works too.

from __future__ import annotations

import argparse
import base64
import codecs
import ctypes
import getpass
import hmac
import json
import os
import platform
import queue
import select
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

cServerName = "multi-shell-mcp-server"
cServerTitle = "multi shell mcp server"
cVersion = "1.0.0"
cWindows = os.name == "nt"

# Protocol versions this server can answer; the first one is offered when the client asks for an unknown version.
lProtocolVersions = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]

cDefaultHost = "127.0.0.1"
cDefaultPort = 44444
cMcpPath = "/api/mcp"
cDocPath = "/api/doc/"
cDefaultTimeoutSeconds = 60
cMaxTimeoutSeconds = 3600
cStdoutHalfBytes = 8 * 1024 # kept from the beginning and from the end of stdout
cStderrHalfBytes = 4 * 1024 # kept from the beginning and from the end of stderr
cReadBytes = 64 * 1024
cPollSeconds = 0.1
cDrainSeconds = 0.25        # output still read after the shell exits
cKillWaitSeconds = 10       # wait for a killed shell before answering without its status
cFreePortSeconds = 5
cMaxBodyBytes = 4 * 1024 * 1024
cLogCommandChars = 300
cCallLogFolder = "~/.multi-shell-mcp-server" # one JSONL file per call
cTokenVariable = "MULTI_SHELL_MCP_TOKEN"
cSwaggerVersion = "5.17.14"

# Prepended to every PowerShell command on Windows: progress bars would reach stderr as CLIXML, and the console code
# page would mangle what is not ASCII.
cPowerShellPrefix = (
  "$ProgressPreference='SilentlyContinue';"
  "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
  "$OutputEncoding=[System.Text.Encoding]::UTF8;"
)

# JSON-RPC error codes
cParseError = -32700
cInvalidRequest = -32600
cMethodNotFound = -32601
cInvalidParams = -32602
cInternalError = -32603

# "tool" is the first part of the tool name, "names" the executables searched for in PATH, in order, "windows_paths"
# the usual places on Windows that PATH may lack, and "arguments" what goes between the interpreter and the command.
ldShellCandidates = [
  {
    "tool": "bash", "title": "Bash", "syntax": "Bash", "names": ["bash"],
    "windows_paths": [r"C:\Program Files\Git\bin\bash.exe"], "arguments": ["-c"],
  },
  {"tool": "zsh", "title": "Zsh", "syntax": "Zsh", "names": ["zsh"], "windows_paths": [], "arguments": ["-c"]},
  {
    "tool": "sh", "title": "sh", "syntax": "POSIX sh", "names": ["sh"],
    "windows_paths": [r"C:\Program Files\Git\bin\sh.exe"], "arguments": ["-c"],
  },
  {
    "tool": "psh", "title": "PowerShell", "syntax": "PowerShell", "names": ["pwsh", "powershell"],
    "windows_paths": [r"C:\Program Files\PowerShell\7\pwsh.exe"],
    "arguments": ["-NoLogo", "-NoProfile", "-NonInteractive", "-Command"],
  },
  {
    "tool": "cmd", "title": "cmd", "syntax": "cmd.exe batch", "names": ["cmd"], "windows_paths": [],
    "arguments": ["/d", "/s", "/c"], "windows_only": True,
  },
]

# Terminal glyph drawn for light and for dark backgrounds.
cIconLight = (
  "<svg xmlns='http://www.w3.org/2000/svg' width='24' height='24' viewBox='0 0 24 24' fill='none' "
  "stroke='%231f2937' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>"
  "<rect x='3' y='3' width='18' height='18' rx='2'/><path d='m7 9 3 3-3 3'/><path d='M12 15h5'/></svg>"
)
cIconDark = (
  "<svg xmlns='http://www.w3.org/2000/svg' width='24' height='24' viewBox='0 0 24 24' fill='none' "
  "stroke='%23e5e7eb' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>"
  "<rect x='3' y='3' width='18' height='18' rx='2'/><path d='m7 9 3 3-3 3'/><path d='M12 15h5'/></svg>"
)

# Filled by fMain: settings and what was found on this computer.
dState = {
  "shells": {},            # tool name -> {"tool", "base", "title", "syntax", "path", "arguments"}
  "home": "",              # default working directory
  "user": "",
  "host": "",
  "os": "",                # e.g. "Debian GNU/Linux 13 (trixie)", "macOS 15.1", "Windows 11 (10.0.26100)"
  "token": "",             # when set, requests need "Authorization: Bearer <token>"
  "allowed_origins": [],   # browser origins allowed besides this same host
  "call_log": "",          # folder of the call log, or empty
  "fallback_encoding": "", # for output that is not UTF-8 (the OEM code page of cmd on Windows)
}
sRunningPids = set()       # process groups of the running commands, killed when the server stops
vRunningLock = threading.Lock()


def fLog(pText: str) -> None:
  sys.stderr.write(time.strftime("%Y-%m-%d %H:%M:%S ") + pText + "\n")
  sys.stderr.flush()


def fLogCommand(pCommand: str) -> str:
  vText = pCommand.replace("\n", "\\n").replace("\r", "\\r")
  return vText if len(vText) <= cLogCommandChars else vText[:cLogCommandChars] + "..."


def fDataUri(pSvg: str) -> str:
  return "data:image/svg+xml;utf8," + pSvg.replace("<", "%3C").replace(">", "%3E").replace(" ", "%20")


def fOsName() -> str:
  """Name and version of the system, so the model writes commands for it."""
  vSystem = platform.system()
  if vSystem == "Darwin":
    return "macOS " + platform.mac_ver()[0]
  if vSystem == "Windows":
    return f"Windows {platform.release()} ({platform.version()})"
  if vSystem == "Linux":
    try:
      for vLine in Path("/etc/os-release").read_text(encoding="utf-8", errors="replace").splitlines():
        if vLine.startswith("PRETTY_NAME="):
          return vLine.split("=", 1)[1].strip().strip('"')
    except OSError:
      pass
  return f"{vSystem} {platform.release()}"


def fFallbackEncoding() -> str:
  """Windows console programs write to pipes in the OEM code page (cp850, cp437...) unless they are told otherwise."""
  if not cWindows:
    return "utf-8"
  try:
    vName = f"cp{ctypes.windll.kernel32.GetOEMCP()}"
    codecs.lookup(vName)
    return vName
  except (AttributeError, LookupError, OSError):
    return "utf-8"


def fDecode(pData: bytes) -> str:
  try:
    return pData.decode("utf-8")
  except UnicodeDecodeError:
    return pData.decode(dState["fallback_encoding"] or "utf-8", "replace")


#
# Shells
#

def fFindExecutable(pNames: list, pWindowsPaths: list) -> str:
  if cWindows:
    for vPath in pWindowsPaths:
      if os.path.isfile(vPath):
        return vPath
    for vName in pNames:
      vPath = shutil.which(vName)
      if vPath:
        return vPath
    return ""
  lFolders = []
  for vFolder in os.environ.get("PATH", "").split(os.pathsep):
    # relative entries would depend on the working directory of each command
    if vFolder.startswith("/"):
      lFolders.append(vFolder)
  lFolders += [
    "/usr/local/bin", "/usr/bin", "/bin", "/opt/homebrew/bin", "/opt/microsoft/powershell/7",
    "/usr/local/microsoft/powershell/7",
  ]
  for vName in pNames:
    for vFolder in lFolders:
      vCandidate = Path(vFolder) / vName
      if vCandidate.is_file() and os.access(vCandidate, os.X_OK):
        return str(vCandidate)
  return ""


def fDetectShells(pSeparator: str) -> dict:
  dShells = {}
  for dCandidate in ldShellCandidates:
    if dCandidate.get("windows_only") and not cWindows:
      continue
    vTool = dCandidate["tool"] + pSeparator + "run"
    vPath = fFindExecutable(dCandidate["names"], dCandidate["windows_paths"])
    if not vPath:
      fLog(f"{dCandidate['title']} not found, {vTool} is not exposed")
      continue
    dShells[vTool] = {
      "tool": vTool,
      "base": dCandidate["tool"],
      "title": dCandidate["title"],
      "syntax": dCandidate["syntax"],
      "path": vPath,
      "arguments": dCandidate["arguments"],
    }
  return dShells


def fShellCommandLine(pShell: dict, pCommand: str):
  """Arguments of the shell process. On Windows a process gets one command line that each program splits its own
  way: cmd /s /c runs what is between the outer quotes as it is, and PowerShell gets the command in base64 UTF-16,
  so no quote of the command is lost on the way."""
  if cWindows and pShell["base"] == "cmd":
    return f'"{pShell["path"]}" /d /s /c "{pCommand}"'
  if cWindows and pShell["base"] == "psh":
    vEncoded = base64.b64encode((cPowerShellPrefix + pCommand).encode("utf-16-le")).decode("ascii")
    return [pShell["path"], "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", vEncoded]
  return [pShell["path"], *pShell["arguments"], pCommand]


#
# Output capture: keeps the beginning and the end of a stream and counts what is left out.
#

def fUtf8CompleteSize(pData: bytes) -> int:
  """Length of the start of pData that does not end in the middle of a UTF-8 character."""
  vSize = len(pData)
  vStart = vSize
  for vBack in range(1, min(4, vSize) + 1):
    if (pData[vSize - vBack] & 0xC0) != 0x80:
      vStart = vSize - vBack
      break
  if vStart == vSize:
    return vSize
  vLead = pData[vStart]
  vLength = 2 if (vLead & 0xE0) == 0xC0 else 3 if (vLead & 0xF0) == 0xE0 else 4 if (vLead & 0xF8) == 0xF0 else 1
  return vStart if vSize - vStart < vLength else vSize


class OutputCapture:
  def __init__(self, pHalfBytes: int):
    self.vHalfBytes = pHalfBytes
    self.vHead = bytearray()
    self.vTail = bytearray()
    self.vTotal = 0

  def mAppend(self, pData: bytes) -> None:
    self.vTotal += len(pData)
    vTaken = 0
    if len(self.vHead) < self.vHalfBytes:
      vTaken = min(len(pData), self.vHalfBytes - len(self.vHead))
      self.vHead += pData[:vTaken]
    if vTaken < len(pData):
      self.vTail += pData[vTaken:]
      if len(self.vTail) > 2 * self.vHalfBytes:
        del self.vTail[:len(self.vTail) - self.vHalfBytes]

  def mText(self) -> str:
    vKept = bytes(self.vTail[-self.vHalfBytes:]) if len(self.vTail) > self.vHalfBytes else bytes(self.vTail)
    vOmitted = self.vTotal - len(self.vHead) - len(vKept)
    if vOmitted == 0:
      return fDecode(bytes(self.vHead) + vKept)
    # the cut may fall inside a UTF-8 character: its pieces count as omitted instead of showing as garbage
    vHead = bytes(self.vHead)
    vEnd = fUtf8CompleteSize(vHead)
    vStart = 0
    while vStart < min(3, len(vKept)) and (vKept[vStart] & 0xC0) == 0x80:
      vStart += 1
    vOmitted += (len(vHead) - vEnd) + vStart
    return fDecode(vHead[:vEnd]) + f"\n[... {vOmitted} bytes omitted ...]\n" + fDecode(vKept[vStart:])


#
# Call log: every command run leaves its own JSONL file in ~/.multi-shell-mcp-server, named after the local time of
# the call down to the millisecond (y2026m10d18h23m59s59ms731.jsonl; -2, -3... for more calls in the same
# millisecond). The first line is the command, written before it runs; then every piece of stdout and stderr as it is
# read, complete and in the order it arrived (the tool result only keeps the beginning and the end of long outputs);
# and last the result, or the reason the command could not start. The folder and the files are only readable by the
# user that runs the server, because the output of a command may hold secrets.
#

def fLogTime() -> str:
  """Local time with its offset, such as '2026-10-18T23:59:59+02:00'."""
  return datetime.now().astimezone().isoformat(timespec="seconds")


class CallLog:
  def __init__(self, pFolder: str):
    self.vFolder = pFolder
    self.vDescriptor = -1
    self.vPath = ""
    self.vWarned = False
    # a UTF-8 character split between two reads is written whole with the next one
    self.aDecoders = [codecs.getincrementaldecoder("utf-8")("replace") for _ in range(2)]

  def mOpen(self) -> bool:
    if not self.vFolder:
      return False
    try:
      vFolder = Path(self.vFolder)
      vFolder.mkdir(mode=0o700, parents=True, exist_ok=True)
      vNow = time.time()
      vBase = time.strftime("y%Ym%md%dh%Hm%Ms%S", time.localtime(vNow)) + "ms%03d" % int((vNow % 1) * 1000)
      vFlags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND | getattr(os, "O_CLOEXEC", 0) \
        | getattr(os, "O_BINARY", 0)
      for vNumber in range(1, 1001):
        vFile = vFolder / (vBase + (f"-{vNumber}" if vNumber > 1 else "") + ".jsonl")
        try:
          self.vDescriptor = os.open(vFile, vFlags, 0o600)
          self.vPath = str(vFile)
          return True
        except FileExistsError:
          continue
    except OSError as vError:
      fLog(f"cannot create the call log in {self.vFolder}: {vError}")
    return False

  def mWrite(self, pLine: dict) -> None:
    """Appends one line with the time of the event; a failed write only leaves the log incomplete."""
    if self.vDescriptor < 0:
      return
    vData = (json.dumps({"time": fLogTime(), **pLine}, ensure_ascii=False) + "\n").encode("utf-8")
    try:
      vWritten = 0
      while vWritten < len(vData):
        vWritten += os.write(self.vDescriptor, vData[vWritten:])
    except OSError as vError:
      if not self.vWarned:
        self.vWarned = True
        fLog(f"cannot write the call log {self.vPath}: {vError}")

  def mOutput(self, pStream: int, pData: bytes) -> None:
    """One line per read of the command output: {"event": "stdout" or "stderr", "data": "..."}."""
    vText = self.aDecoders[pStream].decode(pData)
    if vText:
      self.mWrite({"event": "stdout" if pStream == 0 else "stderr", "data": vText})

  def mFlushOutput(self) -> None:
    """Writes what is left of a character that the command never finished."""
    for vStream in (0, 1):
      vText = self.aDecoders[vStream].decode(b"", final=True)
      if vText:
        self.mWrite({"event": "stdout" if vStream == 0 else "stderr", "data": vText})

  def mClose(self) -> None:
    if self.vDescriptor >= 0:
      try:
        os.close(self.vDescriptor)
      except OSError:
        pass
      self.vDescriptor = -1


#
# Running a command
#

def fKillGroup(pPid: int) -> None:
  """Kills the shell and every process it started: its process group, or its process tree on Windows."""
  if cWindows:
    try:
      subprocess.run(
        ["taskkill", "/F", "/T", "/PID", str(pPid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW, timeout=30,
      )
    except (OSError, subprocess.SubprocessError):
      pass
    return
  try:
    os.killpg(pPid, signal.SIGKILL)
  except OSError:
    pass


def fConnectionClosed(pSocket: socket.socket) -> bool:
  """True when the client closed the connection that waits for the answer."""
  try:
    vReadable, _, _ = select.select([pSocket], [], [], 0)
    return bool(vReadable) and pSocket.recv(1, socket.MSG_PEEK) == b""
  except (OSError, ValueError):
    return True


def fReadPipe(pPipe, pStream: int, pQueue: queue.Queue, pStopped: threading.Event) -> None:
  """Reader thread of stdout or stderr: pipes cannot be polled on Windows, so every platform reads them this way.
  Once the call has answered, what background processes still write is read and dropped, so they never block."""
  try:
    while True:
      vData = pPipe.read1(cReadBytes)
      if not vData:
        break
      if not pStopped.is_set():
        pQueue.put((pStream, vData))
  except (OSError, ValueError):
    pass
  finally:
    pQueue.put((pStream, None))
    try:
      pPipe.close()
    except OSError:
      pass


def fRunCommand(pShell: dict, pCommand: str, pDirectory: str, pTimeoutSeconds: int, pShouldStop, pOnOutput) -> dict:
  """Runs pCommand with the shell in a new session (its own process group, without the server's controlling
  terminal) or, on Windows, in a new process group without a console window. stdin is empty, stdout and stderr are
  captured separately. The deadline and pShouldStop (closed connection) kill the whole group; after a normal exit the
  remaining output is read for a short while, so background processes that keep the pipes open do not block.
  pOnOutput receives every piece of output as it is read (stream 0 is stdout and 1 is stderr), whole."""
  dRun = {
    "stdout": OutputCapture(cStdoutHalfBytes),
    "stderr": OutputCapture(cStderrHalfBytes),
    "exit_code": None,
    "signal": 0,
    "timed_out": False,
    "cancelled": False,
    "error": "",
    "seconds": 0.0,
  }
  dSpawn = {}
  if cWindows:
    dSpawn["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
  else:
    dSpawn["start_new_session"] = True
  vStart = time.monotonic()
  try:
    vProcess = subprocess.Popen(
      fShellCommandLine(pShell, pCommand), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
      cwd=pDirectory, **dSpawn,
    )
  except OSError as vError:
    dRun["error"] = f"cannot start {pShell['path']}: {vError.strerror or vError}"
    return dRun
  with vRunningLock:
    sRunningPids.add(vProcess.pid)

  aCaptures = [dRun["stdout"], dRun["stderr"]]
  vQueue = queue.Queue()
  vStopped = threading.Event()
  for vStream, vPipe in ((0, vProcess.stdout), (1, vProcess.stderr)):
    threading.Thread(target=fReadPipe, args=(vPipe, vStream, vQueue, vStopped), daemon=True).start()
  vOpenPipes = 2
  vDeadline = vStart + pTimeoutSeconds
  vDrainDeadline = None
  vKillTime = None
  vStatus = None
  try:
    while True:
      if vStatus is None:
        vStatus = vProcess.poll()
      vNow = time.monotonic()
      if vStatus is not None:
        if vOpenPipes == 0:
          break
        if vDrainDeadline is None:
          vDrainDeadline = vNow + cDrainSeconds
        if vNow >= vDrainDeadline:
          break
      elif vKillTime is None:
        if pShouldStop():
          dRun["cancelled"] = True
        elif vNow >= vDeadline:
          dRun["timed_out"] = True
        if dRun["cancelled"] or dRun["timed_out"]:
          fKillGroup(vProcess.pid)
          vKillTime = vNow
      elif vNow - vKillTime >= cKillWaitSeconds:
        # a process stuck in the kernel ignores the kill for a while: answer now and reap it later
        threading.Thread(target=vProcess.wait, daemon=True).start()
        break
      try:
        vStream, vData = vQueue.get(timeout=cPollSeconds)
      except queue.Empty:
        continue
      while True:
        if vData is None:
          vOpenPipes -= 1
        else:
          aCaptures[vStream].mAppend(vData)
          pOnOutput(vStream, vData)
        try:
          vStream, vData = vQueue.get_nowait()
        except queue.Empty:
          break
  finally:
    vStopped.set()
    with vRunningLock:
      sRunningPids.discard(vProcess.pid)

  dRun["seconds"] = time.monotonic() - vStart
  if vStatus is not None:
    if vStatus < 0:
      dRun["signal"] = -vStatus
      dRun["exit_code"] = 128 + dRun["signal"]
    else:
      dRun["exit_code"] = vStatus
  return dRun


#
# MCP
#

def fInitializeResult(pParams: dict) -> dict:
  vRequested = pParams.get("protocolVersion")
  vVersion = vRequested if vRequested in lProtocolVersions else lProtocolVersions[0]
  vShellNames = ", ".join(f"{dShell['tool']} ({dShell['path']})" for dShell in dState["shells"].values()) or "none"
  vWhere = f"{dState['host'] or 'the computer where the server runs'} ({dState['os']})" + (
    f" as user {dState['user']}" if dState["user"] else ""
  )
  return {
    "protocolVersion": vVersion,
    "capabilities": {"tools": {"listChanged": False}},
    "serverInfo": {
      "name": cServerName,
      "title": cServerTitle,
      "version": cVersion,
      "icons": [
        {"src": fDataUri(cIconLight), "mimeType": "image/svg+xml", "theme": "light"},
        {"src": fDataUri(cIconDark), "mimeType": "image/svg+xml", "theme": "dark"},
      ],
    },
    "instructions": (
      f"Runs commands on {vWhere}, with the shells found when the server started: {vShellNames}. Every call starts a "
      "new non-interactive process without a terminal and with empty input; the working directory and variables do "
      f"not persist between calls, so pass cwd (default {dState['home']}) or chain the commands. Each call answers a "
      "JSON object with exit_code, stdout and stderr. When the timeout expires every process started by the command "
      "is killed. Long outputs keep only their beginning and their end."
    ),
  }


def fToolInputSchema() -> dict:
  """Arguments of every shell tool, for tools/list and for the OpenAPI document."""
  return {
    "type": "object",
    "properties": {
      "command": {"type": "string", "description": "Command or script to run."},
      "cwd": {"type": "string", "description": f"Working directory (default: {dState['home']})."},
      "timeout": {
        "type": "integer",
        "minimum": 1,
        "maximum": cMaxTimeoutSeconds,
        "description": (
          f"Seconds before every process started by the command is killed (default {cDefaultTimeoutSeconds})."
        ),
      },
    },
    "required": ["command"],
    "additionalProperties": False,
  }


def fToolsListResult() -> dict:
  lTools = []
  for dShell in dState["shells"].values():
    lTools.append({
      "name": dShell["tool"],
      "title": f"Run a {dShell['title']} command",
      "description": (
        f"Run a command or script with {dShell['title']} ({dShell['path']}) on {dState['os']}, on the computer where "
        "the multi shell MCP server runs, and return a JSON object with its exit_code, stdout and stderr. Use "
        f"{dShell['syntax']} syntax. Each call is a new non-interactive process without a terminal: interactive "
        "programs cannot be used, input is empty, and the working directory and variables do not persist between "
        "calls. Background processes must redirect their output, because the call returns when the shell exits."
      ),
      "inputSchema": fToolInputSchema(),
      "annotations": {
        "title": f"Run a {dShell['title']} command",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": True,
      },
    })
  return {"tools": lTools}


def fToolText(pText: str, pIsError: bool) -> dict:
  return {"content": [{"type": "text", "text": pText}], "isError": pIsError}


def fToolsCallResult(pParams: dict, pShouldStop) -> dict:
  vTool = pParams.get("name") if isinstance(pParams.get("name"), str) else ""
  dShell = dState["shells"].get(vTool)
  if dShell is None:
    raise ValueError(f"unknown tool: {vTool}")
  dArguments = pParams.get("arguments") if isinstance(pParams.get("arguments"), dict) else None

  def fArgument(pName: str):
    return dArguments.get(pName) if dArguments is not None else None

  # Every call leaves its own file, also when its arguments are rejected: the command line holds what was asked
  # (cwd and timeout as the model sent them until they are checked) and the error line the reason.
  vLog = CallLog(dState["call_log"])
  vLog.mOpen()
  try:
    dCommandLine = {
      "event": "command",
      "tool": vTool,
      "shell": dShell["title"],
      "cwd": dState["home"] if fArgument("cwd") is None else fArgument("cwd"),
      "timeout": cDefaultTimeoutSeconds if fArgument("timeout") is None else fArgument("timeout"),
      "command": fArgument("command"),
    }

    def fRejected(pReason: str) -> dict:
      vLog.mWrite(dCommandLine)
      vLog.mWrite({"event": "error", "error": pReason})
      return fToolText(pReason, True)

    if not isinstance(fArgument("command"), str):
      return fRejected('The argument "command" (string) is required.')
    vCommand = fArgument("command")
    if "\0" in vCommand:
      return fRejected("The command cannot contain NUL characters.")
    vDirectory = dState["home"]
    if fArgument("cwd") is not None:
      if not isinstance(fArgument("cwd"), str):
        return fRejected('The argument "cwd" must be a string.')
      if fArgument("cwd"):
        vRequested = os.path.expanduser(fArgument("cwd"))
        vDirectory = vRequested if os.path.isabs(vRequested) else os.path.join(dState["home"], vRequested)
    dCommandLine["cwd"] = vDirectory
    if not os.path.isdir(vDirectory):
      return fRejected(f"The working directory does not exist: {vDirectory}")
    vTimeout = cDefaultTimeoutSeconds
    if fArgument("timeout") is not None:
      vRequestedTimeout = fArgument("timeout")
      if isinstance(vRequestedTimeout, bool) or not isinstance(vRequestedTimeout, (int, float)) \
        or vRequestedTimeout != vRequestedTimeout or vRequestedTimeout in (float("inf"), float("-inf")):
        return fRejected('The argument "timeout" must be a number of seconds.')
      vTimeout = int(min(max(float(vRequestedTimeout), 1.0), float(cMaxTimeoutSeconds)))
    dCommandLine["timeout"] = vTimeout

    # the command, before it runs, so the file holds it even if the server stops meanwhile
    vLog.mWrite(dCommandLine)
    dRun = fRunCommand(dShell, vCommand, vDirectory, vTimeout, pShouldStop, vLog.mOutput)
    vLog.mFlushOutput()
    if dRun["error"]:
      fLog(f"{vTool} failed in {vDirectory}: {dRun['error']}: {fLogCommand(vCommand)}")
      vLog.mWrite({"event": "error", "error": dRun["error"]})
      return fToolText(dRun["error"], True)
    vState = " (timed out)" if dRun["timed_out"] else (" (cancelled)" if dRun["cancelled"] else "")
    fLog(
      f"{vTool} exit {dRun['exit_code']} in {dRun['seconds']:.2f} s{vState}, cwd {vDirectory}: "
      f"{fLogCommand(vCommand)}"
    )

    # The text block carries the same JSON object as structuredContent: the model reads it, and clients that render
    # tool results split it into stdout and stderr without guessing where one stream ends and the next one begins.
    # exit_code is null when a killed shell could not be reaped in time.
    dResult = {
      "exit_code": dRun["exit_code"],
      "stdout": dRun["stdout"].mText(),
      "stderr": dRun["stderr"].mText(),
      "signal": dRun["signal"],
      "timed_out": dRun["timed_out"],
      "cancelled": dRun["cancelled"],
      "timeout": vTimeout,
      "seconds": round(dRun["seconds"], 2),
      "shell": dShell["title"],
      "cwd": vDirectory,
    }
    # the whole output is already in the stdout and stderr lines, so the result only counts its bytes
    vLog.mWrite({
      "event": "result",
      "exit_code": dResult["exit_code"],
      "signal": dRun["signal"],
      "timed_out": dRun["timed_out"],
      "cancelled": dRun["cancelled"],
      "seconds": dResult["seconds"],
      "stdout_bytes": dRun["stdout"].vTotal,
      "stderr_bytes": dRun["stderr"].vTotal,
    })
    return {
      "content": [{"type": "text", "text": json.dumps(dResult, ensure_ascii=False)}],
      "structuredContent": dResult,
      "isError": dRun["timed_out"] or dRun["cancelled"],
    }
  finally:
    vLog.mClose()


def fRpcError(pId, pCode: int, pMessage: str) -> dict:
  return {"jsonrpc": "2.0", "id": pId, "error": {"code": pCode, "message": pMessage}}


#
# Browser origins and DNS rebinding
#

def fHostName(pAuthority: str) -> str:
  """'Host:8080' -> 'host'; '[::1]:80' -> '::1'."""
  try:
    return (urlsplit("//" + pAuthority.strip()).hostname or "").lower()
  except ValueError:
    return ""


def fIsLoopback(pHost: str) -> bool:
  """localhost and its subdomains (browsers resolve them to loopback themselves), ::1 and 127.0.0.0/8 written as an IP
  address. A name such as 127.example.com is not loopback: its DNS decides where it points."""
  return (
    pHost == "localhost" or pHost.endswith(".localhost") or pHost == "::1"
    or (pHost.startswith("127.") and fIsIpAddress(pHost))
  )


def fIsIpAddress(pHost: str) -> bool:
  try:
    socket.inet_pton(socket.AF_INET6 if ":" in pHost else socket.AF_INET, pHost)
    return True
  except (OSError, ValueError):
    return False


def fOriginAllowed(pOrigin: str, pHost: str) -> bool:
  """Browsers always send Origin on cross-site requests: pages of this same host (any port, such as the WebUI of
  llama-server on :8080), loopback pages talking to a loopback listener and the origins of --allow-origin may call the
  endpoint (MCP requires Origin validation). Clients without Origin, such as curl, are only subject to the token."""
  if not pOrigin:
    return True
  if "*" in dState["allowed_origins"]:
    return True
  if pOrigin.lower() == "null":
    return False
  if pOrigin.rstrip("/").lower() in {vOrigin.rstrip("/").lower() for vOrigin in dState["allowed_origins"]}:
    return True
  vOriginHost = fHostName(urlsplit(pOrigin).netloc)
  vRequestHost = fHostName(pHost)
  return vOriginHost == vRequestHost or (fIsLoopback(vOriginHost) and fIsLoopback(vRequestHost))


def fHostAllowed(pHost: str) -> bool:
  """Without a token, only Host headers that DNS rebinding cannot forge: IP addresses and localhost. A web page of a
  domain that resolves to this computer would otherwise be same-origin with the server and could run commands."""
  if dState["token"]:
    return True
  vHost = fHostName(pHost)
  return fIsLoopback(vHost) or fIsIpAddress(vHost)


#
# API documentation (/api/doc/)
#

def fOpenApi(pBaseUrl: str) -> dict:
  """Documents every route under /api/. The tool names, and whether POST /api/mcp needs the token, are those of the
  running server."""
  dString = {"type": "string"}
  dJsonRpcAnswer = {"application/json": {"schema": {"$ref": "#/components/schemas/JsonRpcResponse"}}}
  lToolNames = list(dState["shells"])
  dToolName = {"type": "string", "description": "Name of a tool of tools/list."}
  if lToolNames:
    dToolName["enum"] = lToolNames
  vExampleTool = lToolNames[0] if lToolNames else "bash.run"
  vDescription = (
    "MCP server that runs commands with the shells of this computer. MCP Streamable HTTP with plain JSON answers, "
    "without SSE stream and without session. Tools: bash.run, zsh.run, sh.run, psh.run and cmd.run, depending on "
    "the shells installed; each takes command, cwd and timeout."
  )
  if dState["token"]:
    vDescription += (
      " This server runs with --token: press Authorize and paste the token before trying POST " + cMcpPath + "."
    )
  return {
    "openapi": "3.1.0",
    "info": {"title": cServerTitle, "version": cVersion, "description": vDescription},
    "servers": [{"url": pBaseUrl}],
    "paths": {
      cMcpPath: {
        "post": {
          "summary": "One JSON-RPC request or notification (initialize, ping, tools/list, tools/call)",
          "description": (
            "Every POST carries one JSON-RPC message (batches are not supported). Requests get a JSON-RPC answer; "
            "notifications and answers to server requests get 202 without body. Without --token, the Host header "
            "must be an IP address or localhost. Browser requests need an allowed Origin."
          ),
          "security": [{"bearer": []}] if dState["token"] else [],
          "requestBody": {"required": True, "content": {"application/json": {
            "schema": {"$ref": "#/components/schemas/JsonRpcRequest"},
            "examples": {
              "initialize": {"summary": "initialize", "value": {
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {
                  "protocolVersion": lProtocolVersions[0], "capabilities": {},
                  "clientInfo": {"name": "swagger-ui", "version": cSwaggerVersion},
                },
              }},
              "tools_list": {"summary": "tools/list", "value": {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}},
              "tools_call": {"summary": "tools/call", "value": {
                "jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": vExampleTool, "arguments": {"command": "echo hello"}},
              }},
            },
          }}},
          "responses": {
            "200": {
              "description": "JSON-RPC answer: a result, or an error (-32601, -32602 or -32603)",
              "content": dJsonRpcAnswer,
            },
            "202": {"description": "Notification or answer accepted, without body"},
            "400": {
              "description": "Malformed JSON-RPC message (-32700 or -32600), a batch, or an invalid Content-Length",
              "content": dJsonRpcAnswer,
            },
            "401": {
              "description": "Missing or wrong token (only when the server runs with --token)",
              "content": dJsonRpcAnswer,
            },
            "403": {"description": "Origin or Host not allowed", "content": dJsonRpcAnswer},
            "413": {"description": f"Body larger than {cMaxBodyBytes // (1024 * 1024)} MiB", "content": dJsonRpcAnswer},
            "415": {"description": "Content-Type is not application/json", "content": dJsonRpcAnswer},
          },
        },
        "get": {
          "summary": "Not offered: no SSE stream",
          "responses": {"405": {"description": "Use POST", "content": dJsonRpcAnswer}},
        },
        "delete": {
          "summary": "Not offered: no sessions",
          "responses": {"405": {"description": "Use POST", "content": dJsonRpcAnswer}},
        },
        "options": {
          "summary": "CORS preflight of browsers",
          "description": "Answers the Access-Control-Allow-* headers when the Origin is allowed.",
          "responses": {"204": {"description": "Origin allowed"}, "403": {"description": "Origin not allowed"}},
        },
      },
      cDocPath.rstrip("/"): {
        "get": {"summary": f"Redirects to {cDocPath}", "responses": {"301": {"description": f"Location: {cDocPath}"}}},
      },
      cDocPath: {"get": {"summary": "This documentation", "responses": {"200": {"description": "HTML page"}}}},
      cDocPath + "openapi.json": {
        "get": {"summary": "This OpenAPI document", "responses": {"200": {"description": "JSON"}}},
      },
    },
    "components": {
      "securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"}},
      "schemas": {
        "JsonRpcRequest": {
          "type": "object",
          "required": ["jsonrpc", "method"],
          "properties": {
            "jsonrpc": {"type": "string", "enum": ["2.0"]},
            "id": {"oneOf": [dString, {"type": "integer"}], "description": "Absent in notifications"},
            "method": {
              "type": "string",
              "description": (
                "initialize, ping, tools/list or tools/call, or a notification (notifications/initialized)"
              ),
            },
            "params": {"type": "object", "description": "Parameters of the method; for tools/call, ToolsCallParams"},
          },
        },
        "ToolsCallParams": {
          "type": "object",
          "required": ["name", "arguments"],
          "properties": {"name": dToolName, "arguments": {"$ref": "#/components/schemas/ToolArguments"}},
        },
        "ToolArguments": fToolInputSchema(),
        "JsonRpcResponse": {
          "type": "object",
          "required": ["jsonrpc", "id"],
          "properties": {
            "jsonrpc": {"type": "string", "enum": ["2.0"]},
            "id": {"oneOf": [dString, {"type": "integer"}, {"type": "null"}]},
            "result": {
              "type": "object",
              "description": (
                "initialize: protocolVersion, capabilities, serverInfo and instructions; ping: {}; tools/list: tools; "
                "tools/call: ToolsCallResult"
              ),
            },
            "error": {
              "type": "object",
              "required": ["code", "message"],
              "properties": {
                "code": {
                  "type": "integer",
                  "description": (
                    "-32700 parse error, -32600 invalid request, -32601 method not found, -32602 invalid params or "
                    "unknown tool, -32603 internal error"
                  ),
                },
                "message": dString,
              },
            },
          },
        },
        "ToolsCallResult": {
          "type": "object",
          "required": ["content", "isError"],
          "properties": {
            "content": {
              "type": "array",
              "items": {
                "type": "object",
                "properties": {
                  "type": {"type": "string", "enum": ["text"]},
                  "text": {"type": "string", "description": "ToolResult as JSON, or why the call was rejected"},
                },
              },
            },
            "structuredContent": {
              "$ref": "#/components/schemas/ToolResult",
              "description": "Absent when an argument was rejected or the shell could not start",
            },
            "isError": {
              "type": "boolean",
              "description": (
                "True when an argument was rejected, the shell could not start, or the command timed out or was "
                "cancelled"
              ),
            },
          },
        },
        "ToolResult": {
          "type": "object",
          "properties": {
            "exit_code": {
              "type": ["integer", "null"],
              "description": (
                "Exit status (128 + signal on POSIX); null when a killed shell could not be reaped in time"
              ),
            },
            "stdout": {
              "type": "string",
              "description": f"Standard output; long outputs keep their first and last {cStdoutHalfBytes // 1024} KiB",
            },
            "stderr": {
              "type": "string",
              "description": f"Standard error; long outputs keep their first and last {cStderrHalfBytes // 1024} KiB",
            },
            "signal": {"type": "integer", "description": "Signal that ended the shell (POSIX), or 0"},
            "timed_out": {"type": "boolean", "description": "The timeout expired and the command was killed"},
            "cancelled": {"type": "boolean", "description": "The client closed the connection; the command was killed"},
            "timeout": {"type": "integer", "description": "Seconds of timeout applied"},
            "seconds": {"type": "number", "description": "Duration of the command"},
            "shell": {"type": "string", "description": "Shell that ran the command"},
            "cwd": {"type": "string", "description": "Working directory"},
          },
        },
      },
    },
  }


cSwaggerPage = (
  "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
  "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\"><title>API docs</title>"
  f"<link rel=\"stylesheet\" href=\"https://cdn.jsdelivr.net/npm/swagger-ui-dist@{cSwaggerVersion}/swagger-ui.css\">"
  "<style>body{margin:0;background:#fff}.swagger-ui pre,.swagger-ui code{white-space:pre-wrap;overflow-wrap:anywhere}"
  ".swagger-ui table{display:block;overflow-x:auto}</style></head><body><div id=\"swagger-ui\"></div>"
  f"<script src=\"https://cdn.jsdelivr.net/npm/swagger-ui-dist@{cSwaggerVersion}/swagger-ui-bundle.js\"></script>"
  "<script>window.onload=function(){SwaggerUIBundle({url:\"openapi.json\",dom_id:\"#swagger-ui\"});};</script>"
  "</body></html>"
)


#
# HTTP
#

class McpRequestHandler(BaseHTTPRequestHandler):
  protocol_version = "HTTP/1.1"
  server_version = cServerName
  sys_version = ""

  def log_message(self, pFormat, *pArguments) -> None:
    pass # every tool call is logged by fToolsCallResult

  def mCorsHeaders(self) -> dict:
    vOrigin = self.headers.get("Origin", "")
    if not vOrigin or not fOriginAllowed(vOrigin, self.headers.get("Host", "")):
      return {}
    return {
      "Access-Control-Allow-Origin": vOrigin,
      "Access-Control-Expose-Headers": "Mcp-Session-Id, Mcp-Protocol-Version, WWW-Authenticate",
      "Vary": "Origin",
    }

  def mSend(self, pStatus: int, pBody: bytes = b"", pContentType: str = "", pHeaders: dict | None = None) -> None:
    self.send_response(pStatus)
    if pContentType:
      self.send_header("Content-Type", pContentType)
    if pStatus != 204:
      self.send_header("Content-Length", str(len(pBody)))
    self.send_header("Cache-Control", "no-store")
    for vName, vValue in {**self.mCorsHeaders(), **(pHeaders or {})}.items():
      self.send_header(vName, vValue)
    self.end_headers()
    if pBody and self.command != "HEAD":
      self.wfile.write(pBody)

  def mSendJson(self, pStatus: int, pBody: dict, pHeaders: dict | None = None) -> None:
    self.mSend(
      pStatus, json.dumps(pBody, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", pHeaders,
    )

  def mPath(self) -> str:
    return urlsplit(self.path).path

  def mRejected(self) -> bool:
    """Answers and returns True when the Host, the Origin or the token of the request are not acceptable."""
    if not fHostAllowed(self.headers.get("Host", "")):
      fLog(f"rejected a request for host {self.headers.get('Host', '')}: start the server with --token to use names")
      self.mSendJson(403, fRpcError(None, cInvalidRequest, "Host not allowed"))
      return True
    vOrigin = self.headers.get("Origin", "")
    if not fOriginAllowed(vOrigin, self.headers.get("Host", "")):
      fLog(f"rejected a request from origin {vOrigin}: start the server with --allow-origin {vOrigin}")
      self.mSendJson(403, fRpcError(None, cInvalidRequest, "Origin not allowed"))
      return True
    if dState["token"]:
      vAuthorization = self.headers.get("Authorization", "")
      vToken = vAuthorization[7:].strip() if vAuthorization.lower().startswith("bearer ") else ""
      if not hmac.compare_digest(vToken.encode("utf-8"), dState["token"].encode("utf-8")):
        self.mSendJson(
          401, fRpcError(None, cInvalidRequest, "Missing or wrong token"), {"WWW-Authenticate": "Bearer"},
        )
        return True
    return False

  def do_OPTIONS(self) -> None:
    # CORS preflight of the browser (the WebUI of llama-server calls the server from the page)
    if self.mPath() != cMcpPath or not fOriginAllowed(self.headers.get("Origin", ""), self.headers.get("Host", "")):
      self.mSend(403)
      return
    self.mSend(204, pHeaders={
      "Access-Control-Allow-Methods": "POST, GET, DELETE, OPTIONS",
      "Access-Control-Allow-Headers": self.headers.get("Access-Control-Request-Headers", "Content-Type, Authorization"),
      "Access-Control-Max-Age": "600",
    })

  def do_GET(self) -> None:
    vPath = self.mPath()
    if vPath == cMcpPath:
      self.mSendJson(405, fRpcError(None, cInvalidRequest, "Method not allowed: use POST"), {"Allow": "POST"})
    elif vPath == cDocPath.rstrip("/"):
      self.mSend(301, pHeaders={"Location": cDocPath})
    elif vPath == cDocPath:
      self.mSend(200, cSwaggerPage.encode("utf-8"), "text/html; charset=utf-8")
    elif vPath == cDocPath + "openapi.json":
      self.mSendJson(200, fOpenApi(f"http://{self.headers.get('Host', '')}"))
    else:
      self.mSendJson(404, {"error": f"not found; the MCP endpoint is {cMcpPath}"})

  def do_DELETE(self) -> None:
    if self.mPath() == cMcpPath:
      self.mSendJson(405, fRpcError(None, cInvalidRequest, "Method not allowed: use POST"), {"Allow": "POST"})
    else:
      self.mSendJson(404, {"error": "not found"})

  def do_POST(self) -> None:
    if self.mPath() != cMcpPath:
      self.mSendJson(404, {"error": f"not found; the MCP endpoint is {cMcpPath}"})
      return
    if self.mRejected():
      return
    if not self.headers.get("Content-Type", "").lower().startswith("application/json"):
      self.mSendJson(415, fRpcError(None, cInvalidRequest, "Content-Type must be application/json"))
      return
    try:
      vLength = int(self.headers.get("Content-Length", "0"))
    except ValueError:
      vLength = -1
    if vLength < 0 or vLength > cMaxBodyBytes:
      self.mSendJson(413 if vLength > 0 else 400, fRpcError(None, cInvalidRequest, "Invalid Content-Length"))
      self.close_connection = True
      return
    try:
      dMessage = json.loads(self.rfile.read(vLength))
    except ValueError as vError:
      self.mSendJson(400, fRpcError(None, cParseError, f"Parse error: {vError}"))
      return
    if isinstance(dMessage, list):
      self.mSendJson(400, fRpcError(None, cInvalidRequest, "JSON-RPC batches are not supported"))
      return
    if not isinstance(dMessage, dict):
      self.mSendJson(400, fRpcError(None, cInvalidRequest, "Invalid request"))
      return
    # notifications and responses to server requests are acknowledged without a body
    if "id" not in dMessage or "method" not in dMessage:
      self.mSend(202)
      return
    vId = dMessage["id"]
    if not isinstance(dMessage["method"], str):
      self.mSendJson(400, fRpcError(vId, cInvalidRequest, "Invalid request"))
      return
    vMethod = dMessage["method"]
    dParams = dMessage.get("params") if isinstance(dMessage.get("params"), dict) else {}
    try:
      if vMethod == "initialize":
        dResult = fInitializeResult(dParams)
      elif vMethod == "ping":
        dResult = {}
      elif vMethod == "tools/list":
        dResult = fToolsListResult()
      elif vMethod == "tools/call":
        dResult = fToolsCallResult(dParams, lambda: fConnectionClosed(self.connection))
      else:
        self.mSendJson(200, fRpcError(vId, cMethodNotFound, f"Method not found: {vMethod}"))
        return
    except ValueError as vError:
      self.mSendJson(200, fRpcError(vId, cInvalidParams, str(vError)))
      return
    except Exception as vError:
      fLog(f"{vMethod} failed: {vError!r}")
      self.mSendJson(200, fRpcError(vId, cInternalError, f"Internal error: {vError}"))
      return
    try:
      self.mSendJson(200, {"jsonrpc": "2.0", "id": vId, "result": dResult})
    except OSError:
      pass # the client is gone


class McpServer(ThreadingHTTPServer):
  daemon_threads = True
  # On Windows SO_REUSEADDR lets a second program take a port that is in use, so it is only set elsewhere.
  allow_reuse_address = not cWindows


#
# The rule of the project: before listening on a port, close whatever already listens on it (a previous run of this
# server, normally): /proc on Linux, lsof on macOS and BSD, netstat on Windows.
#

def fListeningPids(pPort: int) -> set:
  sPids = set()
  if sys.platform.startswith("linux"):
    sInodes = set()
    for vFile in ("/proc/net/tcp", "/proc/net/tcp6"):
      try:
        lLines = Path(vFile).read_text().splitlines()[1:]
      except OSError:
        continue
      for vLine in lLines:
        lFields = vLine.split()
        try:
          if len(lFields) >= 10 and lFields[3] == "0A" and int(lFields[1].rsplit(":", 1)[1], 16) == pPort:
            sInodes.add(f"socket:[{lFields[9]}]")
        except ValueError:
          continue
    if sInodes:
      for vEntry in Path("/proc").iterdir():
        if not vEntry.name.isdigit():
          continue
        try:
          if any(os.readlink(vFd) in sInodes for vFd in (vEntry / "fd").iterdir()):
            sPids.add(int(vEntry.name))
        except OSError:
          continue
  elif cWindows:
    try:
      vOutput = subprocess.run(
        ["netstat", "-ano"], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=30,
      ).stdout.decode("ascii", "replace")
    except (OSError, subprocess.SubprocessError):
      vOutput = ""
    for vLine in vOutput.splitlines():
      lFields = vLine.split()
      # TCP  0.0.0.0:44444  0.0.0.0:0  LISTENING  1234 (the state word is translated, the empty remote address not)
      if len(lFields) >= 5 and lFields[0].upper() == "TCP" and lFields[1].endswith(f":{pPort}") \
        and lFields[2] in ("0.0.0.0:0", "[::]:0") and lFields[-1].isdigit():
        sPids.add(int(lFields[-1]))
  else:
    try:
      vOutput = subprocess.run(
        ["lsof", "-nP", "-t", f"-iTCP:{pPort}", "-sTCP:LISTEN"], capture_output=True, timeout=30,
      ).stdout.decode("ascii", "replace")
      sPids = {int(vText) for vText in vOutput.split() if vText.isdigit()}
    except (OSError, subprocess.SubprocessError):
      pass
  sPids.discard(os.getpid())
  sPids.discard(0)
  return sPids


def fStopProcess(pPid: int, pForce: bool) -> None:
  try:
    if cWindows:
      # console programs ignore taskkill without /F
      subprocess.run(
        ["taskkill", "/F", "/PID", str(pPid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW, timeout=30,
      )
    else:
      os.kill(pPid, signal.SIGKILL if pForce else signal.SIGTERM)
  except (OSError, subprocess.SubprocessError):
    pass


def fFreePort(pPort: int) -> None:
  sPids = fListeningPids(pPort)
  if not sPids:
    return
  for vPid in sPids:
    fLog(f"port {pPort} is in use by process {vPid}: stopping it")
    fStopProcess(vPid, False)
  vDeadline = time.monotonic() + cFreePortSeconds
  while time.monotonic() < vDeadline and fListeningPids(pPort):
    time.sleep(0.2)
  for vPid in fListeningPids(pPort):
    fStopProcess(vPid, True)


#
# Start
#

def fParseArguments(pArguments: list) -> argparse.Namespace:
  vParser = argparse.ArgumentParser(
    prog="multi_shell_mcp_server.py",
    description=(
      f"{cServerTitle} {cVersion}: MCP Streamable HTTP server that runs commands with the shells of this computer."
    ),
  )
  vParser.add_argument(
    "--host", default=cDefaultHost,
    help=f"address to listen on (default {cDefaultHost}; 0.0.0.0 for every interface)",
  )
  vParser.add_argument("--port", type=int, default=cDefaultPort, help=f"port to listen on (default {cDefaultPort})")
  vParser.add_argument(
    "--token", default=os.environ.get(cTokenVariable, ""),
    help=f"require the header 'Authorization: Bearer TOKEN' (default: ${cTokenVariable}, or none)",
  )
  vParser.add_argument(
    "--allow-origin", action="append", default=[],
    help="browser origin allowed besides this same host, e.g. http://192.168.1.20:8080 (repeatable, * for any)",
  )
  vParser.add_argument(
    "--tool-names", choices=["dot", "underscore"], default="dot",
    help="bash.run (default) or bash_run, for clients that reject dots in tool names",
  )
  vParser.add_argument(
    "--no-call-log", action="store_true",
    help=f"do not keep a JSONL file per call in {cCallLogFolder}",
  )
  vParser.add_argument("--version", action="version", version=f"{cServerName} {cVersion}")
  return vParser.parse_args(pArguments)


def fStopOnSignal(pSignal, pFrame) -> None:
  raise KeyboardInterrupt


def fMain(pArguments: list) -> int:
  vArguments = fParseArguments(pArguments)
  try:
    sys.stderr.reconfigure(errors="replace")
  except (AttributeError, ValueError):
    pass

  dState["fallback_encoding"] = fFallbackEncoding()
  dState["os"] = fOsName()
  dState["shells"] = fDetectShells("_" if vArguments.tool_names == "underscore" else ".")
  vHome = os.path.expanduser("~")
  dState["home"] = vHome if os.path.isdir(vHome) else os.getcwd()
  try:
    dState["user"] = getpass.getuser()
  except (KeyError, OSError, ImportError):
    dState["user"] = ""
  dState["host"] = socket.gethostname()
  dState["token"] = vArguments.token
  dState["allowed_origins"] = vArguments.allow_origin
  dState["call_log"] = "" if vArguments.no_call_log else os.path.expanduser(cCallLogFolder)
  if not dState["shells"]:
    fLog("no shell was found; the server exposes no tools")

  fFreePort(vArguments.port)
  if ":" in vArguments.host:
    McpServer.address_family = socket.AF_INET6
  try:
    vServer = McpServer((vArguments.host, vArguments.port), McpRequestHandler)
  except OSError as vError:
    fLog(f"cannot listen on {vArguments.host}:{vArguments.port}: {vError}")
    return 1

  vShownHost = vArguments.host
  if vShownHost in ("0.0.0.0", "::"):
    vShownHost = dState["host"]
  elif ":" in vShownHost:
    vShownHost = f"[{vShownHost}]"
  fLog(f"{cServerTitle} {cVersion} on {dState['os']}: commands run as {dState['user'] or '?'}@{dState['host']}")
  fLog(f"MCP endpoint (Streamable HTTP): http://{vShownHost}:{vArguments.port}{cMcpPath}")
  fLog(f"API documentation: http://{vShownHost}:{vArguments.port}{cDocPath}")
  fLog("tools: " + (", ".join(f"{vTool} -> {dShell['path']}" for vTool, dShell in dState["shells"].items()) or "none"))
  fLog("token: " + ("required (Authorization: Bearer ...)" if dState["token"] else "none"))
  if dState["call_log"]:
    fLog(f"call log: {dState['call_log']}")
  fLog("stop with Ctrl+C")

  # SIGTERM (and Ctrl+Break on Windows) stop the server like Ctrl+C, so the running commands are killed too
  for vName in ("SIGTERM", "SIGBREAK"):
    if hasattr(signal, vName):
      try:
        signal.signal(getattr(signal, vName), fStopOnSignal)
      except (OSError, ValueError):
        pass
  try:
    vServer.serve_forever()
  except KeyboardInterrupt:
    pass
  finally:
    vServer.server_close()
    with vRunningLock:
      lPids = list(sRunningPids)
    for vPid in lPids:
      fKillGroup(vPid)
    fLog("stopped")
  return 0


if __name__ == "__main__":
  sys.exit(fMain(sys.argv[1:]))
