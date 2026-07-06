import pytest

from pubmate.idmap import IdMap, IdMapEntry
from pubmate.minting import MintBatch, MintedTerm


def _entry(old="alpha", thing="https://example.org/terms/RAa", np="https://w3id.org/np/RAa"):
    return IdMapEntry(old_id=old, thing_uri=thing, np_uri=np)


def test_from_batch_uses_term_id_as_old_id():
    batch = MintBatch(
        terms=[
            MintedTerm("alpha", "https://example.org/terms/RAa", "https://w3id.org/np/RAa", None),
            MintedTerm("beta", "https://example.org/terms/RAb", "https://w3id.org/np/RAb", None),
        ]
    )
    id_map = IdMap.from_batch(batch)
    assert len(id_map) == 2
    assert id_map["alpha"].thing_uri == "https://example.org/terms/RAa"
    assert id_map.np_uri_map == {"alpha": "https://w3id.org/np/RAa", "beta": "https://w3id.org/np/RAb"}


def test_add_idempotent_but_rejects_conflict():
    id_map = IdMap([_entry()])
    id_map.add(_entry())  # identical -> fine
    with pytest.raises(ValueError, match="conflicting id-map entry"):
        id_map.add(_entry(thing="https://example.org/terms/RAdifferent"))
    # overwrite allows replacement
    id_map.add(_entry(thing="https://example.org/terms/RAdifferent"), overwrite=True)
    assert id_map["alpha"].thing_uri == "https://example.org/terms/RAdifferent"


def test_merge_preserves_existing_and_adds_new():
    base = IdMap([_entry("alpha")])
    incoming = IdMap([_entry("beta", "https://example.org/terms/RAb", "https://w3id.org/np/RAb")])
    base.merge(incoming)
    assert set(base.thing_uri_map) == {"alpha", "beta"}


def test_tsv_roundtrip_and_header():
    id_map = IdMap([_entry("b"), _entry("a")])
    tsv = id_map.to_tsv()
    assert tsv.splitlines()[0] == "old_id\tthing_uri\tnp_uri\tfingerprint"
    # entries are sorted by old_id
    assert tsv.splitlines()[1].startswith("a\t")
    assert IdMap.from_tsv(tsv).thing_uri_map == id_map.thing_uri_map


def test_tsv_roundtrips_fingerprint():
    id_map = IdMap([IdMapEntry("a", "https://example.org/terms/RAa", "https://w3id.org/np/RAa", "deadbeef")])
    restored = IdMap.from_tsv(id_map.to_tsv())
    assert restored["a"].fingerprint == "deadbeef"
    assert restored.fingerprint_map == {"a": "deadbeef"}


def test_from_tsv_reads_legacy_three_column_rows():
    legacy = "old_id\tthing_uri\tnp_uri\nalpha\thttps://example.org/terms/RAa\thttps://w3id.org/np/RAa\n"
    id_map = IdMap.from_tsv(legacy)
    assert id_map["alpha"].np_uri == "https://w3id.org/np/RAa"
    assert id_map["alpha"].fingerprint == ""


def test_json_roundtrip():
    id_map = IdMap([_entry("a"), _entry("b", "https://example.org/terms/RAb", "https://w3id.org/np/RAb")])
    assert {e.old_id for e in IdMap.from_json(id_map.to_json())} == {"a", "b"}


def test_from_tsv_rejects_malformed_rows():
    with pytest.raises(ValueError, match="expected 3 or 4 tab-separated fields"):
        IdMap.from_tsv("old_id\tthing_uri\tnp_uri\nalpha\tonly-two-fields\n")


def test_file_roundtrip(tmp_path):
    id_map = IdMap([_entry("a"), _entry("b", "https://example.org/terms/RAb", "https://w3id.org/np/RAb")])
    tsv_path = tmp_path / "id-map.tsv"
    json_path = tmp_path / "id-map.json"
    id_map.write_tsv(tsv_path)
    id_map.write_json(json_path)
    assert IdMap.read_tsv(tsv_path).np_uri_map == id_map.np_uri_map
    assert IdMap.read_json(json_path).np_uri_map == id_map.np_uri_map
