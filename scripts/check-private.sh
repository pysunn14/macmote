#!/bin/sh
# Refuses to let machine-specific or personal details into the repository: user names, host
# names, tailnet domains, home paths, e-mail addresses. Runs as the pre-commit hook and on CI.
# Patterns live in .private-patterns (one extended regex per line, '#' comments); the file is
# ignored by git so each machine lists its own identifiers.
set -eu
root=$(cd "$(dirname "$0")/.." && pwd)
patterns="$root/.private-patterns"
status=0
generic='[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[a-z]{2,}|/Users/[a-z0-9_]+|/home/[a-z0-9_]+|tail[0-9a-f]{6}\.ts\.net|100\.[0-9]+\.[0-9]+\.[0-9]+'
check() {
    file=$1
    case "$file" in *.png|*.jpg|*.ico|*/.private-patterns|.private-patterns) return 0;; esac
    if grep -nHE "$generic" "$file" 2>/dev/null; then status=1; fi
    if [ -f "$patterns" ]; then
        grep -v '^#' "$patterns" | grep -v '^$' | while IFS= read -r pattern; do
            grep -nHE -- "$pattern" "$file" 2>/dev/null && exit 9 || true
        done || status=1
    fi
}
if [ "${1:-}" = "--all" ]; then
    files=$(cd "$root" && git ls-files 2>/dev/null || find . -type f -not -path './.git/*' -not -path './build/*')
else
    files=$(cd "$root" && git diff --cached --name-only --diff-filter=ACMR)
fi
for file in $files; do
    [ -f "$root/$file" ] && check "$root/$file"
done
[ "$status" -eq 0 ] || { echo "check-private: remove the matches above before committing" >&2; exit 1; }
