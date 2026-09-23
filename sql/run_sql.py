"""cleaned_data.csv를 SQLite에 적재하고 zone_summary.sql을 실행한다. (프로젝트 루트에서 실행)"""
import sqlite3
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
df = pd.read_csv(ROOT / "data" / "cleaned_data.csv", encoding="utf-8-sig")
df["is_top10"] = df["is_top10"].astype(str).eq("True").astype(int)
df["is_fire"] = df["is_fire"].astype(str).eq("True").astype(int)
con = sqlite3.connect(":memory:")
df.to_sql("places", con, index=False)
for i, stmt in enumerate(s for s in (ROOT / "sql" / "zone_summary.sql").read_text(encoding="utf-8").split(";") if "SELECT" in s.upper()):
    print(f"\n[Query {i + 1}]")
    print(pd.read_sql(stmt, con).to_string(index=False))
