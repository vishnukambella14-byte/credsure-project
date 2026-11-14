"""CLI: ``python -m app.datagen generate|load`` (api_contract §3, run from ``backend/``)."""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from app.core.config import get_settings
from app.datagen import config as cfg
from app.datagen.generator import generate_valid, write_outputs, write_synthetic_keys
from app.datagen.injector import inject


@dataclass(frozen=True)
class GenerationResult:
    manifest: dict
    generated_dir: Path
    ground_truth_dir: Path
    key_files: list[Path]


def generate(
    profile_name: str,
    seed: int = cfg.SEED,
    *,
    generated_root: Path = cfg.GENERATED_ROOT,
    ground_truth_root: Path = cfg.GROUND_TRUTH_ROOT,
    synthetic_key_dir: Path | None = None,
    platform_key_dir: Path | None = None,
) -> GenerationResult:
    """Valid dataset first, then A1–A4 injection; writes inputs, ground truth, manifest and keys."""
    settings = get_settings()
    profile = cfg.PROFILES[profile_name]
    rng = random.Random(seed)
    valid = generate_valid(profile, rng)
    injected, labels = inject(valid, profile, rng)
    generated_dir, ground_truth_dir = cfg.profile_dirs(profile_name, generated_root, ground_truth_root)
    manifest = write_outputs(injected, labels, profile, seed, generated_dir, ground_truth_dir)
    keys = write_synthetic_keys(
        injected, profile_name,
        synthetic_key_dir or Path(settings.synthetic_key_dir),
        platform_key_dir or Path(settings.signing_key_dir),
    )
    return GenerationResult(manifest, generated_dir, ground_truth_dir, keys)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.datagen")
    commands = parser.add_subparsers(dest="command", required=True)
    gen = commands.add_parser("generate", help="generate a synthetic dataset")
    gen.add_argument("--profile", choices=sorted(cfg.PROFILES), required=True)
    gen.add_argument("--seed", type=int, default=cfg.SEED)
    load = commands.add_parser("load", help="load a generated dataset into the configured database")
    load.add_argument("--profile", choices=sorted(cfg.PROFILES), required=True)
    load.add_argument("--reset", action="store_true", help="recreate the schema before loading")
    args = parser.parse_args(argv)

    started = time.perf_counter()
    if args.command == "generate":
        result = generate(args.profile, args.seed)
        print(json.dumps({"generated": str(result.generated_dir), "ground_truth": str(result.ground_truth_dir),
                          "keys": [str(p) for p in result.key_files], "row_counts": result.manifest["row_counts"],
                          "anomaly_counts": result.manifest["anomaly_counts"]}, indent=2))
    else:
        from app.database.session import SessionLocal, WriteSessionLocal, engine
        from app.datagen.loader import LoadError, load_dataset, reset_schema
        from app.services.crypto import default_key_store

        try:
            if args.reset:
                reset_schema(engine)
            report = load_dataset(args.profile, write_factory=WriteSessionLocal, read_factory=SessionLocal,
                                  key_store=default_key_store(synthetic_profile=args.profile))
        except LoadError as error:
            print(f"load failed: {error}", file=sys.stderr)
            return 1
        print(json.dumps(report.summary(), indent=2))
    print(f"done in {time.perf_counter() - started:.1f}s", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
