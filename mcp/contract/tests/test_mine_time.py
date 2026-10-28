"""MineTime：矿井本地时间不带时区，JSON Schema 不能用 format: date-time。"""

import json
import re
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError
from rescue_contract.models import MineClock


def test_schema_has_no_date_time_format():
    schema = MineClock.model_json_schema()
    assert "date-time" not in json.dumps(schema)


def test_naive_time_roundtrips_and_matches_pattern():
    pattern = MineClock.model_json_schema()["properties"]["now"]["pattern"]
    for t in (datetime(2026, 11, 7, 8, 0), datetime(2026, 11, 2, 9, 42, 0, 981802)):
        dumped = MineClock(now=t).model_dump(mode="json")["now"]
        assert re.match(pattern, dumped), dumped
        assert MineClock.model_validate({"now": dumped}).now == t


@pytest.mark.parametrize("value", ["2026-11-07T08:00:00+08:00", "2026-11-07T00:00:00Z", datetime(2026, 11, 7, tzinfo=timezone(timedelta(hours=8)))])
def test_aware_time_is_rejected(value):
    with pytest.raises(ValidationError, match="不带时区"):
        MineClock(now=value)
