from pathlib import Path

import pandas as pd

RAW = Path("data/raw")
OUT = Path("data/processed")
OUT.mkdir(parents=True, exist_ok=True)

DATA_TITLES = (
    r"data engineer|analytics engineer|data architect|business intelligence"
    r"|\bbi\b|data platform|etl developer|data warehouse"
)
KEEP = [
    "job_id", "title", "company_name", "location", "formatted_experience_level",
    "formatted_work_type", "remote_allowed", "min_salary", "max_salary",
    "pay_period", "listed_time", "job_posting_url", "skills_desc", "description",
]

postings = pd.read_csv(RAW / "postings.csv")
print(f"All postings: {len(postings):,}")
print("Columns:", list(postings.columns))

is_data_job = postings["title"].str.contains(DATA_TITLES, case=False, na=False)
jobs = postings.loc[is_data_job, [c for c in KEEP if c in postings.columns]]
jobs.to_csv(OUT / "data_jobs.csv", index=False)

print(f"\nData jobs kept: {len(jobs):,}")
print(f"Distinct companies: {jobs['company_name'].nunique():,}")
print(f"Distinct locations: {jobs['location'].nunique():,}")
print("\nTop titles:")
print(jobs["title"].value_counts().head(10).to_string())