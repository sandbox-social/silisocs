"""Prepare scenario-suitable personas from a source dataset using LLM filtering.

Pipeline per source record:
1) Suitability gate (Yes/No): keep only personas useful for the scenario.
2) Processing gate (Yes/No, conservative): rewrite only if necessary.
3) Name extraction: parse a clean agent name.
4) Optional initial attributes from scenario probe definitions (strict choices).
5) Optional social-media profile generation.

Outputs JSONL with both original and processed persona fields.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from datasets import load_dataset, load_from_disk
from openai import OpenAI

YES_SET = {"yes", "y", "true", "1"}
NO_SET = {"no", "n", "false", "0"}


@dataclass(frozen=True)
class ChoiceProbe:
    probe_id: str
    question: str
    choices: list[str]


class PersonaPreparationError(RuntimeError):
    """Raised for invalid configuration or irrecoverable processing errors."""


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Prepare personas for a scenario with LLM filtering"
    )
    parser.add_argument("--dataset", default="nvidia/Nemotron-Personas-USA", help="HF dataset id")
    parser.add_argument(
        "--dataset-path",
        default="",
        help=(
            "Optional local path for a dataset saved with datasets.save_to_disk; "
            "when set, --dataset/--split are ignored"
        ),
    )
    parser.add_argument(
        "--hf-cache-dir",
        default="",
        help="Optional Hugging Face cache directory used when loading --dataset",
    )
    parser.add_argument("--split", default="train", help="HF split")
    parser.add_argument(
        "--max-input",
        type=int,
        default=200,
        help="Maximum number of source dataset records to inspect",
    )
    parser.add_argument(
        "--shuffle-seed",
        type=int,
        default=11,
        help="Random seed used when shuffling source records before truncation",
    )
    parser.add_argument(
        "--scenario-config",
        required=True,
        help="Path to scenario YAML containing event/probe definitions",
    )
    parser.add_argument(
        "--output",
        default="processed_personas.jsonl",
        help="Output JSONL path",
    )
    parser.add_argument(
        "--llm-name",
        default="qwen3.5-4b",
        help="Model name sent to OpenAI-compatible API",
    )
    parser.add_argument(
        "--llm-api-base",
        default="http://localhost:30000/v1",
        help="OpenAI-compatible base URL",
    )
    parser.add_argument(
        "--llm-api-key",
        default=os.environ.get("OPENAI_API_KEY", "test-key"),
        help="OpenAI-compatible API key",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.0,
        help="Sampling temperature for strict outputs",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=2,
        help="Retries for parse-constrained LLM calls",
    )
    parser.add_argument(
        "--attribute-probes",
        default="",
        help=(
            "Comma-separated probe IDs from scenario.probes.queries to infer and store as "
            "initial_attributes (ChoiceProbe only)"
        ),
    )
    parser.add_argument(
        "--inline-probe-id",
        default="",
        help="Optional probe id for a command-line ChoiceProbe (example: vote_choice)",
    )
    parser.add_argument(
        "--inline-question-type",
        default="ChoiceProbe",
        help="Question type for command-line probe (currently only ChoiceProbe is supported)",
    )
    parser.add_argument(
        "--inline-question",
        default="",
        help="Optional question text for a command-line ChoiceProbe",
    )
    parser.add_argument(
        "--inline-choices",
        default="",
        help="Comma-separated choices for command-line ChoiceProbe",
    )
    parser.add_argument(
        "--enable-social-media-profile",
        action="store_true",
        help="Enable social media profile generation step",
    )
    parser.add_argument(
        "--include-raw-record",
        action="store_true",
        help="Include full source record under source_record in each output row",
    )
    parser.add_argument(
        "--num-workers",
        type=int,
        default=1,
        help="Number of parallel worker threads (increase for faster processing; consider API rate limits)",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=50,
        help="Print progress every N completed personas",
    )
    parser.add_argument(
        "--explain-rejected",
        action="store_true",
        help="If set, generate one-sentence LLM reasons for rejected personas and save them to JSON",
    )
    parser.add_argument(
        "--rejected-reasons-output",
        default="",
        help=(
            "Optional JSON path for rejected persona reasons. "
            "Defaults to <output_stem>_rejected_reasons.json when --explain-rejected is enabled"
        ),
    )
    return parser.parse_args()


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise PersonaPreparationError(f"Scenario YAML must parse to a mapping: {path}")
    return data


def _scenario_summary(scenario_cfg: dict[str, Any]) -> str:
    parts: list[str] = []
    event = scenario_cfg.get("event") if isinstance(scenario_cfg.get("event"), dict) else {}
    setting = scenario_cfg.get("setting") if isinstance(scenario_cfg.get("setting"), dict) else {}

    context = str(event.get("context", "")).strip()
    if context:
        parts.append(f"Event context: {context}")

    name = str(setting.get("name", "")).strip()
    if name:
        parts.append(f"Setting name: {name}")

    background = setting.get("background", [])
    if isinstance(background, list):
        bg = " ".join(str(x).strip() for x in background if str(x).strip())
        if bg:
            parts.append(f"Setting background: {bg}")

    return "\n".join(parts).strip()


def _extract_choice_probes(scenario_cfg: dict[str, Any], probe_ids: list[str]) -> list[ChoiceProbe]:
    probes = scenario_cfg.get("probes") if isinstance(scenario_cfg.get("probes"), dict) else {}
    queries = probes.get("queries") if isinstance(probes.get("queries"), dict) else {}

    out: list[ChoiceProbe] = []
    for probe_id in probe_ids:
        raw = queries.get(probe_id)
        if not isinstance(raw, dict):
            raise PersonaPreparationError(
                f"Probe id '{probe_id}' not found in scenario.probes.queries"
            )

        qtype = str(raw.get("query_type", "")).strip()
        if qtype != "ChoiceProbe":
            raise PersonaPreparationError(
                f"Probe id '{probe_id}' is query_type='{qtype}', only ChoiceProbe is supported"
            )

        qdata = raw.get("query_data") if isinstance(raw.get("query_data"), dict) else {}
        question = str(qdata.get("question", "")).strip()
        choices_raw = qdata.get("choices", [])
        if not isinstance(choices_raw, list) or not choices_raw:
            raise PersonaPreparationError(
                f"Probe id '{probe_id}' must define non-empty query_data.choices"
            )

        choices = [str(choice).strip() for choice in choices_raw if str(choice).strip()]
        if not choices:
            raise PersonaPreparationError(f"Probe id '{probe_id}' has no valid choices")

        out.append(ChoiceProbe(probe_id=probe_id, question=question, choices=choices))

    return out


def _extract_inline_choice_probe(args: argparse.Namespace) -> list[ChoiceProbe]:
    probe_id = str(args.inline_probe_id).strip()
    question = str(args.inline_question).strip()
    qtype = str(args.inline_question_type).strip()
    choices_csv = str(args.inline_choices).strip()

    has_any = bool(probe_id or question or choices_csv)
    has_all = bool(probe_id and question and choices_csv)

    if has_any and not has_all:
        raise PersonaPreparationError(
            "Inline probe requires all of --inline-probe-id, --inline-question, and --inline-choices"
        )
    if not has_any:
        return []

    if qtype != "ChoiceProbe":
        raise PersonaPreparationError(
            f"Unsupported --inline-question-type '{qtype}'. Only ChoiceProbe is supported"
        )

    choices = [choice.strip() for choice in choices_csv.split(",") if choice.strip()]
    if not choices:
        raise PersonaPreparationError("--inline-choices must include at least one non-empty choice")

    return [ChoiceProbe(probe_id=probe_id, question=question, choices=choices)]


def _extract_persona_text(record: dict[str, Any]) -> str:
    candidates = [
        "persona",
        "text",
        "profile",
        "description",
        "bio",
        "prompt",
    ]
    for key in candidates:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    for key, value in record.items():
        if isinstance(value, str) and len(value.strip()) > 40:
            return value.strip()

    return ""


def _normalize_yes_no(raw: str) -> str | None:
    cleaned = raw.strip().lower()
    cleaned = re.sub(r"[^a-z0-9 ]+", "", cleaned)
    token = cleaned.split()[0] if cleaned.split() else ""
    if token in YES_SET:
        return "yes"
    if token in NO_SET:
        return "no"
    if "yes" in cleaned:
        return "yes"
    if re.search(r"\bno\b", cleaned):
        return "no"
    return None


def _strict_choice(raw: str, choices: list[str]) -> str | None:
    text = raw.strip().strip('"').strip("'")
    if text in choices:
        return text

    lower_map = {choice.lower(): choice for choice in choices}
    if text.lower() in lower_map:
        return lower_map[text.lower()]

    for choice in choices:
        if choice.lower() in text.lower():
            return choice
    return None


def _first_line(raw: str) -> str:
    line = raw.strip().splitlines()[0] if raw.strip().splitlines() else ""
    line = line.strip().strip('"').strip("'")
    return re.sub(r"\s+", " ", line)


def _chat(client: OpenAI, model: str, system: str, user: str, temperature: float) -> str:
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temperature,
        extra_body={
            "top_k": 20,
            "chat_template_kwargs": {"enable_thinking": False},
        },
    )
    content = response.choices[0].message.content if response.choices else ""
    return str(content or "")


def _ask_yes_no(
    client: OpenAI,
    model: str,
    system: str,
    user: str,
    temperature: float,
    max_retries: int,
) -> str:
    for _ in range(max_retries + 1):
        raw = _chat(client, model, system, user, temperature)
        parsed = _normalize_yes_no(raw)
        if parsed is not None:
            return parsed
    raise PersonaPreparationError("LLM failed to return parseable yes/no answer")


def _ask_choice(
    client: OpenAI,
    model: str,
    system: str,
    user: str,
    choices: list[str],
    temperature: float,
    max_retries: int,
) -> str:
    for _ in range(max_retries + 1):
        raw = _chat(client, model, system, user, temperature)
        parsed = _strict_choice(raw, choices)
        if parsed is not None:
            return parsed
    raise PersonaPreparationError("LLM failed to return a valid strict choice")


def _ask_name(
    client: OpenAI,
    model: str,
    persona: str,
    temperature: float,
    max_retries: int,
) -> str:
    system = (
        "Extract a realistic human full name from the persona text. "
        "Return only the name, no explanation, no punctuation outside the name."
    )
    user = f"Persona:\n{persona}\n\nOutput only the name."

    for _ in range(max_retries + 1):
        raw = _chat(client, model, system, user, temperature)
        name = _first_line(raw)
        if len(name) >= 2 and len(name.split()) <= 5:
            return name
    return "Unknown"


def _minimal_rewrite(
    client: OpenAI,
    model: str,
    scenario_text: str,
    persona: str,
    temperature: float,
) -> str:
    system = (
        "You minimally edit persona text only when required for scenario usability. "
        "Preserve original meaning, tone, demographics, and stable traits. "
        "Do not add unnecessary details. Return only rewritten persona text."
    )
    user = (
        f"Scenario:\n{scenario_text}\n\n"
        f"Original persona:\n{persona}\n\n"
        "Rewrite only if necessary for scenario usability. Keep edits minimal and conservative."
    )
    return _chat(client, model, system, user, temperature).strip()


def _social_media_profile(
    client: OpenAI,
    model: str,
    scenario_text: str,
    persona: str,
    temperature: float,
) -> str:
    system = (
        "Write 2-4 specific sentences describing social media behavior. "
        "Include likely posting, replying, liking, and reposting frequency and content interests. "
        "Do not state whether they are interested in election-related content. "
        "Return only the profile text."
    )
    user = (
        f"Scenario:\n{scenario_text}\n\nPersona:\n{persona}\n\nWrite the social media profile now."
    )
    return _chat(client, model, system, user, temperature).strip()


def _rejection_reason(
    client: OpenAI,
    model: str,
    scenario_text: str,
    persona: str,
    temperature: float,
) -> str:
    system = (
        "You explain persona rejection decisions for simulation setup. "
        "Return exactly one short sentence and no extra text."
    )
    user = (
        f"Scenario:\n{scenario_text}\n\n"
        f"Persona:\n{persona}\n\n"
        "Give one sentence explaining why this persona is not suitable for the scenario."
    )
    reason = _first_line(_chat(client, model, system, user, temperature))
    return reason or "Persona was not suitable for the scenario requirements."


def _prepare_one_with_index(
    args_tuple: tuple[
        int, dict[str, Any], str, str, list[ChoiceProbe], str, str, str, str, str, float, int
    ],
) -> tuple[int, dict[str, Any], str]:
    """Process a single record with worker-local LLM client."""
    (
        idx,
        record,
        scenario_text,
        persona_text,
        choice_probes,
        llm_name,
        llm_api_base,
        llm_api_key,
        enable_social_profile_str,
        explain_rejected_str,
        temperature,
        max_retries,
    ) = args_tuple

    # Create client per worker to avoid thread-safety issues
    client = OpenAI(api_key=llm_api_key, base_url=llm_api_base)
    enable_social_profile = enable_social_profile_str == "true"
    explain_rejected = explain_rejected_str == "true"

    try:
        result = _prepare_one_internal(
            client=client,
            model=llm_name,
            scenario_text=scenario_text,
            persona_text=persona_text,
            choice_probes=choice_probes,
            enable_social_profile=enable_social_profile,
            explain_rejected=explain_rejected,
            temperature=temperature,
            max_retries=max_retries,
        )
        return (idx, result, persona_text)
    except Exception as exc:
        return (idx, {"error": str(exc)}, persona_text)


def _prepare_one_internal(
    client: OpenAI,
    model: str,
    scenario_text: str,
    persona_text: str,
    choice_probes: list[ChoiceProbe],
    enable_social_profile: bool,
    explain_rejected: bool,
    temperature: float,
    max_retries: int,
) -> dict[str, Any]:
    suitability_system = (
        "You are a strict scenario suitability filter. Answer with exactly one token: Yes or No."
    )
    suitability_user = (
        f"Scenario:\n{scenario_text}\n\n"
        f"Persona:\n{persona_text}\n\n"
        "Would this persona be useful for this scenario simulation? Output only Yes or No. Personas don't have to exactly match the scenario. It is often useful to have a diverse base of personas, in a follow-up step yoou will be able to make short edits to them if necessary. Only reject clearly unusable personas or highly unsuitable personas."
    )
    useful = _ask_yes_no(
        client, model, suitability_system, suitability_user, temperature, max_retries
    )
    if useful == "no":
        reason = None
        if explain_rejected:
            reason = _rejection_reason(client, model, scenario_text, persona_text, temperature)
        return {"is_useful": False, "rejection_reason": reason}

    process_gate_system = (
        "You are a conservative editor gate. "
        "Default to No unless persona is clearly unusable without edits. "
        "Answer with exactly one token: Yes or No."
    )
    process_gate_user = (
        f"Scenario:\n{scenario_text}\n\n"
        f"Persona:\n{persona_text}\n\n"
        "Does this persona require rewriting to be usable in this scenario? "
        "Output only Yes or No."
    )
    needs_processing = _ask_yes_no(
        client, model, process_gate_system, process_gate_user, temperature, max_retries
    )

    processed_persona = persona_text
    if needs_processing == "yes":
        rewritten = _minimal_rewrite(client, model, scenario_text, persona_text, temperature)
        if rewritten:
            processed_persona = rewritten

    name = _ask_name(client, model, processed_persona, temperature, max_retries)

    initial_attributes: dict[str, str] = {}
    if choice_probes:
        for probe in choice_probes:
            choice_system = (
                "You are selecting one strict choice for an initial simulation attribute. "
                "Return exactly one option string from the provided choices."
            )
            choice_user = (
                f"Scenario:\n{scenario_text}\n\n"
                f"Persona:\n{processed_persona}\n\n"
                f"Question: {probe.question}\n"
                f"Choices: {json.dumps(probe.choices, ensure_ascii=False)}\n\n"
                "Return exactly one choice string from the list."
            )
            initial_attributes[probe.probe_id] = _ask_choice(
                client,
                model,
                choice_system,
                choice_user,
                probe.choices,
                temperature,
                max_retries,
            )

    social_profile = None
    if enable_social_profile:
        social_profile = _social_media_profile(
            client,
            model,
            scenario_text,
            processed_persona,
            temperature,
        )

    return {
        "is_useful": True,
        "needs_processing": needs_processing == "yes",
        "name": name,
        "processed_persona": processed_persona,
        "initial_attributes": initial_attributes,
        "social_media_profile": social_profile,
    }


def main() -> int:
    """Entry point."""
    args = parse_args()

    scenario_path = Path(args.scenario_config).expanduser().resolve()
    if not scenario_path.is_file():
        raise PersonaPreparationError(f"Scenario config not found: {scenario_path}")

    out_path = Path(args.output).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    rejected_path: Path | None = None
    if args.explain_rejected:
        if str(args.rejected_reasons_output).strip():
            rejected_path = Path(args.rejected_reasons_output).expanduser().resolve()
        else:
            rejected_path = out_path.with_name(f"{out_path.stem}_rejected_reasons.json")
        rejected_path.parent.mkdir(parents=True, exist_ok=True)

    scenario_cfg = _load_yaml(scenario_path)
    scenario_text = _scenario_summary(scenario_cfg)
    if not scenario_text:
        raise PersonaPreparationError("Could not extract scenario context from scenario YAML")

    requested_probe_ids = [x.strip() for x in args.attribute_probes.split(",") if x.strip()]
    choice_probes = (
        _extract_choice_probes(scenario_cfg, requested_probe_ids) if requested_probe_ids else []
    )
    choice_probes.extend(_extract_inline_choice_probe(args))

    dataset_path = str(args.dataset_path).strip()
    hf_cache_dir = str(args.hf_cache_dir).strip()

    if dataset_path:
        local_path = Path(dataset_path).expanduser().resolve()
        if not local_path.exists():
            raise PersonaPreparationError(f"Dataset path not found: {local_path}")
        print(f"Loading dataset from disk: {local_path}")
        ds = load_from_disk(str(local_path))
    else:
        print(f"Loading dataset: {args.dataset} [{args.split}]")
        if hf_cache_dir:
            ds = load_dataset(args.dataset, split=args.split, cache_dir=hf_cache_dir)
        else:
            ds = load_dataset(args.dataset, split=args.split)

    print("Materializing dataset rows into memory...")
    records = [dict(row) for row in ds]
    print(f"Dataset rows loaded: {len(records)}")

    random.seed(args.shuffle_seed)
    random.shuffle(records)
    selected = records[: max(0, int(args.max_input))]

    kept = 0
    inspected = 0
    written = 0

    # Prepare work items (skip records without persona text upfront)
    work_items = []
    for idx, record in enumerate(selected):
        persona_text = _extract_persona_text(record)
        if persona_text:
            work_items.append(
                (
                    idx,
                    record,
                    scenario_text,
                    persona_text,
                    choice_probes,
                    args.llm_name,
                    args.llm_api_base,
                    args.llm_api_key,
                    "true" if args.enable_social_media_profile else "false",
                    "true" if args.explain_rejected else "false",
                    float(args.temperature),
                    int(args.max_retries),
                )
            )

    inspected = len(work_items)
    print(f"Processing {inspected} records with {args.num_workers} worker(s)...")

    # Map to track results by index for ordered output
    results_map: dict[int, tuple[dict[str, Any], str]] = {}

    progress_every = max(1, int(args.progress_every))
    started_at = time.time()
    completed = 0
    total = len(work_items)

    with ThreadPoolExecutor(max_workers=args.num_workers) as executor:
        futures = {executor.submit(_prepare_one_with_index, item): item[0] for item in work_items}

        for future in as_completed(futures):
            completed += 1
            idx = futures[future]
            try:
                result_idx, result, persona_text = future.result()
                results_map[result_idx] = (result, persona_text)
            except Exception as exc:  # pragma: no cover
                print(
                    f"[warn] record {idx} failed during parallel processing: {exc}", file=sys.stderr
                )

            if completed % progress_every == 0 or completed == total:
                elapsed = max(time.time() - started_at, 1e-6)
                rate = completed / elapsed
                remaining = max(total - completed, 0)
                eta_seconds = int(remaining / rate) if rate > 0 else -1
                print(
                    (
                        f"Progress: {completed}/{total} completed "
                        f"({(100.0 * completed / max(total, 1)):.1f}%) | "
                        f"rate={rate:.2f}/s | eta={eta_seconds}s"
                    ),
                    flush=True,
                )

    # Write results in order
    rejected_rows: list[dict[str, Any]] = []

    with out_path.open("w", encoding="utf-8") as f:
        for idx, record in enumerate(selected):
            if idx not in results_map:
                continue

            result, persona_text = results_map[idx]

            if result.get("error"):
                print(
                    f"[warn] record {idx} skipped due to error: {result['error']}", file=sys.stderr
                )
                continue

            if not result.get("is_useful", False):
                if args.explain_rejected:
                    rejected_rows.append(
                        {
                            "source_index": idx,
                            "persona": persona_text,
                            "rejection_reason": result.get("rejection_reason")
                            or "Persona was not suitable for the scenario requirements.",
                        }
                    )
                continue

            kept += 1
            row: dict[str, Any] = {
                "source_index": idx,
                "persona": persona_text,
                "processed_persona": result.get("processed_persona", persona_text) or persona_text,
                "name": result.get("name", "Unknown"),
                "needs_processing": bool(result.get("needs_processing", False)),
                "initial_attributes": result.get("initial_attributes", {}),
            }
            if args.enable_social_media_profile:
                row["social_media_profile"] = result.get("social_media_profile")
            if args.include_raw_record:
                row["source_record"] = record

            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            written += 1

    if args.explain_rejected and rejected_path is not None:
        with rejected_path.open("w", encoding="utf-8") as rf:
            json.dump(rejected_rows, rf, ensure_ascii=False, indent=2)

    print("Persona preparation complete")
    print(f"Input sampled records: {len(selected)}")
    print(f"Records inspected: {inspected}")
    print(f"Useful personas kept: {kept}")
    print(f"Rejected personas: {max(inspected - kept, 0)}")
    print(f"Output rows written: {written}")
    print(f"Output file: {out_path}")
    if args.explain_rejected and rejected_path is not None:
        print(f"Rejected reasons file: {rejected_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
