import logging
from pathlib import Path
from typing import Generator, Tuple

import rdflib
from rdflib.namespace import RDFS


logging.basicConfig(level=logging.INFO, format="::%(levelname)s:: %(message)s")
logger = logging.getLogger(__name__)

SCHEMA = rdflib.Namespace("http://schema.org/")


# ------------------------------------------------------------
# Translation cleanup
# ------------------------------------------------------------
def add_language(g: rdflib.Graph, base_namespace: str, property_map: dict[str, str]) -> None:
    if not (base_namespace.endswith("/") or base_namespace.endswith("#")):
        raise ValueError("base_namespace must end with '/' or '#'")

    def build_values_block() -> str:
        return " ".join(f'("{k}" <{v}>)' for k, v in property_map.items())

    TRANSLATIONS = f"<{base_namespace}hasTranslation>"
    PROPERTY_NAME = f"<{SCHEMA.identifier}>"
    LANGUAGE = f"<{SCHEMA.inLanguage}>"
    TRANSLATED_VALUE = f"<{base_namespace}hasTranslatedValue>"
    values_rows = build_values_block()

    logger.info("Running SPARQL CONSTRUCT to convert translations into language-tagged literals")

    construct_query = f"""
        CONSTRUCT {{
            ?subject ?predicate ?literal .
        }}
        WHERE {{
            ?subject {TRANSLATIONS} ?t .

            ?t {PROPERTY_NAME} ?propName ;
            {LANGUAGE} ?lang ;
            {TRANSLATED_VALUE} ?value .

            VALUES (?propName ?predicate) {{
                {values_rows}
            }}

            BIND( STRLANG(?value, ?lang) AS ?literal )
        }}
    """
    constructed = g.query(construct_query)

    for triple in constructed:
        g.add(triple)

    logger.info("Deleting original translation nodes")

    delete_query = f"""
        DELETE {{
            ?subject {TRANSLATIONS} ?t .
            ?t ?p ?o .
        }}
        WHERE {{
            ?subject {TRANSLATIONS} ?t .
            ?t ?p ?o .
        }}
    """
    g.update(delete_query)


def clean_graph(g: rdflib.Graph, base_namespace: str, property_map: dict[str, str]) -> None:
    logger.info("Cleaning graph: converting translation structures")
    add_language(g, base_namespace, property_map=property_map)


# ------------------------------------------------------------
# Graph I/O
# ------------------------------------------------------------
def read_graph(source: str, format: str | None = None) -> rdflib.Graph:
    """
    Load any RDF format rdflib supports.
    If format is None, rdflib will auto-detect based on file extension.
    """
    g = rdflib.Graph()
    logger.info(f"Loading RDF graph from {source}")

    g.parse(source, format=format)
    logger.info(f"Loaded {len(g)} triples")

    return g


def serialize_graph(g: rdflib.Graph, output_path: str) -> None:
    """
    Always serialize to Turtle (.ttl).
    Overwrites existing files.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info(f"Writing graph to {output_path}")
    g.serialize(destination=str(output_path), format="turtle")


# ------------------------------------------------------------
# Assertion splitting
# ------------------------------------------------------------
def _copy_subject_description(
    source: rdflib.Graph,
    target: rdflib.Graph,
    subject: rdflib.term.Identifier,
    visited: set[rdflib.term.Identifier],
) -> None:
    if subject in visited:
        return

    visited.add(subject)

    for triple in source.triples((subject, None, None)):
        target.add(triple)

        _, _, obj = triple
        if isinstance(obj, rdflib.BNode):
            _copy_subject_description(source, target, obj, visited)


def split_into_assertions(
    g: rdflib.Graph,
    all_classes: set[str],
) -> Generator[Tuple[str, rdflib.Graph], None, None]:
    for clss in all_classes:
        try:
            parent = g.namespace_manager.expand_curie(clss)
        except Exception:
            parent = rdflib.URIRef(clss)

        logger.info(f"Finding direct subclasses of {parent}")

        for subclass, _, _ in g.triples((None, RDFS.subClassOf, parent)):
            term_id = subclass.split("#")[-1] if "#" in subclass else subclass.split("/")[-1]

            logger.info(f"Creating assertion graph for {term_id}")

            assertion_graph = rdflib.Graph()
            for prefix, namespace in g.namespaces():
                assertion_graph.bind(prefix, namespace)

            _copy_subject_description(g, assertion_graph, subclass, set())

            yield term_id, assertion_graph
