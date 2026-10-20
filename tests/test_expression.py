from alpharesearch.expression import ExpressionValidator


def test_valid_expression_is_canonicalized():
    result = ExpressionValidator().validate("rank( ts_delta(close, 5) )")
    assert result.valid
    assert result.canonical == "rank(ts_delta(close,5))"
    assert result.fields == ["close"]
    assert "ts_delta" in result.operators


def test_unknown_field_is_rejected():
    result = ExpressionValidator().validate("rank(secret_future_price)")
    assert not result.valid
    assert any(issue.code == "UNKNOWN_FIELD" for issue in result.issues)


def test_negative_delay_is_lookahead():
    result = ExpressionValidator().validate("ts_delay(close, -1)")
    assert not result.valid
    assert any(issue.code == "LOOKAHEAD_DELAY" for issue in result.issues)


def test_parameter_variants_share_family_hash():
    validator = ExpressionValidator()
    first = validator.validate("rank(ts_delta(close, 5))")
    second = validator.validate("rank(ts_delta(close, 20))")
    assert first.family_hash == second.family_hash


def test_commutative_expression_deduplicates():
    validator = ExpressionValidator()
    first = validator.validate("close + volume")
    second = validator.validate("volume + close")
    assert first.canonical == second.canonical
