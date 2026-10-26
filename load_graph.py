import os

import truststore
truststore.inject_into_ssl()

import pandas as pd
from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()

URI = os.environ["NEO4J_URI"]
AUTH = (os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"])
DATABASE = os.environ["NEO4J_DATABASE"]
BATCH_SIZE = 200

# Uniqueness constraints: one node per key, plus an index for fast lookups
CONSTRAINTS = [
    "CREATE CONSTRAINT job_id IF NOT EXISTS FOR (j:Job) REQUIRE j.job_id IS UNIQUE",
    "CREATE CONSTRAINT company_name IF NOT EXISTS FOR (c:Company) REQUIRE c.name IS UNIQUE",
    "CREATE CONSTRAINT location_name IF NOT EXISTS FOR (l:Location) REQUIRE l.name IS UNIQUE",
    "CREATE CONSTRAINT level_name IF NOT EXISTS FOR (e:ExperienceLevel) REQUIRE e.name IS UNIQUE",
]

# MERGE = create if missing, reuse if it exists, so the script is safe to re-run
LOAD_JOBS = """
UNWIND $rows AS row
MERGE (j:Job {job_id: row.job_id})
SET j.title = row.title,
    j.work_type = row.work_type,
    j.remote = row.remote,
    j.min_salary = row.min_salary,
    j.max_salary = row.max_salary,
    j.pay_period = row.pay_period,
    j.url = row.url,
    j.skills_desc = row.skills_desc,
    j.description = row.description
FOREACH (_ IN CASE WHEN row.company IS NULL THEN [] ELSE [1] END |
    MERGE (c:Company {name: row.company})
    MERGE (j)-[:POSTED_BY]->(c))
FOREACH (_ IN CASE WHEN row.location IS NULL THEN [] ELSE [1] END |
    MERGE (l:Location {name: row.location})
    MERGE (j)-[:LOCATED_IN]->(l))
FOREACH (_ IN CASE WHEN row.level IS NULL THEN [] ELSE [1] END |
    MERGE (e:ExperienceLevel {name: row.level})
    MERGE (j)-[:HAS_LEVEL]->(e))
"""

COUNTS = """
MATCH (n) RETURN labels(n)[0] AS label, count(*) AS nodes ORDER BY nodes DESC
"""


def to_rows(df):
    """Turn the CSV into a list of dicts, with pandas NaN converted to None (null)."""
    df = df.astype(object).where(pd.notna(df), None)
    return [
        {
            "job_id": int(r["job_id"]),
            "title": r["title"],
            "company": r["company_name"],
            "location": r["location"],
            "level": r["formatted_experience_level"],
            "work_type": r["formatted_work_type"],
            "remote": r["remote_allowed"] == 1,
            "min_salary": r["min_salary"],
            "max_salary": r["max_salary"],
            "pay_period": r["pay_period"],
            "url": r["job_posting_url"],
            "skills_desc": r["skills_desc"],
            "description": r["description"],
        }
        for _, r in df.iterrows()
    ]


def main():
    rows = to_rows(pd.read_csv("data/processed/data_jobs.csv"))
    with GraphDatabase.driver(URI, auth=AUTH) as driver:
        for statement in CONSTRAINTS:
            driver.execute_query(statement, database_=DATABASE)
        for start in range(0, len(rows), BATCH_SIZE):
            batch = rows[start:start + BATCH_SIZE]
            driver.execute_query(LOAD_JOBS, rows=batch, database_=DATABASE)
            print(f"Loaded {start + len(batch)} / {len(rows)} jobs")
        records, _, _ = driver.execute_query(COUNTS, database_=DATABASE)
        print("\nNodes in the graph:")
        for rec in records:
            print(f"  {rec['label']}: {rec['nodes']}")


if __name__ == "__main__":
    main()