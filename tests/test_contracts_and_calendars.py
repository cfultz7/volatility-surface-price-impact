from datetime import date

import pandas as pd

from surface_impact.calendars import eligible_sessions
from surface_impact.contracts import QuarterlyContract, active_contract_schedule, third_friday


def test_third_friday_and_roll_thursday():
    assert third_friday(2024, 3) == date(2024, 3, 15)
    assert QuarterlyContract(2024, 3).nominal_roll_thursday == date(2024, 3, 7)
    assert QuarterlyContract(2025, 12).nominal_roll_thursday == date(2025, 12, 11)


def test_roll_schedule_2024_2025():
    s = [x.day for x in eligible_sessions("2024-01-01", "2025-12-31")]
    sched = active_contract_schedule(s)
    rolls = [(d, c.candidate_raw_symbol()) for d, (c, r) in sched.items() if r]
    assert rolls == [
        (date(2024, 3, 7), "ESM4"), (date(2024, 6, 13), "ESU4"), (date(2024, 9, 12), "ESZ4"),
        (date(2024, 12, 12), "ESH5"), (date(2025, 3, 13), "ESM5"), (date(2025, 6, 12), "ESU5"),
        (date(2025, 9, 11), "ESZ5"), (date(2025, 12, 11), "ESH6"),
    ]
    assert sched[date(2024, 3, 6)][0].candidate_raw_symbol() == "ESH4"


def test_roll_moves_forward_when_thursday_not_eligible():
    # Pretend the nominal roll Thursday is a closure: next eligible session is used.
    s = [x.day for x in eligible_sessions("2024-02-26", "2024-03-14")]
    elig = set(s) - {date(2024, 3, 7)}
    sched = active_contract_schedule(sorted(elig), eligible=elig)
    assert sched[date(2024, 3, 8)] [1] is True
    assert sched[date(2024, 3, 8)][0].candidate_raw_symbol() == "ESM4"


def test_holidays_closures_early_closes_and_dst():
    days = {x.day: x for x in eligible_sessions("2024-01-01", "2025-12-31")}
    assert date(2025, 1, 9) not in days  # national day of mourning closure
    assert date(2024, 7, 4) not in days
    assert days[date(2024, 11, 29)].early_close
    # DST: 09:30 NY is 14:30 UTC in winter and 13:30 UTC in summer
    assert days[date(2024, 2, 1)].open_utc == pd.Timestamp("2024-02-01 14:30", tz="UTC")
    assert days[date(2024, 8, 1)].open_utc == pd.Timestamp("2024-08-01 13:30", tz="UTC")
    s = days[date(2024, 11, 29)]
    assert s.last_obs_utc == s.close_utc - pd.Timedelta(minutes=15)
    assert s.observation_endpoints()[0].tz_convert("America/New_York").strftime("%H:%M") == "09:40"
