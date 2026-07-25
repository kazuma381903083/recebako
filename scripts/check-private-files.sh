#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
REPO_ROOT="$(git -C "$SCRIPT_DIR/.." rev-parse --show-toplevel)"

shopt -s nocasematch

is_private_path() {
    local path="$1"

    if [[ "$path" == "docs/spec/monthly-report-sample.html" ]]; then
        return 1
    fi

    if [[ "$path" =~ \.(jpg|jpeg|png|heic|heif|tif|tiff|webp|avif)$ ]]; then
        return 0
    fi
    if [[ "$path" =~ \.(db|sqlite|sqlite3)(-(wal|shm|journal))?$ ]]; then
        return 0
    fi
    if [[ "$path" =~ \.(wal|shm)$ ]]; then
        return 0
    fi
    if [[ "$path" =~ \.log(\.[0-9]+)?$ ]] || [[ "$path" =~ (^|/)logs?(/|$) ]]; then
        return 0
    fi
    if [[ "$path" =~ (^|/)(inbox|processing|archive|review|failed)(/|$) ]]; then
        return 0
    fi
    if [[ "$path" =~ (^|/)(private|private-data|private_dataset|private-dataset|private_golden|private-golden|source-copies)(/|$) ]]; then
        return 0
    fi
    if [[ "$path" =~ ^(runtime|var|recebako-data)/ ]]; then
        return 0
    fi
    if [[ "$path" =~ (^|/)reports/.*\.(html|htm)$ ]]; then
        return 0
    fi
    if [[ "$path" =~ (^|/)(monthly[-_]report|recebako[-_]report|personal[-_]report)[^/]*\.(html|htm)$ ]]; then
        return 0
    fi
    if [[ "$path" =~ (^|/)\.env(\..*)?$ ]] && [[ "$path" != *.example ]]; then
        return 0
    fi
    if [[ "$path" =~ (^|/)config\.local\.toml$ ]]; then
        return 0
    fi

    return 1
}

scan_paths() {
    local path
    local violation_found=0

    while IFS= read -r -d '' path; do
        if is_private_path "$path"; then
            violation_found=1
        fi
    done

    return "$violation_found"
}

cd "$REPO_ROOT"
if git ls-files --cached --others --exclude-standard -z -- 2>/dev/null |
    scan_paths; then
    echo "Private-file scan passed."
else
    echo "ERROR: Git-visible private or runtime artifacts were detected; filenames withheld." >&2
    exit 1
fi
