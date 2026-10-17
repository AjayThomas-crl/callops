"""
Load UCI Bank Marketing (bank-additional-full.csv) into Postgres table bank_contacts.

Run:  python load_bank.py
Re-runnable: drops and recreates the table each time.
"""
from pathlib import Path

import pandas as pd

from db import connect

CSV = Path("data/bank/bank-additional/bank-additional-full.csv")
SCHEMA = Path("sql/01_bank_schema.sql")
MONTHS = {m: i for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), start=1)}
FIRST_YEAR = 2008  # per bank-additional-names.txt: "ordered by date (from May 2008 to November 2010)"


def infer_year(month_num: pd.Series) -> pd.Series:
    """The CSV has no year column but is in date order, so every time the month goes
    backwards (e.g. dec -> mar) a new year has started."""
    new_year = month_num.diff() < 0
    return FIRST_YEAR + new_year.cumsum()


def transform(raw: pd.DataFrame) -> pd.DataFrame:
    month_num = raw["month"].map(MONTHS)
    year = infer_year(month_num)
    # Sanity checks: fail loudly rather than load something subtly wrong.
    assert month_num.notna().all(), "unexpected month label"
    assert year.max() == 2010 and (year.diff().fillna(0) >= 0).all(), "year inference broke"

    return pd.DataFrame({
        "row_id": range(1, len(raw) + 1),
        "age": raw["age"],
        "job": raw["job"],
        "marital": raw["marital"],
        "education": raw["education"],
        "has_credit_default": raw["default"],
        "has_housing_loan": raw["housing"],
        "has_personal_loan": raw["loan"],
        "contact_channel": raw["contact"],
        "contact_year": year,
        "contact_month": month_num,
        "day_of_week": raw["day_of_week"],
        "last_call_duration_s": raw["duration"],
        "campaign_contacts": raw["campaign"],
        # 999 is a sentinel for "never contacted", not a real number of days
        "days_since_prev_campaign": raw["pdays"].where(raw["pdays"] != 999).astype("Int16"),
        "was_previously_contacted": raw["pdays"] != 999,
        "previous_contacts": raw["previous"],
        "prev_outcome": raw["poutcome"],
        "emp_var_rate": raw["emp.var.rate"],
        "cons_price_idx": raw["cons.price.idx"],
        "cons_conf_idx": raw["cons.conf.idx"],
        "euribor3m": raw["euribor3m"],
        "nr_employed": raw["nr.employed"],
        "converted": raw["y"].map({"yes": True, "no": False}),
    })


def main() -> None:
    raw = pd.read_csv(CSV, sep=";")
    df = transform(raw)
    print(f"read {len(raw):,} rows from {CSV}")

    cols = list(df.columns)
    with connect() as conn, conn.cursor() as cur:
        cur.execute(SCHEMA.read_text())
        # COPY streams rows in one round trip: much faster than row-by-row INSERTs.
        with cur.copy(f"COPY bank_contacts ({', '.join(cols)}) FROM STDIN") as copy:
            for row in df.itertuples(index=False):
                copy.write_row([None if pd.isna(v) else v for v in row])
        cur.execute("SELECT count(*), sum(converted::int), min(contact_year), max(contact_year) FROM bank_contacts")
        n, conv, y0, y1 = cur.fetchone()
    print(f"loaded {n:,} rows into bank_contacts | converted={conv:,} ({conv / n:.1%}) | years {y0}-{y1}")


if __name__ == "__main__":
    main()
