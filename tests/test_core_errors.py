from __future__ import annotations


from aqsp.core.errors import (
    DataError,
    FreshnessError,
    NotExecutableError,
    DataInconsistencyError,
    MissingDataError,
)


def test_freshness_error():
    exc = FreshnessError("600000", 5, 3)
    assert exc.symbol == "600000"
    assert exc.days_lag == 5
    assert exc.max_allowed == 3
    assert "数据过期" in str(exc)


def test_not_executable_error():
    exc = NotExecutableError("600000", "limit_up_at_open")
    assert exc.symbol == "600000"
    assert exc.reason == "limit_up_at_open"
    assert "信号不可成交" in str(exc)


def test_data_inconsistency_error():
    exc = DataInconsistencyError("600000", "akshare", "sina", 1.5)
    assert exc.symbol == "600000"
    assert exc.source1 == "akshare"
    assert exc.source2 == "sina"
    assert exc.diff_pct == 1.5


def test_missing_data_error():
    exc = MissingDataError("600000", "no data")
    assert exc.symbol == "600000"
    assert exc.reason == "no data"
    assert "缺失数据" in str(exc)


def test_error_hierarchy():
    assert issubclass(FreshnessError, DataError)
    assert issubclass(DataInconsistencyError, DataError)
    assert issubclass(MissingDataError, DataError)
    assert not issubclass(NotExecutableError, DataError)
