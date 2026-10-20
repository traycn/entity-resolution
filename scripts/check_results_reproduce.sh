#!/usr/bin/env bash
# Compare the committed README.md and docs/results.md with the working-tree copies
# that `make all` just regenerated. Runtimes ("4.8s", "4.8 s") vary by machine, so
# only those values are blanked before an otherwise exact comparison.
set -euo pipefail

blank_runtimes() { sed -E 's/(^|[^0-9.])[0-9]+\.[0-9] ?s([^a-z]|$)/\1<runtime>\2/g'; }

status=0
for f in README.md docs/results.md; do
  if ! diff -u --label "committed $f" --label "regenerated $f" \
      <(git show "HEAD:$f" | blank_runtimes) <(blank_runtimes < "$f"); then
    status=1
  fi
done
if [ "$status" -ne 0 ]; then
  echo "Committed results differ from a fresh 'make all' (diff above)." >&2
fi
exit "$status"
