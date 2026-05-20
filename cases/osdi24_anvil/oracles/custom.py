from __future__ import annotations

from pathlib import Path


def _parse_table3(path: Path) -> dict[str, tuple[float, float]]:
	headers = (
		"Controller",
		"Verified (Anvil) Mean",
		"Verified (Anvil) Max",
		"Reference (unverified) Mean",
		"Reference (unverified) Max",
	)
	rows: dict[str, tuple[float, float]] = {}
	for line in path.read_text(encoding="utf-8").splitlines():
		stripped = line.strip()
		if not stripped.startswith("|") or stripped.count("|") < 5:
			continue
		cells = [cell.strip() for cell in stripped.strip("|").split("|")]
		if tuple(cells) == headers:
			continue
		if all(set(cell) <= {"-", ":"} for cell in cells):
			continue
		if len(cells) != len(headers):
			continue
		controller = cells[0]
		verified_mean = float(cells[1].replace(",", ""))
		verified_max = float(cells[2].replace(",", ""))
		reference_mean = float(cells[3].replace(",", ""))
		reference_max = float(cells[4].replace(",", ""))
		mean_ratio = verified_mean / reference_mean if reference_mean else 0.0
		max_ratio = verified_max / reference_max if reference_max else 0.0
		rows[controller] = (mean_ratio, max_ratio)
	return rows


def _compute_ratios(row: dict[str, float]) -> tuple[float, float]:
	mean_ratio = row["verified_anvil_mean"] / row["reference_unverified_mean"]
	max_ratio = row["verified_anvil_max"] / row["reference_unverified_max"]
	return (mean_ratio, max_ratio)
