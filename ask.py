import html
import json
import os
import re
import sys
import webbrowser
from pathlib import Path
from string import Template

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

GRAPH_PROMPT = f"""You translate questions about a job-market knowledge graph into one read-only Cypher query
whose result will be DRAWN as a picture of nodes and relationships.

{SCHEMA}

Rules:
- Use only MATCH, OPTIONAL MATCH, WHERE, WITH, UNWIND, RETURN, ORDER BY and LIMIT.
- Return whole paths, e.g. MATCH p = (j:Job)-[:MENTIONS]->(s:Skill) ... RETURN p, so both nodes and
  relationships are included. Never return only counts, strings or numbers.
- End the query with LIMIT 50 or lower.
- Compare names case-insensitively with toLower() when the user's wording may differ from the data.
- For a US state, match locations ending in ", XX" (e.g. ENDS WITH ", TX").
- If the graph cannot answer the question, set cypher to an empty string and explain why in note."""

# Colors for the HTML graph, one per node label
LABEL_COLORS = {
    "Job": "#4C8EDA",
    "Company": "#F79767",
    "Location": "#57C7E3",
    "ExperienceLevel": "#8DCC93",
    "Skill": "#F16667",
    "SkillCategory": "#D9C8AE",
}
HIDDEN_PROPERTIES = {"description", "skills_desc", "aliases"}
GRAPH_FILE = Path("graph.html")

GRAPH_PAGE = Template("""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Job graph: $question</title>
<script src="https://unpkg.com/vis-network@9.1.9/standalone/umd/vis-network.min.js"></script>
<style>
  body { margin: 0; font-family: Segoe UI, Arial, sans-serif; background: #f7f8fa; color: #1b2430; }
  header { padding: 12px 16px; border-bottom: 1px solid #e3e7ec; background: #fff; }
  header h1 { font-size: 16px; margin: 0 0 4px; }
  header p { margin: 0; font-size: 13px; color: #5b6675; }
  #graph { height: calc(100vh - 70px); }
</style>
</head>
<body>
<header>
  <h1>$question</h1>
  <p>$summary &middot; drag to move, scroll to zoom, hover a bubble for details</p>
</header>
<div id="graph"></div>
<script>
  const nodes = new vis.DataSet($nodes);
  const edges = new vis.DataSet($edges);
  new vis.Network(document.getElementById("graph"), { nodes, edges }, {
    nodes: { shape: "dot", size: 16, font: { size: 13 } },
    edges: { arrows: "to", font: { size: 10, align: "middle", color: "#5b6675" }, color: "#aab4c0" },
    physics: { stabilization: { iterations: 200 } },
  });
</script>
</body>
</html>
""")

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


def generate_cypher(question, previous_error=None, system=CYPHER_PROMPT):
    prompt = f"Question: {question}"
    if previous_error:
        prompt += f"\n\nYour previous query failed with this error, fix it:\n{previous_error}"
    text = ask_claude(system, prompt, {"effort": "medium", "format": CYPHER_FORMAT})
    return json.loads(text)


def run_read_only(driver, cypher, as_graph=False):
    if WRITE_CLAUSES.search(cypher):
        raise ValueError("Blocked: the generated query tries to modify the graph.")

    # Guardrail 2: a read transaction, so Neo4j itself rejects any write
    def read(tx):
        result = tx.run(cypher)
        if not as_graph:
            return [record.data() for record in result][:MAX_ROWS]
        graph = result.graph()
        nodes = [
            {
                "id": node.element_id,
                "label": next(iter(node.labels), "Node"),
                "props": {k: v for k, v in node.items() if k not in HIDDEN_PROPERTIES},
            }
            for node in graph.nodes
        ]
        edges = [
            {"from": rel.start_node.element_id, "to": rel.end_node.element_id, "type": rel.type}
            for rel in graph.relationships
        ]
        return nodes, edges

    with driver.session(database=DATABASE) as session:
        return session.execute_read(read)


def write_graph_page(question, nodes, edges):
    """Save the nodes and relationships as an interactive HTML page."""
    vis_nodes = [
        {
            "id": n["id"],
            "label": str(n["props"].get("title") or n["props"].get("name") or n["label"])[:30],
            "title": "\n".join([n["label"]] + [f"{k}: {v}" for k, v in n["props"].items()]),
            "color": LABEL_COLORS.get(n["label"], "#C990C0"),
        }
        for n in nodes
    ]
    vis_edges = [{"from": e["from"], "to": e["to"], "label": e["type"]} for e in edges]

    def as_js(data):
        # Escape "</" so job text can never close the <script> tag
        return json.dumps(data, default=str).replace("</", "<\\/")

    page = GRAPH_PAGE.substitute(
        question=html.escape(question),
        summary=f"{len(nodes)} nodes, {len(edges)} relationships",
        nodes=as_js(vis_nodes),
        edges=as_js(vis_edges),
    )
    GRAPH_FILE.write_text(page, encoding="utf-8")
    return GRAPH_FILE.resolve()


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


def ask_graph(driver, question):
    plan = generate_cypher(question, system=GRAPH_PROMPT)
    if not plan["cypher"]:
        return None, [], [], plan["note"]
    try:
        nodes, edges = run_read_only(driver, plan["cypher"], as_graph=True)
    except Neo4jError as error:
        plan = generate_cypher(question, previous_error=str(error), system=GRAPH_PROMPT)
        nodes, edges = run_read_only(driver, plan["cypher"], as_graph=True)
    return plan["cypher"], nodes, edges, plan["note"]


def main():
    args = sys.argv[1:]
    graph_mode = "--graph" in args
    question = " ".join(a for a in args if a != "--graph") or input("Ask the job graph: ")

    with GraphDatabase.driver(URI, auth=AUTH) as driver:
        if graph_mode:
            cypher, nodes, edges, note = ask_graph(driver, question)
        else:
            cypher, rows, reply = ask(driver, question)

    if not graph_mode:
        if cypher:
            print(f"\nCypher:\n{cypher}\n\nRows returned: {len(rows)}")
        print(f"\nAnswer:\n{reply}")
        return

    if not cypher:
        print(f"\n{note}")
        return
    print(f"\nCypher (paste into Aura > Query to see it there too):\n{cypher}")
    if not nodes:
        print("\nNo matching nodes, so there is nothing to draw.")
        return
    page = write_graph_page(question, nodes, edges)
    print(f"\n{len(nodes)} nodes, {len(edges)} relationships. Opening {page}")
    webbrowser.open(page.as_uri())


if __name__ == "__main__":
    main()