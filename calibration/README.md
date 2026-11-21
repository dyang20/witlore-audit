# Calibration reports

The five generated Desktop Audits for the calibration sites, as submitted:
TI-OPP-35820, 34659, 35482, 37954 and 43986, each with the evidence ledger it was
built from.

`outputs/` is ignored by git, so these are copied here to be committed:

```bash
da batch --ids TI-OPP-35820,TI-OPP-34659,TI-OPP-35482,TI-OPP-37954,TI-OPP-43986
python tools/calibrate.py --human-dir human_reports
python tools/collect_calibration.py
```

The comparison against the human audits is `docs/comparison.md`; the account of
the differences is `docs/material-differences.md`.
