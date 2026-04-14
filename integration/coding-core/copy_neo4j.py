"""
Copy all data from one Neo4j database to another.
Works on live databases — no need to stop either side.

Usage:
    python tools/copy_neo4j.py

Configure source/target via environment variables or edit below.
"""

import os
import logging
from neo4j import GraphDatabase

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

# Source (local Neo4j)
SRC_URI = os.getenv("SRC_NEO4J_URI", "bolt://localhost:7687")
SRC_USER = os.getenv("SRC_NEO4J_USER", "neo4j")
SRC_PASS = os.getenv("SRC_NEO4J_PASS", "password")
SRC_DB = os.getenv("SRC_NEO4J_DB", "neo4j")

# Target (Docker / Azure Neo4j)
TGT_URI = os.getenv("TGT_NEO4J_URI", "bolt://localhost:7688")
TGT_USER = os.getenv("TGT_NEO4J_USER", "neo4j")
TGT_PASS = os.getenv("TGT_NEO4J_PASS", "password")
TGT_DB = os.getenv("TGT_NEO4J_DB", "neo4j")

BATCH_SIZE = 500


def copy_database():
    src_driver = GraphDatabase.driver(SRC_URI, auth=(SRC_USER, SRC_PASS))
    tgt_driver = GraphDatabase.driver(TGT_URI, auth=(TGT_USER, TGT_PASS))

    try:
        # Verify connections
        src_driver.verify_connectivity()
        logger.info("✓ Connected to source: %s", SRC_URI)
        tgt_driver.verify_connectivity()
        logger.info("✓ Connected to target: %s", TGT_URI)

        # Step 1: Clear target
        logger.info("\n--- Clearing target database ---")
        with tgt_driver.session(database=TGT_DB) as session:
            result = session.run("MATCH (n) RETURN count(n) AS c").single()
            logger.info("  Target has %d nodes (will be deleted)", result["c"])
            session.run("MATCH (n) DETACH DELETE n")
            logger.info("  ✓ Target cleared")

        # Step 2: Copy nodes (batched)
        logger.info("\n--- Copying nodes ---")
        with src_driver.session(database=SRC_DB) as src_session:
            # Get all node labels
            labels_result = src_session.run("CALL db.labels() YIELD label RETURN label")
            labels = [r["label"] for r in labels_result]
            logger.info("  Labels found: %s", labels)

            total_nodes = 0
            for label in labels:
                count_result = src_session.run(
                    f"MATCH (n:`{label}`) RETURN count(n) AS c"
                ).single()
                node_count = count_result["c"]
                logger.info("  Copying %d nodes with label :%s", node_count, label)

                # Read nodes in batches
                offset = 0
                while offset < node_count:
                    nodes_result = src_session.run(
                        f"MATCH (n:`{label}`) "
                        f"RETURN n, labels(n) AS all_labels "
                        f"SKIP $skip LIMIT $limit",
                        skip=offset, limit=BATCH_SIZE
                    )
                    batch = []
                    for record in nodes_result:
                        node = record["n"]
                        props = dict(node)
                        all_labels = record["all_labels"]
                        batch.append({"props": props, "labels": all_labels})

                    if batch:
                        with tgt_driver.session(database=TGT_DB) as tgt_session:
                            for item in batch:
                                label_str = ":".join(f"`{l}`" for l in item["labels"])
                                tgt_session.run(
                                    f"CREATE (n:{label_str}) SET n = $props",
                                    props=item["props"]
                                )
                        total_nodes += len(batch)

                    offset += BATCH_SIZE

            logger.info("  ✓ Copied %d nodes total", total_nodes)

        # Step 3: Copy relationships (batched)
        logger.info("\n--- Copying relationships ---")
        with src_driver.session(database=SRC_DB) as src_session:
            rel_types_result = src_session.run(
                "CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType"
            )
            rel_types = [r["relationshipType"] for r in rel_types_result]
            logger.info("  Relationship types: %s", rel_types)

            total_rels = 0
            for rel_type in rel_types:
                count_result = src_session.run(
                    f"MATCH ()-[r:`{rel_type}`]->() RETURN count(r) AS c"
                ).single()
                rel_count = count_result["c"]
                logger.info("  Copying %d relationships of type :%s", rel_count, rel_type)

                offset = 0
                while offset < rel_count:
                    rels_result = src_session.run(
                        f"MATCH (a)-[r:`{rel_type}`]->(b) "
                        f"RETURN elementId(a) AS src_id, properties(a) AS src_props, labels(a) AS src_labels, "
                        f"       elementId(b) AS tgt_id, properties(b) AS tgt_props, labels(b) AS tgt_labels, "
                        f"       properties(r) AS rel_props "
                        f"SKIP $skip LIMIT $limit",
                        skip=offset, limit=BATCH_SIZE
                    )

                    batch = list(rels_result)
                    if batch:
                        with tgt_driver.session(database=TGT_DB) as tgt_session:
                            for record in batch:
                                rec = dict(record)
                                # Match nodes by their properties (use id or unique key)
                                src_labels = ":".join(f"`{l}`" for l in rec["src_labels"])
                                tgt_labels = ":".join(f"`{l}`" for l in rec["tgt_labels"])

                                # Use 'id' property if available, else match by all props
                                src_match = _build_match(rec["src_props"])
                                tgt_match = _build_match(rec["tgt_props"])

                                tgt_session.run(
                                    f"MATCH (a:{src_labels} {{{src_match}}}) "
                                    f"MATCH (b:{tgt_labels} {{{tgt_match}}}) "
                                    f"CREATE (a)-[r:`{rel_type}`]->(b) "
                                    f"SET r = $rel_props",
                                    rel_props=rec["rel_props"],
                                    **_build_params("src", rec["src_props"]),
                                    **_build_params("tgt", rec["tgt_props"]),
                                )
                        total_rels += len(batch)

                    offset += BATCH_SIZE

            logger.info("  ✓ Copied %d relationships total", total_rels)

        # Step 4: Copy indexes
        logger.info("\n--- Recreating indexes ---")
        with src_driver.session(database=SRC_DB) as src_session:
            indexes = src_session.run("SHOW INDEXES YIELD name, type, labelsOrTypes, properties, state")
            with tgt_driver.session(database=TGT_DB) as tgt_session:
                for idx in indexes:
                    rec = dict(idx)
                    if rec["state"] != "ONLINE":
                        continue
                    if rec["type"] in ("LOOKUP",):
                        continue  # Built-in, can't recreate
                    label = rec["labelsOrTypes"][0] if rec["labelsOrTypes"] else None
                    props = rec["properties"]
                    if not label or not props:
                        continue

                    prop_str = ", ".join(f"n.`{p}`" for p in props)
                    idx_name = rec["name"]

                    if rec["type"] == "VECTOR":
                        # Skip vector indexes — need special syntax with dimensions
                        logger.info("  ⚠ Skipping vector index '%s' (recreate manually)", idx_name)
                        continue

                    try:
                        if rec["type"] == "RANGE":
                            tgt_session.run(
                                f"CREATE INDEX `{idx_name}` IF NOT EXISTS "
                                f"FOR (n:`{label}`) ON ({prop_str})"
                            )
                        elif rec["type"] == "FULLTEXT":
                            tgt_session.run(
                                f"CREATE FULLTEXT INDEX `{idx_name}` IF NOT EXISTS "
                                f"FOR (n:`{label}`) ON EACH [{prop_str}]"
                            )
                        elif rec["type"] == "TEXT":
                            tgt_session.run(
                                f"CREATE TEXT INDEX `{idx_name}` IF NOT EXISTS "
                                f"FOR (n:`{label}`) ON ({prop_str})"
                            )
                        logger.info("  ✓ Created %s index: %s", rec["type"], idx_name)
                    except Exception as e:
                        logger.warning("  ⚠ Index '%s' failed: %s", idx_name, e)

        # Step 5: Verify
        logger.info("\n--- Verification ---")
        with tgt_driver.session(database=TGT_DB) as tgt_session:
            nodes = tgt_session.run("MATCH (n) RETURN count(n) AS c").single()["c"]
            rels = tgt_session.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
            logger.info("  Target: %d nodes, %d relationships", nodes, rels)

        logger.info("\n✓ Done!")

    finally:
        src_driver.close()
        tgt_driver.close()


def _build_match(props: dict, prefix: str = "src") -> str:
    """Build Cypher property match string."""
    for key in ("id", "name", "source_file_path"):
        if key in props:
            return f"`{key}`: ${prefix}_{key}"
    # Last resort: match by all props (slow but works)
    if props:
        first_key = next(iter(props))
        return f"`{first_key}`: ${prefix}_{first_key}"
    return ""


def _build_params(prefix: str, props: dict) -> dict:
    """Build parameter dict for Cypher matching."""
    for key in ("id", "name", "source_file_path"):
        if key in props:
            return {f"{prefix}_{key}": props[key]}
    if props:
        first_key = next(iter(props))
        return {f"{prefix}_{first_key}": props[first_key]}
    return {}


if __name__ == "__main__":
    copy_database()
