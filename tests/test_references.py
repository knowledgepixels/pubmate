import rdflib

from pubmate.references import (
    Ordering,
    iter_term_references,
    order_terms,
    referenced_terms,
    split_references,
)

NS = "https://w3id.org/peh/biochementities/"
TERMS = rdflib.Namespace(NS)
PEH = rdflib.Namespace("https://w3id.org/peh/terms/")
RDFS = rdflib.namespace.RDFS
SKOS = rdflib.Namespace("http://www.w3.org/2004/02/skos/core#")


def _assertion(subject_id, *, refs=(), externals=()):
    g = rdflib.Graph()
    s = TERMS[subject_id]
    g.add((s, RDFS.label, rdflib.Literal(subject_id)))
    # parent is the top class -> NOT an inter-term reference (leaves the namespace)
    g.add((s, RDFS.subClassOf, PEH.BioChemEntity))
    for r in refs:
        g.add((s, PEH.isMetaboliteOf, TERMS[r]))
    for e in externals:
        g.add((s, SKOS.exactMatch, rdflib.URIRef(e)))
    return g, s


# -- reference detection ---------------------------------------------------

def test_referenced_terms_picks_only_in_namespace_other_terms():
    g, s = _assertion("a", refs=["b", "c"], externals=["https://identifiers.org/cas:1-2-3"])
    assert referenced_terms(g, namespace=NS, subject=s) == {TERMS["b"].toPython(), TERMS["c"].toPython()}


def test_iter_term_references_excludes_self_and_top_parent():
    g = rdflib.Graph()
    s = TERMS["a"]
    g.add((s, RDFS.subClassOf, PEH.BioChemEntity))  # top parent, external ns
    g.add((s, PEH.isIsomerOf, s))                    # self-reference
    g.add((s, PEH.isMetaboliteOf, TERMS["b"]))       # real inter-term ref
    refs = list(iter_term_references(g, namespace=NS, subject=s))
    assert refs == [(s, PEH.isMetaboliteOf, TERMS["b"])]


# -- ordering --------------------------------------------------------------

def test_order_acyclic_places_dependencies_first():
    # a -> b -> c ; expect c, b, a
    refs = {"a": {"b"}, "b": {"c"}, "c": set()}
    result = order_terms(refs)
    assert result.deferred == set()
    assert result.order.index("c") < result.order.index("b") < result.order.index("a")


def test_order_two_cycle_defers_exactly_one_edge():
    # symmetric isIsomerOf: a <-> b. One direction resolves inline, the other defers.
    refs = {"a": {"b"}, "b": {"a"}}
    result = order_terms(refs)
    assert len(result.deferred) == 1
    src, tgt = next(iter(result.deferred))
    # the deferred edge points from the earlier-minted term to the later one
    assert result.order.index(tgt) > result.order.index(src)
    # both terms still get minted
    assert set(result.order) == {"a", "b"}


def test_order_three_cycle_defers_one_back_edge():
    # a -> b -> c -> a : a single back-edge should be deferred, two resolve inline
    refs = {"a": {"b"}, "b": {"c"}, "c": {"a"}}
    result = order_terms(refs)
    assert len(result.deferred) == 1
    assert set(result.order) == {"a", "b", "c"}


def test_order_ignores_self_reference():
    refs = {"a": {"a"}}
    result = order_terms(refs)
    assert result.order == ["a"]
    assert result.deferred == set()


def test_order_is_deterministic():
    refs = {"a": {"b"}, "b": {"a"}, "c": set(), "d": {"c"}}
    assert order_terms(refs) == order_terms(refs)
    assert isinstance(order_terms(refs), Ordering)


# -- splitting -------------------------------------------------------------

def test_split_rewrites_resolved_and_holds_back_deferred():
    g, s = _assertion("a", refs=["b", "c"])
    # only b is minted so far; both b and c are in the batch
    resolved = {TERMS["b"].toPython(): NS + "RAbbb"}
    batch = {TERMS["b"].toPython(), TERMS["c"].toPython()}
    split = split_references(g, namespace=NS, subject=s, resolved_uris=resolved, batch_targets=batch)

    # b rewritten to its new URI
    assert (s, PEH.isMetaboliteOf, rdflib.URIRef(NS + "RAbbb")) in split.kept
    # c held back (in batch, not yet minted), still old-id, not in kept
    assert (s, PEH.isMetaboliteOf, TERMS["c"]) not in split.kept
    assert split.deferred == [(s, PEH.isMetaboliteOf, TERMS["c"])]
    assert split.dangling == []
    # non-reference triples preserved (label + top parent)
    assert (s, RDFS.label, rdflib.Literal("a")) in split.kept
    assert (s, RDFS.subClassOf, PEH.BioChemEntity) in split.kept


def test_split_with_all_resolved_has_no_deferred():
    g, s = _assertion("a", refs=["b"])
    resolved = {TERMS["b"].toPython(): NS + "RAbbb"}
    split = split_references(
        g, namespace=NS, subject=s, resolved_uris=resolved, batch_targets={TERMS["b"].toPython()}
    )
    assert split.deferred == []
    assert split.dangling == []
    assert (s, PEH.isMetaboliteOf, rdflib.URIRef(NS + "RAbbb")) in split.kept


def test_split_keeps_dangling_reference_with_old_id():
    # c is referenced but neither resolved nor in the batch -> dangling, kept as-is
    g, s = _assertion("a", refs=["c"])
    split = split_references(
        g, namespace=NS, subject=s, resolved_uris={}, batch_targets={TERMS["a"].toPython()}
    )
    assert split.deferred == []
    assert split.dangling == [(s, PEH.isMetaboliteOf, TERMS["c"])]
    # kept with its old id so the term still mints
    assert (s, PEH.isMetaboliteOf, TERMS["c"]) in split.kept
