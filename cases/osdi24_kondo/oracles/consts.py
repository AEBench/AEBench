from __future__ import annotations

PROTOCOLS: tuple[str, ...] = (
	"clientServer",
	"ringLeaderElection",
	"simplifiedLeaderElection",
	"twoPhaseCommit",
	"paxos",
	"flexPaxos",
	"distributedLock",
	"shardedKv",
	"shardedKvBatched",
	"lockServer",
)
