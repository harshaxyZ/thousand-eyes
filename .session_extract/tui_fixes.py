p = 'src/thousand_eyes/tui.py'
s = open(p, encoding='utf-8').read()
orig = s

def rep(old, new, n=1):
    global s
    assert s.count(old) == n, f'expected {n} of {old[:70]!r}, found {s.count(old)}'
    s = s.replace(old, new)

# 1. await_cmd — the missing read-only shell-out helper (called, never defined)
AWAIT_CMD = '''def await_cmd(command: str, timeout: float = 15.0) -> str:
    """Run one read-only shell command; operator sees exactly what ran."""
    import shlex
    import subprocess
    argv = shlex.split(command)
    if not argv:
        return "(empty command)"
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout)
    except FileNotFoundError:
        return f"(not found: {argv[0]})"
    except subprocess.TimeoutExpired:
        return f"(timed out after {timeout:.0f}s: {command})"
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    body = out or err or "(no output)"
    tail = "\\n…" if len(body) > 1200 else ""
    return f"$ {command}\\n{body[:1200]}{tail}"


'''
rep("""def route_intent(text: str) -> Tuple[str, Optional[str]]:""",
    AWAIT_CMD + """def route_intent(text: str) -> Tuple[str, Optional[str]]:""")

# 2. /exit also quits the app, not just prints "bye"
rep("""        elif cmd in ("/exit", "/quit"):
            self.action_exit()""",
    """        elif cmd in ("/exit", "/quit"):
            self.action_exit()
            self.exit()""")

# 3. YOU lines carry a timestamp — clearly yours, clearly when
rep('''    def _user_line(self, text: str) -> None:
        self._chat_line(f"[bold white]┌─[ YOU ]────────────────────────────"
                        f"──────────────┐[/]\\n[white]{text}[/]\\n"
                        f"[bold white]└────────────────────────────────"
                        f"────────────────┘[/]")''',
    '''    def _user_line(self, text: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self._chat_line(f"[bold white]┌─[ YOU · {stamp} ]"
                        f"──────────────────────────┐[/]\\n[white]{text}[/]\\n"
                        f"[bold white]└────────────────────────────────"
                        f"────────────────┘[/]")''')

# 4. redraw-on-idle only — the banner owns its own timer
rep("""    CSS = CSS

    BINDINGS = [""",
    """    CSS = CSS

    # redraw-on-idle only; the banner owns its own timer
    ENABLE_LIVE_REGION = False

    BINDINGS = [""")

open(p, 'w', encoding='utf-8', newline='').write(s)
compile(s, p, 'exec')
print('edits applied OK, delta', len(s) - len(orig), 'chars')
