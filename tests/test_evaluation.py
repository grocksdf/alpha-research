from alpharesearch.evaluation import EvaluationEngine
from alpharesearch.expression import ExpressionValidator
from alpharesearch.models import SimulationResult

RULES = {
    "min_sharpe": 1.25,
    "min_fitness": 1.0,
    "min_turnover": 0.01,
    "max_turnover": 0.70,
    "max_drawdown": 0.35,
    "min_years": 3,
    "min_positive_year_fraction": 0.60,
    "max_self_correlation": 0.70,
    "max_production_correlation": 0.70,
    "weights": {
        "sharpe": 0.25,
        "fitness": 0.20,
        "turnover": 0.10,
        "drawdown": 0.10,
        "year_stability": 0.20,
        "novelty": 0.15,
    },
}


def test_good_candidate_is_eligible():
    validation = ExpressionValidator().validate("rank(ts_delta(close, 5))")
    simulation = SimulationResult(
        "sim/1",
        "alpha-1",
        {
            "is": {
                "sharpe": 1.8,
                "fitness": 1.4,
                "turnover": 0.25,
                "drawdown": 0.12,
            },
            "yearly": {
                "2022": {"sharpe": 1.1},
                "2023": {"sharpe": 1.4},
                "2024": {"sharpe": 0.8},
            },
        },
        [
            {"name": "SELF_CORRELATION", "status": "PASS", "value": 0.5},
            {"name": "PROD_CORRELATION", "status": "PASS", "value": 0.4},
        ],
        {},
    )
    result = EvaluationEngine(RULES).evaluate(validation, simulation)
    assert result.eligible
    assert result.score > 0


def test_failed_platform_check_blocks_candidate():
    validation = ExpressionValidator().validate("rank(ts_delta(close, 5))")
    simulation = SimulationResult(
        "sim/1",
        "alpha-1",
        {"sharpe": 2.0, "fitness": 1.5, "turnover": 0.2},
        [{"name": "CONCENTRATED_WEIGHT", "status": "FAIL"}],
        {},
    )
    result = EvaluationEngine(RULES).evaluate(validation, simulation)
    assert not result.eligible
    assert any("Platform checks failed" in reason for reason in result.reasons)
