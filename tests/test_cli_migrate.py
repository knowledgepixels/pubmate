import rdflib
from click.testing import CliRunner
from rdflib.namespace import RDFS

from pubmate.cli.migrate import cli
from pubmate.idmap import IdMap

NS = "https://w3id.org/peh/biochementities/"
TERMS = rdflib.Namespace(NS)
PEH = rdflib.Namespace("https://w3id.org/peh/terms/")


def _write(folder, old_id, *, isomer_of=()):
    g = rdflib.Graph()
    s = TERMS[old_id]
    g.add((s, RDFS.label, rdflib.Literal(old_id)))
    g.add((s, RDFS.subClassOf, PEH.BioChemEntity))
    for r in isomer_of:
        g.add((s, PEH.isIsomerOf, TERMS[r]))
    g.serialize(destination=folder / f"{old_id}.ttl", format="turtle")


def test_migrate_dry_run_writes_defining_superseding_and_idmap(tmp_path):
    src = tmp_path / "in"
    src.mkdir()
    # symmetric cycle a <-> b : 2 defining + 1 superseding
    _write(src, "a", isomer_of=["b"])
    _write(src, "b", isomer_of=["a"])
    out = tmp_path / "out"
    idmap = tmp_path / "id-map.tsv"

    result = CliRunner().invoke(
        cli, ["-a", str(src), "--output-dir", str(out), "--id-map-file", str(idmap), "--dry-run"]
    )
    assert result.exit_code == 0, result.output

    trigs = sorted(p.name for p in out.glob("*.trig"))
    assert len(trigs) == 3  # 2 defining + 1 superseding
    assert all(name.startswith("RA") for name in trigs)

    parsed = IdMap.from_tsv(idmap.read_text(encoding="utf-8"))
    assert str(TERMS["a"]) in parsed and str(TERMS["b"]) in parsed
    for entry in parsed:
        assert entry.thing_uri.startswith(f"{NS}RA")
        assert entry.np_uri.startswith("https://w3id.org/np/RA")


def test_migrate_without_keys_requires_dry_run(tmp_path):
    src = tmp_path / "in"
    src.mkdir()
    _write(src, "a")
    result = CliRunner().invoke(cli, ["-a", str(src), "--output-dir", str(tmp_path / "out")])
    assert result.exit_code != 0
    assert "signing keys" in result.output.lower()


def test_migrate_resumes_from_existing_idmap(tmp_path):
    src = tmp_path / "in"
    src.mkdir()
    _write(src, "a")
    out = tmp_path / "out"
    idmap = tmp_path / "id-map.tsv"
    # Pre-seed so the single term is treated as already minted.
    seed = IdMap.from_tsv(
        "old_id\tthing_uri\tnp_uri\n"
        + f"{TERMS['a']}\t{NS}RAseed\thttps://w3id.org/np/RAseed"
    )
    seed.write_tsv(idmap)

    result = CliRunner().invoke(
        cli, ["-a", str(src), "--output-dir", str(out), "--id-map-file", str(idmap), "--dry-run"]
    )
    assert result.exit_code == 0, result.output
    assert list(out.glob("*.trig")) == []  # nothing newly minted
