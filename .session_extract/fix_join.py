import re

p = 'src/thousand_eyes/tui.py'
src = open(p, encoding='utf-8').read()

# The broken pattern: `return "` + newline + `".join(`  (literal newline inside string)
broken = 'return "\n".join('
fixed = 'return "\\n".join('

count = src.count(broken)
print('occurrences of broken pattern:', count)
assert count == 1, 'unexpected occurrence count'
src = src.replace(broken, fixed)

open(p, 'w', encoding='utf-8', newline='').write(src)

# verify compile
compile(src, p, 'exec')
print('compile OK')

# show the region
lines = src.split('\n')
for i, ln in enumerate(lines):
    if 'return "\\n".join(' in ln:
        print(f'line {i+1}: {ln}')
