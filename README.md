# IVF-Labeling

A labelling workbench for embryo time-lapse annotation, in two forms:

| version | where it runs | what it gives you |
|---|---|---|
| **Web app** | any browser, no install — <https://Yukun-Wen.github.io/IVF-Labeling/> | annotate, boxes, export JSONL. Data stays in your browser. |
| **Local server** | `python server/label_tool.py` | the same, plus a shared work queue, second-person review, agreement score and an append-only audit log. |

Both write the same record schema, so labels from either one can be merged.

---

## Quick start (web)

1. Open <https://Yukun-Wen.github.io/IVF-Labeling/>
2. Press **Sample** to load the demo clip, or **Load my frames / Load my video**
   to bring your own material (nothing is uploaded — the browser reads the file).
3. Pick a task on the left, step to the frame, drag a box if the task needs one,
   fill the answer and the rationale, then **Save this label**.
4. **Saved → Download JSONL** when you are done.

Labels live in `localStorage`. They survive a refresh, not a cleared browser
profile, so export at the end of a session.

## Quick start (local server)

```bash
python server/label_tool.py            # opens http://127.0.0.1:8765
python server/label_tool.py --help     # ports, --prelabel, --rebuild-tasks
```

The server version adds what a single annotator does not need:

* a **queue** (`annotation/tasks.json`) — ~7 tasks per clip, claimable, skippable
* **drafts** autosaved every edit, so a crash loses nothing
* **review** — a second person approves or sends back with a comment
* **agreement** — computed across double-annotated items
* **audit** — `annotation/audit.jsonl`, append-only: who, when, what
* `--prelabel` fills answers from `labels/*.csv`; **off by default**, because the
  first pass here is human annotation from scratch

---

## What is in this repository

```
docs/                 the static web app (GitHub Pages root)
  index.html          single file, no dependencies
  assets/frames/      demo frames (AI illustrations — see NOTICE.md)
  assets/media/       demo clip
server/
  label_tool.py       the full workbench, standard library only
  build_assets.py     rebuilds docs/assets from a source frame folder
schema/
  record.schema.json  the annotation record, machine readable
  label_record.md     the same, explained
```

## The record

One line of JSONL per label. `schema/record.schema.json` is the authoritative
version; in short:

```json
{
  "id": "CLINIC_0001__cs#v1",
  "task_id": "CLINIC_0001__cs",
  "video_id": "CLINIC_0001",
  "kind": "cs",
  "qtype": "cs_present",
  "question": "Is a cytoplasmic string visible in this frame?",
  "answer": "yes",
  "answer_type": "closed",
  "choices": "yes|no",
  "rationale": "A thin filament bridges the ICM and the mural TE inside the box.",
  "rationale_basis": "image",
  "answerable_from": "image",
  "include_pgt": "FALSE",
  "frame_index": 440,
  "boxes": "180,95,240,310",
  "box_labels": "cytoplasmic_string",
  "notes": "",
  "source": "illustration",
  "saved": "2025-03-10T14:02:11"
}
```

Three fields carry most of the design intent:

* **`answerable_from`** — `image` (visible in the frame), `clinical_only` (needs
  the record), `outcome_only` (a follow-up fact). Scoring an `outcome_only` item
  as image QA teaches the model to guess a prognosis.
* **`rationale_basis`** — `image` means the reason describes visible features;
  `label` means it merely cites the annotation file. Only `image` rationales are
  evidence.
* **`include_pgt`** — `FALSE` is the non-invasive setting (no ploidy), `TRUE` adds
  PGT-A. Keep them as two groups; feeding ploidy in changes the task.

## The frame interval matters

Every "hour" in this tool is `frame / (60 / acq_interval_min)`. With 15-minute
frames that is 4 frames per hour. If the interval is wrong every derived time is
wrong, so it is a required field, not a default to trust.

## Privacy

The web app never uploads anything: files are read with the File API and labels
live in `localStorage`. The local server binds to `127.0.0.1` only. Neither sends
data anywhere.

## Licence

Code: MIT. The demo frames and clip are AI-generated illustrations under
CC BY-NC-SA 4.0 — see [NOTICE.md](NOTICE.md). They are not patient data and are
not for training.
