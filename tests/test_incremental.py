import rdflib
from rdflib import Literal
from rdflib.namespace import RDF, RDFS

from nanopub.namespaces import NPX
from pubmate.defining import DefiningNanopubBuilder
from pubmate.idmap import IdMap, IdMapEntry
from pubmate.incremental import publish_incremental
from pubmate.minting import SequentialMinter, TermInput
from pubmate.supersede import SupersessionBuilder

NAMESPACE = "https://example.org/terms/"
TPL_OLD = "https://w3id.org/np/RAtemplateOld"
TPL_NEW = "https://w3id.org/np/RAtemplateNew"
REL = rdflib.URIRef("https://example.org/rel/isMetaboliteOf")


def _minter() -> SequentialMinter:
    return SequentialMinter(DefiningNanopubBuilder(NAMESPACE))


def _sup() -> SupersessionBuilder:
    return SupersessionBuilder()


def _wrapped(template):
    builder = DefiningNanopubBuilder(NAMESPACE, template=template)
    return SequentialMinter(builder), SupersessionBuilder(template=template)


def _term(term_id: str, label: str) -> TermInput:
    builder = DefiningNanopubBuilder(NAMESPACE)
    assertion = builder.make_assertion([(RDF.type, RDFS.Class), (RDFS.label, Literal(label))])
    return TermInput(term_id=term_id, assertion=assertion, label=label)


def _term_ref(term_uri: str, label: str, ref_uri: str | None = None) -> TermInput:
    builder = DefiningNanopubBuilder(NAMESPACE)
    stmts = [(RDF.type, RDFS.Class), (RDFS.label, Literal(label))]
    if ref_uri:
        stmts.append((REL, rdflib.URIRef(ref_uri)))
    return TermInput(term_id=term_uri, assertion=builder.make_assertion(stmts), label=label)


def _run(terms, existing=None):
    return publish_incremental(
        terms, minter=_minter(), supersession_builder=_sup(),
        existing=existing or IdMap(), dry_run=True,
    )


def test_new_term_is_minted_and_records_fingerprint():
    result = _run([_term("alpha", "Alpha")])
    assert [m.term_id for m in result.minted.terms] == ["alpha"]
    assert result.superseded == []
    entry = result.id_map["alpha"]
    assert entry.thing_uri.startswith(f"{NAMESPACE}RA")
    assert entry.np_uri.startswith("https://w3id.org/np/RA")
    assert entry.fingerprint != ""


def test_unchanged_term_is_skipped():
    first = _run([_term("alpha", "Alpha")])
    again = _run([_term("alpha", "Alpha")], existing=first.id_map)
    assert again.minted.terms == []
    assert again.superseded == []
    assert again.skipped == ["alpha"]
    # id-map entry carried forward untouched.
    assert again.id_map["alpha"] == first.id_map["alpha"]


def test_drifted_term_is_superseded_keeping_thing_uri():
    first = _run([_term("alpha", "Alpha")])
    before = first.id_map["alpha"]

    drifted = _run([_term("alpha", "Alpha (edited)")], existing=first.id_map)

    assert drifted.minted.terms == []
    assert [s.term_id for s in drifted.superseded] == ["alpha"]
    sup = drifted.superseded[0]
    # The new nanopub supersedes the recorded one...
    assert sup.supersedes_np_uri == before.np_uri
    assert (None, NPX.supersedes, rdflib.URIRef(before.np_uri)) in sup.nanopub.pubinfo
    # ...and is written against the term's fixed, unchanged thing URI.
    assert rdflib.URIRef(before.thing_uri) in set(sup.nanopub.assertion.subjects())

    after = drifted.id_map["alpha"]
    assert after.thing_uri == before.thing_uri            # identity preserved
    assert after.np_uri == sup.np_uri != before.np_uri    # version advanced
    assert after.fingerprint != before.fingerprint        # fingerprint updated


def test_legacy_entry_without_fingerprint_is_backfilled_not_superseded():
    legacy = IdMap([IdMapEntry("alpha", f"{NAMESPACE}RAseed", "https://w3id.org/np/RAseed", "")])
    result = _run([_term("alpha", "Alpha")], existing=legacy)
    assert result.minted.terms == []
    assert result.superseded == []
    assert result.skipped == ["alpha"]
    entry = result.id_map["alpha"]
    # URIs untouched (no re-issue), fingerprint adopted as baseline.
    assert entry.thing_uri == f"{NAMESPACE}RAseed"
    assert entry.np_uri == "https://w3id.org/np/RAseed"
    assert entry.fingerprint != ""


def test_supersede_resolves_interterm_reference_to_new_thing_uri():
    A, B = NAMESPACE + "a", NAMESPACE + "b"
    minter1, sup1 = _wrapped(TPL_OLD)
    first = publish_incremental(
        [_term_ref(A, "Alpha", ref_uri=B), _term_ref(B, "Beta")],
        minter=minter1, supersession_builder=sup1, existing=IdMap(), dry_run=True,
    )
    thing_a = first.id_map[A].thing_uri
    thing_b = first.id_map[B].thing_uri

    # Change the template -> both drift -> both superseded.
    minter2, sup2 = _wrapped(TPL_NEW)
    result = publish_incremental(
        [_term_ref(A, "Alpha", ref_uri=B), _term_ref(B, "Beta")],
        minter=minter2, supersession_builder=sup2, existing=first.id_map, dry_run=True,
    )
    sup_a = next(s for s in result.superseded if s.term_id == A)
    objs = set(sup_a.nanopub.assertion.objects(rdflib.URIRef(thing_a), REL))
    # The reference now points at b's NEW thing URI, not the old-id.
    assert objs == {rdflib.URIRef(thing_b)}
    assert rdflib.URIRef(B) not in set(sup_a.nanopub.assertion.objects())


def test_mixed_batch_mints_skips_and_supersedes():
    first = _run([_term("keep", "Keep"), _term("edit", "Edit")])
    batch = [
        _term("keep", "Keep"),          # unchanged
        _term("edit", "Edit (edited)"),  # drifted
        _term("new", "New"),             # new
    ]
    result = _run(batch, existing=first.id_map)
    assert [m.term_id for m in result.minted.terms] == ["new"]
    assert [s.term_id for s in result.superseded] == ["edit"]
    assert result.skipped == ["keep"]
    assert set(result.id_map.fingerprint_map) == {"keep", "edit", "new"}
