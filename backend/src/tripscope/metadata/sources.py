"""Register manifest sources and datasets in PostgreSQL (shared by the API and the pipeline; no Spark)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from tripscope.metadata.models import Dataset, DataSource
from tripscope.pipeline.manifest import Manifest, SourceSpec


def register_source(session: Session, manifest: Manifest, source: SourceSpec) -> DataSource:
    """Upsert the dataset and data-source rows for a manifest entry. Returns the attached DataSource."""
    spec = manifest.datasets[source.dataset]
    dataset = session.get(Dataset, source.dataset)
    if dataset is None:
        dataset = Dataset(id=source.dataset)
        session.add(dataset)
    dataset.name, dataset.taxi_type = spec.name, spec.taxi_type
    dataset.description, dataset.source_attribution = spec.description, spec.source_attribution

    data_source = session.scalar(select(DataSource).where(DataSource.source_key == source.key))
    if data_source is None:
        data_source = DataSource(source_key=source.key)
        session.add(data_source)
    data_source.dataset_id = source.dataset
    data_source.taxi_type = spec.taxi_type
    data_source.data_period = source.period_start
    data_source.source_uri = source.uri
    data_source.file_name = source.file_name
    data_source.file_format = source.format
    session.flush()
    return data_source
