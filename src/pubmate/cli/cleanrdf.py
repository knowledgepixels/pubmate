import logging
import click

from pubmate import rdfcleaner as rdfcleaner


logging.basicConfig(level=logging.INFO, format="::%(levelname)s:: %(message)s")
logger = logging.getLogger(__name__)


@click.command()
@click.option("--input-ontology-path", required=True)
@click.option("--base-namespace", required=True)
@click.option("--term-output-path", required=True)
@click.option("--term-parent-class", required=True)
@click.option(
    "--parent-subclasses", multiple=True, help="Additional parent classes whose subclasses should be included."
)
def cli(
    input_ontology_path: str,
    base_namespace: str,
    term_output_path: str,
    term_parent_class: str,
    parent_subclasses: list[str] | None = None,
):
    g = rdfcleaner.read_graph(input_ontology_path)
    property_map = {
        "label": "http://www.w3.org/2000/01/rdf-schema#label",
        "name": "http://www.w3.org/2000/01/rdf-schema#label",
        "short_name": "http://schema.org/alternateName",
    }
    rdfcleaner.clean_graph(g, base_namespace=base_namespace, property_map=property_map)

    counter = 0
    if parent_subclasses is None:
        parent_subclasses = set()
    else:
        parent_subclasses = set(parent_subclasses)
    parent_subclasses.add(term_parent_class)

    for term_id, assertion in rdfcleaner.split_into_assertions(g, parent_subclasses):
        output_path = f"{term_output_path}/{term_id}.ttl"
        rdfcleaner.serialize_graph(assertion, output_path)
        counter += 1

    logger.info(f"Processing complete: serialized {counter} assertions")


if __name__ == "__main__":
    cli()
