#!/usr/bin/env bash
set -euo pipefail

ROOT=${CHROMIUM_BUILD_ROOT:-/opt/chromium-build}
METADATA=${CHROMIUM_METADATA_ROOT:-/opt/chromium-build-metadata}
ARCH=${CHROMIUM_ARCH:-arm64}
LOCK=$ROOT/config/packages.$ARCH.lock
POLICY=$ROOT/config/system-library-preflight.schema.json
REPORT=${SYSTEM_LIBRARY_PREFLIGHT:-$METADATA/system-library-preflight.json}

mkdir -p "$(dirname "$REPORT")"
[ -f "$LOCK" ] || { echo "preflight: missing $LOCK" >&2; exit 1; }
[ -f "$POLICY" ] || { echo "preflight: missing $POLICY" >&2; exit 1; }
command -v scanelf >/dev/null 2>&1 || { echo "preflight: scanelf is required" >&2; exit 1; }

python3 - "$LOCK" "$POLICY" "$REPORT" "$ARCH" <<'PY'
import hashlib
import json
import subprocess
import sys
from pathlib import Path

package_lock, policy_path, report_path, architecture = sys.argv[1:]
policy = json.loads(Path(policy_path).read_text(encoding="utf-8"))
package_digest = hashlib.sha256(Path(package_lock).read_bytes()).hexdigest()
policy_digest = hashlib.sha256(Path(policy_path).read_bytes()).hexdigest()

lines = subprocess.run(
    ["scanelf", "-RBF", "%F|%S|%n", "/lib", "/usr/lib"],
    check=True,
    capture_output=True,
    text=True,
).stdout.splitlines()
objects = {}
by_soname = {}
for line in lines:
    if not line.strip():
        continue
    path, soname, needed_text = line.split("|", 2)
    path = path.strip()
    soname = soname.strip() or Path(path).name
    needed = [value for value in needed_text.strip().split(",") if value]
    entry = {"path": path, "soname": soname, "needed": needed}
    objects[path] = entry
    by_soname.setdefault(soname, path)

roots = {
    path: entry
    for path, entry in objects.items()
    if any(Path(path).name.startswith(root) for root in policy["proposed_roots"])
}
if not roots:
    raise SystemExit("preflight found no proposed system-library roots")

edges = []
glibc_entries = []
unclassified_edges = []
exception_leaks = []
visited = set()
pending = [(path, path, True) for path in roots]
while pending:
    parent, path, ffmpeg_descendant = pending.pop()
    if (parent, path) in visited:
        continue
    visited.add((parent, path))
    entry = objects[path]
    for soname in entry["needed"]:
        child = by_soname.get(soname)
        child_path = child or ""
        child_is_ffmpeg = ffmpeg_descendant or any(
            Path(parent).name.startswith(root) for root in policy["ffmpeg_roots"]
        )
        if soname in ("libc.so.6", "ld-linux.so.2", "ld-linux-x86-64.so.2"):
            classification = "forbidden-glibc"
            glibc_entries.append({"path": parent, "soname": soname})
        elif child is None:
            classification = "unclassified"
            unclassified_edges.append({"path": parent, "soname": soname})
        elif soname in policy["exception_sonames"]:
            classification = "ffmpeg-exception" if child_is_ffmpeg else "exception-leak"
            if classification == "exception-leak":
                exception_leaks.append({"path": parent, "soname": soname})
        elif soname.startswith(tuple(policy["ffmpeg_roots"])):
            classification = "ffmpeg-root"
        else:
            classification = "host-owned"
        edges.append({"path": parent, "soname": soname, "classification": classification})
        if child is not None:
            pending.append((parent, child, child_is_ffmpeg))

classifications = {}
for edge in edges:
    classifications[edge["classification"]] = classifications.get(edge["classification"], 0) + 1

report = {
    "schema": policy["schema"],
    "status": "complete",
    "architecture": architecture,
    "package_lock_sha256": package_digest,
    "policy_sha256": policy_digest,
    "environment_compatibility_id": f"{architecture}-{package_digest[:16]}-{policy_digest[:16]}",
    "unclassified_edges": unclassified_edges,
    "glibc_entries": glibc_entries,
    "exception_leaks": exception_leaks,
    "ccache_runtime_classification": "builder-only",
    "classification_counts": classifications,
    "edges": edges,
}
if glibc_entries or unclassified_edges or exception_leaks:
    raise SystemExit(
        "preflight failed: "
        f"glibc={len(glibc_entries)} unclassified={len(unclassified_edges)} "
        f"exception_leaks={len(exception_leaks)}"
    )
Path(report_path).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
