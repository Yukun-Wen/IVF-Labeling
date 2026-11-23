# The label record, field by field

One line of `annotations.jsonl` = one answered question about one frame or one
clip. Both the web app and the local server write this shape.

## Identity

| field | meaning |
|---|---|
| `id` | unique per record, `<task_id>#v<version>` |
| `task_id` | the queue item answered, e.g. `CLINIC_0001__cs` |
| `video_id` | which recording |
| `kind` | task family: describe / grade / events / cs / cs_span / cleavage / keyframe |

## The question

| field | meaning |
|---|---|
| `qtype` | the question family; drives how the answer is scored |
| `question` | the exact sentence put to the model |
| `answer` | short answer only, no explanation |
| `answer_type` | `closed` / `numeric` / `binary` / `open` |
| `choices` | pipe separated allowed answers; **required** for closed and binary |

A closed question without `choices` cannot be scored: `blastocyst` and
`Blastocyst ` and `the blastocyst stage` would all count as different strings.

## The evidence

| field | meaning |
|---|---|
| `rationale` | why this is the answer, citing what is visible |
| `rationale_basis` | `image` = describes visible features · `label` = merely cites the annotation file |
| `frame_index` | the frame the answer refers to; `null` for clip-level answers |
| `boxes` | `x1,y1,x2,y2` in **original** image pixels, `;` between boxes |
| `box_labels` | one label per box, `|` separated |

`rationale_basis` exists because the two kinds of reason are not equally useful.
"Because `phases.csv` puts `tB` at frame 424" is bookkeeping — it says nothing
about the image. "The blastocoel has cavitated and fills the embryo" is evidence.
Only `image` rationales are worth supervising an explainer on.

Boxes must be in original pixel coordinates. A box drawn on a downscaled
screenshot has to be multiplied back before it is stored, or every downstream
crop is wrong.

## Comparability

| field | meaning |
|---|---|
| `answerable_from` | `image` · `clinical_only` · `outcome_only` |
| `include_pgt` | `FALSE` = no ploidy (non-invasive, group A) · `TRUE` = ploidy known (group B) |
| `source` | `illustration` (bundled AI demo frames) · `own` (your files) |
| `notes` | free text for the next person |
| `saved` | ISO timestamp |

`answerable_from` is the field that keeps a dataset honest:

* `image` — the answer is visible in the frame. This is the only kind that can be
  scored as image QA.
* `clinical_only` — the answer lives in the record (maternal age, AMH). Asking it
  of an image teaches the model to guess.
* `outcome_only` — observed after transfer (implantation, live birth). A model
  may *predict* it, but scoring it as a QA answer rewards clairvoyance.

## Minimal valid record

```json
{
  "id": "CLINIC_0001__cs#v1",
  "task_id": "CLINIC_0001__cs",
  "video_id": "CLINIC_0001",
  "qtype": "cs_present",
  "question": "Is a cytoplasmic string visible in this frame?",
  "answer": "yes",
  "answer_type": "closed",
  "choices": "yes|no",
  "rationale": "A thin filament bridges the ICM and the mural TE inside the box.",
  "rationale_basis": "image",
  "answerable_from": "image",
  "frame_index": 440,
  "boxes": "180,95,240,310",
  "box_labels": "cytoplasmic_string"
}
```

`schema/record.schema.json` is the machine readable counterpart.
