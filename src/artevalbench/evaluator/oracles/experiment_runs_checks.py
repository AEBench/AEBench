"""Experiment runs oracle checks: similarity metrics and comparisons."""

from __future__ import annotations

import abc
import dataclasses
import enum
import math
import typing

from collections import Counter
from collections.abc import Callable, Sequence

from . import utils


_ResultT = typing.TypeVar("_ResultT")
_EPS = 1e-12


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class Comparison(typing.Generic[_ResultT]):

    observed: float
    reference: float
    result: _ResultT




def _require_equal_lengths(
    left: Sequence[float],
    right: Sequence[float],
    *,
    label: str,
) -> None:
    if len(left) != len(right):
        raise ValueError(
            f"{label}: length mismatch: left has {len(left)}, right has {len(right)}"
        )


def _require_all_finite(values: Sequence[float], *, label: str) -> None:
    for index, value in enumerate(values):
        if not math.isfinite(value):
            raise ValueError(f"{label}: non-finite value at index {index}: {value!r}")


def _jaccard_set_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    _require_all_finite(left, label="jaccard_set_similarity.left")
    _require_all_finite(right, label="jaccard_set_similarity.right")

    a = set(left)
    b = set(right)

    if len(a) != len(left):
        raise ValueError(
            "jaccard_set_similarity: left input contains duplicates; "
            "use jaccard_multiset_similarity if duplicates are meaningful"
        )
    if len(b) != len(right):
        raise ValueError(
            "jaccard_set_similarity: right input contains duplicates; "
            "use jaccard_multiset_similarity if duplicates are meaningful"
        )

    union = a | b
    if not union:
        return 1.0
    return len(a & b) / len(union)


def _jaccard_multiset_similarity(
    left: Sequence[float],
    right: Sequence[float],
) -> float:
    _require_all_finite(left, label="jaccard_multiset_similarity.left")
    _require_all_finite(right, label="jaccard_multiset_similarity.right")

    a = Counter(left)
    b = Counter(right)
    keys = set(a) | set(b)

    denominator = sum(max(a[key], b[key]) for key in keys)
    if denominator == 0:
        return 1.0

    numerator = sum(min(a[key], b[key]) for key in keys)
    return numerator / denominator


def _dot_product(left: Sequence[float], right: Sequence[float]) -> float:
    _require_equal_lengths(left, right, label="dot_product")
    _require_all_finite(left, label="dot_product.left")
    _require_all_finite(right, label="dot_product.right")
    return sum(a * b for a, b in zip(left, right, strict=True))


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    _require_equal_lengths(left, right, label="cosine_similarity")
    _require_all_finite(left, label="cosine_similarity.left")
    _require_all_finite(right, label="cosine_similarity.right")

    dot = 0.0
    norm_left = 0.0
    norm_right = 0.0
    for a, b in zip(left, right, strict=True):
        dot += a * b
        norm_left += a * a
        norm_right += b * b

    if norm_left <= _EPS and norm_right <= _EPS:
        return 1.0
    if norm_left <= _EPS or norm_right <= _EPS:
        return 0.0
    return dot / (math.sqrt(norm_left) * math.sqrt(norm_right))


def _pearson_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    _require_equal_lengths(left, right, label="pearson_similarity")
    if len(left) < 2:
        raise ValueError(
            f"pearson_similarity: need at least 2 samples, got {len(left)}"
        )
    _require_all_finite(left, label="pearson_similarity.left")
    _require_all_finite(right, label="pearson_similarity.right")

    n = float(len(left))
    mean_left = sum(left) / n
    mean_right = sum(right) / n

    cov = 0.0
    var_left = 0.0
    var_right = 0.0
    for a, b in zip(left, right, strict=True):
        da = a - mean_left
        db = b - mean_right
        cov += da * db
        var_left += da * da
        var_right += db * db

    if var_left <= _EPS and var_right <= _EPS:
        return 1.0 if list(left) == list(right) else 0.0
    if var_left <= _EPS or var_right <= _EPS:
        return 0.0

    return cov / (math.sqrt(var_left) * math.sqrt(var_right))


def _min_max_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    _require_equal_lengths(left, right, label="min_max_similarity")
    _require_all_finite(left, label="min_max_similarity.left")
    _require_all_finite(right, label="min_max_similarity.right")

    numerator = 0.0
    denominator = 0.0
    for index, (a, b) in enumerate(zip(left, right, strict=True)):
        if a < 0.0 or b < 0.0:
            raise ValueError(
                f"min_max_similarity: negative value at index {index}: left={a!r}, right={b!r}"
            )
        numerator += min(a, b)
        denominator += max(a, b)

    if denominator == 0.0:
        return 1.0
    return numerator / denominator


def _numbers_equal(a: float, b: float) -> bool:
    if not math.isfinite(a) or not math.isfinite(b):
        raise ValueError(f"numbers_equal: non-finite input: a={a!r}, b={b!r}")
    return a == b


def _default_numeric_similarity(a: float, b: float, *, abs_epsilon: float) -> float:
    if not math.isfinite(a) or not math.isfinite(b):
        raise ValueError(
            f"default_numeric_similarity: non-finite input: a={a!r}, b={b!r}"
        )

    denominator = max(abs(a), abs(b), abs_epsilon)
    score = 1.0 - (abs(a - b) / denominator)

    if score < 0.0:
        return 0.0
    if score > 1.0:
        return 1.0
    return score


def _summarize_mismatches_bool(
    comparisons: Sequence[Comparison[bool]],
    *,
    max_items: int = 10,
) -> str:
    mismatches: list[str] = []
    total_bad = 0
    for index, comparison in enumerate(comparisons):
        if not comparison.result:
            total_bad += 1
            if len(mismatches) < max_items:
                mismatches.append(
                    f"[{index}] observed={comparison.observed!r}, reference={comparison.reference!r}"
                )
    if not mismatches:
        return ""
    more = total_bad - len(mismatches)
    suffix = f"\n... ({more} more)" if more > 0 else ""
    return "mismatches:\n" + "\n".join(mismatches) + suffix


def _summarize_mismatches_threshold(
    scores: Sequence[Comparison[float]],
    *,
    threshold: float,
    max_items: int = 10,
) -> str:
    mismatches: list[str] = []
    total_bad = 0
    for index, comparison in enumerate(scores):
        if comparison.result < threshold:
            total_bad += 1
            if len(mismatches) < max_items:
                mismatches.append(
                    f"[{index}] score={comparison.result:.6f} "
                    f"observed={comparison.observed!r}, reference={comparison.reference!r}"
                )
    if not mismatches:
        return ""
    more = total_bad - len(mismatches)
    suffix = f"\n... ({more} more)" if more > 0 else ""
    return "mismatches:\n" + "\n".join(mismatches) + suffix


class SimilarityMetric(enum.Enum):

    JACCARD_SET = "jaccard_set"
    JACCARD_MULTISET = "jaccard_multiset"
    DOT_PRODUCT = "dot_product"
    COSINE = "cosine"
    PEARSON = "pearson"
    MIN_MAX = "min_max"


def compute_similarity(
    metric: SimilarityMetric,
    left: Sequence[float],
    right: Sequence[float],
) -> float:
    if metric == SimilarityMetric.JACCARD_SET:
        return _jaccard_set_similarity(left, right)
    if metric == SimilarityMetric.JACCARD_MULTISET:
        return _jaccard_multiset_similarity(left, right)
    if metric == SimilarityMetric.DOT_PRODUCT:
        return _dot_product(left, right)
    if metric == SimilarityMetric.COSINE:
        return _cosine_similarity(left, right)
    if metric == SimilarityMetric.PEARSON:
        return _pearson_similarity(left, right)
    if metric == SimilarityMetric.MIN_MAX:
        return _min_max_similarity(left, right)
    raise ValueError(f"unsupported similarity metric: {metric!r}")


def elementwise_similarity_scores(
    observed: Sequence[float],
    reference: Sequence[float],
    *,
    similarity_fn: Callable[[float, float], float] | None = None,
    abs_epsilon: float = 1e-12,
) -> list[Comparison[float]]:
    _require_equal_lengths(
        observed,
        reference,
        label="elementwise_similarity_scores",
    )
    _require_all_finite(observed, label="elementwise_similarity_scores.observed")
    _require_all_finite(reference, label="elementwise_similarity_scores.reference")
    if abs_epsilon <= 0:
        raise ValueError("elementwise_similarity_scores: abs_epsilon must be > 0")

    if similarity_fn is None:

        def similarity_fn(a: float, b: float) -> float:
            return _default_numeric_similarity(a, b, abs_epsilon=abs_epsilon)

    out: list[Comparison[float]] = []
    for a, b in zip(observed, reference, strict=True):
        out.append(Comparison(observed=a, reference=b, result=similarity_fn(a, b)))
    return out


def elementwise_equal(
    observed: Sequence[float],
    reference: Sequence[float],
) -> list[Comparison[bool]]:
    _require_equal_lengths(observed, reference, label="elementwise_equal")
    _require_all_finite(observed, label="elementwise_equal.observed")
    _require_all_finite(reference, label="elementwise_equal.reference")

    out: list[Comparison[bool]] = []
    for a, b in zip(observed, reference, strict=True):
        out.append(
            Comparison(
                observed=a,
                reference=b,
                result=_numbers_equal(a, b),
            )
        )
    return out


def elementwise_similarity_threshold(
    observed: Sequence[float],
    reference: Sequence[float],
    *,
    threshold: float,
    similarity_fn: Callable[[float, float], float] | None = None,
    abs_epsilon: float = 1e-12,
) -> list[Comparison[bool]]:
    if not math.isfinite(threshold):
        raise ValueError(
            f"elementwise_similarity_threshold: threshold must be finite, got {threshold!r}"
        )
    if not (0.0 <= threshold <= 1.0):
        raise ValueError(
            f"elementwise_similarity_threshold: threshold must be in [0, 1], got {threshold!r}"
        )

    scores = elementwise_similarity_scores(
        observed,
        reference,
        similarity_fn=similarity_fn,
        abs_epsilon=abs_epsilon,
    )

    out: list[Comparison[bool]] = []
    for score in scores:
        out.append(
            Comparison(
                observed=score.observed,
                reference=score.reference,
                result=(score.result >= threshold),
            )
        )
    return out


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class ListSimilarityCheck(utils.BaseCheck):

    observed: Sequence[float]
    reference: Sequence[float]
    metric: SimilarityMetric = SimilarityMetric.JACCARD_SET
    min_similarity: float = 1.0

    def __post_init__(self) -> None:
        if not math.isfinite(self.min_similarity):
            raise ValueError(f"{self.name}: min_similarity must be finite")

        if self.metric in (
            SimilarityMetric.JACCARD_SET,
            SimilarityMetric.JACCARD_MULTISET,
            SimilarityMetric.MIN_MAX,
        ):
            if not (0.0 <= self.min_similarity <= 1.0):
                raise ValueError(
                    f"{self.name}: {self.metric.value} min_similarity must be in [0, 1], "
                    f"got {self.min_similarity!r}"
                )
        if self.metric in (SimilarityMetric.COSINE, SimilarityMetric.PEARSON):
            if not (-1.0 <= self.min_similarity <= 1.0):
                raise ValueError(
                    f"{self.name}: {self.metric.value} min_similarity must be in [-1, 1], "
                    f"got {self.min_similarity!r}"
                )

        object.__setattr__(self, "observed", tuple(self.observed))
        object.__setattr__(self, "reference", tuple(self.reference))

    def check(self) -> utils.CheckResult:
        try:
            score = compute_similarity(
                self.metric,
                self.observed,
                self.reference,
            )
        except ValueError as exc:
            return utils.CheckResult.failure(f"{self.name}: {exc}")

        if score < self.min_similarity:
            return utils.CheckResult.failure(
                f"{self.metric.value} similarity {score:.6f} < min_similarity {self.min_similarity:.6f}"
            )
        return utils.CheckResult.success()


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class ElementwiseEqualityCheck(utils.BaseCheck):

    observed: Sequence[float]
    reference: Sequence[float]
    max_mismatches_to_report: int = 10

    def __post_init__(self) -> None:
        if self.max_mismatches_to_report <= 0:
            raise ValueError(f"{self.name}: max_mismatches_to_report must be > 0")
        object.__setattr__(self, "observed", tuple(self.observed))
        object.__setattr__(self, "reference", tuple(self.reference))

    def check(self) -> utils.CheckResult:
        try:
            comparisons = elementwise_equal(
                self.observed,
                self.reference,
            )
        except ValueError as exc:
            return utils.CheckResult.failure(f"{self.name}: {exc}")

        if all(comparison.result for comparison in comparisons):
            return utils.CheckResult.success()

        detail = _summarize_mismatches_bool(
            comparisons,
            max_items=self.max_mismatches_to_report,
        )
        msg = "elementwise equality check failed"
        if detail:
            msg = f"{msg}\n{detail}"
        return utils.CheckResult.failure(msg)


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class ElementwiseSimilarityThresholdCheck(utils.BaseCheck):

    observed: Sequence[float]
    reference: Sequence[float]
    threshold: float
    abs_epsilon: float = 1e-12
    max_mismatches_to_report: int = 10

    def __post_init__(self) -> None:
        if not math.isfinite(self.threshold):
            raise ValueError(f"{self.name}: threshold must be finite")
        if not (0.0 <= self.threshold <= 1.0):
            raise ValueError(
                f"{self.name}: threshold must be in [0, 1], got {self.threshold!r}"
            )
        if self.abs_epsilon <= 0:
            raise ValueError(f"{self.name}: abs_epsilon must be > 0")
        if self.max_mismatches_to_report <= 0:
            raise ValueError(f"{self.name}: max_mismatches_to_report must be > 0")
        object.__setattr__(self, "observed", tuple(self.observed))
        object.__setattr__(self, "reference", tuple(self.reference))

    def check(self) -> utils.CheckResult:
        try:
            scores = elementwise_similarity_scores(
                self.observed,
                self.reference,
                abs_epsilon=self.abs_epsilon,
            )
        except ValueError as exc:
            return utils.CheckResult.failure(f"{self.name}: {exc}")

        if all(score.result >= self.threshold for score in scores):
            return utils.CheckResult.success()

        detail = _summarize_mismatches_threshold(
            scores,
            threshold=self.threshold,
            max_items=self.max_mismatches_to_report,
        )
        msg = f"elementwise similarity below threshold {self.threshold:.6f}"
        if detail:
            msg = f"{msg}\n{detail}"
        return utils.CheckResult.failure(msg)


class OracleExperimentRunsBase(utils._OraclePhaseBase):
    """Base for experiment runs oracle phases."""

    phase_label = "ExperimentRuns"

    similarity = staticmethod(compute_similarity)
    elementwise_equal = staticmethod(elementwise_equal)
    elementwise_similarity_scores = staticmethod(elementwise_similarity_scores)
    elementwise_similarity_threshold = staticmethod(elementwise_similarity_threshold)

    @abc.abstractmethod
    def requirements(self) -> Sequence[utils.BaseCheck]:
        raise NotImplementedError