# Chameleon Codex trials — 2026-10-14

Thirty trial supervisors finished across ten AEBench cases. All 30 private Swift manifests and archives were downloaded; each archive matched its recorded SHA-256 and byte size. Twenty-five trials reached a four-phase oracle; five ended during Docker snapshotting before the oracle ran. These interruptions retain their original recorded score of zero in the machine-readable record, but are **not** reported as oracle 0/4 results.

The cases below use Codex CLI 0.159.1 with `gpt-6-sol`. ACTO used `xhigh` reasoning effort and an audited four-phase case overlay; the other cases used `high`. Loupe, Calchas, and ICEmu ran sequentially on one cleanly reset host per case; all other trials used separate nodes. These are three new Codex trials per case, distinct from earlier interactive oracle rechecks. DepSurf and EET used unmerged PR #129 and remain provisional.

| Case | Trial 1 | Trial 2 | Trial 3 | Case revision |
| --- | --- | --- | --- | --- |
| Loupe (`asplos24_loupe`) | 4/4 | 4/4 | 4/4 | `0471090` |
| ICEmu (`asplos25_icemu`) | interrupted before oracle | interrupted before oracle | interrupted before oracle | `b1ff4c0` |
| Calchas (`atc24_calchas`) | 3/4 | 3/4 | 3/4 | `b1ff4c0` |
| DepSurf (`eurosys25_depsurf`) | 2/4 | 2/4 | 2/4 | `5cfcb8d` |
| SeBS-Flow (`eurosys25_sebs_flow`) | 2/4 | 2/4 | 2/4 | `b1ff4c0` |
| WALI (`eurosys25_wali`) | 2/4 | 2/4 | 2/4 | `b1ff4c0` |
| EET (`osdi24_eet`) | 1/4 | 1/4 | 1/4 | `5cfcb8d` |
| Paralegal (`osdi25_paralegal`) | 2/4 | 2/4 | 2/4 | `eefc19d` |
| ACTO (`sosp23_acto`) | 0/4 | 1/4 | 1/4 | `da8b505` |
| Wasabi (`sosp24_wasabi`) | interrupted before oracle | 1/4 | interrupted before oracle | `b1ff4c0` |

The [per-trial JSON record](chameleon-2026-10-14-trials.json) preserves each original recorded score, runtime and oracle status, per-phase outcome, pinned revisions, agent settings, times, private object key, archive size, and SHA-256. A non-4/4 result remains a result; it was not overwritten by a retry. ICEmu trials 1–3 and Wasabi trials 1 and 3 stopped before oracle evaluation because the runtime failed to snapshot the agent container. Their `oracle_status` is `pending` and `oracle_score` is `null` in JSON.

Raw trajectories and logs remain in the private Chameleon Swift container `aebench-runs-muneeb-20261014`. They may contain credentials and are not included here. The public record contains no signed download URLs. The requested `AEBench/aebench_eval_traj` repository was inaccessible to the authenticated account (HTTP 404) when this record was prepared, so no trajectory transfer is claimed.

The ACTO entries were run against audited four-phase case revision `da8b505` over AEBench base `b1ff4c0`; the separate exploratory ACTO replay is not included. Loupe used PR #113 `0471090`, Paralegal used PR #112 `eefc19d`, and DepSurf/EET used PR #129 `5cfcb8d`. The remaining cases used `b1ff4c0` from `main`. ACTO’s exact harness and overlay checksums are recorded per trial in JSON.
