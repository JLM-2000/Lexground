from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from sqlalchemy import text

from lexground.config import get_settings
from lexground.db.models import Base
from lexground.db.session import dispose_engine, init_engine, session_scope
from lexground.evaluation.golden import load_golden_set
from lexground.evaluation.harness import EvaluationHarness, Thresholds
from lexground.evaluation.judge import GroundednessJudge
from lexground.ingest.fetch import EurLexClient
from lexground.ingest.runner import CorpusManifest, Ingestor, index_version
from lexground.main import build_query_service
from lexground.observability.logging import configure_logging
from lexground.retrieval.embedder import get_embedder

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MANIFEST = REPO_ROOT / "data" / "corpus.json"
DEFAULT_GOLDEN = REPO_ROOT / "data" / "golden" / "cases.jsonl"
DEFAULT_THRESHOLDS = REPO_ROOT / "data" / "thresholds.json"
DEFAULT_CACHE = REPO_ROOT / "data" / "corpus"


async def init_db() -> int:
    settings = get_settings()
    engine = init_engine(settings)
    async with engine.begin() as connection:
        await connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await connection.run_sync(Base.metadata.create_all)
    await dispose_engine()
    print("schema ready")
    return 0


async def ingest(manifest_path: Path, cache_dir: Path, offline: bool) -> int:
    settings = get_settings()
    init_engine(settings)
    manifest = CorpusManifest.load(manifest_path)
    ingestor = Ingestor(
        embedder=get_embedder(settings),
        client=EurLexClient(cache_dir=cache_dir, offline=offline),
    )
    async with session_scope() as session:
        summary = await ingestor.ingest_manifest(session, manifest)
    await dispose_engine()

    print(f"ingested {summary.documents} documents, {summary.chunks} chunks")
    for skip in summary.skipped:
        print(f"  skipped {skip}", file=sys.stderr)
    return 1 if summary.chunks == 0 else 0


async def evaluate(
    golden_path: Path,
    thresholds_path: Path,
    manifest_path: Path,
    report_path: Path | None,
    use_judge: bool,
) -> int:
    settings = get_settings()
    init_engine(settings)

    cases = load_golden_set(golden_path)
    thresholds = Thresholds.load(thresholds_path)
    manifest = CorpusManifest.load(manifest_path)
    version = index_version(manifest, settings.embedding_model)

    judge = None
    if use_judge:
        if not settings.anthropic_api_key:
            print("judge requested but no API key configured; skipping", file=sys.stderr)
        else:
            judge = GroundednessJudge(settings)

    harness = EvaluationHarness(build_query_service(settings), thresholds, judge)
    async with session_scope() as session:
        report = await harness.run(
            session,
            cases,
            index_version=version,
            git_sha=os.environ.get("GITHUB_SHA"),
        )
    await dispose_engine()

    print(report.render())
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(
                {
                    "index_version": version,
                    "metrics": report.metrics,
                    "passed": report.passed,
                    "failures": report.failures,
                    "cases": report.per_case,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    return 0 if report.passed else 1


def main() -> int:
    configure_logging()
    parser = argparse.ArgumentParser(prog="lexground")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init-db", help="create the schema and pgvector extension")

    ingest_parser = subparsers.add_parser("ingest", help="fetch, chunk and index the corpus")
    ingest_parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    ingest_parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    ingest_parser.add_argument(
        "--offline", action="store_true", help="use only the cached corpus, never the network"
    )

    eval_parser = subparsers.add_parser("evaluate", help="run the golden set and apply the gate")
    eval_parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    eval_parser.add_argument("--thresholds", type=Path, default=DEFAULT_THRESHOLDS)
    eval_parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    eval_parser.add_argument("--report", type=Path, default=None)
    eval_parser.add_argument(
        "--judge", action="store_true", help="also score groundedness with the judge model"
    )

    args = parser.parse_args()

    if args.command == "init-db":
        return asyncio.run(init_db())
    if args.command == "ingest":
        return asyncio.run(ingest(args.manifest, args.cache_dir, args.offline))
    return asyncio.run(
        evaluate(args.golden, args.thresholds, args.manifest, args.report, args.judge)
    )


if __name__ == "__main__":
    raise SystemExit(main())
