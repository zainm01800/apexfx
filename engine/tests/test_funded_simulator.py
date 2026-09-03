from datetime import date, time

import pandas as pd
import pytest

from apex_quant.validation.funded_simulator import (
    DayRecord,
    EquityPoint,
    FundedRules,
    bootstrap_funded_replay,
    chunked_bootstrap_funded_replay,
    firm_session_key,
    iter_stationary_bootstrap_index_chunks,
    make_chunked_bootstrap_spec,
    make_stationary_bootstrap_plan,
    replay_funded_rules,
    resample_day_records,
    synchronized_funded_bootstrap,
    wilson_interval,
)
from apex_quant.validation.funded_simulator import _rebase_sampled_days


def _day(
    number: int,
    *,
    start: float,
    low: float,
    end: float,
    end_equity: float | None = None,
    day_start_equity: float | None = None,
    closed_pnl: float | None = None,
    equity_path: tuple[EquityPoint, ...] = (),
    verified_flat_at_end: bool = False,
    positions_opened: int | None = None,
) -> DayRecord:
    timestamp = pd.Timestamp("2024-01-01 23:00", tz="UTC") + pd.Timedelta(
        days=number
    )
    return DayRecord(
        session=timestamp.date(),
        timestamp=timestamp,
        day_start_balance=start,
        intraday_min_equity=low,
        end_balance=end,
        end_equity=end if end_equity is None else end_equity,
        closed_pnl=end - start if closed_pnl is None else closed_pnl,
        day_start_equity=day_start_equity,
        intraday_min_timestamp=timestamp - pd.Timedelta(hours=8),
        equity_path=equity_path,
        verified_flat_at_end=verified_flat_at_end,
        positions_opened=positions_opened,
    )


def test_intraday_breach_is_not_hidden_by_profitable_eod_close():
    events = pd.DataFrame(
        [
            ("2024-01-02 00:00:00+00:00", 100_000, 100_000),
            ("2024-01-02 12:15:00+00:00", 100_000, 96_900),
            ("2024-01-02 23:00:00+00:00", 110_000, 110_000),
        ],
        columns=["timestamp", "balance", "equity"],
    )
    result = replay_funded_rules(events, FundedRules(initial_balance=100_000))

    assert result.status == "breached"
    assert result.reason == "daily_loss"
    assert result.timestamp == pd.Timestamp("2024-01-02 12:15:00+00:00")
    assert result.margins.daily_loss_buffer == pytest.approx(-100.0)


def test_eod_trailing_floor_advances_and_then_breaches_live_equity():
    rules = FundedRules(
        initial_balance=100_000,
        profit_target_pct=None,
        daily_loss_pct=0.20,
        max_loss_pct=0.10,
        max_loss_mode="eod_trailing",
    )
    result = replay_funded_rules(
        [
            _day(0, start=100_000, low=100_000, end=109_000),
            _day(1, start=109_000, low=98_900, end=98_900),
        ],
        rules,
    )

    assert result.status == "breached"
    assert result.reason == "max_loss"
    assert result.max_loss_floor == pytest.approx(99_000)
    assert result.margins.max_loss_buffer == pytest.approx(-100)


def test_best_day_consistency_delays_pass_until_profit_is_distributed():
    rules = FundedRules(
        initial_balance=100_000,
        profit_target_pct=0.10,
        daily_loss_pct=0.20,
        max_loss_pct=0.20,
        best_day_max_profit_share=0.50,
    )
    first_two_days = [
        _day(0, start=100_000, low=100_000, end=106_000, closed_pnl=6_000),
        _day(1, start=106_000, low=106_000, end=110_000, closed_pnl=4_000),
    ]

    delayed = replay_funded_rules(first_two_days, rules)
    passed = replay_funded_rules(
        first_two_days
        + [
            _day(
                2, start=110_000, low=110_000, end=112_000,
                closed_pnl=2_000, verified_flat_at_end=True,
            )
        ],
        rules,
    )

    assert delayed.status == "active"
    assert delayed.margins.profit_target_buffer == pytest.approx(0.0)
    assert delayed.margins.best_day_profit_share == pytest.approx(0.60)
    assert delayed.margins.best_day_consistency_buffer == pytest.approx(-1_000)
    assert passed.status == "passed"
    assert passed.sessions_processed == 3
    assert passed.margins.best_day_profit_share == pytest.approx(0.50)


def test_best_day_denominator_is_positive_days_profit_not_net_profit():
    rules = FundedRules(
        initial_balance=100_000,
        profit_target_pct=0.10,
        daily_loss_pct=0.20,
        max_loss_pct=0.20,
        best_day_max_profit_share=0.50,
    )
    result = replay_funded_rules(
        [
            _day(0, start=100_000, low=100_000, end=106_000, closed_pnl=6_000),
            _day(1, start=106_000, low=103_500, end=104_000, closed_pnl=-2_000),
            _day(
                2, start=104_000, low=104_000, end=110_000,
                closed_pnl=6_000, verified_flat_at_end=True,
            ),
        ],
        rules,
    )

    # Best day is 60% of 10k net profit, but exactly 50% of the 12k earned on
    # positive days.  The loss day must not shrink the official denominator.
    assert result.status == "passed"
    assert result.positive_days_profit == pytest.approx(12_000)
    assert result.margins.positive_days_profit == pytest.approx(12_000)
    assert result.margins.best_day_profit_share == pytest.approx(0.50)
    assert result.margins.best_day_consistency_buffer == pytest.approx(0.0)


def test_best_day_can_explicitly_use_net_profit_for_other_firm_rules():
    rules = FundedRules(
        initial_balance=100_000,
        profit_target_pct=0.10,
        daily_loss_pct=0.20,
        max_loss_pct=0.20,
        best_day_max_profit_share=0.50,
        best_day_profit_basis="net_profit",
    )
    result = replay_funded_rules(
        [
            _day(0, start=100_000, low=100_000, end=106_000, closed_pnl=6_000),
            _day(1, start=106_000, low=103_500, end=104_000, closed_pnl=-2_000),
            _day(2, start=104_000, low=104_000, end=110_000, closed_pnl=6_000),
        ],
        rules,
    )

    assert result.status == "active"
    assert result.margins.best_day_profit_share == pytest.approx(0.60)
    assert result.margins.best_day_consistency_buffer == pytest.approx(-1_000)


def test_breach_takes_precedence_over_target_reached_on_same_day():
    result = replay_funded_rules(
        [_day(0, start=100_000, low=96_999, end=110_000)],
        FundedRules(initial_balance=100_000),
    )

    assert result.status == "breached"
    assert result.reason == "daily_loss"


def test_profit_target_requires_balance_equity_and_verified_flat_state():
    rules = FundedRules(
        initial_balance=100_000,
        profit_target_pct=0.10,
        daily_loss_pct=0.20,
        max_loss_pct=0.20,
    )

    open_loss = replay_funded_rules(
        [
            _day(
                0,
                start=100_000,
                low=100_000,
                end=110_000,
                end_equity=105_000,
            )
        ],
        rules,
    )
    profitable_but_open = replay_funded_rules(
        [_day(0, start=100_000, low=100_000, end=110_000)],
        rules,
    )
    fully_qualified = replay_funded_rules(
        [
            _day(
                0, start=100_000, low=100_000, end=110_000,
                verified_flat_at_end=True,
            )
        ],
        rules,
    )

    assert open_loss.status == "active"
    assert open_loss.margins.profit_target_buffer == pytest.approx(-5_000)
    assert profitable_but_open.status == "active"
    assert fully_qualified.status == "passed"


def test_minimum_trading_days_delays_target_and_ignores_flat_sessions():
    rules = FundedRules(
        initial_balance=100_000,
        profit_target_pct=0.10,
        daily_loss_pct=0.20,
        max_loss_pct=0.20,
        minimum_trading_days=4,
    )
    days = [
        _day(
            0, start=100_000, low=100_000, end=110_000,
            positions_opened=1,
        ),
        # Merely elapsed firm time is not execution evidence and must not count.
        _day(
            1, start=110_000, low=110_000, end=110_000,
            positions_opened=0,
        ),
        _day(
            2, start=110_000, low=110_000, end=110_100,
            positions_opened=1,
        ),
        _day(
            3, start=110_100, low=110_000, end=110_000,
            positions_opened=1,
        ),
        _day(
            4, start=110_000, low=110_000, end=110_100,
            positions_opened=1, verified_flat_at_end=True,
        ),
    ]

    one_day = replay_funded_rules(days[:1], rules)
    not_enough_trading_days = replay_funded_rules(days[:4], rules)
    qualified = replay_funded_rules(days, rules)

    assert one_day.status == "active"
    assert one_day.trading_days == 1
    assert one_day.minimum_trading_days == 4
    assert not_enough_trading_days.status == "active"
    assert not_enough_trading_days.trading_days == 3
    assert qualified.status == "passed"
    assert qualified.sessions_processed == 5
    assert qualified.trading_days == 4


def test_partial_close_pnl_days_do_not_invent_opening_days():
    rules = FundedRules(
        initial_balance=100_000,
        profit_target_pct=0.10,
        daily_loss_pct=0.20,
        max_loss_pct=0.20,
        minimum_trading_days=4,
    )
    days = [
        _day(
            0, start=100_000, low=100_000, end=102_000,
            positions_opened=1,
        ),
        _day(
            1, start=102_000, low=102_000, end=105_000,
            positions_opened=0,
        ),
        _day(
            2, start=105_000, low=105_000, end=108_000,
            positions_opened=0,
        ),
        _day(
            3, start=108_000, low=108_000, end=110_000,
            positions_opened=0,
        ),
    ]

    result = replay_funded_rules(days, rules)

    assert result.status == "active"
    assert result.trading_days == 1


def test_minimum_trading_days_default_and_validation_preserve_legacy_behavior():
    rules = FundedRules(
        initial_balance=100_000,
        profit_target_pct=0.10,
        daily_loss_pct=0.20,
        max_loss_pct=0.20,
    )
    result = replay_funded_rules(
        [
            _day(
                0, start=100_000, low=100_000, end=110_000,
                verified_flat_at_end=True,
            )
        ],
        rules,
    )

    assert rules.minimum_trading_days == 0
    assert result.status == "passed"
    with pytest.raises(TypeError, match="non-negative integer"):
        FundedRules(initial_balance=100_000, minimum_trading_days=4.0)
    with pytest.raises(TypeError, match="non-negative integer"):
        FundedRules(initial_balance=100_000, minimum_trading_days=True)
    with pytest.raises(ValueError, match="non-negative integer"):
        FundedRules(initial_balance=100_000, minimum_trading_days=-1)


def test_verified_flat_attestation_requires_consistent_balance_and_equity():
    rules = FundedRules(
        initial_balance=100_000,
        profit_target_pct=0.10,
        daily_loss_pct=0.20,
        max_loss_pct=0.20,
    )
    with pytest.raises(
        ValueError, match="verified_flat_at_end requires end_balance and end_equity"
    ):
        _day(
            0,
            start=100_000,
            low=100_000,
            end=110_000,
            end_equity=105_000,
            verified_flat_at_end=True,
        )

    result = replay_funded_rules(
        [
            _day(
                0,
                start=100_000,
                low=100_000,
                end=110_000,
                verified_flat_at_end=True,
            )
        ],
        rules,
    )

    assert result.status == "passed"
    assert result.margins.profit_target_buffer == pytest.approx(0.0)


def test_verified_flat_attestation_is_propagated_and_strictly_typed():
    rules = FundedRules(
        initial_balance=100_000,
        profit_target_pct=0.10,
        daily_loss_pct=0.20,
        max_loss_pct=0.20,
    )
    events = pd.DataFrame(
        {
            "timestamp": [
                "2024-01-02 00:00:00+00:00",
                "2024-01-02 23:00:00+00:00",
            ],
            "balance": [100_000, 110_000],
            "equity": [100_000, 110_000],
            "verified_flat_at_end": [False, True],
        }
    )

    assert replay_funded_rules(events, rules).status == "passed"
    contradictory = events.copy()
    contradictory.loc[1, "equity"] = 105_000
    with pytest.raises(
        ValueError, match="verified_flat_at_end requires balance and equity"
    ):
        replay_funded_rules(contradictory, rules)

    invalid = events.copy()
    invalid["verified_flat_at_end"] = invalid["verified_flat_at_end"].astype(object)
    invalid.loc[1, "verified_flat_at_end"] = "true"
    with pytest.raises(TypeError, match="verified_flat_at_end must be boolean"):
        replay_funded_rules(invalid, rules)


def test_firm_timezone_and_rollover_define_session_without_fixed_utc_offset():
    # 22:30 UTC is 23:30 London in summer: before a 23:45 firm-local rollover.
    key = firm_session_key(
        pd.Timestamp("2024-07-10 22:30:00+00:00"),
        timezone="Europe/London",
        rollover=pd.Timestamp("23:45").time(),
    )
    assert key == date(2024, 7, 9)


def test_session_key_does_not_regress_during_autumn_dst_fold():
    rollover = time(1, 30, fold=0)  # first 01:30, while London is still on BST
    before_fold = firm_session_key(
        pd.Timestamp("2024-10-27 00:45:00+00:00"),
        timezone="Europe/London",
        rollover=rollover,
    )
    after_fold = firm_session_key(
        pd.Timestamp("2024-10-27 01:15:00+00:00"),
        timezone="Europe/London",
        rollover=rollover,
    )

    assert before_fold == date(2024, 10, 27)
    assert after_fold == date(2024, 10, 27)


def test_stationary_bootstrap_plan_is_seed_deterministic():
    first = make_stationary_bootstrap_plan(
        12, n_paths=8, sample_length=20, mean_block_lengths=(5, 10, 21), seed=987
    )
    again = make_stationary_bootstrap_plan(
        12, n_paths=8, sample_length=20, mean_block_lengths=(21, 5, 10), seed=987
    )
    different = make_stationary_bootstrap_plan(
        12, n_paths=8, sample_length=20, mean_block_lengths=(5, 10, 21), seed=988
    )

    assert first == again
    assert first != different


def test_chunked_index_stream_exactly_matches_small_materialized_plan():
    plan = make_stationary_bootstrap_plan(
        7,
        n_paths=23,
        sample_length=11,
        mean_block_lengths=(2, 5),
        seed=456,
    )
    spec = make_chunked_bootstrap_spec(
        7,
        n_paths=23,
        sample_length=11,
        mean_block_lengths=(2, 5),
        seed=456,
        chunk_size=6,
    )

    for mean_length in spec.mean_block_lengths:
        streamed = tuple(
            tuple(int(index) for index in row)
            for chunk in iter_stationary_bootstrap_index_chunks(spec, mean_length)
            for row in chunk
        )
        assert streamed == plan.for_mean_block_length(mean_length).paths


def test_materialized_plan_refuses_production_sized_python_object_graph():
    with pytest.raises(ValueError, match="refusing to materialize"):
        make_stationary_bootstrap_plan(
            252,
            n_paths=100_000,
            sample_length=252,
            mean_block_lengths=(5, 10, 21),
        )


def test_chunked_vector_replay_matches_exact_oracle_and_is_chunk_size_invariant():
    records = [
        _day(0, start=100_000, low=98_500, end=101_000, closed_pnl=1_000),
        _day(1, start=101_000, low=100_300, end=102_000, closed_pnl=1_000),
        _day(2, start=102_000, low=98_800, end=101_500, closed_pnl=-500),
        _day(3, start=101_500, low=100_900, end=103_000, closed_pnl=1_500),
        _day(4, start=103_000, low=99_500, end=102_500, closed_pnl=-500),
    ]
    rules = FundedRules(
        initial_balance=100_000,
        profit_target_pct=0.025,
        daily_loss_pct=0.03,
        max_loss_pct=0.08,
        max_loss_mode="eod_trailing",
        best_day_max_profit_share=0.60,
    )
    plan = make_stationary_bootstrap_plan(
        len(records),
        n_paths=211,
        sample_length=12,
        mean_block_lengths=(2, 5),
        seed=9876,
    )
    for sizing_mode in (
        "conservative_buffer", "fixed_initial", "min_equity_initial", "compound"
    ):
        exact = bootstrap_funded_replay(
            records, rules, plan, sizing_mode=sizing_mode
        )
        chunked = chunked_bootstrap_funded_replay(
            records,
            rules,
            sizing_mode=sizing_mode,
            n_paths=211,
            sample_length=12,
            mean_block_lengths=(2, 5),
            seed=9876,
            chunk_size=17,
        )
        differently_chunked = chunked_bootstrap_funded_replay(
            records,
            rules,
            sizing_mode=sizing_mode,
            n_paths=211,
            sample_length=12,
            mean_block_lengths=(2, 5),
            seed=9876,
            chunk_size=64,
        )

        assert chunked.blocks == exact.blocks
        assert differently_chunked.blocks == exact.blocks


def test_min_equity_initial_sizing_does_not_compound_and_scales_after_losses():
    rules = FundedRules(
        initial_balance=100_000,
        profit_target_pct=None,
        daily_loss_pct=0.50,
        max_loss_pct=0.90,
    )
    winning_source = [
        _day(0, start=100_000, low=100_000, end=110_000)
    ]
    losing_source = [
        _day(0, start=100_000, low=90_000, end=90_000)
    ]

    wins = _rebase_sampled_days(
        winning_source, [0, 0], rules, "min_equity_initial"
    )
    losses = _rebase_sampled_days(
        losing_source, [0, 0], rules, "min_equity_initial"
    )

    assert wins[0].source_risk_base == pytest.approx(100_000.0)
    assert wins[1].source_risk_base == pytest.approx(100_000.0)
    assert wins[-1].end_equity == pytest.approx(120_000.0)
    assert losses[0].source_risk_base == pytest.approx(100_000.0)
    assert losses[1].source_risk_base == pytest.approx(90_000.0)
    assert losses[-1].end_equity == pytest.approx(81_000.0)


def test_unknown_bootstrap_sizing_mode_is_rejected():
    rules = FundedRules(initial_balance=100_000)
    plan = make_stationary_bootstrap_plan(
        1, n_paths=2, sample_length=1, mean_block_lengths=(2,), seed=7
    )
    with pytest.raises(ValueError, match="unsupported bootstrap sizing mode"):
        bootstrap_funded_replay(
            [_day(0, start=100_000, low=100_000, end=100_000)],
            rules,
            plan,
            sizing_mode="invented",  # type: ignore[arg-type]
        )


def test_bootstrap_target_qualification_matches_scalar_and_vector_paths():
    rules = FundedRules(
        initial_balance=100_000,
        profit_target_pct=0.10,
        daily_loss_pct=0.20,
        max_loss_pct=0.20,
    )
    plan = make_stationary_bootstrap_plan(
        1,
        n_paths=19,
        sample_length=1,
        mean_block_lengths=(3,),
        seed=41,
    )

    for verified_flat, end_equity, expected_probability in (
        (False, 105_000, 0.0),
        (False, 110_000, 0.0),
        (True, 110_000, 1.0),
    ):
        source = [
            _day(
                0,
                start=100_000,
                low=100_000,
                end=110_000,
                end_equity=end_equity,
                verified_flat_at_end=verified_flat,
            )
        ]
        scalar = bootstrap_funded_replay(
            source, rules, plan, sizing_mode="fixed_initial"
        )
        vector = chunked_bootstrap_funded_replay(
            source,
            rules,
            sizing_mode="fixed_initial",
            n_paths=19,
            sample_length=1,
            mean_block_lengths=(3,),
            seed=41,
            chunk_size=4,
        )

        assert scalar.blocks == vector.blocks
        assert scalar.blocks[0].pass_probability.estimate == expected_probability


def test_bootstrap_minimum_trading_days_matches_scalar_and_vector_paths():
    source = [
        _day(
            0, start=100_000, low=100_000, end=110_000,
            positions_opened=1, verified_flat_at_end=True,
        )
    ]
    rules = FundedRules(
        initial_balance=100_000,
        profit_target_pct=0.10,
        daily_loss_pct=0.20,
        max_loss_pct=0.20,
        minimum_trading_days=4,
    )
    plan = make_stationary_bootstrap_plan(
        1,
        n_paths=19,
        sample_length=4,
        mean_block_lengths=(3,),
        seed=42,
    )

    scalar = bootstrap_funded_replay(
        source, rules, plan, sizing_mode="fixed_initial"
    )
    vector = chunked_bootstrap_funded_replay(
        source,
        rules,
        sizing_mode="fixed_initial",
        n_paths=19,
        sample_length=4,
        mean_block_lengths=(3,),
        seed=42,
        chunk_size=5,
    )

    assert scalar.blocks == vector.blocks
    assert scalar.blocks[0].pass_probability.estimate == 1.0
    assert scalar.blocks[0].median_sessions_to_pass == pytest.approx(4.0)


def test_bootstrap_trading_days_remain_distinct_across_dst_fallback():
    rules = FundedRules(
        initial_balance=100_000,
        profit_target_pct=0.10,
        daily_loss_pct=0.20,
        max_loss_pct=0.20,
        minimum_trading_days=4,
        session_timezone="Europe/London",
        session_rollover=time(1, 30),
    )
    timestamp = pd.Timestamp("2024-10-26 00:30:00+00:00")
    source = DayRecord(
        session=firm_session_key(
            timestamp,
            timezone=rules.session_timezone,
            rollover=rules.session_rollover,
        ),
        timestamp=timestamp,
        day_start_balance=100_000,
        intraday_min_equity=100_000,
        end_balance=102_500,
        end_equity=102_500,
        closed_pnl=2_500,
        positions_opened=1,
        verified_flat_at_end=True,
    )

    rebased = _rebase_sampled_days(
        [source], [0, 0, 0, 0], rules, "fixed_initial"
    )

    assert [record.session for record in rebased] == [
        date(2024, 10, 26),
        date(2024, 10, 27),
        date(2024, 10, 28),
        date(2024, 10, 29),
    ]
    result = replay_funded_rules(rebased, rules)
    assert result.status == "passed"
    assert result.trading_days == 4


def test_bootstrap_rejects_nonexistent_spring_forward_rollover_consistently():
    rules = FundedRules(
        initial_balance=100_000,
        profit_target_pct=0.10,
        daily_loss_pct=0.20,
        max_loss_pct=0.20,
        minimum_trading_days=4,
        session_timezone="Europe/London",
        session_rollover=time(1, 30),
    )
    timestamp = pd.Timestamp("2024-03-29 13:30:00", tz="Europe/London")
    source = DayRecord(
        session=firm_session_key(
            timestamp,
            timezone=rules.session_timezone,
            rollover=rules.session_rollover,
        ),
        timestamp=timestamp,
        day_start_balance=100_000,
        intraday_min_equity=100_000,
        end_balance=102_500,
        end_equity=102_500,
        closed_pnl=2_500,
        positions_opened=1,
        verified_flat_at_end=True,
    )
    plan = make_stationary_bootstrap_plan(
        1,
        n_paths=7,
        sample_length=4,
        mean_block_lengths=(3,),
        seed=42,
    )

    with pytest.raises(ValueError, match="nonexistent time"):
        bootstrap_funded_replay(
            [source], rules, plan, sizing_mode="fixed_initial"
        )
    with pytest.raises(ValueError, match="nonexistent time"):
        chunked_bootstrap_funded_replay(
            [source],
            rules,
            sizing_mode="fixed_initial",
            n_paths=7,
            sample_length=4,
            mean_block_lengths=(3,),
            seed=42,
            chunk_size=3,
        )


def test_scalar_bootstrap_stops_before_constructing_invalid_post_breach_days():
    source = [
        _day(
            0,
            start=100_000,
            low=-10_000,
            end=-10_000,
            closed_pnl=-110_000,
        )
    ]
    rules = FundedRules(
        initial_balance=100_000,
        profit_target_pct=None,
        daily_loss_pct=0.03,
        max_loss_pct=0.10,
    )
    plan = make_stationary_bootstrap_plan(
        1,
        n_paths=13,
        sample_length=3,
        mean_block_lengths=(3,),
        seed=9,
    )

    scalar = bootstrap_funded_replay(
        source, rules, plan, sizing_mode="fixed_initial"
    )
    vector = chunked_bootstrap_funded_replay(
        source,
        rules,
        sizing_mode="fixed_initial",
        n_paths=13,
        sample_length=3,
        mean_block_lengths=(3,),
        seed=9,
        chunk_size=5,
    )

    assert scalar.blocks == vector.blocks
    assert scalar.blocks[0].breach_probability.estimate == 1.0
    assert scalar.blocks[0].breach_reasons == (("daily_loss", 13),)


def test_vector_replay_preserves_first_ordered_equity_path_breach_reason():
    timestamp = pd.Timestamp("2024-01-01 23:00", tz="UTC")
    equity_path = (
        EquityPoint(timestamp - pd.Timedelta(hours=13), 100_000, 94_000),
        EquityPoint(timestamp - pd.Timedelta(hours=11), 100_000, 89_000),
        EquityPoint(timestamp, 100_000, 100_000),
    )
    source = [
        _day(
            0,
            start=100_000,
            low=89_000,
            end=100_000,
            equity_path=equity_path,
        )
    ]
    rules = FundedRules(
        initial_balance=100_000,
        profit_target_pct=None,
        daily_loss_pct=0.10,
        max_loss_pct=0.05,
    )
    direct = replay_funded_rules(source, rules)
    plan = make_stationary_bootstrap_plan(
        1,
        n_paths=17,
        sample_length=1,
        mean_block_lengths=(2,),
        seed=5,
    )
    scalar = bootstrap_funded_replay(
        source, rules, plan, sizing_mode="fixed_initial"
    )
    vector = chunked_bootstrap_funded_replay(
        source,
        rules,
        sizing_mode="fixed_initial",
        n_paths=17,
        sample_length=1,
        mean_block_lengths=(2,),
        seed=5,
        chunk_size=6,
    )

    assert direct.reason == "max_loss"
    assert scalar.blocks == vector.blocks
    assert scalar.blocks[0].breach_reasons == (("max_loss", 17),)


def test_conservative_bootstrap_uses_nearest_official_floor_as_risk_base():
    source = [_day(0, start=100_000, low=94_900, end=95_000, closed_pnl=-5_000)]
    rules = FundedRules(
        initial_balance=100_000,
        profit_target_pct=None,
        daily_loss_pct=0.03,
        max_loss_pct=0.10,
    )

    conservative = chunked_bootstrap_funded_replay(
        source,
        rules,
        n_paths=20,
        sample_length=2,
        mean_block_lengths=(5,),
        seed=1,
        chunk_size=7,
    )
    fixed_initial = chunked_bootstrap_funded_replay(
        source,
        rules,
        sizing_mode="fixed_initial",
        n_paths=20,
        sample_length=2,
        mean_block_lengths=(5,),
        seed=1,
        chunk_size=7,
    )

    # At inception the base is min(100k current, 100k initial, 3k daily buffer,
    # 10k max-loss buffer) = 3k. Fixed-initial breaches the daily rule immediately.
    assert conservative.sizing_mode == "conservative_buffer"
    assert conservative.blocks[0].breach_probability.estimate == 0.0
    assert fixed_initial.blocks[0].breach_probability.estimate == 1.0

    growth_source = [
        _day(
            0, start=100_000, low=100_000, end=200_000,
            closed_pnl=100_000, verified_flat_at_end=True,
        )
    ]
    growth_rules = FundedRules(
        initial_balance=100_000,
        profit_target_pct=0.05,
        daily_loss_pct=0.03,
        max_loss_pct=0.10,
    )
    conservative_growth = chunked_bootstrap_funded_replay(
        growth_source,
        growth_rules,
        n_paths=10,
        sample_length=1,
        mean_block_lengths=(5,),
        seed=1,
        chunk_size=4,
    )
    fixed_growth = chunked_bootstrap_funded_replay(
        growth_source,
        growth_rules,
        sizing_mode="fixed_initial",
        n_paths=10,
        sample_length=1,
        mean_block_lengths=(5,),
        seed=1,
        chunk_size=4,
    )
    # A +100%-of-source-risk-base day becomes +3k, below the 5k target.
    assert conservative_growth.blocks[0].pass_probability.estimate == 0.0
    assert fixed_growth.blocks[0].pass_probability.estimate == 1.0


def test_phase_start_and_closed_pnl_inputs_fail_closed_when_inconsistent():
    wrong_start = _day(0, start=101_000, low=101_000, end=102_000)
    with pytest.raises(ValueError, match="first day_start_balance"):
        replay_funded_rules([wrong_start], FundedRules(initial_balance=100_000))

    partial_pnl_events = pd.DataFrame(
        {
            "timestamp": [
                "2024-01-02 00:00:00+00:00",
                "2024-01-02 23:00:00+00:00",
            ],
            "balance": [100_000, 101_000],
            "equity": [100_000, 101_000],
            "closed_pnl": [0.0, None],
        }
    )
    with pytest.raises(ValueError, match="every event or omitted"):
        replay_funded_rules(
            partial_pnl_events, FundedRules(initial_balance=100_000)
        )


def test_synchronized_bootstrap_reuses_one_plan_for_aligned_strategies():
    records = [
        _day(0, start=100_000, low=99_800, end=100_500),
        _day(1, start=100_500, low=100_100, end=101_000),
        _day(2, start=101_000, low=100_700, end=101_500),
    ]
    rules = FundedRules(
        initial_balance=100_000,
        profit_target_pct=0.01,
        daily_loss_pct=0.10,
        max_loss_pct=0.20,
    )
    report = synchronized_funded_bootstrap(
        {"candidate_a": records, "candidate_b": records},
        rules,
        n_paths=12,
        sample_length=4,
        mean_block_lengths=(5,),
        seed=123,
    )

    assert report.strategies[0].report.plan is report.plan
    assert report.strategies[1].report.plan is report.plan
    assert report.strategies[0].report.blocks == report.strategies[1].report.blocks


def test_block_resampling_preserves_complete_day_row_tuples():
    records = [
        _day(0, start=100_000, low=99_111, end=100_123, closed_pnl=123),
        _day(1, start=100_123, low=98_222, end=99_667, closed_pnl=-456),
        _day(2, start=99_667, low=97_333, end=100_456, closed_pnl=789),
    ]

    selected = resample_day_records(records, [2, 0, 2, 1])

    assert selected[0] is records[2]
    assert selected[1] is records[0]
    assert selected[2] is records[2]
    assert selected[3] is records[1]
    assert [
        (row.day_start_balance, row.intraday_min_equity, row.end_balance, row.closed_pnl)
        for row in selected
    ] == [
        (99_667, 97_333, 100_456, 789),
        (100_000, 99_111, 100_123, 123),
        (99_667, 97_333, 100_456, 789),
        (100_123, 98_222, 99_667, -456),
    ]


def test_wilson_interval_matches_known_half_success_case_and_handles_edges():
    interval = wilson_interval(5, 10)
    zero = wilson_interval(0, 10)
    all_success = wilson_interval(10, 10)

    assert interval.estimate == 0.5
    assert interval.lower == pytest.approx(0.236593, abs=1e-6)
    assert interval.upper == pytest.approx(0.763407, abs=1e-6)
    assert zero.lower == 0.0
    assert 0.0 < zero.upper < 0.5
    assert 0.5 < all_success.lower < 1.0
    assert all_success.upper == 1.0
