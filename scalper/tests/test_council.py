"""Tests for the Council of Experts module."""

from __future__ import annotations

from scalper.council import (
    CouncilOfExperts,
    parse_desire,
    run_earnings_expert,
    run_mean_reversion_expert,
    run_news_expert,
    main,
)


def test_parse_desire() -> None:
    """Test entity and topic parsing from various user desire inputs."""
    # Test mapped company name
    ticker, topic = parse_desire("buy out on everpure")
    assert ticker == "EVRP"
    assert "buy out" in topic

    # Test mapped company name without on/for preposition
    ticker, topic = parse_desire("buyout everpure")
    assert ticker == "EVRP"
    assert "buyout" in topic

    # Test raw ticker string
    ticker, topic = parse_desire("earnings report for AAPL")
    assert ticker == "AAPL"
    assert "earnings report" in topic

    # Test fallback ticker when no ticker or name is present
    ticker, topic = parse_desire("something general")
    assert ticker == "EVRP"
    assert "something general" in topic


def test_earnings_expert() -> None:
    """Test EarningsExpert determinism and data structure."""
    res1 = run_earnings_expert("EVRP")
    res2 = run_earnings_expert("EVRP")
    assert res1.agent_name == "Earnings Expert"
    assert res1.status == "SUCCESS"
    assert res1.data["ticker"] == "EVRP"
    # Verify determinism
    assert res1.data["eps_actual"] == res2.data["eps_actual"]
    assert "EPS was" in res1.summary


def test_mean_reversion_expert() -> None:
    """Test MeanReversionExpert linear regression and z-score calculations."""
    res = run_mean_reversion_expert("AAPL")
    assert res.agent_name == "Mean Reversion Expert"
    assert res.status == "SUCCESS"
    assert "z_score" in res.data
    assert "regression_mean" in res.data
    assert "Z-Score is" in res.summary


def test_news_expert() -> None:
    """Test NewsExpert headline and sentiment generation."""
    res = run_news_expert("EVRP", "buy out")
    assert res.agent_name == "News Expert"
    assert res.status == "SUCCESS"
    assert len(res.data["headlines"]) == 3
    assert res.data["sentiment_classification"] in ("Bullish", "Bearish", "Neutral")


def test_council_of_experts_convene() -> None:
    """Test end-to-end convening of the council of experts."""
    council = CouncilOfExperts("buy out on everpure")
    assert council.ticker == "EVRP"
    assert "buy out" in council.topic

    results = council.convene()
    assert "Earnings Expert" in results
    assert "Mean Reversion Expert" in results
    assert "News Expert" in results

    consensus = council.generate_consensus()
    assert "=== COUNCIL OF EXPERTS CONSENSUS FOR EVRP ===" in consensus
    assert "Earnings Expert reports" in consensus
    assert "Mean Reversion Expert reports" in consensus
    assert "News Expert reports" in consensus


def test_council_cli(capsys) -> None:
    """Test the CLI entry point of council."""
    # Test valid call
    code = main(["buy", "out", "on", "everpure"])
    assert code == 0
    captured = capsys.readouterr()
    assert "Convening the Council of Experts" in captured.out
    assert "COUNCIL OF EXPERTS CONSENSUS FOR EVRP" in captured.out

    # Test empty call
    code_empty = main([])
    assert code_empty == 1
    captured_empty = capsys.readouterr()
    assert "Error: Please provide a desire or topic" in captured_empty.err
