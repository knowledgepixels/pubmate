"""Migration orchestrator: mint a self-referential term set to nanopub ids.

Drives the one-time migration of an existing vocabulary (terms that reference
each other by their old ids) to nanopub-based identifiers:

1. Build the inter-term dependency graph and a cycle-aware mint order
   (:mod:`pubmate.references`).
2. Mint each term's **defining** nanopub in that order, rewriting its references
   to already-minted terms' new thing URIs inline (the ``np sign -r`` pattern);
   references whose target is not yet minted (cycle back-edges) are held back.
3. For each term that had held-back links, publish a **superseding** nanopub that
   re-states the term against its fixed new URI with *all* references resolved.

The result is the full id-map plus the defining and superseding nanopubs. I/O
(reading assertions, writing trig/id-map) is left to the caller/CLI.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Set, Tuple

import nanopub
import rdflib
from rdflib.namespace import DCTERMS

from pubmate._nanopub_build import preferred_label
from pubmate.defining import DefiningNanopubBuilder
from pubmate.idmap import IdMap, IdMapEntry
from pubmate.minting import MintBatch, MintedTerm, SequentialMinter, term_input_from_assertion
from pubmate.rdf2nanopub import sign_and_publish
from pubmate.references import order_terms, referenced_terms, split_references
from pubmate.supersede import SupersessionBuilder

logger = logging.getLogger(__name__)


@dataclass
class MintedSupersession:
    """A superseding nanopub minted to add a term's held-back links."""

    term_id: str
    supersedes_np_uri: str
    np_uri: str
    nanopub: nanopub.Nanopub


@dataclass
class MigrationResult:
    """The outcome of a migration run."""

    defining: MintBatch = field(default_factory=MintBatch)
    superseding: List[MintedSupersession] = field(default_factory=list)
    id_map: IdMap = field(default_factory=IdMap)
    deferred_edges: Set[Tuple[str, str]] = field(default_factory=set)


def _resolve_all(
    assertion: rdflib.Graph,
    *,
    namespace: str,
    subject: rdflib.URIRef,
    new_subject: rdflib.URIRef,
    thing_uris: Mapping[str, str],
    part_of: Optional[str] = None,
) -> rdflib.Graph:
    """Re-state an assertion against the term's fixed URI with all refs resolved.

    Rewrites the subject (old URI -> ``new_subject``) and every object URI that
    is a known old term id (-> its new thing URI). Used to build the superseding
    nanopub's full assertion once every term has been minted. Dangling term
    references (in-namespace, not resolvable) are dropped — consistent with the
    defining pass — so a superseding nanopub never reintroduces a broken link.

    If ``part_of`` is given, the term's ``dcterms:isPartOf`` link is (re)added, so
    the superseding assertion carries it just like the defining one.
    """
    out = rdflib.Graph()
    for s, p, o in assertion:
        ns = new_subject if s == subject else s
        if isinstance(o, rdflib.URIRef):
            if o == subject:
                no: rdflib.term.Node = new_subject
            elif str(o) in thing_uris:
                no = rdflib.URIRef(thing_uris[str(o)])
            elif str(o).startswith(namespace):
                continue  # dangling term reference -> drop
            else:
                no = o
        else:
            no = o
        out.add((ns, p, no))
    if part_of:
        out.add((new_subject, DCTERMS.isPartOf, rdflib.URIRef(part_of)))
    return out


def migrate_terms(
    assertions: Mapping[str, rdflib.Graph],
    *,
    namespace: str,
    minter: SequentialMinter,
    supersession_builder: SupersessionBuilder,
    existing: Optional[IdMap] = None,
    dry_run: bool = True,
    part_of: Optional[str] = None,
) -> MigrationResult:
    """Migrate a batch of cross-referencing term assertions to nanopub ids.

    Args:
        assertions: ``old_term_uri -> assertion graph``. Each graph's single
            namespaced subject must be that old term URI; references to other
            terms use their old URIs.
        namespace: the term namespace (e.g. ``…/biochementities/``).
        minter: a :class:`~pubmate.minting.SequentialMinter` (carries the
            defining-nanopub builder + signing profile).
        supersession_builder: a :class:`~pubmate.supersede.SupersessionBuilder`
            configured with the *same* signing profile, used to add held-back
            (cyclic) links.
        existing: an id-map of terms already minted in a previous run; these are
            not re-minted, but their URIs are used to resolve references.
        dry_run: sign only (offline), do not publish.
        part_of: if given, every term gets a ``dcterms:isPartOf`` link to this URI
            (e.g. the vocabulary) in both its defining and superseding assertion.
    """
    subjects: Dict[str, rdflib.URIRef] = {old: rdflib.URIRef(old) for old in assertions}
    batch_ids = set(assertions)

    # In-batch references only (out-of-batch targets impose no ordering constraint).
    refs: Dict[str, Set[str]] = {
        old: {r for r in referenced_terms(g, namespace=namespace, subject=subjects[old]) if r in batch_ids}
        for old, g in assertions.items()
    }
    ordering = order_terms(refs)
    logger.info(
        "Ordered %d terms; %d reference(s) deferred to superseding.",
        len(ordering.order),
        len(ordering.deferred),
    )

    result = MigrationResult(id_map=IdMap(list(existing or [])), deferred_edges=ordering.deferred)
    resolved_thing: Dict[str, str] = dict(result.id_map.thing_uri_map)
    minted_by_term: Dict[str, MintedTerm] = {}
    deferred_by_term: Dict[str, list] = {}
    dangling_count = 0

    # -- pass 1: defining nanopubs, references resolved inline --------------
    for old in ordering.order:
        if old in result.id_map:
            logger.info("Skipping already-minted term: %s", old)
            continue
        g = assertions[old]
        split = split_references(
            g, namespace=namespace, subject=subjects[old], resolved_uris=resolved_thing,
            batch_targets=batch_ids,
        )
        if split.dangling:
            dangling_count += len(split.dangling)
            logger.error(
                "%s: %d reference(s) to terms absent from the batch (dangling foreign "
                "key, likely a stale/deduplicated id); dropped from the minted nanopub: %s",
                old, len(split.dangling), [str(o) for _, _, o in split.dangling],
            )
        term = term_input_from_assertion(
            split.kept, namespace=namespace, thing_uri=minter.builder.thing_uri, part_of=part_of,
        )
        minted = minter.mint(term, dry_run=dry_run)
        result.defining.terms.append(minted)
        result.id_map.add(
            IdMapEntry(old_id=old, thing_uri=minted.thing_uri, np_uri=minted.np_uri), overwrite=True
        )
        resolved_thing[old] = minted.thing_uri
        minted_by_term[old] = minted
        if split.deferred:
            deferred_by_term[old] = split.deferred

    if dangling_count:
        logger.error(
            "%d dangling reference(s) dropped across the batch; expected 0 in a clean "
            "migration (every referenced term present). Fix the source data and re-run.",
            dangling_count,
        )

    # -- pass 2: supersede the terms that had held-back links ---------------
    for old, _deferred in deferred_by_term.items():
        minted = minted_by_term[old]
        new_subject = rdflib.URIRef(minted.thing_uri)
        full = _resolve_all(
            assertions[old], namespace=namespace, subject=subjects[old],
            new_subject=new_subject, thing_uris=resolved_thing, part_of=part_of,
        )
        sup_np = supersession_builder.build(
            full, supersedes_np_uri=minted.np_uri, label=_label(full, new_subject),
            suggester_orcid=minter.default_suggester_orcid,
        )
        sup_uri = sign_and_publish(sup_np, dry_run=dry_run)
        logger.info("Superseded %s (%s) -> %s", old, minted.np_uri, sup_uri)
        result.superseding.append(
            MintedSupersession(term_id=old, supersedes_np_uri=minted.np_uri, np_uri=sup_uri, nanopub=sup_np)
        )

    return result


def _label(graph: rdflib.Graph, subject: rdflib.URIRef) -> Optional[str]:
    return preferred_label(graph, subject)
