import rdflib
from rdflib.namespace import RDFS

from pubmate.defining import DefiningNanopubBuilder
from pubmate.fingerprint import fingerprint_term
from pubmate.incremental import publish_incremental
from pubmate.migrate import migrate_terms
from pubmate.minting import SequentialMinter, term_input_from_assertion
from pubmate.supersede import SupersessionBuilder

NS = "https://w3id.org/peh/biochementities/"
TERMS = rdflib.Namespace(NS)
PEH = rdflib.Namespace("https://w3id.org/peh/terms/")
VOCAB = "https://w3id.org/spaces/biochementity/r/vocabulary"
TYPE = "https://w3id.org/peh/terms/BioChemEntity"
TPL_OLD = "https://w3id.org/np/RAtemplateOld"
TPL_NEW = "https://w3id.org/np/RAtemplateNew"


def _assertion(old_id, *, metabolite_of=(), isomer_of=()):
    g = rdflib.Graph()
    s = TERMS[old_id]
    g.add((s, RDFS.label, rdflib.Literal(old_id)))
    g.add((s, RDFS.subClassOf, PEH.BioChemEntity))  # top parent (external, untouched)
    for r in metabolite_of:
        g.add((s, PEH.isMetaboliteOf, TERMS[r]))
    for r in isomer_of:
        g.add((s, PEH.isIsomerOf, TERMS[r]))
    return g


def _batch(spec):
    return {str(TERMS[k]): _assertion(k, **v) for k, v in spec.items()}


def _minter_and_supersession():
    builder = DefiningNanopubBuilder(NS)  # no profile -> ephemeral keyless
    return SequentialMinter(builder), SupersessionBuilder()


def _no_old_id_objects(graph, old_uris):
    """No object in the graph is one of the un-migrated old term URIs."""
    return not any(o in old_uris for _, _, o in graph)


def test_acyclic_chain_resolves_all_inline():
    assertions = _batch({
        "a": {"metabolite_of": ["b"]},
        "b": {"metabolite_of": ["c"]},
        "c": {},
    })
    minter, sup = _minter_and_supersession()
    result = migrate_terms(assertions, namespace=NS, minter=minter, supersession_builder=sup, dry_run=True)

    assert len(result.defining.terms) == 3
    assert result.deferred_edges == set()
    assert result.superseding == []

    minted = {t.term_id: t for t in result.defining.terms}
    a, b = minted[str(TERMS["a"])], minted[str(TERMS["b"])]
    # a's isMetaboliteOf now points at b's NEW thing URI, not the old id
    a_refs = set(a.nanopub.assertion.objects(rdflib.URIRef(a.thing_uri), PEH.isMetaboliteOf))
    assert a_refs == {rdflib.URIRef(b.thing_uri)}

    old_uris = set(assertions)
    for t in result.defining.terms:
        assert _no_old_id_objects(t.nanopub.assertion, old_uris)


def test_two_cycle_defers_one_and_supersedes():
    assertions = _batch({
        "a": {"isomer_of": ["b"]},
        "b": {"isomer_of": ["a"]},
    })
    minter, sup = _minter_and_supersession()
    result = migrate_terms(assertions, namespace=NS, minter=minter, supersession_builder=sup, dry_run=True)

    assert len(result.defining.terms) == 2
    assert len(result.deferred_edges) == 1
    assert len(result.superseding) == 1

    new = result.id_map.thing_uri_map
    old_uris = set(assertions)

    # The defining nanopubs carry no old-id references (the back-edge was held back).
    for t in result.defining.terms:
        assert _no_old_id_objects(t.nanopub.assertion, old_uris)

    # The superseding nanopub re-states the term with the cyclic link resolved to a new URI,
    # and references the defining nanopub it supersedes.
    s = result.superseding[0]
    isomer_objs = set(s.nanopub.assertion.objects(rdflib.URIRef(new[s.term_id]), PEH.isIsomerOf))
    assert isomer_objs and all(str(o) in new.values() for o in isomer_objs)
    assert _no_old_id_objects(s.nanopub.assertion, old_uris)

    # The id-map points at the supersession (the latest version), so a future
    # re-issue supersedes it rather than the defining nanopub.
    assert result.id_map[s.term_id].np_uri == s.np_uri


def test_rerun_with_existing_idmap_mints_nothing():
    assertions = _batch({"a": {"metabolite_of": ["b"]}, "b": {}})
    minter, sup = _minter_and_supersession()
    first = migrate_terms(assertions, namespace=NS, minter=minter, supersession_builder=sup, dry_run=True)

    minter2, sup2 = _minter_and_supersession()
    second = migrate_terms(
        assertions, namespace=NS, minter=minter2, supersession_builder=sup2,
        existing=first.id_map, dry_run=True,
    )
    assert second.defining.terms == []
    assert second.superseding == []
    # the carried-over id-map is preserved
    assert len(second.id_map) == 2


def test_part_of_link_on_defining_and_superseding():
    DCTERMS = rdflib.Namespace("http://purl.org/dc/terms/")
    vocab = rdflib.URIRef("https://w3id.org/spaces/biochementity/r/vocabulary")
    assertions = _batch({"a": {"isomer_of": ["b"]}, "b": {"isomer_of": ["a"]}})  # cycle -> 1 supersession
    minter, sup = _minter_and_supersession()
    result = migrate_terms(
        assertions, namespace=NS, minter=minter, supersession_builder=sup,
        dry_run=True, part_of=str(vocab),
    )
    new = result.id_map.thing_uri_map
    # every defining nanopub links its term to the vocabulary
    for t in result.defining.terms:
        assert (rdflib.URIRef(new[t.term_id]), DCTERMS.isPartOf, vocab) in t.nanopub.assertion
    # and the superseding nanopub keeps the link
    assert result.superseding
    for s in result.superseding:
        assert (rdflib.URIRef(new[s.term_id]), DCTERMS.isPartOf, vocab) in s.nanopub.assertion


def _wrapped_minter(template):
    builder = DefiningNanopubBuilder(NS, template=template, nanopub_types=[TYPE])
    return SequentialMinter(builder), SupersessionBuilder(template=template, nanopub_types=[TYPE])


def _terms_for(assertions, builder):
    return [
        term_input_from_assertion(g, namespace=NS, thing_uri=builder.thing_uri, part_of=VOCAB)
        for g in assertions.values()
    ]


def test_migration_records_source_fingerprints():
    assertions = _batch({"a": {"metabolite_of": ["b"]}, "b": {}})
    minter, sup = _minter_and_supersession()
    result = migrate_terms(
        assertions, namespace=NS, minter=minter, supersession_builder=sup, dry_run=True, part_of=VOCAB,
    )
    fps = result.id_map.fingerprint_map
    assert all(fps.values())  # every migrated term carries a fingerprint
    # It is the fingerprint of the as-given source (references intact), i.e. what
    # publish_incremental will recompute -- not the resolved/minted graph.
    for old, g in assertions.items():
        term = term_input_from_assertion(g, namespace=NS, thing_uri=minter.builder.thing_uri, part_of=VOCAB)
        assert fps[old] == fingerprint_term(term, minter.builder)


def test_incremental_over_migrated_sources_skips_unchanged():
    assertions = _batch({"a": {"metabolite_of": ["b"]}, "b": {}})
    minter, sup = _wrapped_minter(TPL_OLD)
    migrated = migrate_terms(
        assertions, namespace=NS, minter=minter, supersession_builder=sup, dry_run=True, part_of=VOCAB,
    )
    # Re-run the incremental publisher over the SAME sources with the SAME wrapper.
    minter2, sup2 = _wrapped_minter(TPL_OLD)
    result = publish_incremental(
        _terms_for(assertions, minter2.builder), minter=minter2, supersession_builder=sup2,
        existing=migrated.id_map, dry_run=True,
    )
    assert result.minted.terms == []
    assert result.superseded == []
    assert set(result.skipped) == set(assertions)  # no phantom drift


def test_incremental_supersedes_migrated_terms_on_template_change():
    assertions = _batch({"a": {"metabolite_of": ["b"]}, "b": {}})
    minter, sup = _wrapped_minter(TPL_OLD)
    migrated = migrate_terms(
        assertions, namespace=NS, minter=minter, supersession_builder=sup, dry_run=True, part_of=VOCAB,
    )
    # Change only the template; every migrated term must be superseded, keeping its thing URI.
    minter2, sup2 = _wrapped_minter(TPL_NEW)
    result = publish_incremental(
        _terms_for(assertions, minter2.builder), minter=minter2, supersession_builder=sup2,
        existing=migrated.id_map, dry_run=True,
    )
    assert result.minted.terms == []
    assert {s.term_id for s in result.superseded} == set(assertions)
    for s in result.superseded:
        assert migrated.id_map[s.term_id].thing_uri == result.id_map[s.term_id].thing_uri


def test_default_suggester_attributes_defining_and_superseding():
    PROV = rdflib.Namespace("http://www.w3.org/ns/prov#")
    GERTJAN = rdflib.URIRef("https://orcid.org/0000-0001-8327-0142")
    assertions = _batch({"a": {"isomer_of": ["b"]}, "b": {"isomer_of": ["a"]}})  # cycle -> 1 supersession
    builder = DefiningNanopubBuilder(NS)
    minter = SequentialMinter(builder, default_suggester_orcid=str(GERTJAN))
    result = migrate_terms(
        assertions, namespace=NS, minter=minter, supersession_builder=SupersessionBuilder(), dry_run=True,
    )
    # every defining nanopub's assertion is attributed to the default suggester (in provenance)
    for t in result.defining.terms:
        assert GERTJAN in set(t.nanopub.provenance.objects(None, PROV.wasAttributedTo))
    # and so is the superseding one
    assert result.superseding
    for s in result.superseding:
        assert GERTJAN in set(s.nanopub.provenance.objects(None, PROV.wasAttributedTo))
