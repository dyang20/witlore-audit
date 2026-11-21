import zipfile, csv, io
z = zipfile.ZipFile(r"data\asr\r_tower.zip")
print([n for n in z.namelist()][:10])
for rec in ("CO", "RA", "EN"):
    m = [n for n in z.namelist() if n.upper().endswith(rec + ".DAT")][0]
    with z.open(m) as fh:
        r = csv.reader(io.TextIOWrapper(fh, "latin-1"), delimiter="|")
        print("\n---", rec, "---")
        for i, row in enumerate(r):
            print([f"{j}:{v}" for j, v in enumerate(row)][:26])
            if i >= 1: break