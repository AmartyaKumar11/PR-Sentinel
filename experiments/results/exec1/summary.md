# Execution backend experiment

Success counts runs whose outcome is success. Acceptance is the harness test, not the quality gate.
Spread is the sample standard deviation (n-1). Time is seconds. Lines are git numstat against the seeded head.

| condition | n | success | acceptance | time (s) | files | lines added | lines removed | mergeable |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A/cursor | 1 | 1/1 | 1/1 | 60.81 ± n/a | 2.00 ± n/a | 24.00 ± n/a | 0.00 ± n/a | 1/1 |
| A/treefleet | 1 | 0/1 | 0/0 | n/a | n/a | n/a | n/a | 0/0 |
