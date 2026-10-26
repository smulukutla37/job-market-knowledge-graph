import json
import os
import re
import sys

import truststore
truststore.inject_into_ssl()

import anthropic
from dotenv import load_dotenv
from neo4j import GraphDatabase
from neo4j.exceptions import Neo4jError

load_dotenv()

URI = os.environ["NEO4J_URI"]
AUTH = (os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"])
DATABASE = os.environ["NEO4J_DATABASE"]
MODEL = "claude-opus-5-5"
MAX_ROWS = 50

SCHEMA = """Graph schema (Neo4j):
Nodes:
  (:Job {job_id, title, work_type, remote, min_salary, max_salary, pay_period, url})
  (:Company {name})
  (:Location {name})          e.g. "Atlanta, GA", "New York, NY", "United States"
  (:ExperienceLevel {name})   e.g. "Entry level", "Associate", "Mid-Senior level", "Director"
  (:Skill {name, aliases})    e.g. "Python", "Spark", "Power BI", "AWS"
  (:SkillCategory {name})     e.g. "Language", "Cloud", "BI", "Lakehouse"
Relationships:
  (:Job)-[:POSTED_BY]->(:Company)
  (:Job)-[:LOCATED_IN]->(:Location)
  (:Job)-[:HAS_LEVEL]->(:ExperienceLevel)
  (:Job)-[:MENTIONS]->(:Skill)        skill appears in the job text (not necessarily required)
  (:Skill)-[:IN_CATEGORY]->(:SkillCategory)"""

CYPHER_PROMPT = f"""You translate questions about a job-market knowledge graph into one read-only Cypher query.

{SCHEMA}

Rules:
- Use only MATCH, OPTIONAL MATCH, WHERE, WITH, UNWIND, RETURN, ORDER BY and LIMIT.
- Never return Job.description; return titles, names and counts instead.
- End the query with LIMIT 25 or lower.
- Compare names case-insensitively with toLower() when the user's wording may differ from the data.
- For a US state, match locations ending in ", XX" (e.g. ENDS WITH ", TX").
- If the graph cannot answer the question, set cypher to an empty string and explain why in note."""

ANSWER_PROMPT = """You answer questions about a data-engineering job market using only the query results you are given.
Be concise and specific: name the items and include the counts.
If the results are empty, say no matching data was found.
Skills come from text matching (MENTIONS), so call them skills "mentioned" in postings, not "required"."""

CYPHER_FORMAT = {
    "type": "json_schema",
    "schema": {
        "type": "object",
        "properties": {"cypher": {"type": "string"}, "note": {"type": "string"}},
        "required": ["cypher", "note"],
        "additionalProperties": False,
    },
}

# Guardrail 1: reject anything that could change the graph before it reaches Neo4j
WRITE_CLAUSES = re.compile(
    r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|LOAD\s+CSV|CALL|FOREACH)\b", re.IGNORECASE
)

client = anthropic.Anthropic()


def ask_claude(system, user, output_config):
    """One Claude call; falls back automatically if the model declines."""
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config=output_config,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude declined to answer this question.")
    return next(b.text for b in response.content if b.type == "text")


def generate_cypher(question, previous_error=None):
    prompt = f"Question: {question}"
    if previous_error:
        prompt += f"\n\nYour previous query failed with this error, fix it:\n{previous_error}"
    text = ask_claude(
        CYPHER_PROMPT, prompt, {"effort": "medium", "format": CYPHER_FORMAT}
    )
    return json.loads(text)


def run_read_only(driver, cypher):
    if WRITE_CLAUSES.search(cypher):
        raise ValueError("Blocked: the generated query tries to modify the graph.")

    # Guardrail 2: a read transaction, so Neo4j itself rejects any write
    def read(tx):
        return [record.data() for record in tx.run(cypher)][:MAX_ROWS]

    with driver.session(database=DATABASE) as session:
        return session.execute_read(read)


def answer(question, cypher, rows):
    user = (
        f"Question: {question}\n\nCypher used:\n{cypher}\n\n"
        f"Results (JSON):\n{json.dumps(rows, default=str)}"
    )
    return ask_claude(ANSWER_PROMPT, user, {"effort": "low"})


def ask(driver, question):
    plan = generate_cypher(question)
    if not plan["cypher"]:
        return None, [], plan["note"]
    try:
        rows = run_read_only(driver, plan["cypher"])
    except Neo4jError as error:
        # Self-correction: give Claude the database error and try once more
        plan = generate_cypher(question, previous_error=str(error))
        rows = run_read_only(driver, plan["cypher"])
    return plan["cypher"], rows, answer(question, plan["cypher"], rows)


def main():
    question = " ".join(sys.argv[1:]) or input("Ask the job graph: ")
    with GraphDatabase.driver(URI, auth=AUTH) as driver:
        cypher, rows, reply = ask(driver, question)
    if cypher:
        print(f"\nCypher:\n{cypher}\n\nRows returned: {len(rows)}")
    print(f"\nAnswer:\n{reply}")


if __name__ == "__main__":
    main()