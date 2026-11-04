"""Classify raw AnonNet product descriptions into broad, reviewable categories."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import torch
from transformers import pipeline


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "dataset" / "nodes_anon.csv"
DEFAULT_OUTPUT = ROOT / "dataset" / "derived" / "nodes_anon_categorized.csv"
DEFAULT_CHECKPOINT = ROOT / "dataset" / "derived" / "anonnet_category_checkpoint.jsonl"
MODEL_ID = "MoritzLaurer/deberta-v3-base-zeroshot-v2.0"
MODEL_REVISION = "8e7e5af5983a0ddb1a5b45a38b129ab69e2258e8"
MODEL_DIR = ROOT / "models" / "deberta-v3-base-zeroshot-v2.0"
MODEL_WEIGHTS = MODEL_DIR / "model.safetensors"

# Candidate labels describe broad product or service families. They are a
# project taxonomy, not verified supplier capabilities or standardized codes.
CATEGORIES = {
    "METALS": "metals and metal products, including steel, aluminum, lead, and metal parts",
    "CHEMICALS": "chemicals and industrial chemical products, including chemical raw materials, gases, coatings, and fertilizers",
    "PLASTICS_RUBBER": "plastics and rubber, including resins, rubber materials, and plastic products",
    "MINERALS_BUILDING": "minerals and building materials, including cement, glass, ceramics, and stone",
    "FORESTRY_PAPER": "forestry, wood, pulp, paper, and paper products",
    "AGRICULTURE_FOOD": "agriculture, food, beverages, and food ingredients",
    "TEXTILES_APPAREL": "textiles, fibers, fabrics, apparel, footwear, and leather products",
    "ELECTRONICS": "electronics, semiconductors, sensors, circuit boards, and electronic components",
    "ELECTRICAL_EQUIPMENT": "electrical equipment, including motors, transformers, batteries, and power equipment",
    "MACHINERY_EQUIPMENT": "machinery and industrial equipment, including pumps, valves, and production machinery",
    "VEHICLES_TRANSPORT_EQUIPMENT": "vehicles and transport equipment, including automotive parts, ships, and aircraft equipment",
    "HEALTHCARE_PRODUCTS": "pharmaceuticals, medical devices, and healthcare consumables",
    "ENERGY_FUELS": "energy and fuels, including oil, gas, coal, electricity, and fuel products",
    "SOFTWARE_IT": "software products and information technology services",
    "LOGISTICS_SERVICES": "logistics, freight, transportation, and warehousing services",
    "FINANCIAL_SERVICES": "financial services, including asset management, insurance, and banking",
    "BUSINESS_SERVICES": "business and professional services, including consulting, human resources, and industrial support services",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalize_country(raw: str | None) -> str:
    value = (raw or "").strip()
    if not value or value.casefold() in {"none", "null", "unknown", "n/a", "na"}:
        return "None"
    return value


def classify(
    descriptions: list[str],
    batch_size: int,
    input_hash: str,
    checkpoint_path: Path,
):
    if not torch.backends.mps.is_available():
        raise RuntimeError("MPS is unavailable in this process; run outside a sandbox with Metal access.")
    if not MODEL_WEIGHTS.is_file():
        from huggingface_hub import snapshot_download

        snapshot_download(
            repo_id=MODEL_ID,
            revision=MODEL_REVISION,
            local_dir=MODEL_DIR,
        )

    model_revision = MODEL_REVISION
    model_weights_sha256 = sha256(MODEL_WEIGHTS)
    checkpoint_meta = {
        "inputSha256": input_hash,
        "model": MODEL_ID,
        "modelPath": str(MODEL_DIR.relative_to(ROOT)),
        "modelRevision": model_revision,
        "modelWeightsSha256": model_weights_sha256,
        "taxonomyVersion": "1.0",
        "categories": CATEGORIES,
    }
    results: dict[str, dict[str, object]] = {}
    if checkpoint_path.exists():
        with checkpoint_path.open(encoding="utf-8") as checkpoint:
            header = json.loads(next(checkpoint))
            if header != {"metadata": checkpoint_meta}:
                raise ValueError(f"Checkpoint does not match current input/model/taxonomy: {checkpoint_path}")
            for line in checkpoint:
                item = json.loads(line)
                results[item["description"]] = item["prediction"]
    else:
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        with checkpoint_path.open("w", encoding="utf-8") as checkpoint:
            checkpoint.write(json.dumps({"metadata": checkpoint_meta}, ensure_ascii=False) + "\n")

    pending = [description for description in descriptions if description not in results]
    if not pending:
        return results, model_revision, model_weights_sha256

    classifier = pipeline(
        "zero-shot-classification",
        model=str(MODEL_DIR),
        tokenizer=str(MODEL_DIR),
        device=torch.device("mps"),
    )
    labels = list(CATEGORIES.values())
    label_to_id = {label: category_id for category_id, label in CATEGORIES.items()}
    for start in range(0, len(pending), batch_size):
        batch = pending[start : start + batch_size]
        predictions = classifier(
            batch,
            candidate_labels=labels,
            hypothesis_template="This company's product or service description is about {}.",
            multi_label=False,
            batch_size=batch_size,
            truncation=True,
        )
        if isinstance(predictions, dict):
            predictions = [predictions]
        for description, prediction in zip(batch, predictions, strict=True):
            ranked = [
                {"category": label_to_id[label], "score": float(score)}
                for label, score in zip(prediction["labels"], prediction["scores"], strict=True)
            ]
            results[description] = {
                "category": ranked[0]["category"],
                "score": ranked[0]["score"],
                "alternatives": ranked[1:3],
            }
        completed = len(results)
        with checkpoint_path.open("a", encoding="utf-8") as checkpoint:
            for description in batch:
                checkpoint.write(json.dumps({
                    "description": description,
                    "prediction": results[description],
                }, ensure_ascii=False) + "\n")
        if completed == len(descriptions) or completed % 400 == 0:
            print(f"Classified {completed}/{len(descriptions)} descriptions", flush=True)
    return results, model_revision, model_weights_sha256


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--batch-size", type=int, default=8)
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")

    with args.input.open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames is None or not {"id", "location", "products"}.issubset(reader.fieldnames):
            raise ValueError("Expected CSV columns: id, location, products")
        original_fields = list(reader.fieldnames)
        rows = list(reader)

    descriptions = list(dict.fromkeys(
        (row.get("products") or "").strip()
        for row in rows
        if (row.get("products") or "").strip()
    ))
    input_hash = sha256(args.input)
    checkpoint_path = args.output.with_name(DEFAULT_CHECKPOINT.name)
    predictions, model_revision, model_weights_sha256 = classify(
        descriptions, args.batch_size, input_hash, checkpoint_path
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    output_fields = original_fields + ["country", "product_category"]
    counts: Counter[str] = Counter()
    missing_products = 0
    missing_countries = 0
    with args.output.open("w", newline="", encoding="utf-8") as target:
        writer = csv.DictWriter(target, fieldnames=output_fields)
        writer.writeheader()
        for row in rows:
            product_text = (row.get("products") or "").strip()
            if not product_text:
                category = "None"
                missing_products += 1
            else:
                category = predictions[product_text]["category"]
                counts[category] += 1
            country = normalize_country(row.get("location"))
            missing_countries += country == "None"
            writer.writerow({
                **row,
                "country": country,
                "product_category": category,
            })

    details_path = args.output.with_suffix(".prediction_details.json")
    details_path.write_text(
        json.dumps(predictions, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    metadata = {
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "input": str(args.input.relative_to(ROOT)) if args.input.is_relative_to(ROOT) else str(args.input),
        "inputSha256": input_hash,
        "output": str(args.output.relative_to(ROOT)) if args.output.is_relative_to(ROOT) else str(args.output),
        "model": MODEL_ID,
        "modelPath": str(MODEL_DIR.relative_to(ROOT)),
        "modelRevision": model_revision,
        "modelWeightsSha256": model_weights_sha256,
        "device": "mps",
        "taxonomyVersion": "1.0",
        "categories": CATEGORIES,
        "assignmentStrategy": "top-1 across the fixed taxonomy; score is normalized across candidate labels",
        "emptyProducts": "None",
        "countryDerivation": "trimmed source location label; empty and explicit unknown tokens are None",
        "countryNote": "This is the source location label, not a verified or complete company facility footprint.",
        "nonemptyDescriptionsClassified": len(descriptions),
        "missingProducts": missing_products,
        "missingCountries": missing_countries,
        "rowCount": len(rows),
        "categoryAssignmentCounts": counts,
        "scoreNote": "Scores are relative model outputs, not calibrated probabilities or evidence of production capability. See prediction_details for the top alternatives.",
    }
    metadata_path = args.output.with_suffix(".metadata.json")
    metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    checkpoint_path.unlink(missing_ok=True)
    print(json.dumps({
        "output": str(args.output),
        "metadata": str(metadata_path),
        "predictionDetails": str(details_path),
        "rows": len(rows),
        "uniqueNonemptyDescriptions": len(descriptions),
        "missingProducts": missing_products,
        "missingCountries": missing_countries,
        "categoryAssignmentCounts": counts,
        "device": "mps",
        "modelRevision": model_revision,
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
