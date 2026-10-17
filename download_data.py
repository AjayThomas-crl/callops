"""
CallOps - download both datasets into ./data
Run:  pip install huggingface_hub requests
      python download_data.py
"""
import io
import zipfile
from pathlib import Path

import requests
from huggingface_hub import snapshot_download

DATA = Path("data")
DATA.mkdir(exist_ok=True)

# 1) UCI Bank Marketing (small, ~1 MB) -> data/bank/
print("Downloading UCI Bank Marketing...")
r = requests.get("https://archive.ics.uci.edu/static/public/222/bank+marketing.zip", timeout=120)
r.raise_for_status()
outer = zipfile.ZipFile(io.BytesIO(r.content))
bank_dir = DATA / "bank"
bank_dir.mkdir(exist_ok=True)
# The UCI zip contains nested zips (bank.zip, bank-additional.zip)
for name in outer.namelist():
    if name.endswith(".zip"):
        zipfile.ZipFile(io.BytesIO(outer.read(name))).extractall(bank_dir)
    else:
        outer.extract(name, bank_dir)
print("  ->", sorted(p.name for p in bank_dir.rglob("*.csv")))

# 2) CallCenterEN transcripts (~1.4 GB) -> data/callcenter/
#    Research / non-commercial license: keep it out of your GitHub repo (add data/ to .gitignore).
print("Downloading CallCenterEN transcripts (~1.4 GB, this takes a while)...")
path = snapshot_download(
    repo_id="AIxBlock/92k-real-world-call-center-scripts-english",
    repo_type="dataset",
    local_dir=DATA / "callcenter",
)
print("  ->", path)
print("Done. Add 'data/' to .gitignore.")
