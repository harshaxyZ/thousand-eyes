import json

path = r'C:\Users\harsh\.claude\projects\C--Users-harsh-toal-cli\493f2670-9a28-4f63-a369-c2b9fff7c180.jsonl'
out = open(r'C:\Users\harsh\toal-cli\.session_extract\session_actions.txt', 'w', encoding='utf-8')
with open(path, encoding='utf-8', errors='replace') as f:
    for i, line in enumerate(f):
        try:
            obj = json.loads(line)
        except Exception:
            continue
        t = obj.get('type','')
        msg = obj.get('message', {})
        role = msg.get('role','')
        if t == 'assistant' and role == 'assistant':
            content = msg.get('content')
            if isinstance(content, list):
                for c in content:
                    if not isinstance(c, dict):
                        continue
                    if c.get('type') == 'text' and c.get('text','').strip():
                        out.write(f'--- LINE {i} ASSISTANT TEXT ---\n')
                        out.write(c['text'][:2000] + '\n\n')
                    elif c.get('type') == 'tool_use':
                        name = c.get('name','')
                        inp = c.get('input', {})
                        desc = ''
                        if name == 'Bash':
                            desc = inp.get('description','')
                            cmd = inp.get('command','')[:200]
                            out.write(f'--- LINE {i} TOOL {name} | {desc} | cmd: {cmd}\n')
                        elif name == 'Edit':
                            fp = inp.get('file_path','')
                            old = inp.get('old_string','')[:80].replace('\n','\n')
                            out.write(f'--- LINE {i} TOOL Edit | {fp} | old: {old}\n')
                        elif name == 'Write':
                            fp = inp.get('file_path','')
                            out.write(f'--- LINE {i} TOOL Write | {fp}\n')
                        elif name == 'Agent':
                            out.write(f'--- LINE {i} TOOL Agent | {inp.get("description","")} | prompt: {inp.get("prompt","")[:150]}\n')
                        else:
                            out.write(f'--- LINE {i} TOOL {name} | {str(inp)[:150]}\n')
out.close()
print('done')
