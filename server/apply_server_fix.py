# -*- coding: utf-8 -*-
"""Patch server/label_tool.py: rebuild the task queue when the material changes.

Before this, tasks.json was written once and then reused forever, so a video
added to manifest.csv never got tasks and a deleted one left orphans behind.

Also tidy .gitignore, whose `annotation/` + `!annotation/.gitkeep` pair could
never work (git will not descend into an ignored directory).
"""
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / 'server' / 'label_tool.py'
GI = ROOT / '.gitignore'

src = io.open(TOOL, encoding='utf-8').read()
applied, missed = [], []


def rep(name, old, new):
    global src
    if old not in src:
        missed.append(name)
        return
    src = src.replace(old, new, 1)
    applied.append(name)


rep('import hashlib', "import csv\nimport json",
    "import csv\nimport hashlib\nimport json")

# --- signature + rebuild-aware loader -------------------------------------
rep('queue signature', '''def build_tasks(force=False):
    if F_TASKS.exists() and not force:
        return json.loads(F_TASKS.read_text(encoding='utf-8'))
    tasks = []''',
    '''def queue_signature():
    """A change here means the queue no longer matches the material."""
    parts = []
    for vid in sorted(MANIFEST):
        med = media_of(vid)
        parts.append(f'{vid}:{med["kind"]}:{len(med["frames"])}:'
                     f'{1 if med["video"] else 0}')
    for rel in ('labels/phases.csv', 'labels/cytoplasmic_strings.csv',
                'labels/grades.csv', 'labels/captions.jsonl'):
        p = ROOT / rel
        parts.append(f'{rel}:{p.stat().st_mtime_ns if p.exists() else 0}')
    return hashlib.md5('|'.join(parts).encode()).hexdigest()[:12]


def build_tasks(force=False):
    if F_TASKS.exists() and not force:
        return load_tasks()
    tasks = []''')

rep('save wrapper', '''    F_TASKS.write_text(json.dumps(tasks, indent=1, ensure_ascii=False), encoding='utf-8')
    return tasks


def load_tasks():
    return build_tasks(False)


def save_tasks(tasks):
    F_TASKS.write_text(json.dumps(tasks, indent=1, ensure_ascii=False), encoding='utf-8')''',
    '''    save_tasks(tasks)
    return tasks


def load_tasks():
    """Return the queue, rebuilding it when the material has changed.

    Statuses and assignees of tasks that still exist are carried over, so adding
    one video does not wipe the progress on the others.
    """
    sig = queue_signature()
    old = []
    if F_TASKS.exists():
        try:
            data = json.loads(F_TASKS.read_text(encoding='utf-8'))
        except (json.JSONDecodeError, OSError):
            data = {}
        if isinstance(data, dict):
            if data.get('sig') == sig:
                return data.get('tasks', [])
            old = data.get('tasks', [])
        elif isinstance(data, list):      # older plain-list format
            old = data

    fresh = build_tasks(force=True)
    keep = {t['id']: t for t in old}
    for t in fresh:
        prev = keep.get(t['id'])
        if prev:
            t['status'] = prev.get('status', t['status'])
            t['assignee'] = prev.get('assignee', '')
    save_tasks(fresh)
    return fresh


def save_tasks(tasks):
    F_TASKS.write_text(json.dumps({'sig': queue_signature(), 'tasks': tasks},
                                  indent=1, ensure_ascii=False), encoding='utf-8')''')

io.open(TOOL, 'w', encoding='utf-8').write(src)

# --- .gitignore ------------------------------------------------------------
gi = io.open(GI, encoding='utf-8').read() if GI.exists() else ''
if 'annotation/annotation' not in gi and 'annotation/*' not in gi:
    gi = (gi.replace('annotation/\n', '') or '') + \
         'annotation/*\n!annotation/.gitkeep\n'
    io.open(GI, 'w', encoding='utf-8').write(gi)
    applied.append('.gitignore')

print(f'applied : {", ".join(applied) if applied else "none"}')
if missed:
    print(f'MISSED  : {", ".join(missed)}')
    sys.exit(1)
