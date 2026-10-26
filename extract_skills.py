import os
import re
from collections import Counter

import truststore
truststore.inject_into_ssl()

import pandas as pd
from dotenv import load_dotenv
from neo4j import GraphDatabase

from skills_taxonomy import SKILLS

load_dotenv()

URI = os.environ["NEO4J_URI"]
AUTH = (os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"])
DATABASE = os.environ["NEO4J_DATABASE"]
BATCH_SIZE = 1000

# One compiled pattern per canonical skill: all its aliases, whole words only
PATTERNS = {
    name: re.compile(r"\b(?:" + "|".join(aliases) + r")\b", re.IGNORECASE)
    for name, (_, aliases) in SKILLS.items()
}

CONSTRAINTS = [
    "CREATE CONSTRAINT skill_name IF NOT EXISTS FOR (s:Skill) REQUIRE s.name IS UNIQUE",
    "CREATE CONSTRAINT category_name IF NOT EXISTS FOR (c:SkillCategory) REQUIRE c.name IS UNIQUE",
]

LOAD_SKILLS = """
UNWIND $skills AS s
MERGE (k:Skill {name: s.name})
SET k.aliases = s.aliases
MERGE (c:SkillCategory {name: s.category})
MERGE (k)-[:IN_CATEGORY]->(c)
"""

LINK_JOBS = """
UNWIND $links AS l
MATCH (j:Job {job_id: l.job_id})
MATCH (k:Skill {name: l.skill})
MERGE (j)-[:MENTIONS]->(k)
"""


def find_skills(text):
    """Return the canonical names of every skill whose aliases appear in the text."""
    return sorted(name for name, pattern in PATTERNS.items() if pattern.search(text))


def main():
    jobs = pd.read_csv("data/processed/data_jobs.csv")
    text = (
        jobs["title"].fillna("") + " " + jobs["description"].fillna("")
        + " " + jobs["skills_desc"].fillna("")
    )

    links, counts, no_skills = [], Counter(), 0
    for job_id, job_text in zip(jobs["job_id"], text):
        found = find_skills(job_text)
        no_skills += not found
        counts.update(found)
        links.extend({"job_id": int(job_id), "skill": s} for s in found)

    skills = [
        {"name": name, "category": category, "aliases": aliases}
        for name, (category, aliases) in SKILLS.items()
    ]

    with GraphDatabase.driver(URI, auth=AUTH) as driver:
        for statement in CONSTRAINTS:
            driver.execute_query(statement, database_=DATABASE)
        driver.execute_query(LOAD_SKILLS, skills=skills, database_=DATABASE)
        for start in range(0, len(links), BATCH_SIZE):
            driver.execute_query(
                LINK_JOBS, links=links[start:start + BATCH_SIZE], database_=DATABASE
            )

    print(f"Skills in taxonomy: {len(skills)}")
    print(f"Job-skill links created: {len(links):,}")
    print(f"Jobs with no skills found: {no_skills}")
    print("\nTop 15 skills:")
    for name, n in counts.most_common(15):
        print(f"  {name}: {n}")


if __name__ == "__main__":
    main()