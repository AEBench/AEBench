# AE Agent Smoke Test Artifact

Minimal task for quick testing of ae_agent (host/docker + evaluation). Should complete in under a minute.

## Task

1. In this directory (the artifact root), create a file named **success.txt**.
2. The file must contain exactly the single character **1** (no newline required).
3. No other steps are required.

Example (bash): `echo -n 1 > success.txt`

After you finish, the benchmark will run an evaluation script that checks for this file and outputs a score (1 if correct, 0 otherwise).
