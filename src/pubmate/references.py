"""Inter-term reference resolution for the identifier migration.

When a batch of terms references each other by their *old* identifiers, those
references must be rewritten to the terms' *new* thing URIs before (or while)
minting. A reference can be baked into a term's defining nanopub only if the
referenced term is minted first: its new URI — and therefore its content hash /
artifact code — must already be fixed. So we mint in an order that lets as many
references as possible resolve inline, and report the unavoidable back-edges
(e.g. the symmetric ``isIsomerOf`` cycles) that must instead be added later by a
superseding nanopub.

This is pubmate's equivalent of nanopub-java's ``np sign -r`` (its
``CrossRefResolver``): a ``Resource -> IRI`` rewrite over each assertion, plus
the ordering needed to make that rewrite sound.

The functions here are pure graph/RDF logic — no signing, no I/O — so the
migration's hardest part (cycle handling) is unit-testable in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Mapping, Set, Tuple

import rdflib


def iter_term_references(
    assertion: rdflib.Graph,
    *,
    namespace: str,
    subject: rdflib.URIRef,
) -> Iterator[Tuple[rdflib.URIRef, rdflib.URIRef, rdflib.URIRef]]:
    """Yield the ``(s, p, o)`` triples that are *inter-term* references.

    A reference to another term is any triple whose object is a ``URIRef`` in
    ``namespace`` other than the term's own ``subject`` (e.g. ``isMetaboliteOf``,
    ``isIsomerOf``, or ``subClassOf`` pointing at another biochementity).
    References that leave the namespace (external ``exactMatch``, the top
    ``…/terms/BioChemEntity`` parent, ``hasContext`` ROR ids, …) are *not*
    inter-term links and are left untouched.
    """
    for s, p, o in assertion:
        if isinstance(o, rdflib.URIRef) and o != subject and str(o).startswith(namespace):
            yield s, p, o


def referenced_terms(
    assertion: rdflib.Graph,
    *,
    namespace: str,
    subject: rdflib.URIRef,
) -> Set[str]:
    """The set of other-term URIs (as strings) this term references."""
    return {str(o) for _, _, o in iter_term_references(assertion, namespace=namespace, subject=subject)}


@dataclass
class Ordering:
    """A mint order plus the references that cannot be resolved inline.

    Attributes:
        order: term ids in the order they should be minted. A term's in-batch
            references that point *earlier* in this order resolve inline; the
            rest are deferred.
        deferred: ``(source, target)`` edges that must be added by a later
            superseding pass — the back-edges left by cycles (and any reference
            whose target could not be ordered before its source).
    """

    order: List[str] = field(default_factory=list)
    deferred: Set[Tuple[str, str]] = field(default_factory=set)


def order_terms(references: Mapping[str, Set[str]]) -> Ordering:
    """Order terms so references resolve inline where possible (cycle-aware).

    ``references`` maps each term id to the set of *in-batch* term ids it
    references. Targets outside the batch (already minted, or external) must not
    appear here — they impose no ordering constraint.

    Greedy Kahn-style: repeatedly place every term whose references are all
    already placed. When none qualify, the remaining terms form one or more
    cycles; break them by placing the term with the fewest still-unplaced
    references (ties broken by id, for determinism), recording those unplaced
    references as ``deferred``. Repeat until everything is placed.

    The result is deterministic and places dependencies before dependents for
    all acyclic edges; only genuine cycle back-edges land in ``deferred``.
    """
    remaining: Set[str] = set(references)
    # Restrict each term's refs to in-batch targets, ignore self-references.
    refs: Dict[str, Set[str]] = {
        t: {r for r in references[t] if r in remaining and r != t} for t in remaining
    }

    placed: Set[str] = set()
    order: List[str] = []
    deferred: Set[Tuple[str, str]] = set()

    while remaining:
        ready = sorted(t for t in remaining if refs[t] <= placed)
        if ready:
            for t in ready:
                order.append(t)
                placed.add(t)
                remaining.discard(t)
            continue

        # Cycle: nothing is fully satisfiable. Break deterministically by
        # picking the term with the fewest outstanding refs (then smallest id);
        # its outstanding references become deferred back-edges.
        pick = min(remaining, key=lambda t: (len(refs[t] - placed), t))
        for target in sorted(refs[pick] - placed):
            deferred.add((pick, target))
        order.append(pick)
        placed.add(pick)
        remaining.discard(pick)

    return Ordering(order=order, deferred=deferred)


_Triple = Tuple[rdflib.URIRef, rdflib.URIRef, rdflib.URIRef]


@dataclass
class SplitReferences:
    """The outcome of classifying a term's inter-term references.

    Attributes:
        kept: the assertion graph to mint now — non-reference triples, references
            already resolved to new thing URIs, and any *dangling* references
            left as-is (so the term still mints).
        deferred: reference triples (old-id) whose target is in the batch but not
            yet minted (cycle back-edges); re-added later by superseding.
        dangling: reference triples whose target is neither resolved nor in the
            batch — an unknown/unmigratable reference. Kept in ``kept`` as-is, but
            reported so the caller can warn (in a full migration this should be
            empty).
    """

    kept: rdflib.Graph
    deferred: List[_Triple] = field(default_factory=list)
    dangling: List[_Triple] = field(default_factory=list)


def split_references(
    assertion: rdflib.Graph,
    *,
    namespace: str,
    subject: rdflib.URIRef,
    resolved_uris: Mapping[str, str],
    batch_targets: Set[str],
) -> SplitReferences:
    """Classify an assertion's inter-term references for minting.

    Each inter-term reference is sorted into one of three cases:

    * target in ``resolved_uris`` (already minted) — rewritten to its new thing
      URI and kept;
    * target in ``batch_targets`` but not yet minted — **deferred** (a cycle
      back-edge), removed from ``kept`` and re-added later by superseding;
    * target neither resolved nor in the batch — **dangling**: kept as-is (old
      id) so the term still mints, and reported for a warning.

    Non-reference triples always pass through to ``kept`` unchanged.
    """
    result = SplitReferences(kept=rdflib.Graph())
    for s, p, o in assertion:
        is_ref = isinstance(o, rdflib.URIRef) and o != subject and str(o).startswith(namespace)
        if not is_ref:
            result.kept.add((s, p, o))
        elif str(o) in resolved_uris:
            result.kept.add((s, p, rdflib.URIRef(resolved_uris[str(o)])))
        elif str(o) in batch_targets:
            result.deferred.append((s, p, o))
        else:
            result.kept.add((s, p, o))
            result.dangling.append((s, p, o))
    return result
