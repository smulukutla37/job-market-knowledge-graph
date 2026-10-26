import os
import truststore
truststore.inject_into_ssl()
from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()

uri = os.environ["NEO4J_URI"]
auth = (os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"])
database = os.environ["NEO4J_DATABASE"]

with GraphDatabase.driver(uri, auth=auth) as driver:
    driver.verify_connectivity()
    records, _, _ = driver.execute_query(
        "RETURN 'Connected to Neo4j!' AS message", database_=database
    )
    print(records[0]["message"])