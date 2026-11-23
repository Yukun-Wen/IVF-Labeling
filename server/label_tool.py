#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""label_tool.py — annotation workbench for the embryo dataset.

Follows the usual industrial annotation loop rather than a bare form:

    queue -> pre-label -> human corrects -> submit -> review -> audit

What that means concretely
--------------------------
* **Work queue**   the material becomes ~7 tasks per video (describe, grade, key
  events, cytoplasmic string, string span, abnormal cleavage, key frame).  A
  task is claimed by one person, so two annotators never edit the same item.
* **Pre-labelling** anything already derivable from ``labels/*.csv`` arrives
  pre-filled; the annotator confirms or corrects. This is where the speed comes
  from, and it is the standard model-assisted loop.
* **Draft autosave** every edit lands in a draft, so a refresh loses nothing.
  Submitting promotes the draft to a versioned annotation.
* **Review**       a submitted item can be approved or sent back with a comment
  by a second person; agreement is computed across double-annotated items.
* **Audit trail**  every action appends one line to ``audit.jsonl``.
* **Video first**  if a clip exists it drives navigation; freeze a moment to
  annotate the still frame, because boxes need a static image to be precise.

Run
    python label_tool.py                  # http://127.0.0.1:8765
    python label_tool.py --rebuild-tasks  # regenerate the queue

Files written (all under annotation/)
    tasks.json, annotations.jsonl, drafts.jsonl, reviews.jsonl, audit.jsonl

Standard library only; binds to 127.0.0.1.
"""
import argparse
import csv
import json
import re
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
ANN = ROOT / 'annotation'
ANN.mkdir(exist_ok=True)

# Pre-labelling is OFF by default: the first pass is human annotation from
# scratch, so annotators see guidance rather than a suggested answer.  Turn it
# back on with --prelabel once derived answers have been checked.
PRELABEL = False

F_TASKS = ANN / 'tasks.json'
F_ANN = ANN / 'annotations.jsonl'
F_DRAFT = ANN / 'drafts.jsonl'
F_REVIEW = ANN / 'reviews.jsonl'
F_AUDIT = ANN / 'audit.jsonl'

IMG_EXT = {'.png', '.jpg', '.jpeg', '.tif', '.tiff', '.bmp'}
KF_RE = re.compile(r'(\d{3,6})')


# ------------------------------------------------------------------ loaders ---
def read_csv(rel):
    p = ROOT / rel
    if not p.exists():
        return []
    with open(p, encoding='utf-8-sig', newline='') as fh:
        return [r for r in csv.DictReader(fh) if any((v or '').strip() for v in r.values())]


def read_jsonl(p):
    if not Path(p).exists():
        return []
    out = []
    with open(p, encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return out


def write_jsonl(p, rows):
    p = Path(p)
    tmp = p.with_suffix(p.suffix + '.tmp')
    with open(tmp, 'w', encoding='utf-8') as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + '\n')
    tmp.replace(p)


def append_jsonl(p, rec):
    with open(p, 'a', encoding='utf-8') as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + '\n')


MANIFEST = {r['video_id'].strip(): r for r in read_csv('manifest.csv')}
SPLIT = {r['patient_id'].strip(): r['split'].strip() for r in read_csv('split.csv')}
INFO = {r['key'].strip(): r['value'].strip() for r in read_csv('dataset_info.csv')}
try:
    FPH = 60.0 / float(INFO.get('acq_interval_min', '15'))
except ValueError:
    FPH = 4.0

PHASES = {}
for _r in read_csv('labels/phases.csv'):
    _vid = _r['video_id'].strip()
    _f = (_r.get('frame') or _r.get('start_frame') or '').strip()
    if _vid and _f.lstrip('-').isdigit():
        PHASES.setdefault(_vid, []).append({'event': _r['event'].strip(), 'frame': int(_f)})
for _v in PHASES:
    PHASES[_v].sort(key=lambda d: d['frame'])

CS_BOXES = {}
for _r in read_csv('labels/cytoplasmic_strings.csv'):
    try:
        CS_BOXES.setdefault(_r['video_id'].strip(), {}).setdefault(
            int(_r['frame_index']), []).append(
            [int(_r['x1']), int(_r['y1']), int(_r['x2']), int(_r['y2'])])
    except (KeyError, ValueError):
        pass

GRADES = {}
for _r in read_csv('labels/grades.csv'):
    GRADES.setdefault(_r['video_id'].strip(), []).append(_r)

CAPTIONS = {}
for _r in read_jsonl(ROOT / 'labels' / 'captions.jsonl'):
    CAPTIONS.setdefault(str(_r.get('video_id', '')).strip(), []).append(_r)

CLINICAL = {r['cycle_id'].strip(): r for r in read_csv('clinical.csv')}
GENETICS = {r['video_id'].strip(): r for r in read_csv('genetics.csv')}


def media_of(vid):
    """-> {'kind': video|frames|illustration|none, 'frames': [...], 'video': Path|None}"""
    m = MANIFEST.get(vid, {})
    fdir = ROOT / (m.get('frames_dir') or '')
    frames = []
    if fdir.is_dir():
        frames = sorted(p for p in fdir.iterdir() if p.suffix.lower() in IMG_EXT)

    video = None
    for cand in (ROOT / 'examples' / f'ai_timelapse_{vid}.mp4',
                 ROOT / 'videos' / vid / 'raw.mp4'):
        if cand.exists():
            video = cand
            break

    if frames:
        return {'kind': 'frames', 'video': video,
                'frames': [{'path': p, 'frame_no': i, 'name': p.name}
                           for i, p in enumerate(frames)]}

    alt = ROOT / 'examples' / f'ai_keyframes_{vid}'
    if alt.is_dir():
        out = []
        for p in sorted(alt.iterdir()):
            if p.suffix.lower() not in IMG_EXT:
                continue
            hit = KF_RE.search(p.stem)
            out.append({'path': p, 'frame_no': int(hit.group(1)) if hit else len(out),
                        'name': p.name})
        out.sort(key=lambda d: d['frame_no'])
        return {'kind': 'illustration', 'frames': out, 'video': video}

    return {'kind': 'none', 'frames': [], 'video': video}


# ------------------------------------------------------------- task planning ---
KINDS = {
    'describe': ('Describe the whole clip', 'description'),
    'grade':    ('Grade the blastocyst (expansion/ICM/TE)', 'expansion'),
    'events':   ('Confirm the key event frames', 'timing'),
    'cs':       ('Cytoplasmic string: present, and where', 'cs_present'),
    'cs_span':  ('Cytoplasmic string: which frames', 'cs_interval'),
    'cleavage': ('Any abnormal cleavage', 'abnormal_cleavage'),
    'keyframe': ('Pick the decisive frame for viability', 'keyframe'),
}


# what the annotator should look at, shown in the panel instead of a pre-filled answer
TASK_GUIDE = {
    'describe': 'Watch the whole clip, then describe the developmental course: which stages '
                'appear, when compaction starts, how the blastocoel forms. Do not paste a '
                'single-frame caption.',
    'grade': 'Find the frame you would grade from (usually the last expanded blastocyst), '
             'then record expansion 0-5, ICM A-D and TE A-D as three separate fields.',
    'events': 'Step through the clip and record the frame where each event starts: tPB2, '
              'tPNf, t2, t4, t8, tM, tSB, tB. Leave an event blank if it never happens.',
    'cs': 'Scan the expanded-blastocyst window. If a thin filament bridges the ICM and the '
          'mural TE, draw one box around it. If none is visible, answer "no" and leave the '
          'box list empty.',
    'cs_span': 'Find the first and the last frame in which a string is visible and write '
               'them as first-last. A string that appears in only one frame is a span of 1.',
    'cleavage': 'Replay the cleavage divisions. Note direct cleavage (1 to 3 cells), reverse '
                'cleavage (cells merging) or chaotic cleavage, and give the frame where it '
                'is clearest.',
    'keyframe': 'Pick the single frame you would show a colleague to justify your viability '
                'judgement, then say what makes it decisive.',
}
def build_tasks(force=False):
    if F_TASKS.exists() and not force:
        return json.loads(F_TASKS.read_text(encoding='utf-8'))
    tasks = []
    for vid in MANIFEST:
        med = media_of(vid)
        if med['kind'] == 'none':
            continue
        ph = {p['event']: p['frame'] for p in PHASES.get(vid, [])}
        for kind, (title, qtype) in KINDS.items():
            if kind in ('cs', 'cs_span') and vid not in CS_BOXES:
                continue
            hint = ph.get('tB') or (PHASES.get(vid) or [{'frame': 0}])[0]['frame']
            if kind == 'cs' and vid in CS_BOXES:
                hint = min(CS_BOXES[vid])
            tasks.append({
                'id': f'{vid}__{kind}', 'video_id': vid, 'kind': kind,
                'title': title, 'qtype': qtype, 'frame_hint': hint,
                'priority': 1 if kind in ('cs', 'grade') else 2,
                'status': 'todo', 'assignee': '',
                'created': time.strftime('%Y-%m-%dT%H:%M:%S'),
            })
    F_TASKS.write_text(json.dumps(tasks, indent=1, ensure_ascii=False), encoding='utf-8')
    return tasks


def load_tasks():
    return build_tasks(False)


def save_tasks(tasks):
    F_TASKS.write_text(json.dumps(tasks, indent=1, ensure_ascii=False), encoding='utf-8')


def audit(actor, action, task_id, detail=''):
    append_jsonl(F_AUDIT, {'ts': time.strftime('%Y-%m-%dT%H:%M:%S'),
                           'actor': actor or 'anon', 'action': action,
                           'task_id': task_id, 'detail': detail})


# ------------------------------------------------------------- pre-labelling ---
def suggest(vid, kind):
    """What the label files already imply, so the annotator only confirms.

    Disabled while PRELABEL is False — the first pass is human annotation from
    scratch, so the annotator gets guidance for the task instead of a suggested
    answer.  Navigation aids (the phase chips) stay: jumping to a frame is not
    an answer.
    """
    if not PRELABEL:
        return {'answer': '', 'frame': None, 'source': 'none',
                'note': TASK_GUIDE.get(kind, '')}
    ph = {p['event']: p['frame'] for p in PHASES.get(vid, [])}
    if kind == 'describe':
        caps = CAPTIONS.get(vid, [])
        if caps:
            c = max(caps, key=lambda r: int(r.get('frame_index') or 0))
            return {'answer': c.get('text', ''), 'frame': c.get('frame_index'),
                    'source': 'labels/captions.jsonl',
                    'note': 'expert caption; edit if the clip shows more'}
    if kind == 'grade' and vid in GRADES:
        g = GRADES[vid][0]
        return {'answer': f"expansion {g.get('expansion')}, ICM {g.get('icm')}, "
                          f"TE {g.get('te')}",
                'frame': g.get('frame_index'), 'source': 'labels/grades.csv',
                'note': 'verbatim from the grading sheet'}
    if kind == 'events' and ph:
        txt = '; '.join(f'{e}={f}' for e, f in sorted(ph.items(), key=lambda kv: kv[1]))
        return {'answer': txt, 'frame': ph.get('tB'), 'source': 'labels/phases.csv',
                'note': 'confirm each frame; fix any that look wrong'}
    if kind == 'cs' and vid in CS_BOXES:
        f = min(CS_BOXES[vid])
        return {'answer': 'yes', 'frame': f, 'source': 'labels/cytoplasmic_strings.csv',
                'boxes': CS_BOXES[vid][f], 'note': 'box pre-placed from the CSV'}
    if kind == 'cs_span' and vid in CS_BOXES:
        fs = sorted(CS_BOXES[vid])
        return {'answer': f'{fs[0]}-{fs[-1]}', 'frame': fs[0],
                'source': 'labels/cytoplasmic_strings.csv',
                'note': 'first and last frame carrying a string'}
    if kind == 'keyframe' and vid in GRADES:
        return {'answer': '', 'frame': GRADES[vid][0].get('frame_index'),
                'source': 'labels/grades.csv', 'note': 'no label exists; your call'}
    return {'answer': '', 'frame': None, 'source': 'none', 'note': 'no label exists'}


# --------------------------------------------------------------------- html ---
HTML = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>embryo annotation workbench</title>
<style>
:root{--ink:#12263a;--dim:#5b7185;--brand:#0f766e;--bg:#f4f7fa;--line:#d6e2ea;--warn:#b45309}
*{box-sizing:border-box}
body{margin:0;font:14px/1.45 "Segoe UI",system-ui,sans-serif;color:var(--ink);background:var(--bg)}
header{display:flex;gap:12px;align-items:center;padding:9px 16px;background:#fff;
       border-bottom:1px solid var(--line);flex-wrap:wrap}
header b{font-size:15px}
.tabs{display:flex;gap:4px;margin-left:8px}
.tabs button{border:1px solid transparent;background:none;padding:6px 12px;border-radius:6px;cursor:pointer}
.tabs button.on{background:var(--bg);border-color:var(--line);font-weight:600}
select,input,textarea,button{font:inherit;color:inherit}
select,input[type=text],input[type=number],textarea{border:1px solid var(--line);
       border-radius:6px;padding:5px 8px;background:#fff}
button{border:1px solid var(--line);background:#fff;border-radius:6px;padding:6px 12px;cursor:pointer}
button:hover{border-color:var(--brand)}
button.primary{background:var(--brand);color:#fff;border-color:var(--brand);font-weight:600}
button.danger{color:#b3122e;border-color:#f0c2cc}
.badge{font-size:11px;padding:2px 8px;border-radius:99px;font-weight:600;white-space:nowrap}
.s-todo{background:#eef3f7;color:#41586e}.s-in_progress{background:#fde8d3;color:var(--warn)}
.s-submitted{background:#dbeafe;color:#1d4ed8}.s-approved{background:#d6f0ec;color:#0f5f59}
.s-rejected{background:#fde2e6;color:#b3122e}.s-skipped{background:#eceff2;color:#6b8094}
.b-illu{background:#fde8d3;color:var(--warn)}
main{padding:12px}
.grid{display:grid;grid-template-columns:320px 1fr 370px;gap:12px;align-items:start}
@media(max-width:1250px){.grid{grid-template-columns:1fr}}
.card{background:#fff;border:1px solid var(--line);border-radius:10px;padding:11px}
.card h3{margin:0 0 8px;font-size:12px;color:var(--dim);font-weight:700;letter-spacing:.04em}
.queue{display:flex;flex-direction:column;gap:5px;max-height:72vh;overflow:auto}
.item{border:1px solid var(--line);border-radius:7px;padding:7px 9px;cursor:pointer;font-size:13px}
.item:hover{border-color:var(--brand)}
.item.on{border-color:var(--brand);box-shadow:inset 0 0 0 2px #d6f0ec}
.item .row1{display:flex;justify-content:space-between;gap:6px;align-items:center}
.item .sub{font-size:11px;color:var(--dim);margin-top:2px}
canvas{max-width:100%;height:auto;border-radius:6px;cursor:crosshair;background:#eef3f7}
video{width:100%;max-width:100%;border-radius:6px;background:#000}
.controls{display:flex;gap:6px;align-items:center;margin-top:8px;flex-wrap:wrap}
.controls input[type=range]{flex:1;min-width:110px}
.chips{display:flex;gap:4px;flex-wrap:wrap;margin-top:6px}
.chips button{padding:2px 8px;font-size:12px}
.row{display:grid;grid-template-columns:98px 1fr;gap:8px;align-items:center;margin-bottom:7px}
.row label{font-size:12px;color:var(--dim)}
.row.wide{grid-template-columns:1fr}
textarea{width:100%;resize:vertical;min-height:54px}
.hint{font-size:11px;color:var(--dim)}
.boxlist div{display:flex;justify-content:space-between;background:#fff5f7;border:1px solid #f5c2cd;
             border-radius:5px;padding:3px 7px;font-size:12px;margin-bottom:3px}
.boxlist button{border:none;background:none;color:#b3122e;padding:0 4px;cursor:pointer}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line)}
th{color:var(--dim);font-weight:600;font-size:12px}
.toast{position:fixed;right:16px;bottom:16px;background:var(--ink);color:#fff;padding:9px 16px;
       border-radius:8px;opacity:0;transition:opacity .2s;z-index:9}
.toast.on{opacity:1}
.kpi{display:flex;gap:10px;flex-wrap:wrap}
.kpi div{background:#fff;border:1px solid var(--line);border-radius:9px;padding:9px 14px;min-width:96px}
.kpi b{display:block;font-size:22px}
.kpi span{font-size:11px;color:var(--dim)}
kbd{background:#eef3f7;border:1px solid var(--line);border-radius:4px;padding:0 4px;font-size:11px}
</style></head><body>

<header>
  <b>embryo workbench</b>
  <div class="tabs">
    <button id="t-annot" class="on" onclick="tab('annot')">annotate</button>
    <button id="t-review" onclick="tab('review')">review</button>
    <button id="t-progress" onclick="tab('progress')">progress</button>
  </div>
  <span style="flex:1"></span>
  <label class="hint">I am</label>
  <input id="who" type="text" value="AN1" style="width:80px" onchange="saveWho()">
  <button onclick="location='/api/export?kind=annotations'">export jsonl</button>
</header>

<main>
<section id="p-annot">
<div class="grid">
  <div class="card">
    <h3>QUEUE <span class="hint" id="qcount"></span></h3>
    <div style="display:flex;gap:6px;margin-bottom:8px">
      <select id="fstatus" onchange="loadQueue()" style="flex:1">
        <option value="todo">todo</option>
        <option value="mine">assigned to me</option>
        <option value="all">all</option>
        <option value="submitted">submitted</option>
        <option value="rejected">sent back</option>
      </select>
      <button onclick="claimNext()">claim next</button>
    </div>
    <div class="queue" id="queue"></div>
  </div>

  <div class="card">
    <h3 id="taskTitle">no task selected</h3>
    <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:flex-start">
      <div id="videoWrap" style="display:none;flex:1 1 290px;min-width:270px">
        <video id="vid" controls preload="auto" onloadedmetadata="onMeta()"></video>
        <div class="controls">
          <button onclick="freeze()">freeze</button>
          <span class="hint" id="vtime"></span>
        </div>
      </div>
      <div style="flex:1 1 290px;min-width:270px">
        <canvas id="cv" width="500" height="500"></canvas>
        <div class="hint" style="margin-top:4px">annotate on this still</div>
      </div>
    </div>
    <div class="controls">
      <button onclick="step(-1)">&#9664;</button>
      <input type="range" id="slider" min="0" max="0" value="0" oninput="goto(+this.value)">
      <button onclick="step(1)">&#9654;</button>
      <input type="number" id="jump" style="width:76px" placeholder="frame" onchange="jumpTo(+this.value)">
    </div>
    <div class="hint" id="pos" style="margin-top:4px"></div>
    <div class="chips" id="phaseBtns"></div>
    <p class="hint" style="margin:8px 0 0">
      drag to draw a box &middot; <kbd>&larr;</kbd><kbd>&rarr;</kbd> frame &middot;
      <kbd>Enter</kbd> submit &middot; <kbd>s</kbd> draft &middot; <kbd>Esc</kbd> cancel box
    </p>
  </div>

  <div class="card">
    <h3>ANSWER</h3>
    <div id="suggestBox" class="hint" style="display:none;background:#f7fbfa;
         border:1px solid #d6f0ec;border-radius:6px;padding:7px;margin-bottom:8px"></div>
    <div class="row"><label>qtype</label><select id="qtype" onchange="fillQ()"></select></div>
    <div class="row wide"><label>question</label><input id="question" type="text"></div>
    <div class="row"><label>answer</label><input id="answer" type="text"></div>
    <div class="row"><label>type</label>
      <select id="answer_type"><option>closed</option><option>numeric</option>
      <option>binary</option><option>open</option></select></div>
    <div class="row"><label>choices</label><input id="choices" type="text" placeholder="yes|no"></div>
    <div class="row wide"><label>rationale</label><textarea id="rationale"
      placeholder="what is visible that makes this the answer"></textarea></div>
    <div class="row"><label>basis</label>
      <select id="rationale_basis"><option>image</option><option>label</option></select></div>
    <div class="row"><label>answerable</label>
      <select id="answerable_from"><option>image</option><option>clinical_only</option>
      <option>outcome_only</option></select></div>
    <div class="row"><label>include pgt</label>
      <select id="include_pgt"><option>FALSE</option><option>TRUE</option></select></div>
    <div class="row wide"><label class="hint">boxes</label>
      <div class="boxlist" id="boxlist"></div></div>
    <div class="row wide"><label>notes</label><input id="notes" type="text"></div>
    <div style="display:flex;gap:8px;margin-top:6px">
      <button class="primary" style="flex:1" onclick="submitTask()">submit</button>
      <button onclick="saveDraft(true)">draft</button>
      <button class="danger" onclick="skipTask()">skip</button>
    </div>
    <p class="hint" id="draftState" style="margin:6px 0 0"></p>
  </div>
</div>
</section>

<section id="p-review" style="display:none">
  <div class="card">
    <h3>SUBMITTED, WAITING FOR A SECOND PAIR OF EYES</h3>
    <table><thead><tr><th>kind</th><th>video</th><th>qtype</th><th>answer</th>
      <th>annotator</th><th>action</th></tr></thead><tbody id="revBody"></tbody></table>
  </div>
  <div class="card" style="margin-top:12px">
    <h3>AGREEMENT ON DOUBLE-ANNOTATED ITEMS</h3>
    <div id="agree" class="hint">no duplicated items yet</div>
  </div>
</section>

<section id="p-progress" style="display:none">
  <div class="card"><h3>STATUS</h3><div class="kpi" id="kpi"></div></div>
  <div class="grid" style="grid-template-columns:1fr 1fr;margin-top:12px">
    <div class="card"><h3>BY VIDEO</h3><div id="byVideo"></div></div>
    <div class="card"><h3>AUDIT (last 20)</h3><div id="auditLog" class="hint"></div></div>
  </div>
</section>
</main>
<div class="toast" id="toast"></div>

<script>
const QTPL={stage:"What developmental stage is shown?",
timing:"At what hour did the embryo reach the {stage} stage?",
blastocyst:"Has the embryo formed a blastocoel yet?",
cell_count:"How many blastomeres are visible?",
cs_present:"Is a cytoplasmic string visible in this frame?",
cs_count:"How many cytoplasmic strings are visible?",
cs_interval:"Between which frames is a cytoplasmic string visible?",
expansion:"What is the expansion grade?",
icm:"How would you grade the inner cell mass?",
te:"How would you grade the trophectoderm?",
abnormal_cleavage:"Is there any abnormal cleavage (direct, reverse, chaotic)?",
fragmentation:"What fraction of the embryo is fragmented?",
zona:"Is the zona pellucida thinning?",
keyframe:"Is this a key frame for judging viability?",
description:"Describe the morphology of this embryo.",
live_birth:"What is the probability of live birth for this transfer?"};
const QTYPES=Object.keys(QTPL);
const CHOICES={blastocyst:"yes|no",cs_present:"yes|no",abnormal_cleavage:"yes|no",
zona:"yes|no",keyframe:"yes|no",live_birth:"0|1",expansion:"0|1|2|3|4|5",
icm:"A|B|C|D",te:"A|B|C|D",stage:"zygote|2-cell|4-cell|8-cell|morula|blastocyst"};

let FRAMES=[],IDX=0,BOXES=[],DRAG=null,img=null,scale=1,MED={},TASK=null,WHO='AN1';
const FPH=__FPH__||4;

function $(id){return document.getElementById(id)}
function toast(m){const t=$('toast');t.textContent=m;t.classList.add('on');
  clearTimeout(t._h);t._h=setTimeout(()=>t.classList.remove('on'),1700)}

function saveWho(){WHO=$('who').value||'anon';try{localStorage.setItem('who',WHO)}catch(e){}loadQueue()}
if(window.__WHO__) {WHO=window.__WHO__}

function tab(n){
  ['annot','review','progress'].forEach(t=>{
    $('p-'+t).style.display=(t===n?'':'none');
    $('t-'+t).classList.toggle('on',t===n);
  });
  if(n==='review')loadReview();
  if(n==='progress')loadProgress();
}

async function boot(){
  $('qtype').innerHTML=QTYPES.map(q=>`<option>${q}</option>`).join('');
  fillQ();
  drawEmpty();
  await loadQueue();
  const want=new URLSearchParams(location.search).get('task');
  if(want){ try{ await openTask(want); }catch(e){ console.warn(e); } }
}

async function loadQueue(){
  const st=$('fstatus').value;
  const r=await (await fetch(`/api/tasks?status=${st}&who=${encodeURIComponent(WHO)}`)).json();
  const q=$('queue');
  $('qcount').textContent=`(${r.tasks.length})`;
  if(!r.tasks.length){q.innerHTML='<div class="hint">nothing here</div>';return}
  q.innerHTML=r.tasks.map(t=>`
    <div class="item ${TASK&&TASK.id===t.id?'on':''}" onclick="openTask('${t.id}')">
      <div class="row1"><span>${t.title}</span>
        <span class="badge s-${t.status}">${t.status}</span></div>
      <div class="sub">${t.video_id} &middot; ${t.kind} &middot; p${t.priority}
        ${t.assignee?' &middot; '+t.assignee:''}</div>
    </div>`).join('');
}

async function claimNext(){
  const r=await (await fetch('/api/tasks?status=todo&who='+encodeURIComponent(WHO))).json();
  if(!r.tasks.length){toast('queue empty');return}
  openTask(r.tasks[0].id);
}

async function openTask(tid){
  const r=await (await fetch('/api/task?id='+encodeURIComponent(tid))).json();
  if(!r.task){toast('task not found');return}
  TASK=r.task;MED=r.media||{};
  $('taskTitle').textContent=TASK.title+' — '+TASK.video_id;

  if(MED.video){$('videoWrap').style.display='';const v=$('vid');
    if(v.dataset.src!==MED.video){v.dataset.src=MED.video;
      v.src='/api/video?vid='+encodeURIComponent(TASK.video_id)}}
  else $('videoWrap').style.display='none';

  FRAMES=MED.frames||[];
  $('slider').max=Math.max(0,FRAMES.length-1);

  const s=r.suggest||{};
  // the question template must follow the task, so reset it here; a restored
  // draft overwrites it afterwards if the annotator edited the wording
  $('qtype').value=TASK.qtype;fillQ(true);
  if(s.answer)$('answer').value=s.answer;
  if(s.source&&s.source!=='none'){
    $('suggestBox').style.display='';
    $('suggestBox').innerHTML='<b>pre-filled</b> from '+s.source+'<br>'+(s.note||'');
  } else $('suggestBox').style.display='none';

  BOXES=(s.boxes||[]).map(b=>b.slice());renderBoxes();

  const d=await (await fetch('/api/draft?task='+encodeURIComponent(tid))).json();
  if(d.draft){applyDraft(d.draft);}
  else{ if(s.frame!=null)jumpTo(s.frame); else goto(0); }

  await fetch('/api/claim',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({task_id:tid,actor:WHO})});
  await loadQueue();
}

function applyDraft(d){
  $('answer').value=d.answer||'';
  $('rationale').value=d.rationale||'';
  $('notes').value=d.notes||'';
  if(d.question)$('question').value=d.question;
  BOXES=(d.boxes||'').split(';').filter(x=>x.trim()).map(x=>x.split(',').map(Number));
  renderBoxes();
  if(d.frame_index!=null)jumpTo(d.frame_index);
  $('draftState').textContent='draft restored';
}

function fillQ(reset){
  if(reset===undefined)reset=true;
  const q=$('qtype').value;
  if(reset)$('question').value=QTPL[q]||'';
  const ch=CHOICES[q];
  $('choices').value=ch||'';
  $('answer_type').value=ch?(q==='live_birth'?'binary':'closed'):(q==='timing'?'numeric':'open');
}

function onMeta(){
  const v=$('vid');
  if(!MED.video)return;
  $('vtime').textContent=v.duration.toFixed(1)+' s clip';
}

function totalFrames(){
  const m=MED.frames||[];
  return m.length?m[m.length-1].frame_no+1:500;
}

function freeze(){
  const v=$('vid');
  if(!MED.video||!v.duration){toast('no clip');return}
  const f=Math.round(v.currentTime/v.duration*(totalFrames()-1));
  jumpTo(f);toast('frozen at frame '+f);
}

function goto(i){
  if(!FRAMES.length){drawEmpty();return}
  IDX=Math.max(0,Math.min(FRAMES.length-1,i));
  $('slider').value=IDX;
  const f=FRAMES[IDX];
  img=new Image();
  img.onload=()=>{scale=Math.min(1,500/img.width);
    $('cv').width=Math.round(img.width*scale);
    $('cv').height=Math.round(img.height*scale);redraw()};
  img.src='/frame?vid='+encodeURIComponent(TASK.video_id)+'&i='+IDX;
  $('jump').value=f.frame_no;
  const h=(f.frame_no/FPH).toFixed(1);
  $('pos').innerHTML='frame '+f.frame_no+' &middot; '+h+' h &middot; '+(IDX+1)+'/'+FRAMES.length+
    (MED.kind==='illustration'?' <span class="badge b-illu">illustration</span>':'');
  renderPhases();
}
function step(d){goto(IDX+d)}
function jumpTo(fn){let b=0,bd=1e9;(FRAMES||[]).forEach((f,i)=>{const dd=Math.abs(f.frame_no-fn);
  if(dd<bd){bd=dd;b=i}});goto(b)}

function renderPhases(){
  const el=$('phaseBtns');
  const ph=(TASK&&TASK.phases)||[];
  if(!ph.length){el.innerHTML='';return}
  el.innerHTML=ph.map(p=>`<button onclick="jumpTo(${p.frame})">${p.event} ${p.frame}</button>`).join('');
}

function drawEmpty(){
  const c=$('cv'),x=c.getContext('2d');
  x.fillStyle='#eef3f7';x.fillRect(0,0,c.width,c.height);
  x.fillStyle='#8aa0b4';x.font='14px Segoe UI';x.textAlign='center';
  x.fillText(TASK?'no frames for this video':'select a task from the queue',c.width/2,c.height/2);
}
function redraw(){
  const c=$('cv'),x=c.getContext('2d');
  if(!img){drawEmpty();return}
  x.clearRect(0,0,c.width,c.height);x.drawImage(img,0,0,c.width,c.height);
  x.lineWidth=2;
  BOXES.forEach((b,i)=>{const[a,bb,dd,e]=b.map(v=>v*scale);
    x.strokeStyle='#e11d48';x.strokeRect(a,bb,dd-a,e-bb);
    x.fillStyle='#e11d48';x.font='12px Segoe UI';x.textAlign='left';
    x.fillText('#'+(i+1),a+2,Math.max(12,bb-3))});
  if(DRAG){const{x0,y0,x1,y1}=DRAG;x.setLineDash([4,3]);x.strokeStyle='#0f766e';
    x.strokeRect(Math.min(x0,x1),Math.min(y0,y1),Math.abs(x1-x0),Math.abs(y1-y0));
    x.setLineDash([])}
}
function toImg(e){const r=$('cv').getBoundingClientRect();
  return [(e.clientX-r.left)*($('cv').width/r.width),
          (e.clientY-r.top)*($('cv').height/r.height)]}

document.addEventListener('DOMContentLoaded',()=>{
  const cv=$('cv');
  cv.addEventListener('mousedown',e=>{const[x,y]=toImg(e);DRAG={x0:x,y0:y,x1:x,y1:y}});
});
window.addEventListener('mousemove',e=>{if(!DRAG)return;
  const[x,y]=toImg(e);DRAG.x1=x;DRAG.y1=y;redraw()});
window.addEventListener('mouseup',()=>{
  if(!DRAG)return;
  const{x0,y0,x1,y1}=DRAG;
  const b=[Math.round(Math.min(x0,x1)/scale),Math.round(Math.min(y0,y1)/scale),
           Math.round(Math.max(x0,x1)/scale),Math.round(Math.max(y0,y1)/scale)];
  DRAG=null;
  if(b[2]-b[0]>3&&b[3]-b[1]>3){BOXES.push(b);renderBoxes();saveDraft(false)}
  redraw()});

function renderBoxes(){
  const el=$('boxlist');
  if(!BOXES.length){el.innerHTML='<span class="hint">none yet — drag on the image</span>';return}
  el.innerHTML=BOXES.map((b,i)=>`<div><span>#${i+1} ${b.join(',')}</span>
    <button onclick="delBox(${i})">&#10005;</button></div>`).join('');
}
function delBox(i){BOXES.splice(i,1);renderBoxes();redraw();saveDraft(false)}

function payload(){
  const f=FRAMES[IDX]||{};
  return {task_id:TASK?TASK.id:null,video_id:TASK?TASK.video_id:null,
    kind:TASK?TASK.kind:null,qtype:$('qtype').value,question:$('question').value,
    answer:$('answer').value,answer_type:$('answer_type').value,
    choices:$('choices').value,rationale:$('rationale').value,
    rationale_basis:$('rationale_basis').value,
    answerable_from:$('answerable_from').value,include_pgt:$('include_pgt').value,
    frame_index:(f.frame_no!=null?f.frame_no:null),
    boxes:BOXES.map(b=>b.join(',')).join(';'),
    box_labels:BOXES.length?'cytoplasmic_string':'',
    notes:$('notes').value,actor:WHO,source:MED.kind||''};
}

let dt=null;
function autosave(){clearTimeout(dt);dt=setTimeout(()=>saveDraft(false),700)}
document.addEventListener('input',e=>{
  if(e.target&&['answer','rationale','notes','question'].includes(e.target.id))autosave()});

async function saveDraft(loud){
  if(!TASK)return;
  await fetch('/api/draft',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({task_id:TASK.id,draft:payload()})});
  $('draftState').textContent='draft saved '+(new Date()).toLocaleTimeString();
  if(loud)toast('draft saved');
}

async function submitTask(){
  if(!TASK){toast('pick a task first');return}
  const p=payload();
  if(!p.question){toast('question is empty');return}
  if(!p.answer){toast('answer is empty');return}
  const r=await fetch('/api/submit',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(p)});
  const j=await r.json();
  if(!j.ok){toast('error: '+j.error);return}
  toast('submitted · '+j.done+'/'+j.total+' done');
  TASK=null;$('taskTitle').textContent='no task selected';
  const c=$('cv').getContext('2d');c.clearRect(0,0,500,500);
  await loadQueue();
}

async function skipTask(){
  if(!TASK)return;
  await fetch('/api/skip',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({task_id:TASK.id,actor:WHO,reason:'needs better frames'})});
  toast('skipped');TASK=null;await loadQueue();
}

async function loadReview(){
  const r=await (await fetch('/api/review')).json();
  $('revBody').innerHTML=r.items.length?r.items.map(x=>`
    <tr><td>${x.kind||''}</td><td>${x.video_id}</td><td>${x.qtype}</td>
      <td>${String(x.answer||'').slice(0,60)}</td><td>${x.annotator||''}</td>
      <td><button onclick="decide('${x.task_id}','approve')">approve</button>
          <button class="danger" onclick="decide('${x.task_id}','reject')">send back</button></td>
    </tr>`).join(''):'<tr><td colspan="6" class="hint">nothing waiting</td></tr>';
  $('agree').textContent=r.agreement||'no duplicated items yet';
}

async function decide(id,verdict){
  const c=prompt(verdict==='reject'?'why send it back?':'comment (optional)','')||'';
  await fetch('/api/review',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({id,verdict,comment:c,actor:WHO})});
  toast(verdict);loadReview();
}

async function loadProgress(){
  const r=await (await fetch('/api/progress')).json();
  $('kpi').innerHTML=Object.entries(r.counts).map(([k,v])=>
    `<div><b>${v}</b><span>${k}</span></div>`).join('');
  $('byVideo').innerHTML='<table><thead><tr><th>video</th><th>total</th><th>done</th>'+
    '<th>skipped</th></tr></thead><tbody>'+
    r.by_video.map(v=>`<tr><td>${v.video_id}</td><td>${v.total}</td><td>${v.done}</td>`+
      `<td>${v.skipped}</td></tr>`).join('')+'</tbody></table>';
  $('auditLog').innerHTML=r.audit.map(a=>
    `${a.ts.slice(11)} <b>${a.actor}</b> ${a.action} ${a.task_id}`).join('<br>')||'no events yet';
}

document.addEventListener('keydown',e=>{
  if(['INPUT','TEXTAREA','SELECT'].includes(e.target.tagName))return;
  if(e.key==='ArrowLeft'){e.preventDefault();step(-1)}
  if(e.key==='ArrowRight'){e.preventDefault();step(1)}
  if(e.key==='Enter'){e.preventDefault();submitTask()}
  if(e.key==='s'){e.preventDefault();saveDraft(true)}
  if(e.key==='Escape'){DRAG=null;redraw()}
});

try{const w=localStorage.getItem('who');if(w){WHO=w;$('who').value=w}}catch(e){}
boot();
</script></body></html>
"""


# ------------------------------------------------------------------- server ---
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype='application/json'):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get('Content-Length') or 0)
        try:
            return json.loads(self.rfile.read(n) or b'{}')
        except json.JSONDecodeError:
            return {}

    # ------------------------------------------------------------------ GET ---
    def do_GET(self):
        parsed = urlparse(self.path)
        path, q = parsed.path, parse_qs(parsed.query)

        if path == '/':
            html = HTML.replace('__FPH__', str(FPH))
            return self._send(200, html, 'text/html; charset=utf-8')

        if path == '/api/tasks':
            st = (q.get('status') or ['todo'])[0]
            who = (q.get('who') or [''])[0]
            tasks = load_tasks()
            if st == 'todo':
                tasks = [t for t in tasks if t['status'] == 'todo']
            elif st == 'mine':
                tasks = [t for t in tasks if t.get('assignee') == who and
                         t['status'] in ('todo', 'in_progress')]
            elif st == 'submitted':
                tasks = [t for t in tasks if t['status'] == 'submitted']
            elif st == 'rejected':
                tasks = [t for t in tasks if t['status'] == 'rejected']
            tasks = sorted(tasks, key=lambda t: (t['priority'], t['video_id']))
            return self._send(200, {'tasks': tasks[:200]})

        if path == '/api/task':
            tid = (q.get('id') or [''])[0]
            t = next((t for t in load_tasks() if t['id'] == tid), None)
            if not t:
                return self._send(404, {'task': None})
            med = media_of(t['video_id'])
            return self._send(200, {
                'task': dict(t, phases=PHASES.get(t['video_id'], [])),
                'media': {'kind': med['kind'], 'video': bool(med['video']),
                          'frames': [{'frame_no': f['frame_no'], 'name': f['name']}
                                     for f in med['frames']]},
                'suggest': suggest(t['video_id'], t['kind']),
            })

        if path == '/api/frames':
            vid = (q.get('vid') or [''])[0]
            med = media_of(vid)
            return self._send(200, {'kind': med['kind'], 'video': bool(med['video']),
                                    'frames': [{'frame_no': f['frame_no'],
                                                'name': f['name']}
                                               for f in med['frames']]})

        if path == '/frame':
            vid = (q.get('vid') or [''])[0]
            try:
                i = int((q.get('i') or ['0'])[0])
            except ValueError:
                return self._send(400, {'error': 'bad index'})
            med = media_of(vid)
            if not med['frames'] or not (0 <= i < len(med['frames'])):
                return self._send(404, {'error': 'no such frame'})
            p = med['frames'][i]['path']
            ct = 'image/jpeg' if p.suffix.lower() in ('.jpg', '.jpeg') else 'image/png'
            return self._send(200, p.read_bytes(), ct)

        if path == '/api/video':
            vid = (q.get('vid') or [''])[0]
            med = media_of(vid)
            if not med['video']:
                return self._send(404, {'error': 'no clip'})
            return _stream_file(self, med['video'])

        if path == '/api/draft':
            tid = (q.get('task') or [''])[0]
            d = next((d for d in read_jsonl(F_DRAFT) if d.get('task_id') == tid), None)
            return self._send(200, {'draft': d.get('draft') if d else None})

        if path == '/api/review':
            anns = read_jsonl(F_ANN)
            waiting = [a for a in anns if a.get('status') == 'submitted']
            by_task = {}
            for a in anns:
                by_task.setdefault(a.get('task_id'), []).append(a)
            dupes = [v for v in by_task.values()
                     if len({x.get('annotator') for x in v}) > 1]
            text = 'no duplicated items yet'
            if dupes:
                agree = tot = 0
                for group in dupes:
                    ans = [str(x.get('answer', '')).strip().lower() for x in group]
                    for i in range(len(ans)):
                        for j in range(i + 1, len(ans)):
                            tot += 1
                            agree += ans[i] == ans[j]
                text = f'{agree}/{tot} double-annotated pairs agree ' \
                       f'({agree / max(tot, 1):.0%})'
            return self._send(200, {'items': waiting[-50:], 'agreement': text})

        if path == '/api/progress':
            tasks = load_tasks()
            keys = ['todo', 'in_progress', 'submitted', 'approved', 'rejected', 'skipped']
            counts = {k: sum(t['status'] == k for t in tasks) for k in keys}
            by_video = []
            for vid in MANIFEST:
                ts = [t for t in tasks if t['video_id'] == vid]
                by_video.append({'video_id': vid, 'total': len(ts),
                                 'done': sum(t['status'] in ('submitted', 'approved')
                                             for t in ts),
                                 'skipped': sum(t['status'] == 'skipped' for t in ts)})
            return self._send(200, {'counts': counts, 'by_video': by_video,
                                    'audit': list(reversed(read_jsonl(F_AUDIT)))[:20]})

        if path == '/api/export':
            kind = (q.get('kind') or ['annotations'])[0]
            src = {'annotations': F_ANN, 'audit': F_AUDIT,
                   'reviews': F_REVIEW, 'drafts': F_DRAFT}.get(kind, F_ANN)
            body = ''.join(json.dumps(r, ensure_ascii=False) + '\n'
                           for r in read_jsonl(src))
            b = body.encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/jsonl')
            self.send_header('Content-Disposition',
                             f'attachment; filename="{kind}.jsonl"')
            self.send_header('Content-Length', str(len(b)))
            self.end_headers()
            self.wfile.write(b)
            return

        return self._send(404, {'error': 'not found'})

    # ----------------------------------------------------------------- POST ---
    def do_POST(self):
        d = self._body()
        path = self.path

        if path == '/api/claim':
            tid, who = d.get('task_id'), d.get('actor', 'anon')
            tasks = load_tasks()
            for t in tasks:
                if t['id'] != tid:
                    continue
                if t.get('assignee') and t['assignee'] != who and t['status'] == 'in_progress':
                    return self._send(409, {'ok': False,
                                            'error': f"claimed by {t['assignee']}"})
                t['assignee'] = who
                if t['status'] == 'todo':
                    t['status'] = 'in_progress'
                break
            save_tasks(tasks)
            return self._send(200, {'ok': True})

        if path == '/api/draft':
            tid = d.get('task_id')
            if not tid:
                return self._send(400, {'ok': False, 'error': 'task_id required'})
            drafts = [x for x in read_jsonl(F_DRAFT) if x.get('task_id') != tid]
            drafts.append({'task_id': tid, 'draft': d.get('draft', {}),
                           'saved': time.strftime('%H:%M:%S')})
            write_jsonl(F_DRAFT, drafts)
            return self._send(200, {'ok': True})

        if path == '/api/submit':
            err = _validate(d)
            if err:
                return self._send(400, {'ok': False, 'error': err})
            anns = read_jsonl(F_ANN)
            tid = d.get('task_id')
            version = 1 + max([a.get('version', 1) for a in anns
                               if a.get('task_id') == tid] or [0])
            rec = dict(d, id=f'{tid}#v{version}', version=version,
                       status='submitted',
                       submitted=time.strftime('%Y-%m-%dT%H:%M:%S'))
            append_jsonl(F_ANN, rec)

            tasks = load_tasks()
            for t in tasks:
                if t['id'] == tid:
                    t['status'] = 'submitted'
                    t['assignee'] = d.get('actor', '')
                    break
            save_tasks(tasks)
            write_jsonl(F_DRAFT, [x for x in read_jsonl(F_DRAFT)
                                  if x.get('task_id') != tid])
            audit(d.get('actor', 'anon'), 'submit', tid,
                  f"v{version} answer={str(d.get('answer'))[:40]}")
            done = sum(t['status'] in ('submitted', 'approved') for t in tasks)
            return self._send(200, {'ok': True, 'done': done, 'total': len(tasks)})

        if path == '/api/skip':
            tid, who = d.get('task_id'), d.get('actor', 'anon')
            tasks = load_tasks()
            for t in tasks:
                if t['id'] == tid:
                    t['status'] = 'skipped'
                    t['assignee'] = who
                    break
            save_tasks(tasks)
            audit(who, 'skip', tid, d.get('reason', ''))
            return self._send(200, {'ok': True})

        if path == '/api/review':
            aid, verdict = d.get('id'), d.get('verdict')
            if verdict not in ('approve', 'reject'):
                return self._send(400, {'ok': False, 'error': 'bad verdict'})
            anns = read_jsonl(F_ANN)
            hit = False
            for a in anns:
                if a.get('task_id') == aid and a.get('status') == 'submitted':
                    a['status'] = 'approved' if verdict == 'approve' else 'rejected'
                    hit = True
                    break
            if hit:
                write_jsonl(F_ANN, anns)
            append_jsonl(F_REVIEW, {'ts': time.strftime('%Y-%m-%dT%H:%M:%S'),
                                    'actor': d.get('actor', ''), 'task_id': aid,
                                    'verdict': verdict, 'comment': d.get('comment', '')})
            tasks = load_tasks()
            for t in tasks:
                if t['id'] == aid:
                    t['status'] = 'approved' if verdict == 'approve' else 'rejected'
                    break
            save_tasks(tasks)
            audit(d.get('actor', ''), 'review:' + verdict, aid, d.get('comment', ''))
            return self._send(200, {'ok': True})

        return self._send(404, {'ok': False, 'error': 'not found'})


def _validate(d):
    if not str(d.get('question', '')).strip():
        return 'question is empty'
    if not str(d.get('answer', '')).strip():
        return 'answer is empty'
    if d.get('video_id') not in MANIFEST:
        return 'unknown video_id'
    at = d.get('answer_type')
    ch = [c.strip() for c in str(d.get('choices', '')).split('|') if c.strip()]
    if at in ('closed', 'binary'):
        if not ch:
            return 'a closed answer needs a choices list'
        if str(d.get('answer')).strip() not in ch:
            return f"answer {d.get('answer')!r} not in choices {ch}"
    if at == 'numeric':
        try:
            float(str(d.get('answer')))
        except ValueError:
            return 'numeric answer is not a number'
    for b in str(d.get('boxes', '')).split(';'):
        if b.strip() and len(b.split(',')) != 4:
            return f'box needs 4 numbers: {b}'
    return None


def _stream_file(handler, path):
    """Serve media with byte-range support so <video> can seek."""
    data = path.read_bytes()
    total = len(data)
    ctype = 'video/mp4' if path.suffix.lower() == '.mp4' else 'application/octet-stream'
    rng = handler.headers.get('Range')
    if rng and rng.startswith('bytes='):
        try:
            a, b = rng[6:].split('-')
            start = int(a) if a else 0
            end = int(b) if b else total - 1
        except ValueError:
            start, end = 0, total - 1
        start, end = max(0, start), min(total - 1, end)
        chunk = data[start:end + 1]
        handler.send_response(206)
        handler.send_header('Content-Type', ctype)
        handler.send_header('Content-Range', f'bytes {start}-{end}/{total}')
        handler.send_header('Accept-Ranges', 'bytes')
        handler.send_header('Content-Length', str(len(chunk)))
        handler.end_headers()
        handler.wfile.write(chunk)
        return
    handler.send_response(200)
    handler.send_header('Content-Type', ctype)
    handler.send_header('Accept-Ranges', 'bytes')
    handler.send_header('Content-Length', str(total))
    handler.end_headers()
    handler.wfile.write(data)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=8765)
    ap.add_argument('--no-browser', action='store_true')
    ap.add_argument('--prelabel', action='store_true',
                    help='pre-fill answers from labels/*.csv (off by default)')
    ap.add_argument('--rebuild-tasks', action='store_true',
                    help='regenerate the queue, discarding current statuses')
    args = ap.parse_args()

    global PRELABEL
    PRELABEL = bool(args.prelabel)
    build_tasks(args.rebuild_tasks)
    srv = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    url = f'http://127.0.0.1:{args.port}'
    tasks = load_tasks()
    print(f'workbench  ->  {url}')
    print(f'queue      ->  {len(tasks)} task(s)')
    print(f'writes     ->  annotation/  (tasks, annotations, drafts, reviews, audit)')
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print('\nstopped')


if __name__ == '__main__':
    main()
