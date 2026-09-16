# taxcalc-ai/src/taxcalc_ai/dags/__init__.py
"""Airflow DAG definitions for the sidecar's ingest pipeline.

A package inside ``src/taxcalc_ai`` rather than a top-level ``dags/`` directory, because the
DAG's tasks import this project's own modules. Airflow's convention of a flat ``dags/`` folder
on ``PYTHONPATH`` works for self-contained DAGs and breaks for one that is the orchestration
layer over an installed package: the file ends up either duplicating the pipeline logic or
manipulating ``sys.path`` to find it. Keeping the DAG inside the package means ``uv sync``
installs it, ``mypy --strict`` type-checks it, and the import check in CI is a one-liner.

Deployment mounts or syncs this directory into ``AIRFLOW__CORE__DAGS_FOLDER``; nothing here
depends on the DAG file's location on disk.
"""
