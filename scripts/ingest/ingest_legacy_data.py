from pathlib import Path
import pandas as pd
from sqlalchemy import text

from src.db import engine

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CSV_PATH = PROJECT_ROOT / "data" / "raw" / "logistic.csv"
TABLE_NAME = "TBL_SC_FLEET_HIST_RAW"


def main():
    if not CSV_PATH.exists():
        raise FileNotFoundError(f"CSV not found: {CSV_PATH}")

    df = pd.read_csv(CSV_PATH, low_memory=False)
    # low_memory=False tells Pandas not to use its low-memory, chunk-based type-inference approach. It can help avoid mixed-type warnings, though it may use more memory.

    # Remove empty columns sometimes created by spreadsheet exports.
    df = df.loc[
        :, ~df.columns.astype(str).str.match(r"^Unnamed:")
    ]

    df.columns = df.columns.str.strip()


    if df.empty:
        raise ValueError("The CSV contains no data rows.")


    # df.columns.duplicated() identifies repeated column names.
    # .any() checks whether at least one duplicate exists.
    if df.columns.duplicated().any():
        raise ValueError("The CSV contains duplicate column names.")

    print("CSV:", CSV_PATH)
    print("Rows:", len(df))
    print("Columns:", len(df.columns))
    print("\nColumn names:")
    print(df.columns.tolist())
    print("\nData types:")
    print(df.dtypes)
    print("\nFirst 5 rows:")
    print(df.head().to_string())

    # Convert timestamp only if every non-null value parses.
    if "timestamp" in df.columns:
        parsed = pd.to_datetime(df["timestamp"], errors="coerce")
        non_null = df["timestamp"].notna()

        if non_null.any() and parsed[non_null].notna().all():
            df["timestamp"] = parsed

    # Initial import: creates the table using inferred column types.
    # WARNING: replace drops and recreates an existing table.
    df.to_sql(
        TABLE_NAME,
        con=engine,
        if_exists="replace",
        index=False,
        chunksize=2000,
        method="multi",
    )

    with engine.connect() as conn:
        count = conn.execute(
            text(f"SELECT COUNT(*) FROM `{TABLE_NAME}`")
        ).scalar_one()

    print(f"\nSuccessfully imported {count} rows.")
    print(f"Table: legacy_logistics.{TABLE_NAME}")


if __name__ == "__main__":
    main()
