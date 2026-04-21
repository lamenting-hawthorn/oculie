"""
Comprehensive tests for the Polymarket Weather Prediction Agent bot modules.

Covers: database, noaa_fetcher, wunderground_fetcher, market_scanner,
trade_engine, and reporter.
"""

import os
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# 1. Database tests
# ---------------------------------------------------------------------------

from bot.database import (
    DEFAULT_SETTINGS,
    get_alerts,
    get_brier_score,
    get_bot_status,
    get_calibration_buckets,
    get_city_date_exposure,
    get_direction_exposure,
    get_exposure_summary,
    get_open_positions,
    get_pnl_by_city,
    get_pnl_summary,
    get_setting,
    get_total_exposure,
    get_trade_history,
    init_db,
    insert_alert,
    insert_position,
    insert_trade,
    set_setting,
)


class TestDatabase:
    """Tests for bot/database.py async SQLite operations."""

    async def test_init_db(self, db):
        """init_db creates all tables and seeds 21 default settings."""
        # Verify tables exist
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {row["name"] for row in await cursor.fetchall()}
        assert "trades" in tables
        assert "positions" in tables
        assert "settings" in tables
        assert "alerts" in tables
        assert "scan_log" in tables

        # Verify default settings count
        cursor = await db.execute("SELECT COUNT(*) AS cnt FROM settings")
        row = await cursor.fetchone()
        assert row["cnt"] == len(DEFAULT_SETTINGS)

    async def test_insert_and_get_trade(self, db):
        """Insert a trade, then retrieve it via get_trade_history."""
        trade_id = await insert_trade(
            db,
            city="New York City",
            market_question="Will the high be between 40F and 45F?",
            direction="YES",
            noaa_probability=0.72,
            market_price=0.55,
            edge=0.17,
            bet_size=25.0,
            entry_price=0.55,
            paper_trade=True,
        )
        assert trade_id is not None
        assert trade_id > 0

        trades = await get_trade_history(db, city="New York City")
        assert len(trades) == 1
        assert trades[0]["city"] == "New York City"
        assert trades[0]["direction"] == "YES"
        assert trades[0]["bet_size"] == 25.0

    async def test_get_pnl_summary(self, db):
        """Insert won/lost trades and verify the summary math."""
        # Won trade: bought at 0.40, resolved at $1 => pnl = (1 - 0.40) * size = +15
        await insert_trade(
            db,
            city="Chicago",
            market_question="Q1",
            direction="YES",
            noaa_probability=0.70,
            market_price=0.40,
            edge=0.30,
            bet_size=25.0,
            entry_price=0.40,
            exit_price=1.0,
            outcome="won",
            pnl=15.0,
            paper_trade=True,
            resolved_at=datetime.now(timezone.utc).isoformat(),
        )

        # Lost trade: bought at 0.60, resolved at $0 => pnl = -0.60 * size = -18
        await insert_trade(
            db,
            city="Miami",
            market_question="Q2",
            direction="YES",
            noaa_probability=0.65,
            market_price=0.60,
            edge=0.05,
            bet_size=30.0,
            entry_price=0.60,
            exit_price=0.0,
            outcome="lost",
            pnl=-18.0,
            paper_trade=True,
            resolved_at=datetime.now(timezone.utc).isoformat(),
        )

        summary = await get_pnl_summary(db)
        assert summary["total_trades"] == 2
        assert summary["wins"] == 1
        assert summary["losses"] == 1
        assert summary["total_pnl"] == pytest.approx(-3.0)
        assert summary["win_rate"] == 50.0

    async def test_settings_crud(self, db):
        """get/set settings and verify updates."""
        # Read default
        val = await get_setting(db, "paper_mode")
        assert val == "true"

        # Update
        await set_setting(db, "paper_mode", "false")
        val = await get_setting(db, "paper_mode")
        assert val == "false"

        # Non-existent key
        val = await get_setting(db, "nonexistent_key")
        assert val is None

    async def test_insert_alert(self, db):
        """Insert an alert, then retrieve it via get_alerts."""
        alert_id = await insert_alert(
            db,
            alert_type="trade_entered",
            city="Dallas",
            message="Trade entered for Dallas",
            channel="telegram",
        )
        assert alert_id > 0

        alerts = await get_alerts(db, alert_type="trade_entered")
        assert len(alerts) == 1
        assert alerts[0]["city"] == "Dallas"
        assert alerts[0]["channel"] == "telegram"

    async def test_open_positions(self, db):
        """Insert a position, verify get_open_positions returns it."""
        await db.execute(
            "INSERT INTO positions (city, market_question, direction, entry_price, size) "
            "VALUES (?, ?, ?, ?, ?)",
            ("Seattle", "Q_pos", "YES", 0.50, 30.0),
        )
        await db.commit()

        positions = await get_open_positions(db)
        assert len(positions) == 1
        assert positions[0]["city"] == "Seattle"
        assert positions[0]["size"] == 30.0

    async def test_total_exposure(self, db):
        """Insert positions and verify sum of sizes."""
        await db.execute(
            "INSERT INTO positions (city, market_question, direction, entry_price, size) "
            "VALUES (?, ?, ?, ?, ?)",
            ("Atlanta", "Q1", "YES", 0.40, 20.0),
        )
        await db.execute(
            "INSERT INTO positions (city, market_question, direction, entry_price, size) "
            "VALUES (?, ?, ?, ?, ?)",
            ("Miami", "Q2", "NO", 0.60, 35.0),
        )
        await db.commit()

        exposure = await get_total_exposure(db)
        assert exposure == pytest.approx(55.0)


# ---------------------------------------------------------------------------
# 2. NOAA fetcher tests
# ---------------------------------------------------------------------------

from bot.noaa_fetcher import (
    CITY_CONFIGS,
    CityForecast,
    ForecastPeriod,
    calculate_probability_distribution,
)


class TestNoaaFetcher:
    """Tests for bot/noaa_fetcher.py forecast parsing and probability distributions."""

    def test_city_configs(self):
        """All 6 US cities are configured with correct grid coords."""
        expected_cities = {
            "New York City", "Chicago", "Miami", "Dallas", "Seattle", "Atlanta"
        }
        assert set(CITY_CONFIGS.keys()) == expected_cities

        for city, config in CITY_CONFIGS.items():
            assert "office" in config, f"{city} missing 'office'"
            assert "gridX" in config, f"{city} missing 'gridX'"
            assert "gridY" in config, f"{city} missing 'gridY'"
            assert isinstance(config["gridX"], int)
            assert isinstance(config["gridY"], int)

    def test_calculate_probability_distribution(self):
        """Given a mock CityForecast, verify distribution sums to ~1.0
        and peak bucket contains the forecast temp."""
        target_date = "2026-03-25"
        forecast = CityForecast(
            city="New York City",
            station="OKX/33,37",
            periods=[
                ForecastPeriod(
                    name="Wednesday",
                    temperature=55,
                    temperatureUnit="F",
                    shortForecast="Sunny",
                    detailedForecast="Sunny with a high near 55.",
                    startTime=f"{target_date}T06:00:00-05:00",
                    endTime=f"{target_date}T18:00:00-05:00",
                    isDaytime=True,
                ),
            ],
            fetched_at=datetime(2026, 3, 25, 12, 0, tzinfo=timezone.utc),
        )

        buckets = calculate_probability_distribution(forecast, target_date)
        assert len(buckets) > 0

        total_prob = sum(b.probability for b in buckets)
        assert total_prob == pytest.approx(1.0, abs=0.01)

        # The peak bucket should contain or border the forecast temp 55.
        # Buckets are [low, high) so temp 55 lands at the boundary of [50,55) and [55,60).
        peak = max(buckets, key=lambda b: b.probability)
        assert peak.low <= 55 <= peak.high

    def test_probability_buckets_are_5f(self):
        """All buckets are exactly 5 degF wide."""
        target_date = "2026-03-25"
        forecast = CityForecast(
            city="Chicago",
            station="LOT/65,76",
            periods=[
                ForecastPeriod(
                    name="Wednesday",
                    temperature=45,
                    temperatureUnit="F",
                    shortForecast="Cloudy",
                    detailedForecast="Cloudy.",
                    startTime=f"{target_date}T06:00:00-06:00",
                    endTime=f"{target_date}T18:00:00-06:00",
                    isDaytime=True,
                ),
            ],
            fetched_at=datetime(2026, 3, 25, 12, 0, tzinfo=timezone.utc),
        )

        buckets = calculate_probability_distribution(forecast, target_date)
        assert len(buckets) > 0
        for b in buckets:
            assert b.high - b.low == 5, f"Bucket {b.low}-{b.high} is not 5F wide"


# ---------------------------------------------------------------------------
# 3. Open-Meteo fetcher tests
# ---------------------------------------------------------------------------

from bot.open_meteo_fetcher import (
    CITIES,
    EnsembleForecastDay,
    InternationalCityForecast,
)
from bot.open_meteo_fetcher import (
    calculate_probability_distribution as om_calc_prob_dist,
)


class TestOpenMeteoFetcher:
    """Tests for bot/open_meteo_fetcher.py city configs and distributions."""

    def test_city_configs(self):
        """All 4 active international cities are configured with lat/lon/model."""
        expected = {"London", "Seoul", "Shanghai", "Hong Kong"}
        assert expected.issubset(set(CITIES.keys()))

        for city in expected:
            cfg = CITIES[city]
            assert "latitude" in cfg, f"{city} missing latitude"
            assert "longitude" in cfg, f"{city} missing longitude"
            assert "primary_model" in cfg, f"{city} missing primary_model"
            assert "temp_unit" in cfg, f"{city} missing temp_unit"
            assert cfg["temp_unit"] == "celsius", f"{city} should use celsius"

    def test_calculate_probability_distribution_celsius(self):
        """Given a mock ensemble forecast, distribution sums to ~1.0."""
        target_date = "2026-03-25"
        # Simulate 10 ensemble members spread around 11°C
        members = [9.0, 10.0, 10.0, 11.0, 11.0, 11.0, 12.0, 12.0, 13.0, 14.0]
        forecast = InternationalCityForecast(
            city="London",
            unit="C",
            forecast_days=[
                EnsembleForecastDay(
                    date=target_date,
                    temp_max_p50=11.0,
                    ensemble_temp_max=members,
                ),
            ],
            fetched_at=datetime(2026, 3, 25, 10, 0, tzinfo=timezone.utc),
        )

        buckets = om_calc_prob_dist(forecast, target_date)
        assert len(buckets) > 0

        total_prob = sum(b.probability for b in buckets)
        assert total_prob == pytest.approx(1.0, abs=0.01)

        # Peak bucket should contain the mode (11°C — 3 members)
        peak = max(buckets, key=lambda b: b.probability)
        assert peak.low <= 11.0 <= peak.high

    def test_probability_buckets_are_1c(self):
        """All Open-Meteo buckets are exactly 1 degC wide."""
        target_date = "2026-03-25"
        members = [float(t) for t in range(2, 12)]  # 2,3,...,11
        forecast = InternationalCityForecast(
            city="Seoul",
            unit="C",
            forecast_days=[
                EnsembleForecastDay(
                    date=target_date,
                    temp_max_p50=6.0,
                    ensemble_temp_max=members,
                ),
            ],
            fetched_at=datetime(2026, 3, 25, 10, 0, tzinfo=timezone.utc),
        )

        buckets = om_calc_prob_dist(forecast, target_date)
        assert len(buckets) > 0
        for b in buckets:
            assert b.high - b.low == pytest.approx(1.0), (
                f"Bucket {b.low}-{b.high} is not 1C wide"
            )

    def test_fallback_to_p50_when_no_ensemble(self):
        """Falls back to a point-mass spike at p50 when ensemble is empty."""
        target_date = "2026-03-25"
        forecast = InternationalCityForecast(
            city="Shanghai",
            unit="C",
            forecast_days=[
                EnsembleForecastDay(
                    date=target_date,
                    temp_max_p50=18.0,
                    ensemble_temp_max=[],  # no members
                ),
            ],
            fetched_at=datetime(2026, 3, 25, 10, 0, tzinfo=timezone.utc),
        )

        buckets = om_calc_prob_dist(forecast, target_date)
        assert len(buckets) == 1
        assert buckets[0].probability == pytest.approx(1.0)
        assert buckets[0].low <= 18.0 <= buckets[0].high


# ---------------------------------------------------------------------------
# 4. Market scanner tests
# ---------------------------------------------------------------------------

from bot.market_scanner import parse_market_question


class TestMarketScanner:
    """Tests for bot/market_scanner.py question parsing."""

    def test_parse_market_question_fahrenheit(self):
        """Parse a standard Fahrenheit range question."""
        q = (
            "Will the high temperature in New York City be between "
            "40°F and 45°F on March 25, 2026?"
        )
        result = parse_market_question(q)
        assert result is not None
        assert result["city"] == "new_york"
        assert result["temp_low"] == 40.0
        assert result["temp_high"] == 45.0
        assert result["temp_unit"] == "F"
        assert result["target_date"] == "2026-03-25"

    def test_parse_market_question_celsius(self):
        """Parse a Celsius range question."""
        q = (
            "Will the high temperature in London be between "
            "10°C and 15°C on March 25, 2026?"
        )
        result = parse_market_question(q)
        assert result is not None
        assert result["city"] == "london"
        assert result["temp_low"] == 10.0
        assert result["temp_high"] == 15.0
        assert result["temp_unit"] == "C"
        assert result["target_date"] == "2026-03-25"

    def test_parse_market_question_invalid(self):
        """Return None for a non-temperature question."""
        q = "Will Bitcoin reach $100,000 by December 31, 2026?"
        result = parse_market_question(q)
        assert result is None

    def test_parse_market_question_above(self):
        """Parse 'above X degF' format."""
        q = (
            "Will the high temperature in Dallas be above "
            "90°F on April 1, 2026?"
        )
        result = parse_market_question(q)
        assert result is not None
        assert result["city"] == "dallas"
        assert result["temp_low"] == 90.0
        assert result["temp_high"] == 999.0
        assert result["temp_unit"] == "F"
        assert result["target_date"] == "2026-04-01"

    def test_parse_market_question_below(self):
        """Parse 'below X degF' format."""
        q = (
            "Will the high temperature in Chicago be below "
            "32°F on January 15, 2026?"
        )
        result = parse_market_question(q)
        assert result is not None
        assert result["city"] == "chicago"
        assert result["temp_low"] == -999.0
        assert result["temp_high"] == 32.0
        assert result["temp_unit"] == "F"
        assert result["target_date"] == "2026-01-15"


# ---------------------------------------------------------------------------
# 5. Trade engine tests
# ---------------------------------------------------------------------------

from bot.trade_engine import (
    TradeSignal,
    _direction_cap_reason,
    _normalize_market_group,
    _select_portfolio_opportunities,
    calculate_edge,
    check_risk_limits,
    kelly_criterion,
    size_position,
)


class TestTradeEngine:
    """Tests for bot/trade_engine.py edge, Kelly, sizing, and risk checks."""

    def test_calculate_edge(self):
        """Edge = forecast_prob - market_price."""
        assert calculate_edge(0.75, 0.55) == pytest.approx(0.20)
        assert calculate_edge(0.30, 0.50) == pytest.approx(-0.20)
        assert calculate_edge(0.50, 0.50) == pytest.approx(0.0)

    def test_kelly_criterion_positive_edge(self):
        """With 75% forecast and 20% market price, Kelly fraction is positive."""
        kf = kelly_criterion(0.75, 0.20)
        assert kf > 0
        # Formula: b = (1/0.20) - 1 = 4, f* = (0.75*4 - 0.25)/4 = (3.0-0.25)/4 = 0.6875
        # Capped at 0.25
        assert kf == pytest.approx(0.25)

    def test_kelly_criterion_no_edge(self):
        """With 20% forecast and 80% market price, Kelly returns 0 (no edge)."""
        kf = kelly_criterion(0.20, 0.80)
        assert kf == 0.0

    def test_kelly_criterion_cap(self):
        """Fraction is capped at kelly_cap."""
        # Very large edge should be capped
        kf = kelly_criterion(0.95, 0.10, kelly_cap=0.10)
        assert kf == pytest.approx(0.10)

    def test_size_position(self):
        """Position is capped at max_bet and has $1 minimum."""
        # Normal case
        pos = size_position(0.10, 500.0, 50.0)
        assert pos == 50.0  # 0.10 * 500 = 50

        # Below max_bet
        pos = size_position(0.05, 500.0, 50.0)
        assert pos == 25.0  # 0.05 * 500 = 25

        # Capped at max_bet
        pos = size_position(0.50, 500.0, 50.0)
        assert pos == 50.0  # 0.50 * 500 = 250, capped at 50

    def test_size_position_below_minimum(self):
        """If kelly * bankroll < $1, returns 0."""
        pos = size_position(0.001, 100.0, 50.0)
        # 0.001 * 100 = $0.10 < $1
        assert pos == 0.0

    async def test_check_risk_limits_pass(self, db):
        """Risk check passes with default settings and small bet."""
        # Default: max_bet=50, max_exposure=200, floor=100
        ok, reason = await check_risk_limits(db, 25.0)
        assert ok is True
        assert reason == "ok"

    async def test_check_risk_limits_exceeds_max_bet(self, db):
        """Risk check fails when bet exceeds max_bet_size."""
        ok, reason = await check_risk_limits(db, 75.0)
        assert ok is False
        assert "max_bet_size" in reason

    async def test_check_risk_limits_exceeds_exposure(self, db):
        """Risk check fails when total exposure would be exceeded."""
        # Insert existing positions that fill up exposure
        await db.execute(
            "INSERT INTO positions (city, market_question, direction, entry_price, size) "
            "VALUES (?, ?, ?, ?, ?)",
            ("NYC", "Q", "YES", 0.5, 180.0),
        )
        await db.commit()

        ok, reason = await check_risk_limits(db, 25.0)
        assert ok is False
        assert "max_total_exposure" in reason

    async def test_check_risk_limits_floor(self, db):
        """Risk check fails when balance would drop below account floor."""
        # With default max_exposure=200, floor=100, and no positions:
        # estimated_balance = 200 - 0 = 200
        # A bet of 110 => 200 - 110 = 90 < 100 floor
        ok, reason = await check_risk_limits(db, 110.0)
        # This would first be caught by max_bet_size (50), so set it higher
        await set_setting(db, "max_bet_size", "200.0")
        ok, reason = await check_risk_limits(db, 110.0)
        assert ok is False
        assert "account_floor" in reason

    async def test_check_risk_limits_counts_pending_exposure(self, db):
        """Pending scan-cycle exposure should count before positions are inserted."""
        await set_setting(db, "account_floor", "0.0")
        await set_setting(db, "max_bet_size", "100.0")
        ok, reason = await check_risk_limits(db, 45.0, pending_exposure=180.0)
        assert ok is False
        assert "max_total_exposure" in reason

    def test_normalize_market_group_caps_sum_at_one(self):
        """Same city/date market probabilities are normalized when their sum exceeds 1."""
        markets = [MagicMock(name=f"m{i}") for i in range(4)]
        normalized = _normalize_market_group(
            list(zip(markets, [0.40, 0.35, 0.30, 0.25]))
        )
        assert sum(prob for _, prob in normalized) == pytest.approx(1.0)
        assert normalized[0][1] == pytest.approx(0.40 / 1.30)

    def test_direction_cap_allows_bootstrap_but_blocks_imbalance(self):
        """The direction cap should not deadlock an empty book, but should cap imbalance."""
        assert (
            _direction_cap_reason(
                direction="YES",
                bet_size=20.0,
                existing_yes=0.0,
                existing_no=0.0,
                pending_yes=0.0,
                pending_no=0.0,
                max_direction_ratio=0.75,
            )
            is None
        )

        reason = _direction_cap_reason(
            direction="YES",
            bet_size=20.0,
            existing_yes=120.0,
            existing_no=40.0,
            pending_yes=0.0,
            pending_no=0.0,
            max_direction_ratio=0.75,
        )
        assert reason is not None
        assert "YES direction ratio" in reason

    def _signal(
        self,
        condition_id: str,
        *,
        city: str = "seattle",
        target_date: str = "2026-04-14",
        direction: str = "YES",
        edge: float = 0.20,
        kelly_fraction: float = 1.0,
    ) -> TradeSignal:
        return TradeSignal(
            city=city,
            condition_id=condition_id,
            token_id=f"token-{condition_id}",
            question=(
                f"Will the high temperature in {city} be between "
                f"50F and 55F on April 14, 2026?"
            ),
            target_date=target_date,
            direction=direction,
            noaa_probability=0.70,
            market_price=0.50,
            edge=edge,
            kelly_fraction=kelly_fraction,
            bet_size=0.0,
            paper=True,
        )

    async def test_portfolio_selection_enforces_batch_total_exposure(self, db):
        """Five $45 candidates should select only four under a $200 exposure cap."""
        await set_setting(db, "account_floor", "0.0")
        await set_setting(db, "max_bet_size", "45.0")
        await set_setting(db, "max_total_exposure", "200.0")

        raw = [self._signal(f"cond-{idx}") for idx in range(5)]
        selected = await _select_portfolio_opportunities(
            db,
            raw,
            bankroll=225.0,
            max_bet_size=45.0,
            max_total_exposure=200.0,
            account_floor=0.0,
            max_city_date_exposure=999.0,
            max_trades_per_city_date=99,
            max_direction_ratio=1.0,
        )

        assert len(selected) == 4
        assert sum(s.bet_size for s in selected) == pytest.approx(180.0)

    async def test_portfolio_selection_enforces_city_date_cap(self, db):
        """Same-city/date candidates should be greedily capped by group exposure."""
        await set_setting(db, "account_floor", "0.0")
        await set_setting(db, "max_bet_size", "20.0")
        await set_setting(db, "max_total_exposure", "200.0")

        raw = [self._signal(f"nyc-{idx}", city="new_york") for idx in range(6)]
        selected = await _select_portfolio_opportunities(
            db,
            raw,
            bankroll=200.0,
            max_bet_size=20.0,
            max_total_exposure=200.0,
            account_floor=0.0,
            max_city_date_exposure=40.0,
            max_trades_per_city_date=99,
            max_direction_ratio=1.0,
        )

        assert len(selected) == 2
        assert sum(s.bet_size for s in selected) == pytest.approx(40.0)

    async def test_portfolio_selection_enforces_direction_cap(self, db):
        """A YES-heavy book should reject another YES under a 75% direction cap."""
        await set_setting(db, "account_floor", "0.0")
        await set_setting(db, "max_bet_size", "20.0")
        await set_setting(db, "max_total_exposure", "200.0")
        await db.execute(
            "INSERT INTO positions (city, market_question, direction, entry_price, size) "
            "VALUES (?, ?, ?, ?, ?)",
            ("seattle", "Q yes", "YES", 0.5, 120.0),
        )
        await db.execute(
            "INSERT INTO positions (city, market_question, direction, entry_price, size) "
            "VALUES (?, ?, ?, ?, ?)",
            ("miami", "Q no", "NO", 0.5, 40.0),
        )
        await db.commit()

        selected = await _select_portfolio_opportunities(
            db,
            [self._signal("next-yes", direction="YES")],
            bankroll=40.0,
            max_bet_size=20.0,
            max_total_exposure=200.0,
            account_floor=0.0,
            max_city_date_exposure=999.0,
            max_trades_per_city_date=99,
            max_direction_ratio=0.75,
        )

        assert selected == []

    async def test_check_risk_limits_uses_exposure_override(self, db):
        """Risk check can enforce a live session budget below the global cap."""
        await set_setting(db, "max_bet_size", "100.0")
        await set_setting(db, "account_floor", "0.0")

        ok, reason = await check_risk_limits(
            db,
            60.0,
            max_total_exposure_override=50.0,
        )

        assert ok is False
        assert "max_total_exposure" in reason

    async def test_check_risk_limits_account_floor_override(self, db):
        """Live budget checks can spend the chosen budget despite the paper floor."""
        ok, reason = await check_risk_limits(
            db,
            50.0,
            max_total_exposure_override=50.0,
            account_floor_override=0.0,
        )

        assert ok is True
        assert reason == "ok"

    async def test_live_trading_budget_from_env(self, db, monkeypatch):
        """Live startup saves an env-provided budget after checking wallet balance."""
        from bot.main import _configure_live_trading_budget

        monkeypatch.setenv("LIVE_TRADING_BUDGET_USDC", "50")
        with patch("bot.clob_client.build_clob_client", return_value=MagicMock()), patch(
            "bot.clob_client.get_usdc_balance",
            return_value=100.0,
        ):
            await _configure_live_trading_budget(db)

        assert await get_setting(db, "live_trading_budget_usdc") == "50.00"

    async def test_live_trading_budget_rejects_wallet_overage(self, db, monkeypatch):
        """Live startup refuses a budget above the wallet balance."""
        from bot.main import _configure_live_trading_budget

        monkeypatch.setenv("LIVE_TRADING_BUDGET_USDC", "150")
        with patch("bot.clob_client.build_clob_client", return_value=MagicMock()), patch(
            "bot.clob_client.get_usdc_balance",
            return_value=100.0,
        ):
            with pytest.raises(RuntimeError, match="exceeds wallet balance"):
                await _configure_live_trading_budget(db)


# ---------------------------------------------------------------------------
# 6. Reporter tests
# ---------------------------------------------------------------------------

from bot.reporter import (
    format_trade_entered,
    format_trade_resolved,
)


class TestReporter:
    """Tests for bot/reporter.py message formatting."""

    async def test_format_trade_entered(self):
        """Verify live trade message matches expected format."""
        trade = {
            "city": "New York City",
            "market_question": "40-45°F",
            "noaa_probability": 72,
            "market_price": 55,
            "edge": 17,
            "bet_size": 25,
        }
        msg = await format_trade_entered(trade, paper=False)
        assert "Trade entered" in msg
        assert "New York City" in msg
        assert "40-45°F" in msg
        assert "72%" in msg
        assert "55%" in msg
        assert "+17%" in msg
        assert "$25" in msg

    async def test_format_trade_entered_paper(self):
        """Verify paper-mode message has PAPER prefix."""
        trade = {
            "city": "Chicago",
            "market_question": "30-35°F",
            "noaa_probability": 60,
            "market_price": 40,
            "edge": 20,
            "bet_size": 15,
        }
        msg = await format_trade_entered(trade, paper=True)
        assert "[PAPER]" in msg
        assert "Signal" in msg
        assert "Chicago" in msg
        assert "Would bet" in msg

    async def test_format_trade_resolved_won(self):
        """Verify won-trade format."""
        trade = {
            "city": "Miami",
            "market_question": "80-85°F",
            "entry_price": 0.40,
            "pnl": 15.0,
            "outcome": "won",
        }
        msg = await format_trade_resolved(trade)
        assert "Won" in msg
        assert "Miami" in msg
        assert "80-85°F" in msg
        assert "$0.4" in msg
        assert "Profit" in msg
        assert "+$15" in msg

    async def test_format_trade_resolved_lost(self):
        """Verify lost-trade format."""
        trade = {
            "city": "Dallas",
            "market_question": "90-95°F",
            "entry_price": 0.60,
            "pnl": -18.0,
            "outcome": "lost",
        }
        msg = await format_trade_resolved(trade)
        assert "Lost" in msg
        assert "Dallas" in msg
        assert "90-95°F" in msg
        assert "$0.6" in msg
        assert "Loss" in msg
        assert "-$18" in msg


# ---------------------------------------------------------------------------
# 7. Database — insert_position and get_bot_status
# ---------------------------------------------------------------------------


class TestDatabasePositionAndStatus:
    """Tests for insert_position() and get_bot_status() helpers."""

    @pytest.fixture
    async def db(self, tmp_path):
        path = str(tmp_path / "test.db")
        conn = await init_db(path)
        yield conn
        await conn.close()

    async def test_insert_position_creates_row(self, db):
        """insert_position() should write to positions table and return an int ID."""
        trade_id = await insert_trade(
            db,
            city="NYC",
            market_question="40-45°F",
            direction="YES",
            noaa_probability=0.70,
            market_price=0.50,
            edge=0.20,
            bet_size=20.0,
            entry_price=0.50,
            paper_trade=True,
        )
        pos_id = await insert_position(
            db,
            trade_id=trade_id,
            condition_id="cond-123",
            city="NYC",
            market_question="40-45°F",
            token_id="tok-abc",
            direction="YES",
            entry_price=0.50,
            size=20.0,
            paper=True,
        )
        assert isinstance(pos_id, int) and pos_id > 0

        positions = await get_open_positions(db)
        assert len(positions) == 1
        assert positions[0]["condition_id"] == "cond-123"
        assert positions[0]["token_id"] == "tok-abc"
        assert positions[0]["trade_id"] == trade_id

    async def test_insert_position_none_condition_id(self, db):
        """condition_id=None should be stored as NULL without error."""
        trade_id = await insert_trade(
            db,
            city="Chicago",
            market_question="30-35°F",
            direction="NO",
            noaa_probability=0.30,
            market_price=0.55,
            edge=0.25,
            bet_size=10.0,
            entry_price=0.45,
            paper_trade=True,
        )
        pos_id = await insert_position(
            db,
            trade_id=trade_id,
            condition_id=None,
            city="Chicago",
            market_question="30-35°F",
            token_id="",
            direction="NO",
            entry_price=0.45,
            size=10.0,
            paper=True,
        )
        assert pos_id > 0
        positions = await get_open_positions(db)
        assert positions[0]["condition_id"] is None

    async def test_get_bot_status_default(self, db):
        """get_bot_status() returns 'running' when no setting is present."""
        status = await get_bot_status(db)
        assert status == "running"

    async def test_get_bot_status_after_set(self, db):
        """get_bot_status() returns the stored value after set_setting."""
        await set_setting(db, "bot_status", "stopped")
        status = await get_bot_status(db)
        assert status == "stopped"

    async def test_total_exposure_counts_open_only(self, db):
        """Closed positions should not count toward total exposure."""
        from datetime import datetime, timezone
        await db.execute(
            "INSERT INTO positions (city, market_question, direction, entry_price, size) VALUES (?, ?, ?, ?, ?)",
            ("Miami", "Q_open", "YES", 0.40, 25.0),
        )
        await db.execute(
            "INSERT INTO positions (city, market_question, direction, entry_price, size, closed_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("Miami", "Q_closed", "YES", 0.40, 50.0, datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()
        exposure = await get_total_exposure(db)
        assert exposure == pytest.approx(25.0)

    async def test_city_date_and_direction_exposure_helpers(self, db):
        """Exposure helpers should count only open matching positions."""
        await db.execute(
            "INSERT INTO positions (city, market_question, direction, entry_price, size) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                "seattle",
                "Will the high temperature in Seattle be 50-55F on April 14, 2026?",
                "YES",
                0.40,
                25.0,
            ),
        )
        await db.execute(
            "INSERT INTO positions (city, market_question, direction, entry_price, size) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                "seattle",
                "Will the high temperature in Seattle be 55-60F on April 15, 2026?",
                "NO",
                0.40,
                30.0,
            ),
        )
        await db.commit()

        city_date = await get_city_date_exposure(db, "seattle", "2026-04-14")
        yes_exposure = await get_direction_exposure(db, "YES")
        no_exposure = await get_direction_exposure(db, "NO")

        assert city_date == pytest.approx(25.0)
        assert yes_exposure == pytest.approx(25.0)
        assert no_exposure == pytest.approx(30.0)

    async def test_reporting_helpers(self, db):
        """Per-city P&L, calibration, Brier, and exposure summaries should agree."""
        await insert_trade(
            db,
            city="Seattle",
            market_question="Q1",
            direction="YES",
            noaa_probability=0.70,
            market_price=0.40,
            edge=0.30,
            bet_size=25.0,
            entry_price=0.40,
            outcome="won",
            pnl=15.0,
            paper_trade=True,
            resolved_at=datetime.now(timezone.utc).isoformat(),
        )
        await insert_trade(
            db,
            city="Seattle",
            market_question="Q2",
            direction="YES",
            noaa_probability=0.30,
            market_price=0.40,
            edge=-0.10,
            bet_size=10.0,
            entry_price=0.40,
            outcome="lost",
            pnl=-10.0,
            paper_trade=True,
            resolved_at=datetime.now(timezone.utc).isoformat(),
        )
        await db.execute(
            "INSERT INTO positions (city, market_question, direction, entry_price, size, unrealized_pnl) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("Seattle", "Q open", "YES", 0.50, 12.0, -2.5),
        )
        await db.commit()

        city_rows = await get_pnl_by_city(db)
        calibration = await get_calibration_buckets(db)
        brier = await get_brier_score(db)
        exposure = await get_exposure_summary(db)

        assert city_rows[0]["city"] == "Seattle"
        assert city_rows[0]["total_pnl"] == pytest.approx(5.0)
        assert sum(bucket["count"] for bucket in calibration) == 2
        assert brier == pytest.approx(((0.70 - 1.0) ** 2 + (0.30 - 0.0) ** 2) / 2)
        assert exposure["realized_pnl"] == pytest.approx(5.0)
        assert exposure["open_exposure"] == pytest.approx(12.0)
        assert exposure["worst_case_loss"] == pytest.approx(-12.0)


# ---------------------------------------------------------------------------
# 8. Resolver — _calculate_pnl edge cases
# ---------------------------------------------------------------------------


class TestResolverCalculatePnl:
    """Tests for _calculate_pnl() in bot/resolver.py."""

    def setup_method(self):
        from bot.resolver import _calculate_pnl, MarketResolution
        self._calculate_pnl = _calculate_pnl
        self.MarketResolution = MarketResolution

    def _res(self, outcome: str | None, resolved: bool = True):
        return self.MarketResolution(
            condition_id="test",
            resolved=resolved,
            outcome=outcome,
            resolution_price=1.0 if outcome and outcome.lower() == "yes" else 0.0,
        )

    def test_yes_direction_win(self):
        """YES direction wins when market resolves YES."""
        pnl = self._calculate_pnl("YES", self._res("Yes"), entry_price=0.50, bet_size=20.0)
        assert pnl == pytest.approx(20.0)

    def test_yes_direction_loss(self):
        """YES direction loses when market resolves NO."""
        pnl = self._calculate_pnl("YES", self._res("No"), entry_price=0.50, bet_size=20.0)
        assert pnl == pytest.approx(-20.0)

    def test_no_direction_win(self):
        """NO direction wins when market resolves NO."""
        pnl = self._calculate_pnl("NO", self._res("No"), entry_price=0.40, bet_size=10.0)
        assert pnl == pytest.approx(15.0)

    def test_no_direction_loss(self):
        """NO direction loses when market resolves YES."""
        pnl = self._calculate_pnl("NO", self._res("Yes"), entry_price=0.40, bet_size=10.0)
        assert pnl == pytest.approx(-10.0)

    def test_zero_entry_price_returns_zero(self):
        """entry_price=0 guard should return 0.0 without ZeroDivisionError."""
        pnl = self._calculate_pnl("YES", self._res("Yes"), entry_price=0.0, bet_size=20.0)
        assert pnl == 0.0

    def test_negative_entry_price_returns_zero(self):
        """Negative entry_price should return 0.0 (same guard)."""
        pnl = self._calculate_pnl("YES", self._res("Yes"), entry_price=-0.1, bet_size=20.0)
        assert pnl == 0.0

    def test_high_edge_pnl(self):
        """Low entry price (high edge) yields correct profit."""
        # Buy YES at 0.20, resolves YES: profit = (1-0.20) / 0.20 * bet = 4 * bet
        pnl = self._calculate_pnl("YES", self._res("Yes"), entry_price=0.20, bet_size=10.0)
        assert pnl == pytest.approx(40.0)


# ---------------------------------------------------------------------------
# 9. Resolver — local paper fallback
# ---------------------------------------------------------------------------


class TestResolverLocalPaperFallback:
    """Tests for local paper resolution when Gamma cannot find a mock market."""

    async def _open_paper_position(
        self,
        db,
        *,
        direction: str = "YES",
        temp_low: float = 50.0,
        temp_high: float = 55.0,
    ) -> int:
        target_date = (date.today() - timedelta(days=1)).isoformat()
        trade_id = await insert_trade(
            db,
            city="seattle",
            market_question="56-57°F",
            condition_id="paper-missing",
            token_id="token-paper",
            target_date=target_date,
            temp_low=temp_low,
            temp_high=temp_high,
            temp_unit="F",
            direction=direction,
            noaa_probability=0.70,
            market_price=0.50,
            edge=0.20,
            bet_size=10.0,
            entry_price=0.50,
            paper_trade=True,
        )
        await insert_position(
            db,
            trade_id=trade_id,
            condition_id="paper-missing",
            city="seattle",
            market_question="56-57°F",
            token_id="token-paper",
            direction=direction,
            entry_price=0.50,
            size=10.0,
            paper=True,
            target_date=target_date,
            temp_low=temp_low,
            temp_high=temp_high,
            temp_unit="F",
        )
        return trade_id

    def test_actual_temp_range_uses_rounded_market_temperature(self):
        from bot.resolver import _actual_temp_resolves_yes

        assert _actual_temp_resolves_yes(54.6, 50.0, 55.0) is True
        assert _actual_temp_resolves_yes(55.6, 50.0, 55.0) is False
        assert _actual_temp_resolves_yes(49.6, -999.0, 50.0) is True
        assert _actual_temp_resolves_yes(55.4, 55.0, 999.0) is True

    async def test_resolve_positions_locally_closes_paper_404_win(self, db):
        from bot.resolver import MarketResolution, resolve_positions

        trade_id = await self._open_paper_position(db, temp_low=50.0, temp_high=55.0)
        with patch(
            "bot.resolver.check_resolution",
            new_callable=AsyncMock,
            return_value=MarketResolution(
                condition_id="paper-missing",
                resolved=False,
                not_found=True,
            ),
        ), patch(
            "bot.resolver.fetch_actual_high_temperature",
            new_callable=AsyncMock,
            return_value=54.7,
        ), patch("bot.resolver.send_alert", new_callable=AsyncMock):
            resolved = await resolve_positions(db)

        assert len(resolved) == 1
        positions = await get_open_positions(db)
        assert positions == []
        trades = await get_trade_history(db)
        trade = next(t for t in trades if t["id"] == trade_id)
        assert trade["outcome"] == "won"
        assert trade["pnl"] == pytest.approx(10.0)

    async def test_resolve_positions_does_not_locally_close_live_404(self, db):
        from bot.resolver import MarketResolution, resolve_positions

        target_date = (date.today() - timedelta(days=1)).isoformat()
        trade_id = await insert_trade(
            db,
            city="seattle",
            market_question="56-57°F",
            condition_id="live-missing",
            token_id="token-live",
            target_date=target_date,
            temp_low=50.0,
            temp_high=55.0,
            temp_unit="F",
            direction="YES",
            noaa_probability=0.70,
            market_price=0.50,
            edge=0.20,
            bet_size=10.0,
            entry_price=0.50,
            paper_trade=False,
        )
        await insert_position(
            db,
            trade_id=trade_id,
            condition_id="live-missing",
            city="seattle",
            market_question="56-57°F",
            token_id="token-live",
            direction="YES",
            entry_price=0.50,
            size=10.0,
            paper=False,
            target_date=target_date,
            temp_low=50.0,
            temp_high=55.0,
            temp_unit="F",
        )

        with patch(
            "bot.resolver.check_resolution",
            new_callable=AsyncMock,
            return_value=MarketResolution(
                condition_id="live-missing",
                resolved=False,
                not_found=True,
            ),
        ), patch(
            "bot.resolver.fetch_actual_high_temperature",
            new_callable=AsyncMock,
            return_value=54.7,
        ), patch("bot.resolver.get_current_price", new_callable=AsyncMock, return_value=None):
            resolved = await resolve_positions(db)

        assert resolved == []
        assert len(await get_open_positions(db)) == 1


# ---------------------------------------------------------------------------
# 10. Trade engine — _select_token_id helper
# ---------------------------------------------------------------------------


class TestSelectTokenId:
    """Tests for _select_token_id() in bot/trade_engine.py."""

    def setup_method(self):
        from bot.trade_engine import _select_token_id
        self._select_token_id = _select_token_id

    def _make_token(self, outcome: str, token_id: str):
        tok = MagicMock()
        tok.outcome = outcome
        tok.token_id = token_id
        return tok

    def _make_market(self, tokens):
        m = MagicMock()
        m.tokens = tokens
        return m

    def test_selects_yes_token(self):
        tokens = [self._make_token("YES", "tok-yes"), self._make_token("NO", "tok-no")]
        market = self._make_market(tokens)
        assert self._select_token_id(market, "YES") == "tok-yes"

    def test_selects_no_token(self):
        tokens = [self._make_token("YES", "tok-yes"), self._make_token("NO", "tok-no")]
        market = self._make_market(tokens)
        assert self._select_token_id(market, "NO") == "tok-no"

    def test_case_insensitive(self):
        tokens = [self._make_token("Yes", "tok-yes")]
        market = self._make_market(tokens)
        assert self._select_token_id(market, "yes") == "tok-yes"

    def test_missing_token_returns_empty_string(self):
        tokens = [self._make_token("YES", "tok-yes")]
        market = self._make_market(tokens)
        assert self._select_token_id(market, "NO") == ""

    def test_no_tokens_returns_empty_string(self):
        market = self._make_market([])
        assert self._select_token_id(market, "YES") == ""

    def test_missing_tokens_attribute_returns_empty_string(self):
        market = MagicMock(spec=[])  # no 'tokens' attribute
        assert self._select_token_id(market, "YES") == ""


# ---------------------------------------------------------------------------
# 10. Resolver — check_early_exit
# ---------------------------------------------------------------------------


class TestCheckEarlyExit:
    """Tests for check_early_exit() in bot/resolver.py."""

    def setup_method(self):
        from bot.resolver import check_early_exit
        self.check = check_early_exit

    def test_yes_exit_triggered(self):
        """YES position: price drops 30%+ → exit."""
        assert self.check("YES", entry_price=0.60, current_price=0.40, exit_threshold=0.30) is True

    def test_yes_no_exit_small_drop(self):
        """YES position: price drops < 30% → no exit."""
        assert self.check("YES", entry_price=0.60, current_price=0.50, exit_threshold=0.30) is False

    def test_yes_exact_threshold_not_triggered(self):
        """YES position: price exactly at threshold boundary → no exit (strict <)."""
        # entry * (1 - 0.30) = 0.42; current == 0.42 → not triggered
        assert self.check("YES", entry_price=0.60, current_price=0.42, exit_threshold=0.30) is False

    def test_yes_just_below_threshold(self):
        """YES position: price one tick below threshold → exit."""
        assert self.check("YES", entry_price=0.60, current_price=0.419, exit_threshold=0.30) is True

    def test_no_exit_triggered(self):
        """NO position: price rises 30%+ → exit."""
        assert self.check("NO", entry_price=0.40, current_price=0.53, exit_threshold=0.30) is True

    def test_no_no_exit_small_rise(self):
        """NO position: price rises < 30% → no exit."""
        assert self.check("NO", entry_price=0.40, current_price=0.45, exit_threshold=0.30) is False

    def test_zero_entry_price_guard(self):
        """Zero entry price should never trigger exit (guard against divide-by-zero)."""
        assert self.check("YES", entry_price=0.0, current_price=0.0, exit_threshold=0.30) is False

    def test_direction_case_insensitive(self):
        """direction should be matched case-insensitively."""
        assert self.check("yes", entry_price=0.60, current_price=0.40, exit_threshold=0.30) is True
        assert self.check("Yes", entry_price=0.60, current_price=0.40, exit_threshold=0.30) is True

    def test_unknown_direction_treated_as_no(self):
        """Unknown direction falls through to NO logic."""
        # NO logic: exit if current > entry * (1 + threshold)
        assert self.check("UNKNOWN", entry_price=0.40, current_price=0.53, exit_threshold=0.30) is True


# ---------------------------------------------------------------------------
# 12. Reporter — poll_telegram_commands
# ---------------------------------------------------------------------------


class TestPollTelegramCommands:
    """Tests for poll_telegram_commands() in bot/reporter.py."""

    @pytest.fixture
    async def db(self, tmp_path):
        path = str(tmp_path / "test.db")
        conn = await init_db(path)
        yield conn
        await conn.close()

    async def test_no_op_when_env_vars_missing(self, db):
        """Returns immediately without HTTP call when TELEGRAM_BOT_TOKEN is unset."""
        from bot.reporter import poll_telegram_commands
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("TELEGRAM_BOT_TOKEN", None)
            os.environ.pop("TELEGRAM_CHAT_ID", None)
            # Should complete without error and without making any network call
            with patch("httpx.AsyncClient") as mock_client:
                await poll_telegram_commands(db)
                mock_client.assert_not_called()

    async def test_offset_advances_after_updates(self, db):
        """Processed update_ids cause the stored offset to advance."""
        from bot.reporter import poll_telegram_commands

        fake_response = {
            "result": [
                {
                    "update_id": 100,
                    "message": {
                        "chat": {"id": "999"},
                        "text": "/status",
                    },
                }
            ]
        }

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value=fake_response)

        mock_get = AsyncMock(return_value=mock_resp)

        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_CHAT_ID": "999"}):
            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.get = mock_get
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)
                mock_client_cls.return_value = mock_client

                with patch("bot.reporter._handle_telegram_command", new_callable=AsyncMock):
                    await poll_telegram_commands(db)

        stored = await get_setting(db, "telegram_update_offset")
        assert stored == "101"  # update_id + 1

    async def test_ignores_updates_from_other_chats(self, db):
        """Messages from chats that don't match TELEGRAM_CHAT_ID are ignored."""
        from bot.reporter import poll_telegram_commands

        fake_response = {
            "result": [
                {
                    "update_id": 200,
                    "message": {
                        "chat": {"id": "other_chat"},
                        "text": "/pause",
                    },
                }
            ]
        }

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value=fake_response)

        mock_get = AsyncMock(return_value=mock_resp)

        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_CHAT_ID": "my_chat"}):
            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.get = mock_get
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)
                mock_client_cls.return_value = mock_client

                with patch("bot.reporter._handle_telegram_command", new_callable=AsyncMock) as mock_handle:
                    await poll_telegram_commands(db)

        mock_handle.assert_not_called()

    async def test_network_error_does_not_raise(self, db):
        """HTTP failure is swallowed — poll_telegram_commands never raises."""
        from bot.reporter import poll_telegram_commands

        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_CHAT_ID": "999"}):
            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.get = AsyncMock(side_effect=Exception("network error"))
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)
                mock_client_cls.return_value = mock_client

                await poll_telegram_commands(db)  # must not raise
