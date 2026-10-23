from __future__ import annotations

TABLES_REF = "tables.ref.json"

# Fraction of each table's value cells that must match the reference
TABLE_MIN_MATCH_FRACTION = 0.8

TABLES_DIR = ("paper", "tabs")
FIGURES_DIR = ("paper", "figs")
OUTPUT_DIR = ("output",)

# Paper table to .tex file (see DepSurf's save_latex)
TABLE_FILES = {
	"table3_src": "src.tex",
	"table4_breakdown": "breakdown.tex",
	"table5_config": "cfg.tex",
	"table6_dup": "dup.tex",
	"table7_bcc": "bcc.tex",
	"table8_summary": "summary.tex",
}

# Paper figure to .pdf file (see DepSurf's save_fig)
FIGURE_FILES = {
	"figure4_report": "report.pdf",
	"figure5_inline": "inline.pdf",
	"figure6_rename": "rename.pdf",
}

# Object files (see DepSurf's save_pkl) need to generate tables and figures
PKL_FILES = ("src.pkl", "config.pkl", "inline.pkl", "rename.pkl", "dup.pkl", "bcc.pkl")

# Per-version text format logs for the 40_inline covers script
VERSION_DUMPS = {
	"inline": ("selective.txt", 21),
	"dup": ("static_static.txt", 5),
}

# Log files content written by 30_diff (Table 3)
SRC_DIFF_PAIRS = {
	"LTS (2 yr)": ("LTS", ("4.4_4.15", "4.15_5.4", "5.4_5.15", "5.15_6.8")),
	"Regular Releases (6 mo)": (
		"Regular",
		(
			"4.4_4.8",
			"4.8_4.10",
			"4.10_4.13",
			"4.13_4.15",
			"4.15_4.18",
			"4.18_5.0",
			"5.0_5.3",
			"5.3_5.4",
			"5.4_5.8",
			"5.8_5.11",
			"5.11_5.13",
			"5.13_5.15",
			"5.15_5.19",
			"5.19_6.2",
			"6.2_6.5",
			"6.5_6.8",
		),
	),
}

# 30_diff generated files (Table 5)
CONFIG_DIFF_PAIRS = (
	"Arch/amd64_arm64",
	"Arch/amd64_armhf",
	"Arch/amd64_ppc64el",
	"Arch/amd64_riscv64",
	"Flavor/generic_aws",
	"Flavor/generic_azure",
	"Flavor/generic_gcp",
	"Flavor/generic_lowlatency",
)
