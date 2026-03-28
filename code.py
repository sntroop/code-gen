import os
import sys
import json
import asyncio
import subprocess
import shutil
import re
import platform
import signal
import hashlib
import tempfile
from pathlib import Path
from typing import Optional, Dict, Any, Tuple
from datetime import datetime
from dotenv import load_dotenv

try:
    import aiohttp
except ImportError:
    subprocess.run([sys.executable, "-m", "pip", "install", "aiohttp", "-q"])
    import aiohttp

load_dotenv()

API_KEY = os.getenv("VSEGPT_KEY")
API_URL = "https://api.vsegpt.ru/v1/chat/completions"
MODEL = "google/gemini-2.5-flash-lite-pre-06-17"
MAX_ITER = 40
MAX_READ = 8000

class Colors:
    RESET = "\033[0m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    DIM = "\033[2m"

def get_term_width():
    return shutil.get_terminal_size((100, 30)).columns

def print_line():
    print(f"{Colors.DIM}{'─' * get_term_width()}{Colors.RESET}")

def print_ok(msg):
    print(f"  {Colors.GREEN}✓{Colors.RESET} {msg}")

def print_err(msg):
    print(f"  {Colors.RED}✗{Colors.RESET} {msg}")

def print_info(msg):
    print(f"  {Colors.BLUE}•{Colors.RESET} {msg}")

class Tools:
    @staticmethod
    def shell(cmd: str, cwd: str = None, timeout: int = 120) -> Dict:
        try:
            result = subprocess.run(
                cmd, shell=True, cwd=cwd,
                capture_output=True, text=True, timeout=timeout
            )
            out = (result.stdout + result.stderr).strip()
            return {
                "ok": result.returncode == 0,
                "rc": result.returncode,
                "output": out[:6000]
            }
        except subprocess.TimeoutExpired:
            return {"ok": False, "rc": -1, "output": f"Timeout {timeout}s"}
        except Exception as e:
            return {"ok": False, "rc": -1, "output": str(e)}

    @staticmethod
    def read(path: str) -> Dict:
        try:
            p = Path(path)
            if not p.exists():
                return {"ok": False, "output": f"Not found: {path}"}
            content = p.read_text(encoding="utf-8", errors="replace")
            truncated = len(content) > MAX_READ
            return {
                "ok": True,
                "output": content[:MAX_READ],
                "truncated": truncated
            }
        except Exception as e:
            return {"ok": False, "output": str(e)}

    @staticmethod
    def write(path: str, content: str) -> Dict:
        try:
            p = Path(path)
            if p.exists():
                bak = p.with_suffix(p.suffix + ".bak")
                shutil.copy2(p, bak)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            return {"ok": True, "output": f"Written {path}"}
        except Exception as e:
            return {"ok": False, "output": str(e)}

    @staticmethod
    def patch(path: str, old: str, new: str) -> Dict:
        try:
            p = Path(path)
            if not p.exists():
                return {"ok": False, "output": f"Not found: {path}"}
            content = p.read_text(encoding="utf-8", errors="replace")
            if old not in content:
                return {"ok": False, "output": "Pattern not found"}
            bak = p.with_suffix(p.suffix + ".bak")
            shutil.copy2(p, bak)
            p.write_text(content.replace(old, new, 1), encoding="utf-8")
            return {"ok": True, "output": f"Patched {path}"}
        except Exception as e:
            return {"ok": False, "output": str(e)}

    @staticmethod
    def ls(path: str, depth: int = 2) -> Dict:
        try:
            p = Path(path)
            if not p.exists():
                return {"ok": False, "output": f"Not found: {path}"}
            lines = []
            skip = {".git", "build", "__pycache__", "node_modules", "venv", ".gradle"}

            def walk(d: Path, prefix: str, level: int):
                if level > depth:
                    return
                try:
                    items = sorted(d.iterdir(), key=lambda x: (x.is_file(), x.name))
                except:
                    return
                for item in items[:60]:
                    if item.name in skip or item.name.startswith("."):
                        continue
                    marker = "📁" if item.is_dir() else "📄"
                    lines.append(f"{prefix}{marker} {item.name}")
                    if item.is_dir():
                        walk(item, prefix + "  ", level + 1)

            walk(p, "", 0)
            return {"ok": True, "output": "\n".join(lines) or "(empty)"}
        except Exception as e:
            return {"ok": False, "output": str(e)}

    @staticmethod
    def find(path: str, pattern: str) -> Dict:
        try:
            p = Path(path)
            skip = {".git", "build", "__pycache__", "node_modules", "venv"}
            found = []
            for f in p.rglob(pattern):
                if any(s in f.parts for s in skip):
                    continue
                found.append(str(f.relative_to(p)))
                if len(found) >= 100:
                    break
            return {"ok": True, "output": "\n".join(found) if found else "Not found"}
        except Exception as e:
            return {"ok": False, "output": str(e)}

    @staticmethod
    def grep(path: str, pattern: str, recursive: bool = True) -> Dict:
        flag = "-r" if recursive else ""
        cmd = f'grep -n {flag} --include="*.{{kt,java,py,js,ts,dart,go,rs,xml,gradle,json}}" "{pattern}" "{path}" 2>/dev/null | head -60'
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        return {"ok": True, "output": result.stdout.strip() or "No matches"}

    @staticmethod
    def mkdir(path: str) -> Dict:
        try:
            Path(path).mkdir(parents=True, exist_ok=True)
            return {"ok": True, "output": f"Created {path}"}
        except Exception as e:
            return {"ok": False, "output": str(e)}

    @staticmethod
    def rm(path: str) -> Dict:
        try:
            p = Path(path)
            if not p.exists():
                return {"ok": False, "output": "Not exists"}
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
            return {"ok": True, "output": f"Removed {path}"}
        except Exception as e:
            return {"ok": False, "output": str(e)}

    @staticmethod
    def cp(src: str, dst: str) -> Dict:
        try:
            s, d = Path(src), Path(dst)
            if not s.exists():
                return {"ok": False, "output": "Source not found"}
            d.parent.mkdir(parents=True, exist_ok=True)
            if s.is_dir():
                shutil.copytree(s, d)
            else:
                shutil.copy2(s, d)
            return {"ok": True, "output": f"Copied {src} -> {dst}"}
        except Exception as e:
            return {"ok": False, "output": str(e)}

    @staticmethod
    def chmod(path: str, mode: str) -> Dict:
        return Tools.shell(f"chmod {mode} {path}")

    @staticmethod
    def git(args: str, cwd: str) -> Dict:
        return Tools.shell(f"git {args}", cwd=cwd)

    @staticmethod
    def info() -> Dict:
        info = {
            "os": platform.system(),
            "arch": platform.machine(),
            "python": platform.python_version(),
            "cwd": os.getcwd(),
        }
        return {"ok": True, "output": json.dumps(info, indent=2)}

def dispatch(name: str, args: Dict, cwd: str = ".") -> Dict:
    t = Tools
    try:
        if name == "shell":
            return t.shell(args["cmd"], cwd=args.get("cwd", cwd))
        elif name == "read":
            return t.read(args["path"])
        elif name == "write":
            return t.write(args["path"], args["content"])
        elif name == "patch":
            return t.patch(args["path"], args["old"], args["new"])
        elif name == "ls":
            return t.ls(args["path"], args.get("depth", 2))
        elif name == "find":
            return t.find(args["path"], args["pattern"])
        elif name == "grep":
            return t.grep(args["path"], args["pattern"])
        elif name == "mkdir":
            return t.mkdir(args["path"])
        elif name == "rm":
            return t.rm(args["path"])
        elif name == "cp":
            return t.cp(args["src"], args["dst"])
        elif name == "chmod":
            return t.chmod(args["path"], args["mode"])
        elif name == "git":
            return t.git(args["args"], args.get("cwd", cwd))
        elif name == "info":
            return t.info()
        else:
            return {"ok": False, "output": f"Unknown: {name}"}
    except KeyError as e:
        return {"ok": False, "output": f"Missing arg: {e}"}
    except Exception as e:
        return {"ok": False, "output": str(e)}

class VseGPT:
    def __init__(self):
        self.session = None

    async def _get_session(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
        return self.session

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

    async def ask(self, messages, stream=True):
        session = await self._get_session()
        headers = {
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": MODEL,
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": 2048,
            "stream": stream,
        }

        full = ""
        try:
            async with session.post(API_URL, json=payload, headers=headers, timeout=120) as resp:
                if resp.status != 200:
                    return f"[Error {resp.status}]"
                if stream:
                    async for line in resp.content:
                        line = line.decode("utf-8", errors="replace").strip()
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                            delta = chunk["choices"][0]["delta"].get("content", "")
                            if delta:
                                print(delta, end="", flush=True)
                                full += delta
                        except:
                            pass
                    print()
                else:
                    data = await resp.json()
                    full = data["choices"][0]["message"]["content"]
        except Exception as e:
            return f"[Error: {e}]"
        return full

def extract_tool(text):
    patterns = [
        r"```tool\s*\n(.*?)\n```",
        r"```json\s*\n(\{[^`]*\"tool\"[^`]*\})\s*\n```",
        r"<tool>(.*?)</tool>",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1).strip())
            except:
                pass
    m = re.search(r'\{\s*"tool"\s*:\s*"[^"]+"\s*,\s*"args"\s*:', text)
    if m:
        start = m.start()
        depth, end = 0, start
        for i, ch in enumerate(text[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        try:
            return json.loads(text[start:end])
        except:
            pass
    return None

class Executor:
    def __init__(self, work_dir):
        self.cwd = str(Path(work_dir).absolute())
        self.client = VseGPT()
        self.history = []
        signal.signal(signal.SIGINT, lambda *_: sys.exit(0))

    async def step(self, msg):
        msgs = [{"role": "user", "content": msg}]
        resp = await self.client.ask(msgs)
        tool = extract_tool(resp)
        return resp, tool

    async def run(self, task):
        print_line()
        print(f"  Task: {task}")
        print(f"  Dir: {self.cwd}")
        print_line()

        current = task
        for i in range(MAX_ITER):
            print(f"\n  Step {i+1}/{MAX_ITER}")

            resp, tool = await self.step(current)

            if not tool:
                print_err("No tool call")
                break

            name = tool.get("tool")
            args = tool.get("args", {})
            print(f"  Tool: {name} {json.dumps(args)[:100]}")

            result = dispatch(name, args, self.cwd)

            if result.get("ok"):
                print_ok("Done")
                out = result.get("output", "")
                if out:
                    for line in out.split("\n")[:30]:
                        print(f"    {line}")
            else:
                print_err(result.get("output", "Failed"))

            if name == "finish" or result.get("done"):
                print_ok(f"Finished: {result.get('output', 'Done')}")
                break

            current = f"[Result] {name}: {result.get('output', '')[:2000]}"

    async def loop(self):
        os.system("clear" if os.name != "nt" else "cls")
        print(f"\n  Dir: {self.cwd}")
        print(f"  Model: {MODEL}")
        print(f"  Commands: exit, clear")
        print_line()

        while True:
            try:
                task = input(f"\n  > ").strip()
                if not task:
                    continue
                if task.lower() in ("exit", "quit", "q"):
                    break
                if task.lower() == "clear":
                    self.history = []
                    os.system("clear" if os.name != "nt" else "cls")
                    continue
                await self.run(task)
            except KeyboardInterrupt:
                break

        await self.client.close()
        print()

async def main():
    work_dir = sys.argv[1] if len(sys.argv) > 1 else "."
    p = Path(work_dir).absolute()
    if not p.exists():
        print(f"Not found: {p}")
        sys.exit(1)
    ex = Executor(str(p))
    await ex.loop()

if __name__ == "__main__":
    asyncio.run(main())