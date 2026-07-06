"""Incremental mint/supersede publishing with drift detection.

Re-runnable publishing of defining nanopubs. For each term, compare its current
identity fingerprint (:mod:`pubmate.fingerprint`) against the one recorded in the
id-map and take one of three actions:

* **new** -- term absent from the id-map: mint a defining nanopub, record its
  thing/nanopub URIs and fingerprint.
* **unchanged** -- fingerprint matches the recorded one: skip (carry the id-map
  entry forward untouched).
* **drifted** -- fingerprint differs: *supersede*. Re-state the changed assertion
  against the term's **existing fixed thing URI** (so the term keeps its identity),
  with every inter-term reference resolved to its new thing URI via the id-map
  (:func:`pubmate.references.resolve_references`) -- the targets are all minted, so
  this preserves link resolution rather than reverting to old-id references. The
  nanopub ``npx:supersedes`` the recorded one; the id-map's ``np_uri``/
  ``fingerprint`` are repointed at the new version. The thing URI is unchanged --
  only the defining/superseding nanopub URI advances, chained by supersession.

Note the mint path (new terms) still mints the assertion *as given*; reference
resolution is applied on the supersede path, where every referenced term is
guaranteed to be minted already.

Legacy id-map rows carry no fingerprint. Rather than mass-supersede on the first
fingerprinted run, such a term is treated as unchanged and its current fingerprint
is *backfilled* as the baseline (with a warning) -- adopting "what is published
now" as the reference. A genuine wrapper change made before fingerprints existed
must therefore be forced by hand; it cannot be inferred without a prior baseline.

I/O (reading assertions, writing trig/id-map) is left to the caller/CLI, mirroring
:func:`pubmate.migrate.migrate_terms`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

import rdflib

from pubmate._nanopub_build import preferred_label
from pubmate.fingerprint import fingerprint_term
from pubmate.idmap import IdMap, IdMapEntry
from pubmate.migrate import MintedSupersession
from pubmate.minting import MintBatch, SequentialMinter, TermInput
from pubmate.rdf2nanopub import sign_and_publish
from pubmate.references import resolve_references
from pubmate.supersede import SupersessionBuilder

logger = logging.getLogger(__name__)


@dataclass
class IncrementalResult:
    """The outcome of an incremental publish run."""

    minted: MintBatch = field(default_factory=MintBatch)
    superseded: List[MintedSupersession] = field(default_factory=list)
    #: term_ids that were unchanged and skipped (includes backfilled legacy rows).
    skipped: List[str] = field(default_factory=list)
    id_map: IdMap = field(default_factory=IdMap)


def publish_incremental(
    terms: Sequence[TermInput],
    *,
    minter: SequentialMinter,
    supersession_builder: SupersessionBuilder,
    existing: Optional[IdMap] = None,
    dry_run: bool = True,
) -> IncrementalResult:
    """Mint new terms, skip unchanged ones, and supersede drifted ones.

    Args:
        terms: the terms to publish (assertions keyed on the builder's placeholder
            thing URI, e.g. from ``term_input_from_assertion``).
        minter: a :class:`~pubmate.minting.SequentialMinter` carrying the defining
            builder + signing profile.
        supersession_builder: a :class:`~pubmate.supersede.SupersessionBuilder`
            configured with the *same* signing profile/wrapper, used for drifted
            terms.
        existing: id-map from previous runs (fingerprints included where known).
        dry_run: sign only (offline), do not publish.

    Returns an :class:`IncrementalResult` whose ``id_map`` is the complete,
    updated map (existing entries carried forward, plus new/superseded/backfilled).
    """
    result = IncrementalResult(id_map=IdMap(list(existing or [])))
    placeholder = minter.builder.thing_uri
    default_suggester = minter.default_suggester_orcid

    for term in terms:
        current_fp = fingerprint_term(term, minter.builder, default_suggester=default_suggester)

        if term.term_id not in result.id_map:
            minted = minter.mint(term, dry_run=dry_run)
            result.id_map.add(
                IdMapEntry(term.term_id, minted.thing_uri, minted.np_uri, current_fp),
                overwrite=True,
            )
            result.minted.terms.append(minted)
            continue

        entry = result.id_map[term.term_id]

        if entry.fingerprint == "":
            logger.warning(
                "Backfilling baseline fingerprint for legacy term (no prior fingerprint "
                "to compare against; adopting current build as baseline): %s",
                term.term_id,
            )
            result.id_map.add(
                IdMapEntry(term.term_id, entry.thing_uri, entry.np_uri, current_fp),
                overwrite=True,
            )
            result.skipped.append(term.term_id)
            continue

        if entry.fingerprint == current_fp:
            logger.info("Skipping unchanged term: %s", term.term_id)
            result.skipped.append(term.term_id)
            continue

        # Drift: supersede against the existing fixed thing URI. Rebuild the
        # assertion from source with every inter-term reference resolved to its
        # new thing URI via the id-map (all targets are already minted), so the
        # re-issued nanopub preserves link resolution instead of reverting to
        # old-id references.
        fixed = rdflib.URIRef(entry.thing_uri)
        full = resolve_references(
            term.assertion,
            namespace=minter.builder.namespace,
            subject=placeholder,
            new_subject=fixed,
            thing_uris=result.id_map.thing_uri_map,
        )
        sup_np = supersession_builder.build(
            full,
            supersedes_np_uri=entry.np_uri,
            label=term.label or preferred_label(full, fixed),
            suggester_orcid=term.suggester_orcid or default_suggester,
            derived_from=term.derived_from,
        )
        sup_uri = sign_and_publish(sup_np, dry_run=dry_run)
        logger.info("Superseded drifted term %s (%s) -> %s", term.term_id, entry.np_uri, sup_uri)
        result.id_map.add(
            IdMapEntry(term.term_id, entry.thing_uri, sup_uri, current_fp),
            overwrite=True,
        )
        result.superseded.append(
            MintedSupersession(
                term_id=term.term_id,
                supersedes_np_uri=entry.np_uri,
                np_uri=sup_uri,
                nanopub=sup_np,
            )
        )

    return result
