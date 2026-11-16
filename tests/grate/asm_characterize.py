#!/usr/bin/env python3
"""Repeatable hardware characterization runner for the grate assembler.

A JSON manifest describes the program, known-result controls, and variants.
Controls run before and after the sweep. Each invocation saves complete
stdout/stderr and appends a machine-readable row to results.tsv.
"""

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
RESULT_RE = re.compile(r"^result\s+(0x[0-9a-fA-F]{8})\s*$", re.MULTILINE)
STAGES = ("vs", "fs", "lnk")


def word(value, where):
    if isinstance(value, bool):
        raise ValueError(f"{where}: expected a 32-bit result word")
    if isinstance(value, int):
        result = value
    elif isinstance(value, str):
        try:
            result = int(value, 0)
        except ValueError as exc:
            raise ValueError(f"{where}: expected an integer or 0x-prefixed word") from exc
    else:
        raise ValueError(f"{where}: expected an integer or 0x-prefixed word")
    if not 0 <= result <= 0xffffffff:
        raise ValueError(f"{where}: result is outside the 32-bit range")
    return result


def slug(value):
    return (re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_.-") or "step")[:64]


def source_path(value, where):
    if not isinstance(value, str) or not value:
        raise ValueError(f"{where}: expected a non-empty source path")
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    path = path.resolve()
    if not path.is_file():
        raise ValueError(f"{where}: file does not exist: {path}")
    return path


def source_files(output, program, step, phase, iteration):
    paths = {
        stage: source_path(step.get(stage, program.get(stage)), f"{phase}.{stage}")
        for stage in STAGES
    }
    replacements = step.get("replace", {})
    if not isinstance(replacements, dict):
        raise ValueError(f"{phase}.replace must be an object keyed by vs/fs/lnk")
    for stage, mapping in replacements.items():
        if stage not in STAGES or not isinstance(mapping, dict) or not mapping:
            raise ValueError(f"{phase}.replace.{stage}: expected a non-empty stage mapping")
        data = paths[stage].read_text(encoding="utf-8")
        for placeholder, replacement in mapping.items():
            if not isinstance(placeholder, str) or not placeholder:
                raise ValueError(f"{phase}.replace.{stage}: placeholder must be non-empty")
            count = data.count(placeholder)
            if count != 1:
                raise ValueError(
                    f"{phase}.replace.{stage}: {placeholder!r} must occur exactly once; found {count}"
                )
            data = data.replace(placeholder, str(replacement), 1)
        generated = output / "generated" / f"{slug(phase)}-{iteration}-{stage}.asm"
        generated.parent.mkdir(parents=True, exist_ok=True)
        generated.write_text(data, encoding="utf-8")
        paths[stage] = generated
    return paths


def environment(base, local, where):
    result = os.environ.copy()
    result["LC_ALL"] = "C"
    for label, values in (("environment", base), ("step.environment", local)):
        if not isinstance(values, dict):
            raise ValueError(f"{where}.{label} must be an object")
        for key, value in values.items():
            if not isinstance(key, str) or not key:
                raise ValueError(f"{where}.{label}: invalid variable name")
            if value is None:
                result.pop(key, None)
            elif isinstance(value, (str, int, float, bool)):
                result[key] = str(value)
            else:
                raise ValueError(f"{where}.{label}.{key}: expected a scalar or null")
    return result


def execute(command, env, timeout):
    start = time.monotonic()
    process = subprocess.Popen(
        command, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, errors="replace", start_new_session=(os.name == "posix")
    )
    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        else:
            process.terminate()
        try:
            stdout, stderr = process.communicate(timeout=1)
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
            stdout, stderr = process.communicate()
    elapsed = (time.monotonic() - start) * 1000
    return process.returncode, stdout, stderr, timed_out, elapsed


def run_step(assembler, output, program, base_args, base_env, step, phase,
             iteration, timeout, writer, stream):
    if not isinstance(step, dict) or not isinstance(step.get("name"), str) or not step.get("name"):
        raise ValueError(f"{phase}: each step needs a name")
    name = step["name"]
    paths = source_files(output, program, step, f"{phase}-{name}", iteration)
    command = [str(assembler)]
    for option, stage in (("--vs", "vs"), ("--fs", "fs"), ("--lnk", "lnk")):
        command.extend((option, str(paths[stage])))
    args = step.get("args", [])
    if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
        raise ValueError(f"{phase}.{name}.args must be an array of strings")
    command.extend(base_args)
    command.extend(args)
    command.extend(("--result", "--testonly"))
    expected = None if step.get("expected") is None else word(step["expected"], f"{phase}.{name}.expected")
    if expected is not None:
        command.extend(("--expected", f"0x{expected:08X}"))

    env = environment(base_env, step.get("environment", {}), f"{phase}.{name}")
    rc, stdout, stderr, timed_out, elapsed = execute(command, env, timeout)
    logbase = f"{slug(phase)}-{slug(name)}-{iteration:03d}"
    stdout_path = output / "logs" / f"{logbase}.stdout"
    stderr_path = output / "logs" / f"{logbase}.stderr"
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")

    matches = RESULT_RE.findall(stdout)
    result = int(matches[0], 16) if len(matches) == 1 else None
    if timed_out:
        status = "timeout"
    elif rc != 0:
        status = "exit-error"
    elif len(matches) != 1:
        status = "missing-result" if not matches else "multiple-results"
    elif expected is not None and result != expected:
        status = "mismatch"
    else:
        status = "pass"

    hashes = {
        stage: hashlib.sha256(path.read_bytes()).hexdigest()
        for stage, path in paths.items()
    }
    row = {
        "timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds"),
        "phase": phase,
        "name": name,
        "iteration": iteration,
        "status": status,
        "exit_code": "" if rc is None else rc,
        "timed_out": str(timed_out).lower(),
        "result": "" if result is None else f"0x{result:08X}",
        "expected": "" if expected is None else f"0x{expected:08X}",
        "elapsed_ms": f"{elapsed:.3f}",
        "stdout": str(stdout_path.relative_to(output)),
        "stderr": str(stderr_path.relative_to(output)),
        "command": json.dumps(command, separators=(",", ":")),
        "source_sha256": json.dumps(hashes, sort_keys=True, separators=(",", ":")),
    }
    writer.writerow(row)
    stream.flush()
    print(f"{phase:14s} {name:28.28s} run {iteration}: {status}"
          + (f" result=0x{result:08X}" if result is not None else ""), flush=True)
    return status


def git_value(*args):
    try:
        result = subprocess.run(
            ["git", "-C", str(ROOT), *args], check=True, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5
        )
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def device_model():
    for path in (Path("/proc/device-tree/model"),
                 Path("/sys/firmware/devicetree/base/model")):
        try:
            return path.read_bytes().rstrip(b"\0").decode("utf-8", "replace")
        except OSError:
            pass
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", help="JSON characterization manifest")
    parser.add_argument("--assembler", help="assembler executable; defaults to $GRATE_ASSEMBLER or tools/assembler")
    parser.add_argument("--output", help="result directory; defaults to characterization/<name>-<UTC timestamp>")
    parser.add_argument("--repeat", type=int, help="variant repetitions (default: manifest value or 3)")
    parser.add_argument("--control-repeat", type=int, default=1, help="repetitions for controls before and after")
    parser.add_argument("--timeout", type=float, help="per-draw timeout in seconds (default: manifest value or 20)")
    args = parser.parse_args()

    manifest_path = Path(args.manifest).resolve()
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    if not isinstance(manifest, dict):
        parser.error("manifest root must be an object")
    name = manifest.get("name", manifest_path.stem)
    program = manifest.get("program")
    controls = manifest.get("controls")
    variants = manifest.get("variants")
    if not isinstance(name, str) or not isinstance(program, dict):
        parser.error("manifest needs a name and program object")
    try:
        for stage in STAGES:
            source_path(program.get(stage), f"program.{stage}")
        if not isinstance(controls, list) or not controls:
            raise ValueError("manifest must define at least one control")
        if not isinstance(variants, list) or not variants:
            raise ValueError("manifest must define at least one variant")
        if any(not isinstance(c, dict) or c.get("expected") is None for c in controls):
            raise ValueError("every control needs an expected result word")
        if any(not isinstance(v, dict) or not isinstance(v.get("name"), str) or not v.get("name") for v in variants):
            raise ValueError("every variant needs a name")
    except ValueError as exc:
        parser.error(str(exc))

    assembler = Path(args.assembler or os.environ.get("GRATE_ASSEMBLER") or "tools/assembler")
    if not assembler.is_absolute():
        assembler = (ROOT / assembler).resolve()
    if not assembler.is_file() or not os.access(assembler, os.X_OK):
        parser.error(f"assembler is missing or not executable: {assembler}")

    repeat = args.repeat if args.repeat is not None else manifest.get("repeat", 3)
    control_repeat = args.control_repeat
    timeout = args.timeout if args.timeout is not None else manifest.get("timeout", 20)
    if not isinstance(repeat, int) or repeat < 1 or control_repeat < 1 or not isinstance(timeout, (int, float)) or timeout <= 0:
        parser.error("repeat counts must be positive and timeout must be positive")

    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = Path(args.output or ROOT / "characterization" / f"{slug(name)}-{stamp}").resolve()
    if out.exists() and any(out.iterdir()):
        parser.error(f"output directory is not empty: {out}")
    out.mkdir(parents=True, exist_ok=True)

    inherited_environment = {
        key: value for key, value in os.environ.items()
        if key.startswith("GRATE_") or key in ("DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR")
    }
    metadata = {
        "name": name,
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "manifest": str(manifest_path),
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "assembler": str(assembler),
        "repository_root": str(ROOT),
        "git_commit": git_value("rev-parse", "HEAD"),
        "git_status": git_value("status", "--short"),
        "uname": " ".join(os.uname()) if hasattr(os, "uname") else None,
        "device_model": device_model(),
        "inherited_environment": inherited_environment,
        "repeat": repeat,
        "control_repeat": control_repeat,
        "timeout_seconds": timeout,
        "manifest_data": manifest,
    }
    (out / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    fields = [
        "timestamp_utc", "phase", "name", "iteration", "status", "exit_code",
        "timed_out", "result", "expected", "elapsed_ms", "stdout", "stderr",
        "command", "source_sha256",
    ]
    statuses = []
    variants_started = False
    with (out / "results.tsv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        stream.flush()
        for control in controls:
            for iteration in range(1, control_repeat + 1):
                statuses.append(run_step(assembler, out, program, manifest.get("arguments", []),
                    manifest.get("environment", {}), control, "control-before", iteration,
                    timeout, writer, stream))
        if all(status == "pass" for status in statuses):
            variants_started = True
            for variant in variants:
                count = variant.get("repeat", repeat)
                if not isinstance(count, int) or count < 1:
                    raise ValueError(f"variant {variant.get('name', '<unnamed>')}: repeat must be positive")
                for iteration in range(1, count + 1):
                    statuses.append(run_step(assembler, out, program, manifest.get("arguments", []),
                        manifest.get("environment", {}), variant, "variant", iteration,
                        timeout, writer, stream))
        if variants_started:
            for control in controls:
                for iteration in range(1, control_repeat + 1):
                    statuses.append(run_step(assembler, out, program, manifest.get("arguments", []),
                        manifest.get("environment", {}), control, "control-after", iteration,
                        timeout, writer, stream))

    summary = {
        "success": bool(statuses) and all(status == "pass" for status in statuses),
        "variants_started": variants_started,
        "runs": len(statuses),
        "passed": sum(status == "pass" for status in statuses),
        "failed": sum(status != "pass" for status in statuses),
        "results_file": "results.tsv",
        "logs_directory": "logs/",
    }
    metadata["summary"] = summary
    (out / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"results: {out / 'results.tsv'}")
    return 0 if summary["success"] else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        print(f"asm_characterize.py: {exc}", file=sys.stderr)
        sys.exit(2)
