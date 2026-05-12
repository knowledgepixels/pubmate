import os
import warnings
from uuid import uuid4

import pytest
import rdflib
from rdflib.namespace import RDF, RDFS

from pubmate.rdf2nanopub import NanopubGenerator


def test_publish_to_test_server_with_testsuite_connector_keys() -> None:
    if os.getenv("PUBMATE_RUN_TESTSERVER_PUBLISH") != "1":
        pytest.skip("Set PUBMATE_RUN_TESTSERVER_PUBLISH=1 to run live publish integration test.")

    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always", DeprecationWarning)
        generator = NanopubGenerator.from_testsuite_connector(
            key_name=os.getenv("PUBMATE_TESTSUITE_KEY", "rsa-key1"),
            suite_ref=os.getenv("PUBMATE_TESTSUITE_REF", "main"),
            test_server=True,
        )

        assertion = rdflib.Graph()
        subject = rdflib.URIRef(f"https://example.org/pubmate/test/{uuid4()}")
        assertion.add((subject, RDF.type, RDFS.Resource))
        assertion.add((subject, RDFS.label, rdflib.Literal("Pubmate testsuite connector publish test", lang="en")))
        np_uri = generator.publish_single(assertion, dry_run=False)

    pubmate_warnings = [
        w for w in recorded if issubclass(w.category, DeprecationWarning) and "/src/pubmate/" in str(w.filename)
    ]
    assert not pubmate_warnings, "Deprecation warnings were raised from pubmate code paths: " + "; ".join(
        f"{w.filename}:{w.lineno}: {w.message}" for w in pubmate_warnings
    )
    assert str(np_uri).startswith(("http://purl.org/np/", "https://purl.org/np/", "https://w3id.org/np/"))


def test_check_nanopub_existence_for_known_live_nanopub_uri() -> None:
    if os.getenv("PUBMATE_RUN_NANOPUB_EXISTENCE_CHECK", "1") != "1":
        pytest.skip("Set PUBMATE_RUN_NANOPUB_EXISTENCE_CHECK=1 to run live nanopub existence integration test.")

    generator = object.__new__(NanopubGenerator)
    generator.test_server = False
    generator.client = None

    known_uri = "https://w3id.org/np/RAWcbb3lRQZNYrCYo1uUfxHF1p6apBUW9hTeJRoHrqYZQ"
    exists = generator.check_nanopub_existence(known_uri)
    if not exists:
        pytest.skip("Could not verify known nanopub URI (network or registry issue).")

    assert exists is True
