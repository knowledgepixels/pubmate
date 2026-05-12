from pathlib import Path

import rdflib
from rdflib.namespace import OWL, RDF, RDFS

from pubmate.rdfcleaner import clean_graph, split_into_assertions


FIXTURE = Path(__file__).parent / "input" / "resources.ttl"
NO_TRANSLATIONS_FIXTURE = Path(__file__).parent / "input" / "no-translations.ttl"
PEHTERMS = rdflib.Namespace("https://w3id.org/peh/terms/")
SCHEMA = rdflib.Namespace("http://schema.org/")
BIOCHEM_ENTITY = rdflib.URIRef("https://w3id.org/peh/biochementities/0071a0804e")
MATRIX = rdflib.URIRef("https://w3id.org/peh/matrices/01KRE01VQ2SPV822T9AN64ZDQ3")
MATRIX_PARENT = rdflib.URIRef("https://w3id.org/peh/matrices/01KQ9QXVQNNJBQFZTAC8Y3ARD1")


def _read_resources() -> rdflib.Graph:
    graph = rdflib.Graph()
    graph.parse(FIXTURE)
    return graph


def _read_no_translations() -> rdflib.Graph:
    graph = rdflib.Graph()
    graph.parse(NO_TRANSLATIONS_FIXTURE)
    return graph


def test_clean_graph_uses_schema_identifier_and_in_language_for_translations() -> None:
    graph = _read_resources()

    clean_graph(
        graph,
        base_namespace=str(PEHTERMS),
        property_map={
            "name": str(RDFS.label),
            "short_name": str(SCHEMA.alternateName),
        },
    )

    assert (
        BIOCHEM_ENTITY,
        RDFS.label,
        rdflib.Literal("N-acetyl-S-(2-carbamoyl-ethyl)cysteine", lang="nl-BE"),
    ) in graph
    assert (BIOCHEM_ENTITY, SCHEMA.alternateName, rdflib.Literal("AAMA", lang="nl-BE")) in graph
    assert list(graph.objects(BIOCHEM_ENTITY, PEHTERMS.hasTranslation)) == []


def test_split_into_assertions_preserves_resource_blank_node_descriptions() -> None:
    graph = _read_resources()
    assertions = dict(split_into_assertions(graph, {str(PEHTERMS.BioChemEntity)}))
    assertion = assertions["0071a0804e"]

    assert set(assertions) == {"0071a0804e", "18f71f941d", "8a1e31974c", "c75af2cf34", "d54ea27f86"}
    assert (BIOCHEM_ENTITY, RDFS.subClassOf, PEHTERMS.BioChemEntity) in assertion
    assert not list(assertion.triples((rdflib.URIRef("https://w3id.org/peh/biochementities/18f71f941d"), None, None)))

    context_alias_nodes = list(assertion.objects(BIOCHEM_ENTITY, PEHTERMS.hasContextAlias))
    assert len(context_alias_nodes) == 1
    context_alias = context_alias_nodes[0]
    assert isinstance(context_alias, rdflib.BNode)
    assert (context_alias, RDF.type, PEHTERMS.ContextAlias) in assertion
    assert (context_alias, SCHEMA.alternateName, rdflib.Literal("aama")) in assertion
    assert (context_alias, SCHEMA.identifier, rdflib.Literal("short_name")) in assertion
    assert (context_alias, PEHTERMS.hasContext, rdflib.URIRef("https://ror.org/04gq0w522")) in assertion

    translation_nodes = list(assertion.objects(BIOCHEM_ENTITY, PEHTERMS.hasTranslation))
    assert len(translation_nodes) == 2
    assert all(isinstance(node, rdflib.BNode) for node in translation_nodes)
    assert {(node, RDF.type, PEHTERMS.Translation) in assertion for node in translation_nodes} == {True}
    assert set(assertion.objects(None, SCHEMA.inLanguage)) == {rdflib.Literal("nl-BE")}
    assert set(assertion.objects(None, PEHTERMS.hasTranslatedValue)) == {
        rdflib.Literal("AAMA"),
        rdflib.Literal("N-acetyl-S-(2-carbamoyl-ethyl)cysteine"),
    }


def test_cleaned_split_assertion_contains_language_tagged_slot_translations() -> None:
    graph = _read_resources()
    clean_graph(
        graph,
        base_namespace=str(PEHTERMS),
        property_map={
            "name": str(RDFS.label),
            "short_name": str(SCHEMA.alternateName),
        },
    )

    assertions = dict(split_into_assertions(graph, {str(PEHTERMS.BioChemEntity)}))
    assertion = assertions["0071a0804e"]

    assert (
        BIOCHEM_ENTITY,
        RDFS.label,
        rdflib.Literal("N-acetyl-S-(2-carbamoyl-ethyl)cysteine", lang="nl-BE"),
    ) in assertion
    assert (BIOCHEM_ENTITY, SCHEMA.alternateName, rdflib.Literal("AAMA", lang="nl-BE")) in assertion
    assert list(assertion.objects(BIOCHEM_ENTITY, PEHTERMS.hasTranslation)) == []


def test_cleaned_split_assertion_without_translations_contains_matrix_statements() -> None:
    graph = _read_no_translations()
    clean_graph(
        graph,
        base_namespace=str(PEHTERMS),
        property_map={
            "name": str(RDFS.label),
            "short_name": str(SCHEMA.alternateName),
        },
    )

    assertions = dict(split_into_assertions(graph, {str(MATRIX_PARENT)}))
    assertion = assertions["01KRE01VQ2SPV822T9AN64ZDQ3"]

    assert set(assertions) == {"01KRE01VQ2SPV822T9AN64ZDQ3"}
    assert set(assertion) == {
        (MATRIX, RDF.type, OWL.Class),
        (MATRIX, RDFS.label, rdflib.Literal("Our example matrix")),
        (MATRIX, SCHEMA.description, rdflib.Literal("This is just an example matrix")),
        (MATRIX, RDFS.subClassOf, MATRIX_PARENT),
    }
