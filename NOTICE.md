# NOTICE — provenance of the bundled media

The frames in `docs/assets/frames/` and the clip in `docs/assets/media/` are
**AI-generated / composite illustrations**. They exist so the labelling tool has
something to show before real recordings are available.

> **They are NOT patient data.**
> **Do not put them into a training set.**
> **Do not use them to evaluate a model.**

Every generated image and video carries the watermark
`AI-generated illustration - not patient data`, applied with `ffmpeg drawtext`
or Pillow — it is not model output.

## What they were generated from

| source | use | licence |
|---|---|---|
| Gomez et al. 2022, *A time-lapse embryo dataset for morphokinetic parameter prediction* — Zenodo [10.5281/zenodo.7912264](https://doi.org/10.5281/zenodo.7912264) | reference morphology | CC BY 4.0 |
| InVitroVision image package — figshare [10.6084/m9.figshare.32024349](https://doi.org/10.6084/m9.figshare.32024349) | reference frames | CC BY-NC-SA 4.0 |
| Ma et al., *Front. Med.* 2022, [10.3389/fmed.2022.934327](https://doi.org/10.3389/fmed.2022.934327) | cytoplasmic-string appearance | CC BY |

The derived illustrations in this repository are therefore distributed under
**CC BY-NC-SA 4.0** (attribution, non-commercial, share-alike). Commercial use of
these images requires separate permission from the original data owners.

## Attribution line

> Illustrative embryo frames generated from reference imagery in the Nantes
> time-lapse dataset (Gomez et al., 2022) and the InVitroVision image package;
> morphological guidance from Ma et al. (2022). AI-generated illustrations, not
> patient data, used under CC BY-NC-SA 4.0.

## If you replace them with real data

Delete `docs/assets/frames/` and `docs/assets/media/`, drop your own frames in,
and re-run `python server/build_assets.py` (edit the source path at the top of
that file). Then update `SAMPLE` in `docs/index.html` — the file names encode the
frame number, e.g. `00440_cs.jpg` is frame 440 with label `cs`.

Real recordings bring consent and de-identification obligations that these
illustrations do not. Check them before publishing a repository that contains
them.
