p = 'src/thousand_eyes/tui.py'
s = open(p, encoding='utf-8').read()

def rep(old, new, n=1):
    global s
    assert s.count(old) == n, f'expected {n} of {old[:70]!r}, found {s.count(old)}'
    s = s.replace(old, new)

# 1. status line shows the LIVE model (client_for resolves provider + model)
rep('''        if a is None:
            return (f"idle · model {self.model} · budget {self.budget} calls · "
                    f"no run in flight")''',
    '''        if a is None:
            return (f"idle · model {self._live_model()} · budget "
                    f"{self.budget} calls · no run in flight")''')

# 2. idle HUD line uses the live model too
rep('''        if planner is None:
            bar.update(progress=100)
            stats.update("idle · model " + self.model)
            return''',
    '''        if planner is None:
            bar.update(progress=100)
            stats.update("idle · model " + self._live_model())
            return''')

# 3. helper: resolve the real model name (client_for knows the provider)
rep('''    def _chat_client(self):''',
    '''    def _live_model(self) -> str:
        """The model that will actually answer — provider-aware, cheap."""
        client = self._chat_client()
        if client is not None:
            return getattr(client, "model", self.model) or self.model
        return self.model

    def _chat_client(self):''')

# 4. live typing preview: model + running tail in the stream line, one clean
#    render of the full reply at the end (no duplicate partial lines)
rep('''            async for chunk, _stats in llm.chat(msgs):
                out += chunk
                self._stream_line(out[-90:].replace("\\n", " "))''',
    '''            async for chunk, _stats in llm.chat(msgs):
                out += chunk
                self._stream_line(
                    f"{self._live_model()} typing… "
                    + out[-70:].replace("\\n", " "))''')

open(p, 'w', encoding='utf-8', newline='').write(s)
compile(s, p, 'exec')
print('polish applied OK')
