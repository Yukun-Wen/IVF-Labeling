# -*- coding: utf-8 -*-
"""Build the annotation workbook that humans actually fill in.

Annotators should never type JSON.  They get one flat table with dropdowns; the
converter (annotate_to_jsonl.py) turns it into the nested training records.

    python make_annotation_template.py

Emits
    annotation/annotation_template.xlsx   dropdowns, 4 worked rows, a lists sheet
    annotation/annotation_template.csv    same columns, for people who prefer csv

Columns marked (auto) are filled by the converter from manifest/clinical/
genetics -- the annotator leaves them blank and never types the same value twice.
"""
import csv
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

ROOT = Path(__file__).resolve().parent
OUTDIR = ROOT / 'annotation'
OUTDIR.mkdir(exist_ok=True)
XLSX = OUTDIR / 'annotation_template.xlsx'
CSVP = OUTDIR / 'annotation_template.csv'

# ------------------------------------------------------------------ schema ---
# (column, dropdown values or None, help text)
COLS = [
    ('id',            None, 'unique id; e.g. CLINIC_0001_vqa_0007'),
    ('video_id',      None, 'must exist in manifest.csv'),
    ('task',          ['vqa.frame', 'vqa.clip', 'predict.outcome', 'agent.trajectory'],
     'what kind of question this row is'),
    ('granularity',   ['frame', 'clip', 'cycle'], 'what one row is about'),
    ('frame_index',   None, 'the frame asked about; blank for clip/cycle tasks'),
    ('frames',        None, 'for clip tasks: "all" or comma separated frames'),
    ('qtype',         ['stage', 'timing', 'blastocyst', 'cell_count', 'cs_present',
                       'cs_count', 'cs_interval', 'expansion', 'icm', 'te',
                       'abnormal_cleavage', 'fragmentation', 'zona', 'keyframe',
                       'description', 'live_birth'],
     'question family; drives scoring'),
    ('question',      None, 'the question, in English'),
    ('answer',        None, 'short answer only'),
    ('answer_type',   ['closed', 'numeric', 'binary', 'open'], 'how the answer is scored'),
    ('choices',       None, 'closed/binary only, pipe separated: yes|no  or  0|1'),
    ('unit',          ['hours', 'cells', 'percent', 'grade', ''],
     'unit for numeric answers'),
    ('rationale',     None, 'WHY this is the answer, citing what is visible'),
    ('rationale_basis', ['image', 'label'],
     'image = describes visible features; label = merely cites the annotation file'),
    ('evidence_type', ['box', 'whole_frame', 'none'], 'how the evidence is shown'),
    ('boxes',         None, 'x1,y1,x2,y2 -- separate several with ;'),
    ('box_labels',    None, 'one label per box, pipe separated'),
    ('output_images', None, '(auto) rendered from boxes; leave blank'),
    ('answerable_from', ['image', 'clinical_only', 'outcome_only'],
     'what can legitimately answer this'),
    ('include_pgt',   ['TRUE', 'FALSE'], 'FALSE = group A (no ploidy), TRUE = group B'),
    ('annotator',     None, 'who wrote this row'),
    ('notes',         None, 'anything the next person should know'),
]

SAMPLE_ROWS = [
    # 1 frame-level, with a box
    ['CLINIC_0001_vqa_0001', 'CLINIC_0001', 'vqa.frame', 'frame', 440, '',
     'cs_present', 'Is a cytoplasmic string visible in this frame?', 'yes',
     'closed', 'yes|no', '', 
     'A thin filament spans the blastocoel from the inner cell mass toward the mural '
     'trophectoderm, inside the marked box.',
     'image', 'box', '180,95,240,310', 'cytoplasmic_string', '',
     'image', 'FALSE', 'AN1', 'clearest example in the clip'],

    # 2 clip-level timing, no box
    ['CLINIC_0001_vqa_0002', 'CLINIC_0001', 'vqa.clip', 'clip', '', 'all',
     'timing', 'At what hour did the embryo reach the 4-cell stage?', '38.0',
     'numeric', '', 'hours',
     'Three cells up to frame 151 and four distinct blastomeres from frame 152; at four '
     'frames per hour that is 38.0 h after insemination.',
     'image', 'none', '', '', '',
     'image', 'FALSE', 'AN1', ''],

    # 3 outcome prediction -- answer is the observed fact, not a prediction
    ['CLINIC_0001_pred_0001', 'CLINIC_0001', 'predict.outcome', 'cycle', '', '',
     'live_birth', 'What is the probability of live birth for this transfer?', '1',
     'binary', '0|1', '',
     'The stored label is the outcome observed after transfer; it is not visible in the '
     'recording and the model is scored on AUC, not on exact match.',
     'label', 'none', '', '', '',
     'outcome_only', 'TRUE', 'AN1', 'group B; also produce a group A copy'],

    # 4 agent trajectory -- the composed question
    ['CLINIC_0001_agent_0001', 'CLINIC_0001', 'agent.trajectory', 'clip', 440, 'all',
     'cs_interval', 'Find the frames where a cytoplasmic string appears, draw where it is, '
     'explain why they matter, and list comparable frames.', '440-441',
     'open', '', '',
     'Strings are visible only between frames 440 and 441, after tB (frame 424), i.e. '
     'during expansion when the inner cell mass and trophectoderm are separated by the '
     'blastocoel. A frame outside that window cannot answer yes.',
     'image', 'box', '180,95,240,310', 'cytoplasmic_string', '',
     'image', 'FALSE', 'AN1', 'needs tool definitions in tools.json'],
]

LIST_ROWS = [
    ('qtype', sorted({c[1][0] for c in [] }) or []),
]


def main():
    wb = Workbook()
    ws = wb.active
    ws.title = 'annotations'

    head_fill = PatternFill('solid', fgColor='1F4E79')
    auto_fill = PatternFill('solid', fgColor='EDE7F6')

    for c, (name, _vals, help_txt) in enumerate(COLS, start=1):
        cell = ws.cell(row=1, column=c, value=name)
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = head_fill
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        ws.column_dimensions[cell.column_letter].width = max(12, min(28, len(name) + 8))
        ws.cell(row=2, column=c, value=help_txt).font = Font(italic=True, size=9, color='6B8094')
        if name == 'output_images':
            for r in (1, 2):
                ws.cell(row=r, column=c).fill = auto_fill

    for r, row in enumerate(SAMPLE_ROWS, start=3):
        for c, val in enumerate(row, start=1):
            cell = ws.cell(row=r, column=c, value=val)
            cell.alignment = Alignment(vertical='top', wrap_text=(c in (8, 13)))

    # dropdowns, applied from row 3 down to 2000
    n_rows = 2000
    for c, (name, vals, _h) in enumerate(COLS, start=1):
        if not vals:
            continue
        letter = ws.cell(row=1, column=c).column_letter
        dv = DataValidation(type='list', formula1='"' + ','.join(vals) + '"',
                            allow_blank=True, showDropDown=False)
        dv.error = 'pick one of: ' + ', '.join(vals)
        dv.errorTitle = 'value not allowed'
        ws.add_data_validation(dv)
        dv.add(f'{letter}3:{letter}{n_rows}')

    ws.freeze_panes = 'A3'

    # help sheet: what each dropdown value means
    ws2 = wb.create_sheet('values')
    ws2.append(['column', 'allowed values', 'meaning'])
    for name, vals, help_txt in COLS:
        ws2.append([name, ', '.join(vals) if vals else '(free text)', help_txt])
    for cell in ws2[1]:
        cell.font = Font(bold=True)
        cell.fill = head_fill
        cell.font = Font(bold=True, color='FFFFFF')
    ws2.column_dimensions['A'].width = 20
    ws2.column_dimensions['B'].width = 60
    ws2.column_dimensions['C'].width = 60
    for row in ws2.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical='top', wrap_text=True)

    # rules sheet
    ws3 = wb.create_sheet('rules')
    for line in [
        ['标注规则'],
        [''],
        ['1', '不要手写 JSON。只填这张表，跑 annotate_to_jsonl.py 生成训练文件。'],
        ['2', 'rationale_basis 必须诚实：写“因为标注文件说 tB 在 424 帧”就选 label，'
              '写“囊胚腔已充满”才选 image。'],
        ['3', 'boxes 用 x1,y1,x2,y2，基于**原图分辨率**，多个框用分号隔开。'],
        ['4', 'answerable_from=outcome_only 的行不能按对错评分，只按 AUC。'],
        ['5', 'include_pgt：FALSE = A 组（对应提案口径），TRUE = B 组（真实临床）。'],
        ['6', '一个病人只能落在一个 split 里。填完跑 check_dataset.py。'],
        ['7', '怀疑某帧答不了，就不要编——写进 notes，转给第二个人复核。'],
    ]:
        ws3.append(line if len(line) == 1 else line)
    ws3.column_dimensions['A'].width = 6
    ws3.column_dimensions['B'].width = 110
    for row in ws3.iter_rows():
        for cell in row:
            cell.alignment = Alignment(vertical='top', wrap_text=True)

    wb.save(XLSX)
    print(f'wrote {XLSX.relative_to(ROOT)}')

    with open(CSVP, 'w', encoding='utf-8-sig', newline='') as fh:
        w = csv.writer(fh)
        w.writerow([c[0] for c in COLS])
        for row in SAMPLE_ROWS:
            w.writerow(row)
    print(f'wrote {CSVP.relative_to(ROOT)}')
    print(f'\n{len(COLS)} columns, {len(SAMPLE_ROWS)} worked rows, '
          f'{sum(1 for c in COLS if c[1])} dropdown columns')


if __name__ == '__main__':
    main()
