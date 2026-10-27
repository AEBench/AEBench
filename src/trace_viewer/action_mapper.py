from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from enum import Enum
from typing import Any


class SemanticAction(str, Enum):
	SETUP = "setup"
	BUILD = "build"
	THINK = "think"
	RUN = "run"
	VERIFY = "verify"
	MODIFY = "modify"
	MISC = "misc"


ACTION_METADATA: dict[str, dict[str, str]] = {
	SemanticAction.SETUP.value: {
		"label": "Setup",
		"short_label": "Setup",
		"color": "#ef4444",
		"icon": "settings",
	},
	SemanticAction.BUILD.value: {
		"label": "Build",
		"short_label": "Build",
		"color": "#f97316",
		"icon": "box",
	},
	SemanticAction.THINK.value: {
		"label": "Think",
		"short_label": "Think",
		"color": "#eab308",
		"icon": "search",
	},
	SemanticAction.RUN.value: {
		"label": "Run",
		"short_label": "Run",
		"color": "#22c55e",
		"icon": "play",
	},
	SemanticAction.VERIFY.value: {
		"label": "Verify",
		"short_label": "Verify",
		"color": "#3b82f6",
		"icon": "check-circle",
	},
	SemanticAction.MODIFY.value: {
		"label": "Modify",
		"short_label": "Modify",
		"color": "#6366f1",
		"icon": "edit",
	},
	SemanticAction.MISC.value: {
		"label": "Misc",
		"short_label": "Misc",
		"color": "#a855f7",
		"icon": "activity",
	},
}

_ENV_SETUP_CMDS = {
	"apt",
	"apt-get",
	"dpkg",
	"yum",
	"apk",
	"pacman",
	"brew",
	"pip",
	"pip3",
	"uv",
	"conda",
	"mamba",
	"poetry",
	"pipenv",
	"virtualenv",
	"venv",
	"rustup",
	"npm",
	"yarn",
	"pnpm",
}

_DOWNLOAD_CMDS = {
	"wget",
	"curl",
	"gdown",
	"aria2c",
	"git-lfs",
}

_ARCHIVE_CMDS = {
	"tar",
	"unzip",
	"bunzip2",
	"gunzip",
	"unrar",
	"7z",
	"zstd",
	"xz",
}

_CHECKSUM_CMDS = {
	"md5sum",
	"sha256sum",
	"sha1sum",
	"sha512sum",
	"cksum",
}

_BUILD_CMDS = {
	"make",
	"cmake",
	"ninja",
	"meson",
	"bazel",
	"gcc",
	"g++",
	"clang",
	"clang++",
	"rustc",
	"javac",
	"ant",
	"mvn",
	"gradle",
	"sbt",
}

_EXPLORATION_CMDS = {
	"ls",
	"find",
	"tree",
	"pwd",
	"cat",
	"head",
	"tail",
	"less",
	"more",
	"bat",
	"grep",
	"rg",
	"ag",
	"ack",
	"wc",
	"du",
	"file",
	"stat",
	"which",
	"whereis",
	"type",
	"command",
	"uname",
}

_READONLY_SAFE_EXECS = _EXPLORATION_CMDS | {"sed", "git", "echo", "printf", "true", "sort", "uniq", "tr", "cut", "awk"}

_STRIP_PREFIX_REGEX = re.compile(
	r"^(?:sudo|time|nohup|exec)\s+", re.IGNORECASE
)

_BASH_WRAPPER_REGEX = re.compile(
	r"^(?:/bin/)?(?:bash|sh|zsh)\s+(?:-[a-zA-Z]*c\s+)([\"'])(.*)\1\s*$",
	re.DOTALL,
)

_WRITE_REDIR_REGEX = re.compile(r"(?:^|[^0-9>&])>>?\s*(?!/dev/null\b)&?[a-zA-Z0-9_./~-]")


def unwrap_command(raw_cmd: str) -> str:
	cmd = raw_cmd.strip()
	while True:
		match = _BASH_WRAPPER_REGEX.match(cmd)
		if match:
			cmd = match.group(2).strip()
			continue
		stripped = _STRIP_PREFIX_REGEX.sub("", cmd)
		if stripped != cmd:
			cmd = stripped.strip()
			continue
		break
	return cmd


def extract_command_executables(cmd_str: str) -> list[str]:
	parts = re.split(r"&&|\|\||[;|]", cmd_str)
	execs: list[str] = []
	for part in parts:
		tokens = part.strip().split()
		for token in tokens:
			if "=" in token and not token.startswith("-"):
				continue
			if token in ("sudo", "time", "nohup", "exec"):
				continue
			base = token.split("/")[-1] if not token.startswith(("./", "../")) else token
			execs.append(base.lower())
			break
	return execs


def is_readonly_shell_command(cmd_str: str) -> bool:
	cmd_lower = unwrap_command(cmd_str).lower()
	if not cmd_lower:
		return False
	if re.search(r"\bsed\s+-[a-zA-Z]*i\b", cmd_lower):
		return False
	if "<<" in cmd_lower or _WRITE_REDIR_REGEX.search(cmd_lower):
		return False
	if re.search(r"\bgit\s+(?:add|commit|checkout|reset|clean|apply|merge|rebase|cherry-pick|mv|rm)\b", cmd_lower):
		return False
	execs = extract_command_executables(cmd_lower)
	if not execs:
		return False
	if any(e == "sed" for e in execs) and not re.search(r"\bsed\s+-n\b", cmd_lower):
		return False
	if any(e == "git" for e in execs) and not re.search(
		r"\bgit\s+(?:status|log|diff|branch|show|rev-parse|ls-files)\b", cmd_lower
	):
		return False
	return all(e in _READONLY_SAFE_EXECS for e in execs)


def _classify_description(desc_lower: str) -> str | None:
	if not desc_lower:
		return None
	if any(
		w in desc_lower
		for w in ("plot", "verify", "verif", "proof", "passed", "compare", "reference", "result", "metric", "check_claim")
	):
		return SemanticAction.VERIFY.value
	if any(
		w in desc_lower
		for w in (
			"run benchmark",
			"reproduce",
			"reproduction",
			"execute experiment",
			"run test",
			"experiment",
			"workload",
			"run all",
			"figure script",
			"figure",
		)
	):
		return SemanticAction.RUN.value
	if any(
		w in desc_lower
		for w in (
			"install",
			"setup",
			"dependency",
			"dependencies",
			"environment",
			"download",
			"fetch",
			"data prep",
			"explore repo",
			"explore directory",
			"find files",
			"explore",
			"inspect",
		)
	):
		return SemanticAction.SETUP.value
	if any(w in desc_lower for w in ("build", "compile")):
		return SemanticAction.BUILD.value
	if any(w in desc_lower for w in ("patch", "edit", "fix", "modify")):
		return SemanticAction.MODIFY.value
	return None


def classify_command(raw_command: str, description: str = "") -> str:
	desc_lower = description.lower() if description else ""
	cmd = unwrap_command(raw_command)
	cmd_lower = cmd.lower()

	if not cmd_lower:
		return _classify_description(desc_lower) or SemanticAction.MISC.value

	execs = extract_command_executables(cmd_lower)

	if re.search(r"\bsed\s+-[a-zA-Z]*i\b", cmd_lower):
		return SemanticAction.MODIFY.value
	if re.search(r"\bpatch\s+", cmd_lower) or "git apply" in cmd_lower:
		return SemanticAction.MODIFY.value
	if "<<" in cmd and "eof" in cmd_lower:
		return SemanticAction.MODIFY.value
	if re.search(r"\b(echo|printf|cat)\b.*>\s*[^/]+", cmd) and not cmd.strip().endswith("> /dev/null"):
		return SemanticAction.MODIFY.value

	if is_readonly_shell_command(cmd_lower):
		if ("build.log" in cmd_lower or "compile.log" in cmd_lower) and not any(
			k in cmd_lower for k in ("verify", "eval", "test")
		):
			return SemanticAction.BUILD.value
		if ("/refs/" in cmd_lower or "ref.json" in cmd_lower or "gold.csv" in cmd_lower or "ground_truth" in cmd_lower) and any(
			e in ("cat", "head", "tail", "less", "more", "bat", "grep", "rg", "diff", "cmp", "sed") for e in execs
		):
			return SemanticAction.VERIFY.value
		if re.search(
			r"(?:[a-zA-Z0-9_.-]*(?:verify|eval|test|result|claim|output)[a-zA-Z0-9_.-]*\.(?:log|output)|/tasks/[a-zA-Z0-9_.-]+\.output)\b",
			cmd_lower,
		):
			return SemanticAction.VERIFY.value
		if "results/" in cmd_lower and any(e in ("cat", "head", "tail", "ls", "wc", "grep", "rg") for e in execs):
			return SemanticAction.VERIFY.value
		if re.search(r"\b(cat|head|tail|less|more|bat)\b.*\.(csv|tsv|png|pdf)\b", cmd_lower) and "protocols.csv" not in cmd_lower:
			return SemanticAction.VERIFY.value
		return SemanticAction.SETUP.value

	if re.search(r"\b(?:--version|--list-sdks|--list-runtimes)\b", cmd_lower) and not any(
		e in _ENV_SETUP_CMDS | _DOWNLOAD_CMDS | _ARCHIVE_CMDS | _BUILD_CMDS for e in execs
	) and not any(w in cmd_lower for w in ("mkdir", "cp ", "mv ", "ln ", "chmod", "zipfile")):
		return SemanticAction.SETUP.value

	if "/refs/" in cmd_lower or "ref.json" in cmd or "gold.csv" in cmd_lower or "ground_truth" in cmd_lower:
		return SemanticAction.VERIFY.value
	if re.search(r"\b(diff|cmp)\b", cmd_lower) and any(
		term in cmd_lower for term in ("output", "result", "log", "gold", "ground", "table", "csv", "ref")
	):
		return SemanticAction.VERIFY.value
	if any(re.search(rf"\b{re.escape(c)}\b", cmd_lower) for c in _CHECKSUM_CMDS):
		return SemanticAction.VERIFY.value
	if re.search(
		r"\bpython[0-9.]*\s+.*(?:plot|analyze|parse_results|check_claims|eval_results|calc_metrics|verify)[a-zA-Z0-9_.-]*\.py\b",
		cmd_lower,
	):
		return SemanticAction.VERIFY.value
	if "csv" in cmd_lower and any(
		term in cmd_lower for term in ("dictreader", "sloc", "gold", "reduction", "calc", "metric", "parse", "csv count")
	):
		return SemanticAction.VERIFY.value
	if re.search(
		r"(?:[a-zA-Z0-9_.-]*(?:verify|eval|test|result|claim|output)[a-zA-Z0-9_.-]*\.(?:log|output)|/tasks/[a-zA-Z0-9_.-]+\.output)\b",
		cmd_lower,
	) and any(
		term in cmd_lower for term in ("cat", "head", "tail", "less", "more", "bat", "rg", "grep", "sed", "wc")
	) and "until " not in cmd_lower:
		return SemanticAction.VERIFY.value
	if "results/" in cmd_lower and any(term in cmd_lower for term in ("wc -l", "grep", "ls")):
		return SemanticAction.VERIFY.value

	if any(re.search(rf"\b{re.escape(pkg)}\b", cmd_lower) for pkg in _ENV_SETUP_CMDS):
		return SemanticAction.SETUP.value
	if "python" in cmd_lower and "-m pip" in cmd_lower:
		return SemanticAction.SETUP.value
	if re.search(r"\b(cargo\s+install|dotnet\s+tool|dotnet\s+add)\b", cmd_lower):
		return SemanticAction.SETUP.value
	if re.search(r"\b(export\s+(?:path|ld_library_path|pythonpath)=|source\s+.*activate)", cmd_lower):
		return SemanticAction.SETUP.value
	if "setup.py" in cmd_lower and any(w in cmd_lower for w in ("install", "develop", "egg_info")):
		return SemanticAction.SETUP.value
	if "docker" in cmd_lower and any(w in cmd_lower for w in ("build", "run", "pull", "daemon")):
		return SemanticAction.SETUP.value
	if any(re.search(rf"\b{re.escape(c)}\b", cmd_lower) for c in _DOWNLOAD_CMDS) or "git lfs" in cmd_lower:
		return SemanticAction.SETUP.value
	if any(re.search(rf"\b{re.escape(c)}\b", cmd_lower) for c in _ARCHIVE_CMDS):
		return SemanticAction.SETUP.value
	if re.search(
		r"\b(download|get_data|fetch_data|prep_data|prepare_benchmarks|generate_data|make_dataset|setup)[a-zA-Z0-9_.-]*\b",
		cmd_lower,
	):
		return SemanticAction.SETUP.value
	if "dataset" in cmd_lower and any(w in cmd_lower for w in ("download", "extract", "fetch", "prep", "generate")):
		return SemanticAction.SETUP.value
	if "zipfile" in cmd_lower and "z3" in cmd_lower:
		return SemanticAction.SETUP.value

	if any(re.search(rf"\b{re.escape(b)}\b", cmd_lower) for b in _BUILD_CMDS):
		if "make" in cmd_lower and re.search(r"\bmake\s+(test|bench|check|run)\b", cmd_lower):
			return SemanticAction.RUN.value
		return SemanticAction.BUILD.value
	if re.search(r"\b(cargo\s+build|go\s+build|dotnet\s+build)\b", cmd_lower):
		return SemanticAction.BUILD.value
	if re.search(r"\b(build\.sh|compile\.sh)\b", cmd_lower):
		return SemanticAction.BUILD.value
	if ("build.log" in cmd_lower or "compile.log" in cmd_lower) and not any(k in cmd_lower for k in ("verify", "eval", "test")):
		return SemanticAction.BUILD.value

	if re.search(r"\b(pytest|ctest|cargo\s+test|dotnet\s+test)\b", cmd_lower):
		return SemanticAction.RUN.value
	if any(
		e in ("dafny", "z3", "boogie") or e.startswith(("z3-", "./dafny", "./verify"))
		for e in execs
	) and "--version" not in cmd_lower:
		return SemanticAction.RUN.value
	if re.search(r"\./(?:run|eval|bench|reproduce|test|verify-all|figure)[a-zA-Z0-9_.-]*", cmd_lower):
		if re.search(r"^\./verify-all\b.*>\s*\S+\s+2>&1;\s*result=\$\?;\s*cp\b", cmd_lower):
			return SemanticAction.RUN.value
		return SemanticAction.RUN.value
	if re.search(r"\./[a-zA-Z0-9_.-]+(?:/[a-zA-Z0-9_.-]+)*/verify\b", cmd_lower):
		return SemanticAction.RUN.value
	if any(w in cmd_lower for w in ("benchmark", "reproduce", "run_exp", "eval_results", "plot_figures")):
		return SemanticAction.RUN.value
	if re.search(r"\bpython[0-9.]*\s+.*(?:run|eval|bench|reproduce)[a-zA-Z0-9_.-]*\.py\b", cmd_lower):
		return SemanticAction.RUN.value
	if re.search(r"\b(bash|sh)\s+.*(?:run|eval|bench|reproduce|figure)[a-zA-Z0-9_.-]*\.sh\b", cmd_lower):
		return SemanticAction.RUN.value
	if "figure" in cmd_lower and (".sh" in cmd_lower or "bash" in cmd_lower or "chmod +x" in cmd_lower):
		return SemanticAction.RUN.value
	if cmd_lower.startswith("until ") and "sleep" in cmd_lower:
		return SemanticAction.VERIFY.value

	if re.search(r"\b(ps\s+aux|ps\s+-ef|kill|killall|top|uptime|whoami|uname)\b", cmd_lower):
		return SemanticAction.MISC.value
	if "/proc/" in cmd_lower or "sleep" in cmd_lower:
		return SemanticAction.MISC.value

	if re.search(r"\bgit\s+(status|log|diff|branch|show|rev-parse|ls-files)\b", cmd_lower):
		return SemanticAction.SETUP.value
	if re.search(r"\bsed\s+-n\b", cmd_lower):
		return SemanticAction.SETUP.value
	if execs and all(e in _EXPLORATION_CMDS for e in execs):
		if re.search(r"\b(cat|head|tail|less|more|bat)\b.*\.(csv|tsv|png|pdf)\b", cmd_lower):
			return SemanticAction.VERIFY.value
		return SemanticAction.SETUP.value
	if any(re.search(rf"\b{re.escape(c)}\b", cmd_lower) for c in _EXPLORATION_CMDS):
		if re.search(r"\b(cat|head|tail|less|more|bat)\b.*\.(csv|tsv|png|pdf)\b", cmd_lower):
			return SemanticAction.VERIFY.value
		return SemanticAction.SETUP.value

	return _classify_description(desc_lower) or SemanticAction.MISC.value


def classify_event(
	event: dict[str, Any],
	trace_format: str = "",
	tool_action_map: dict[str, str] | None = None,
) -> tuple[str, str]:
	ev_type = str(event.get("type", ""))
	subtype = str(event.get("subtype", ""))

	if subtype == "init":
		return SemanticAction.SETUP.value, ACTION_METADATA[SemanticAction.SETUP.value]["short_label"]

	if trace_format == "claude_code" or "blocks" in event:
		if ev_type == "system":
			raw = event.get("raw")
			if isinstance(raw, dict):
				tool_use_id = raw.get("tool_use_id")
				if tool_use_id and tool_action_map and str(tool_use_id) in tool_action_map:
					action = tool_action_map[str(tool_use_id)]
					return action, ACTION_METADATA[action]["short_label"]
				raw_desc = raw.get("description") or raw.get("summary") or ""
				action = classify_command("", description=str(raw_desc))
				return action, ACTION_METADATA[action]["short_label"]
			return SemanticAction.MISC.value, ACTION_METADATA[SemanticAction.MISC.value]["short_label"]

		if ev_type == "result":
			return SemanticAction.MISC.value, ACTION_METADATA[SemanticAction.MISC.value]["short_label"]

		if ev_type == "assistant":
			blocks = event.get("blocks")
			if isinstance(blocks, list):
				for block in blocks:
					if not isinstance(block, dict):
						continue
					b_type = block.get("type")
					if b_type == "tool_use":
						tool_name = str(block.get("name", ""))
						inp = block.get("input")
						inp_dict: dict[str, Any] = inp if isinstance(inp, dict) else {}

						if tool_name == "Bash":
							cmd = str(inp_dict.get("command", ""))
							desc = str(inp_dict.get("description", ""))
							action = classify_command(cmd, description=desc)
							return action, ACTION_METADATA[action]["short_label"]

						if tool_name in ("Read", "Glob", "Grep"):
							file_path = str(inp_dict.get("file_path") or inp_dict.get("path") or "").lower()
							if file_path.endswith(".output") and "/tasks/" in file_path:
								return SemanticAction.VERIFY.value, ACTION_METADATA[SemanticAction.VERIFY.value]["short_label"]
							return SemanticAction.SETUP.value, ACTION_METADATA[SemanticAction.SETUP.value]["short_label"]

						if tool_name in ("Edit", "Write"):
							return SemanticAction.MODIFY.value, ACTION_METADATA[SemanticAction.MODIFY.value]["short_label"]

						return SemanticAction.MISC.value, ACTION_METADATA[SemanticAction.MISC.value]["short_label"]

				if any(isinstance(b, dict) and b.get("type") in ("thinking", "text") for b in blocks):
					return SemanticAction.THINK.value, ACTION_METADATA[SemanticAction.THINK.value]["short_label"]

			return SemanticAction.THINK.value, ACTION_METADATA[SemanticAction.THINK.value]["short_label"]

		if ev_type == "user":
			blocks = event.get("blocks")
			if isinstance(blocks, list) and tool_action_map:
				for block in blocks:
					if isinstance(block, dict) and block.get("type") == "tool_result":
						tool_use_id = block.get("tool_use_id")
						if tool_use_id and str(tool_use_id) in tool_action_map:
							action = tool_action_map[str(tool_use_id)]
							return action, ACTION_METADATA[action]["short_label"]

			return SemanticAction.MISC.value, ACTION_METADATA[SemanticAction.MISC.value]["short_label"]

		return SemanticAction.MISC.value, ACTION_METADATA[SemanticAction.MISC.value]["short_label"]

	if ev_type == "codex_item":
		item = event.get("item")
		if isinstance(item, dict):
			item_type = str(item.get("type", ""))
			if item_type == "command_execution":
				cmd = str(item.get("command", ""))
				action = classify_command(cmd)
				return action, ACTION_METADATA[action]["short_label"]

			if item_type == "file_change":
				return SemanticAction.MODIFY.value, ACTION_METADATA[SemanticAction.MODIFY.value]["short_label"]

			if item_type in ("reasoning", "agent_message", "web_search"):
				return SemanticAction.THINK.value, ACTION_METADATA[SemanticAction.THINK.value]["short_label"]

			return SemanticAction.MISC.value, ACTION_METADATA[SemanticAction.MISC.value]["short_label"]

	return SemanticAction.MISC.value, ACTION_METADATA[SemanticAction.MISC.value]["short_label"]


def compute_action_summary(events: Sequence[dict[str, Any]]) -> dict[str, Any]:
	counts: Counter[str] = Counter()
	sequence: list[str] = []

	for event in events:
		action = event.get("action")
		if not action:
			action, _ = classify_event(event)
		counts[action] += 1
		sequence.append(action)

	total = len(events)
	percentages: dict[str, float] = {}
	if total > 0:
		for action_key in SemanticAction:
			percentages[action_key.value] = round((counts[action_key.value] / total) * 100, 1)

	return {
		"total_events": total,
		"counts": {a.value: counts.get(a.value, 0) for a in SemanticAction},
		"percentages": percentages,
		"actions_present": [a.value for a in SemanticAction if counts.get(a.value, 0) > 0],
		"metadata": ACTION_METADATA,
	}
