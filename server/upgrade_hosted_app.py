# -*- coding: utf-8 -*-
"""Upgrade the hosted (GitHub Pages) app with the three fixes from the local build.

1. Frame interval is now a setting, not a hardcoded 15 minutes.
2. Video extraction takes 4-200 frames instead of a fixed 24.
3. Labels can be written straight to a file on disk with the File System Access
   API, so they survive a cleared browser profile; localStorage stays as the
   fallback and the automatic store.

Also adds ?tab= aliases and a Save-to-file button.

    python server/upgrade_hosted_app.py
"""
import io
import sys
from pathlib import Path

P = Path(__file__).resolve().parents[1] / 'docs' / 'index.html'
src = io.open(P, encoding='utf-8').read()
orig_len = len(src)
applied, missed = [], []


def rep(name, old, new):
    global src
    if old not in src:
        missed.append(name)
        return
    src = src.replace(old, new, 1)
    applied.append(name)


# ---------------------------------------------------------------- 1. header ---
rep('header buttons',
    """  <span class="hint">keys: <kbd>&larr;</kbd><kbd>&rarr;</kbd> frame · <kbd>Enter</kbd> save · <kbd>Esc</kbd> cancel box</span>
  <button onclick="exportJsonl()">Export JSONL</button>""",
    """  <span class="hint">keys: <kbd>&larr;</kbd><kbd>&rarr;</kbd> frame · <kbd>Enter</kbd> save · <kbd>Esc</kbd> cancel box</span>
  <button id="fileBtn" onclick="pickSaveFile()" title="Write labels straight to a file on this computer">Save to file</button>
  <button onclick="exportJsonl()">Export JSONL</button>""")

# ------------------------------------------------- 2. interval + video frame N --
rep('interval constant',
    """const FPH=60/SAMPLE.interval_min;""",
    """/* the acquisition interval is a setting now: it is the only number that turns
   a frame index into hours, so it must not be hardcoded */
let INTERVAL=parseFloat(localStorage.getItem('ivf-interval')||'')||SAMPLE.interval_min;
function fph(){return INTERVAL>0?60/INTERVAL:null}""")

rep('interval use 1/2',
    """  const timeTxt=MED.kind==='illustration'?' · '+(frameNo/FPH).toFixed(1)+' h':'';""",
    """  const r=fph();
  const timeTxt=(MED.kind==='illustration'&&r)?' · '+(frameNo/r).toFixed(1)+' h':'';""")

rep('interval use 2/2',
    """  $('pos').innerHTML='<span class="badge b-illu">illustration · not patient data</span> '+FRAMES.length+' frames loaded';""",
    """  $('pos').innerHTML='<span class="badge b-illu">illustration · not patient data</span> '+FRAMES.length+' frames loaded';
  syncInterval();""")

# ------------------------------------------------------- 3. video frame count --
rep('video N input',
    """      <button class="tiny" onclick="pickVideo()">Load my video</button>
      <button class="tiny" onclick="loadSample()">Sample</button>""",
    """      <button class="tiny" onclick="pickVideo()">Load my video</button>
      <button class="tiny" onclick="loadSample()">Sample</button>""")

rep('kill old N=24',
    """  const N=24, dur=v.duration;""",
    """  const N=Math.max(4,Math.min(200,parseInt(document.getElementById('videoN')?.value||'48',10)));
  const dur=v.duration;""")

rep('videoN control',
    """  <div class="filebar">
      <button class="tiny" onclick="pickFiles()">Load my frames</button>
      <button class="tiny" onclick="pickVideo()">Load my video</button>
      <button class="tiny" onclick="loadSample()">Sample</button>
    </div>""",
    """  <div class="filebar">
      <button class="tiny" onclick="pickFiles()">Load my frames</button>
      <button class="tiny" onclick="pickVideo()">Load my video</button>
      <button class="tiny" onclick="loadSample()">Sample</button>
    </div>
    <div class="filebar" style="align-items:center">
      <label class="hint" for="videoN">stills per video</label>
      <input type="number" id="videoN" min="4" max="200" value="48"
             style="width:82px" onchange="localStorage.setItem('ivf-videon',this.value)">
    </div>
    <div class="filebar" style="align-items:center">
      <label class="hint" for="interval">frame interval</label>
      <input type="number" id="interval" min="0.5" max="1440" step="0.5"
             style="width:82px" onchange="setInterval2(this.value)">
      <span class="hint">minutes per frame</span>
    </div>""")

rep('restore videoN',
    """function pickFiles(){ $('fFiles').click() }""",
    """function pickFiles(){ $('fFiles').click() }""")

# ------------------------------------------------------ 4. save-to-file (FSA) --
rep('save file function',
    """function exportJsonl(){""",
    """/* ---------------------------------------------------------------------------
   Writing straight to disk.  The File System Access API lets the page keep a
   handle on a file and rewrite it, so a label survives a cleared browser
   profile.  Only Chromium browsers offer it; the rest fall back to a download,
   and localStorage remains the automatic store either way.
   --------------------------------------------------------------------------- */
let FILE_HANDLE=null;

function labelsBody(){
  return load().map(r=>JSON.stringify(r)).join('\\n')+'\\n';
}

async function pickSaveFile(){
  if(!window.showSaveFilePicker){
    toast('this browser cannot write files directly — use Export JSONL instead');
    return;
  }
  try{
    FILE_HANDLE=await window.showSaveFilePicker({
      suggestedName:'labels.jsonl',
      types:[{description:'JSON Lines',accept:{'application/jsonl':['.jsonl']}}]
    });
    await writeToFile();
    toast('labels will now be saved to that file');
    renderFileState();
  }catch(e){
    if(e.name!=='AbortError')toast('could not open the file: '+e.message,true);
  }
}

async function writeToFile(){
  if(!FILE_HANDLE)return false;
  try{
    const w=await FILE_HANDLE.createWritable();
    await w.write(labelsBody());
    await w.close();
    renderFileState();
    return true;
  }catch(e){
    toast('write failed: '+e.message,true);
    FILE_HANDLE=null;renderFileState();
    return false;
  }
}

function renderFileState(){
  const b=document.getElementById('fileBtn');
  if(!b)return;
  b.textContent=FILE_HANDLE?'Saving to file ✓':'Save to file';
  b.style.borderColor=FILE_HANDLE?'#0f766e':'';
  b.style.color=FILE_HANDLE?'#0f766e':'';
}

function setInterval2(v){
  const n=parseFloat(v);
  if(!(n>0)||n>1440){toast('interval must be between 0.5 and 1440 minutes',true);return}
  INTERVAL=n;
  try{localStorage.setItem('ivf-interval',String(n))}catch(e){}
  syncInterval();
  if(FRAMES.length)goto(IDX);
  toast('frame interval set to '+n+' min ('+(60/n).toFixed(1)+' frames/hour)');
}

function syncInterval(){
  const el=document.getElementById('interval');
  if(el)el.value=INTERVAL;
  const vn=document.getElementById('videoN');
  if(vn)vn.value=localStorage.getItem('ivf-videon')||'48';
}

function exportJsonl(){""")

# keep the file in sync after every mutation
rep('sync after save',
    """  refreshCounts();buildQueue();
  toast('✓ saved · '+all.length+' label(s) in this browser');""",
    """  refreshCounts();buildQueue();
  writeToFile();
  toast('✓ saved · '+all.length+' label(s)'
        +(FILE_HANDLE?' · written to your file':' in this browser'));""")

rep('sync after delete',
    """  const all=load();all.splice(i,1);store(all);
  refreshCounts();renderSaved();buildQueue();""",
    """  const all=load();all.splice(i,1);store(all);
  refreshCounts();renderSaved();buildQueue();writeToFile();""")

rep('sync after import',
    """    store(Object.values(byTask));
    refreshCounts();renderSaved();buildQueue();""",
    """    store(Object.values(byTask));
    refreshCounts();renderSaved();buildQueue();writeToFile();""")

# ------------------------------------------------------------ 5. tab aliases ---
rep('tab aliases',
    """  const qs=new URLSearchParams(location.search);
  const want=qs.get('task');""",
    """  const qs=new URLSearchParams(location.search);
  const alias={labels:'saved',label:'saved',annotation:'annot',annotate:'annot'};
  const rawTab=(qs.get('tab')||'').toLowerCase();
  const startTab=alias[rawTab]||rawTab;
  if(['annot','saved','schema'].includes(startTab))tab(startTab);
  syncInterval();renderFileState();
  const want=qs.get('task');""")

# a short note in the Saved tab about persistence
rep('persistence note',
    """    <p class="hint">Data lives in this browser's localStorage. Export before clearing the
      browser cache, or after finishing a session.</p>""",
    """    <p class="hint">Labels are kept in this browser's localStorage automatically.
      Press <b>Save to file</b> in the header to also write them to a real file on your
      computer — that copy survives clearing the browser, and it is the one to keep.
      Frame images are not persisted on this hosted version; use the downloadable
      local package if you need the frames stored too.</p>""")

io.open(P, 'w', encoding='utf-8').write(src)
print(f'applied : {", ".join(applied) if applied else "none"}')
if missed:
    print(f'MISSED  : {", ".join(missed)}')
    sys.exit(1)
print(f'chars   : {orig_len} -> {len(src)}')
