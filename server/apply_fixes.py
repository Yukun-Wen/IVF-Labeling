# -*- coding: utf-8 -*-
"""Apply the bug fixes to docs/index.html.

Kept as a script because the replacements are long and quoting them through a
shell mangles the quotes.  Run from anywhere:

    python apply_fixes.py
"""
import io
import sys
from pathlib import Path

P = Path(__file__).resolve().parents[1] / 'docs' / 'index.html'
src = io.open(P, encoding='utf-8').read()
orig = src
applied, missed = [], []


def rep(name, old, new):
    global src
    if old not in src:
        missed.append(name)
        return
    src = src.replace(old, new, 1)
    applied.append(name)


# ---------------------------------------------------------------------------
# 1. seeking: 'seeked' does not fire when the requested time equals the current
#    time, so the first extraction (t=0) hung forever and "Load my video" did
#    nothing.  Add a promise that always settles.
# ---------------------------------------------------------------------------
rep('seek helper', """/* ------------------------------------------------------------------ viewer -- */""",
    """/* ------------------------------------------------------------------ viewer -- */
/* 'seeked' never fires when the target time equals the current time, so a bare
   await on it hangs (that broke the first frame of video extraction). */
function seekTo(v,t){
  return new Promise(resolve=>{
    if(Math.abs((v.currentTime||0)-t)<0.02){ resolve(); return }
    let done=false;
    const fire=()=>{ if(done)return; done=true; v.removeEventListener('seeked',fire); resolve() };
    v.addEventListener('seeked',fire);
    try{ v.currentTime=t }catch(e){}
    setTimeout(fire,1500);            // a stubborn seek must not hang the loop
  });
}""")

rep('video extraction await', """  for(let i=0;i<N;i++){
    v.currentTime=dur*i/(N-1);
    await new Promise(r=>{v.onseeked=r});
    cx.drawImage(v,0,0);""",
    """  for(let i=0;i<N;i++){
    await seekTo(v,dur*i/(N-1));
    cx.drawImage(v,0,0);""")

rep('video decode guard', """  await new Promise(r=>{v.onloadedmetadata=r});
  const N=24, dur=v.duration;""",
    """  await new Promise(r=>{v.onloadedmetadata=r});
  if(!v.videoWidth){ toast('this browser could not decode that video'); return }
  const N=24, dur=v.duration;""")

# ---------------------------------------------------------------------------
# 2. showStill raced: a slow earlier image could land after a faster later one
#    and overwrite the canvas, so the picture and the frame number disagreed.
# ---------------------------------------------------------------------------
rep('image load token', """function showStill(src,frameNo,extra){
  CUR={src:src,frame_no:frameNo};
  img=new Image();
  img.onload=()=>{
    scale=img.width>520?Math.min(1,520/img.width):1;
    $('cv').width=img.width;$('cv').height=img.height;
    redraw();
  };
  img.onerror=()=>drawEmpty('image failed to load');
  img.src=src;""",
    """let stillToken=0;
function showStill(src,frameNo,extra){
  const tok=++stillToken;            // ignore images that finish out of order
  CUR={src:src,frame_no:frameNo};
  const im=new Image();
  im.onload=()=>{
    if(tok!==stillToken) return;
    img=im;
    scale=im.width>520?Math.min(1,520/im.width):1;
    $('cv').width=im.width;$('cv').height=im.height;
    redraw();
  };
  im.onerror=()=>{ if(tok===stillToken) drawEmpty('image failed to load') };
  im.src=src;""")

# ---------------------------------------------------------------------------
# 3. the "#vN" suffix was a global counter, not a per-task edit count, and the
#    schema promised versions while save() replaced the previous one.
# ---------------------------------------------------------------------------
rep('per-task version', """function buildRecord(){
  const f=FRAMES[IDX]||{};
  const n=savedCount()+1;
  const taskId=TASK?TASK.id:('UNSET__task');""",
    """function nextVersion(taskId){
  const prev=load().filter(x=>x.task_id===taskId);
  const max=prev.reduce((m,x)=>{
    const v=parseInt(String(x.id||'').split('#v')[1]||'0',10);
    return Number.isFinite(v)?Math.max(m,v):m;
  },0);
  return max+1;
}

function buildRecord(){
  const f=FRAMES[IDX]||{};
  const taskId=TASK?TASK.id:('UNSET__task');
  const n=nextVersion(taskId);""")

# ---------------------------------------------------------------------------
# 4. uploaded stills are not on a 15-minute grid, so printing "hours" invented a
#    timeline.  Only the sample clip has a known frame interval.
# ---------------------------------------------------------------------------
rep('hours only for sample clip', """  $('jump').value=frameNo;
  const h=(frameNo/FPH).toFixed(1);
  $('pos').innerHTML=`<span class="pill">frame ${frameNo} · ${h} h${extra||''}</span>`+badges();""",
    """  $('jump').value=frameNo;
  /* only the sample clip has a known acquisition interval; do not invent hours
     for arbitrary uploaded frames */
  const timeTxt=MED.kind==='illustration'?' · '+(frameNo/FPH).toFixed(1)+' h':'';
  $('pos').innerHTML=`<span class="pill">frame ${frameNo}${timeTxt}${extra||''}</span>`+badges();""")

# ---------------------------------------------------------------------------
# 5. import dropped rows that had no task_id without saying so
# ---------------------------------------------------------------------------
rep('import reports skipped', """    const byTask={};
    load().forEach(r=>byTask[r.task_id]=r);
    rows.forEach(r=>{if(r.task_id)byTask[r.task_id]=r});
    store(Object.values(byTask));
    refreshCounts();renderSaved();buildQueue();
    toast('imported '+rows.length+' record(s)');""",
    """    const byTask={};
    load().forEach(r=>byTask[r.task_id]=r);
    let kept=0, skipped=0;
    rows.forEach(r=>{ if(r&&r.task_id){ byTask[r.task_id]=r; kept++ } else skipped++ });
    store(Object.values(byTask));
    refreshCounts();renderSaved();buildQueue();
    toast('imported '+kept+' record(s)'+(skipped?', skipped '+skipped+' without task_id':''));""")

P.write_text(src, encoding='utf-8')
print(f'applied : {", ".join(applied) if applied else "none"}')
if missed:
    print(f'MISSED  : {", ".join(missed)}')
    sys.exit(1)
print(f'changed : {len(orig)} -> {len(src)} chars')
