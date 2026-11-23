# -*- coding: utf-8 -*-
"""Turn the filled annotation table into training JSONL.

The annotator fills a flat spreadsheet (annotation_template.xlsx or .csv).  This
script is the only place that knows the nested training schema, so the format
lives in one file instead of in every annotator's head.

What it fills in automatically (annotators leave these blank):
    video_path, frames_per_hour, acq_interval_min, insemination_iso  <- manifest.csv
    context_text                                                    <- clinical.csv + genetics.csv
    split                                                           <- split.csv
    output_images                                                   <- rendered from boxes

    python annotate_to_jsonl.py                       # xlsx -> jsonl
    python annotate_to_jsonl.py --in annotation/x.csv --out x.jsonl
    python annotate_to_jsonl.py --strict               # fail on the first bad row
"""
import argparse
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def read_table(path):
    """Read .xlsx (first sheet) or .csv into a list of dicts."""
    if path.suffix.lower() == '.xlsx':
        from openpyxl import load_workbook
        wb = load_workbook(path, data_only=True)
        ws = wb['annotations'] if 'annotations' in wb.sheetnames else wb.active
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return []
        head = [str(h).strip() if h is not None else '' for h in rows[0]]
        out = []
        for r in rows[2:]:                        # row 1 header, row 2 help text
            if all(v is None or str(v).strip() == '' for v in r):
                continue
            out.append({head[i]: ('' if r[i] is None else str(r[i]).strip())
                        for i in range(min(len(head), len(r)))})
        return out
    with open(path, encoding='utf-8-sig', newline='') as fh:
        return [r for r in csv.DictReader(fh)
                if any((v or '').strip() for v in r.values())]


def read_csv(rel):
    p = ROOT / rel
    if not p.exists():
        return []
    with open(p, encoding='utf-8-sig', newline='') as fh:
        return [r for r in csv.DictReader(fh)
                if any((v or '').strip() for v in r.values())]


def parse_boxes(s):
    """'180,95,240,310; 300,120,340,220' -> [[180,95,240,310], [300,120,340,220]]"""
    out = []
    for chunk in (s or '').split(';'):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = [p.strip() for p in chunk.split(',')]
        if len(parts) != 4:
            raise ValueError(f'box needs 4 numbers, got {chunk!r}')
        out.append([int(round(float(p))) for p in parts])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--in', dest='inp',
                    default=str(ROOT / 'annotation' / 'annotation_template.xlsx'))
    ap.add_argument('--out', default=str(ROOT / 'samples_annotated.jsonl'))
    ap.add_argument('--strict', action='store_true',
                    help='stop at the first row that cannot be converted')
    args = ap.parse_args()

    src = Path(args.inp)
    if not src.exists():
        raise SystemExit(f'no such file: {src}')
    table = read_table(src)

    man = {r['video_id'].strip(): r for r in read_csv('manifest.csv')}
    split_of = {r['patient_id'].strip(): r['split'].strip() for r in read_csv('split.csv')}
    clinical = {r['cycle_id'].strip(): r for r in read_csv('clinical.csv')}
    genetics = {r['video_id'].strip(): r for r in read_csv('genetics.csv')}
    info = {r['key'].strip(): r['value'].strip() for r in read_csv('dataset_info.csv')}
    try:
        fph = 60.0 / float(info.get('acq_interval_min', '15'))
    except ValueError:
        fph = 4.0

    def context_text(vid, include_pgt):
        m = man.get(vid, {})
        c = clinical.get(m.get('cycle_id', '').strip(), {})
        g = genetics.get(vid, {})
        bits = [f"Day-{c.get('transfer_day', '5')} transfer.",
                f"Patient {c.get('age_years', '?')} y, "
                f"AMH {c.get('amh_ng_ml', '?')} ng/mL, "
                f"endometrium {c.get('endometrium_mm', '?')} mm, "
                f"{c.get('coc_retrieved', '?')} COCs retrieved, "
                f"{c.get('mii_oocytes', '?')} MII, "
                f"fertilisation {c.get('fertilisation', 'IVF')}."]
        if include_pgt and g.get('pgt_a_result'):
            bits.append(f"PGT-A {g['pgt_a_result']}.")
        else:
            bits.append("PGT-A not available.")
        return ' '.join(bits)

    out, problems = [], []
    for i, r in enumerate(table, start=3):
        rid = (r.get('id') or '').strip()
        vid = (r.get('video_id') or '').strip()
        try:
            if not rid or not vid:
                raise ValueError('id and video_id are required')
            if vid not in man:
                raise ValueError(f'video_id {vid} is not in manifest.csv')

            m = man[vid]
            inc_pgt = (r.get('include_pgt') or 'FALSE').strip().upper() == 'TRUE'
            boxes = parse_boxes(r.get('boxes'))
            box_labels = [b.strip() for b in (r.get('box_labels') or '').split('|') if b.strip()]

            frames_raw = (r.get('frames') or '').strip()
            fi = (r.get('frame_index') or '').strip()
            if fi:
                frames = [int(float(fi))]
            elif frames_raw.lower() == 'all':
                frames = 'all'
            elif frames_raw:
                frames = [int(float(x)) for x in frames_raw.split(',') if x.strip()]
            else:
                frames = []

            gran = (r.get('granularity') or 'frame').strip()
            task = (r.get('task') or 'vqa.frame').strip()
            at = (r.get('answer_type') or 'closed').strip()
            choices = [c.strip() for c in (r.get('choices') or '').split('|') if c.strip()] or None

            evidence = {
                'frame': int(float(fi)) if fi else None,
                'boxes': boxes,
                'box_labels': box_labels,
            }
            if not boxes:
                evidence['boxes'] = []
            out_img = []
            if boxes and fi:
                out_img.append({
                    'path': f'images/{rid}.png',
                    'caption': (r.get('question') or '')[:80],
                    'render': {'frame': int(float(fi)), 'boxes': boxes,
                               'label': box_labels[0] if box_labels else 'region'},
                })

            rec = {
                'id': rid,
                'task': task,
                'split': split_of.get(m.get('patient_id', '').strip(), ''),
                'input': {
                    'video_id': vid,
                    'video_path': m.get('frames_dir', ''),
                    'frames': frames,
                    'time_window': None,
                    'context_text': context_text(vid, inc_pgt),
                    'question': (r.get('question') or '').strip(),
                },
                'output': {
                    'answer': (r.get('answer') or '').strip(),
                    'answer_type': at,
                    'choices': choices,
                    'rationale': (r.get('rationale') or '').strip(),
                    'rationale_basis': (r.get('rationale_basis') or 'image').strip(),
                    'evidence': evidence,
                    'output_images': out_img,
                },
                'meta': {
                    'granularity': gran,
                    'qtype': (r.get('qtype') or '').strip(),
                    'answerable_from': (r.get('answerable_from') or 'image').strip(),
                    'include_pgt': inc_pgt,
                    'frames_per_hour': fph,
                    'annotator': (r.get('annotator') or '').strip(),
                    'notes': (r.get('notes') or '').strip(),
                },
            }
            if (r.get('unit') or '').strip():
                rec['output']['unit'] = r['unit'].strip()

            # a couple of checks that catch the mistakes that actually happen
            if at in ('closed', 'binary') and not choices:
                raise ValueError('closed/binary answer needs a choices list')
            if choices and rec['output']['answer'] not in choices:
                raise ValueError(f"answer {rec['output']['answer']!r} not in choices {choices}")
            if at == 'numeric' and rec['output']['answer']:
                float(rec['output']['answer'])
            if boxes and not fi:
                raise ValueError('boxes were given but frame_index is empty')
            out.append(rec)
        except Exception as e:
            msg = f'row {i} ({rid or "no id"}): {e}'
            problems.append(msg)
            if args.strict:
                raise SystemExit(msg)

    dst = Path(args.out)
    with open(dst, 'w', encoding='utf-8') as fh:
        for rec in out:
            fh.write(json.dumps(rec, ensure_ascii=False) + '\n')

    print(f'converted {len(out)} row(s) -> {dst}')
    if problems:
        print(f'\n{len(problems)} row(s) skipped:')
        for p in problems:
            print('  !', p)
    if out:
        from collections import Counter
        c = Counter(r['task'] for r in out)
        print('\nby task: ' + ', '.join(f'{k}={v}' for k, v in sorted(c.items())))
        n_box = sum(1 for r in out if r['output']['evidence']['boxes'])
        print(f'with boxes: {n_box}   with output image: '
              f'{sum(1 for r in out if r["output"]["output_images"])}')


if __name__ == '__main__':
    main()
