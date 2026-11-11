"""Tests of the saved indicator settings."""

import json

import pytest

from indian_stock_market_mcp import settings
from indian_stock_market_mcp.settings import SETTINGS_PATH_ENV, SettingsError


@pytest.fixture
def settings_file():
    return settings.get_settings_path()


def test_without_a_file_every_setting_is_its_default():
    assert settings.load_settings() == settings.DEFAULTS


def test_the_environment_variable_chooses_the_file(monkeypatch, tmp_path):
    monkeypatch.setenv(SETTINGS_PATH_ENV, str(tmp_path / "elsewhere.json"))

    settings.update_settings({"rsi_period": 21})

    assert (tmp_path / "elsewhere.json").exists()


def test_an_update_changes_only_the_named_settings(settings_file):
    updated, customised = settings.update_settings({"rsi_period": 21})

    assert updated["rsi_period"] == 21
    assert updated["atr_period"] == settings.DEFAULTS["atr_period"]
    assert customised == ["rsi_period"]
    assert json.loads(settings_file.read_text()) == {"settings": {"rsi_period": 21}}


def test_updates_accumulate_across_calls():
    settings.update_settings({"rsi_period": 21})
    settings.update_settings({"atr_period": 10})

    assert settings.load_settings()["rsi_period"] == 21
    assert settings.load_settings()["atr_period"] == 10


def test_a_value_set_back_to_its_default_is_no_longer_stored(settings_file):
    settings.update_settings({"rsi_period": 21})
    _, customised = settings.update_settings({"rsi_period": 14})

    assert customised == []
    assert json.loads(settings_file.read_text()) == {"settings": {}}


def test_ema_periods_drop_repeats_and_keep_order():
    updated, _ = settings.update_settings({"ema_periods": [50, 10, 50]})

    assert updated["ema_periods"] == [50, 10]


def test_benchmark_names_are_cleaned_but_auto_keywords_are_kept():
    updated, _ = settings.update_settings(
        {"default_benchmarks": ["nifty 50", "AUTO_SIZE", "my banks"]}
    )

    assert updated["default_benchmarks"] == ["NIFTY_50", "auto_size", "MY_BANKS"]


@pytest.mark.parametrize(
    ("changes", "fragment"),
    [
        ({"rsi_period": 1}, "between 2 and 100"),
        ({"rsi_period": 101}, "between 2 and 100"),
        ({"rsi_period": 14.5}, "whole number"),
        ({"rsi_period": True}, "whole number"),
        ({"rsi_period": "14"}, "whole number"),
        ({"near_high_percent": 0.1}, "between 0.5 and 50"),
        ({"near_high_percent": float("nan")}, "between 0.5 and 50"),
        ({"illiquid_below_crore": -1}, "between 0 and 1000"),
        ({"year_sessions": 100}, "between 200 and 260"),
        ({"ema_periods": []}, "1 to 5 items"),
        ({"ema_periods": [2, 3, 4, 5, 6, 7]}, "1 to 5 items"),
        ({"ema_periods": [1]}, "between 2 and 400"),
        ({"default_benchmarks": ["A", "B", "C", "D", "E", "F"]}, "1 to 5 items"),
        ({"default_benchmarks": [""]}, "1 to 40 characters"),
        ({"relative_strength_periods": {}}, "1 to 6 names"),
        ({"relative_strength_periods": {"3 m": 63}}, "letters, digits"),
        ({"relative_strength_periods": {"3m": 2}}, "between 5 and 504"),
        ({"nonsense": 1}, "Unknown setting"),
        ({}, "non-empty"),
    ],
)
def test_invalid_changes_are_rejected_and_nothing_is_saved(
    changes, fragment, settings_file
):
    with pytest.raises(SettingsError, match=fragment):
        settings.update_settings(changes)

    assert not settings_file.exists()


def test_a_bad_value_among_good_ones_saves_none_of_them(settings_file):
    with pytest.raises(SettingsError):
        settings.update_settings({"atr_period": 10, "rsi_period": 1})

    assert not settings_file.exists()


def test_a_damaged_file_is_reported_and_left_alone(settings_file):
    settings_file.write_text("{not json")

    with pytest.raises(SettingsError, match="could not be read"):
        settings.load_settings()
    with pytest.raises(SettingsError):
        settings.update_settings({"rsi_period": 21})

    assert settings_file.read_text() == "{not json"


def test_a_file_with_an_out_of_range_value_is_reported(settings_file):
    settings_file.write_text(json.dumps({"settings": {"rsi_period": 9999}}))

    with pytest.raises(SettingsError, match="could not be read"):
        settings.load_settings()


def test_reset_with_no_names_restores_every_default(settings_file):
    settings.update_settings({"rsi_period": 21, "atr_period": 10})

    restored, reset = settings.reset_settings()

    assert restored == settings.DEFAULTS
    assert reset == ["rsi_period", "atr_period"]
    assert settings.load_settings() == settings.DEFAULTS


def test_reset_with_names_restores_only_those():
    settings.update_settings({"rsi_period": 21, "atr_period": 10})

    restored, reset = settings.reset_settings(["rsi_period", "year_sessions"])

    assert reset == ["rsi_period"]  # year_sessions was never changed
    assert restored["rsi_period"] == 14
    assert restored["atr_period"] == 10


def test_reset_rejects_an_unknown_name():
    with pytest.raises(SettingsError, match="Unknown setting"):
        settings.reset_settings(["nonsense"])


def test_reset_with_nothing_saved_creates_no_file(settings_file):
    _, reset = settings.reset_settings()

    assert reset == []
    assert not settings_file.exists()


def test_effective_settings_apply_overrides_and_ignore_none():
    settings.update_settings({"rsi_period": 21})

    effective = settings.effective_settings(rsi_period=7, atr_period=None)

    assert effective["rsi_period"] == 7  # this question's override wins
    assert effective["atr_period"] == 14  # None means "use the saved value"
    assert settings.load_settings()["rsi_period"] == 21  # nothing was saved


def test_effective_settings_validate_overrides():
    with pytest.raises(SettingsError):
        settings.effective_settings(rsi_period=1)


def test_describe_shows_values_defaults_and_what_was_customised():
    settings.update_settings({"rsi_period": 21})

    report = settings.describe_settings()

    assert report["settings"]["rsi_period"] == 21
    assert report["defaults"]["rsi_period"] == 14
    assert report["customised"] == ["rsi_period"]


def test_every_default_passes_its_own_validator():
    for name, value in settings.DEFAULTS.items():
        assert settings.VALIDATORS[name](value) == value
