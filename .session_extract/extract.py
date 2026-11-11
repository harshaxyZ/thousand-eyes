import json

path = r'C:\Users\user\.claude\projects\C--Users-user-toal-cli\493f2670-9a28-4f63-a369-c2b9fff7c180.jsonl'
out = open(r'C:\Users\user\toal-cli\.session_extract\session_prompts.txt', 'w', encoding='utf-8')
with open(path, encoding='utf-8', errors='replace') as f:
    for i, line in enumerate(f):
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if obj.get('type') != 'user':
            continue
        msg = obj.get('message', {})
        content = msg.get('content')
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            texts = []
            for c in content:
                if isinstance(c, dict) and c.get('type') == 'text':
                    texts.append(c.get('text', ''))
            text = '\n'.join(texts)
        else:
            continue
        if not text.strip():
            continue
        if text.strip().startswith('<') or 'tool_result' in text[:100]:
            continue
        out.write(f'===== LINE {i} | {obj.get("timestamp","")} =====\n')
        out.write(text + '\n\n')
out.close()
print('done')
