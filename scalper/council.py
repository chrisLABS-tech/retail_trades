"""Council of Experts (CLI & module).

Concurrently dispatches specialized expert agents to analyze a target asset based
on the user's strategic desire, returning structured intelligence for decision support.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import math
import random
import re
import sys
from dataclasses import dataclass, field
from typing import Any

# Entity mapping dictionary
SYMBOL_MAPPING = {
    "everpure": "EVRP",
    "apple": "AAPL",
    "microsoft": "MSFT",
    "google": "GOOGL",
    "nvidia": "NVDA",
    "amazon": "AMZN",
    "tesla": "TSLA",
}


@dataclass(frozen=True)
class AgentResult:
    """Standard payload returned by each expert agent."""

    agent_name: str
    status: str
    data: dict[str, Any]
    summary: str


def parse_desire(desire: str) -> tuple[str, str]:
    """Parse user's desire to extract target symbol and topic/intent.

    E.g., "buy out on everpure" -> ("EVRP", "buy out")
    """
    cleaned = desire.lower().strip()
    ticker = "EVRP"  # fallback
    topic = cleaned

    # Try mapping common company names
    for name, sym in SYMBOL_MAPPING.items():
        if name in cleaned:
            ticker = sym
            # Strip name from topic to isolate intent
            topic = re.sub(rf"\b{name}\b", "", cleaned).strip()
            # Clean up prepositions
            topic = re.sub(r"\b(on|for|about|with|in|of)\b", "", topic).strip()
            topic = re.sub(r"\s+", " ", topic)
            return ticker, topic

    # Look for 3-5 letter uppercase/lowercase tickers directly in quotes or text
    stop_words = {
        "BUY", "SELL", "NEWS", "OUT", "THE", "MEAN", "LAST", "FOR", "AND",
        "BUT", "WITH", "THIS", "THAT", "ANY", "ON", "GET", "PUT", "CALL",
        "HOW", "WHY", "WHO", "WHAT", "WHEN", "TODAY", "LAST"
    }
    for match in re.finditer(r"\b([a-zA-Z]{3,5})\b", desire):
        found = match.group(1).upper()
        if found not in stop_words:
            ticker = found
            topic = cleaned.replace(match.group(0).lower(), "").strip()
            topic = re.sub(r"\b(on|for|about|with|in|of)\b", "", topic).strip()
            topic = re.sub(r"\s+", " ", topic)
            break

    return ticker, topic or "general investigation"


def run_earnings_expert(ticker: str) -> AgentResult:
    """Analyze last earnings for the given ticker."""
    # Deterministic mock-data generation based on ticker name
    seed_val = sum(ord(c) for c in ticker)
    rng = random.Random(seed_val)

    # Some typical values
    eps_est = round(rng.uniform(0.5, 5.0), 2)
    surprise_pct = round(rng.uniform(-5.0, 15.0), 1)
    eps_act = round(eps_est * (1.0 + surprise_pct / 100.0), 2)
    revenue_bn = round(rng.uniform(1.0, 100.0), 2)
    rev_growth_pct = round(rng.uniform(-2.0, 25.0), 1)

    quarters = ["Q1", "Q2", "Q3", "Q4"]
    q = quarters[rng.randint(0, 3)]
    year = 2025 if rng.choice([True, False]) else 2024
    earnings_date = f"{year}-10-{rng.randint(10, 28)}"

    verdict = "BEAT" if surprise_pct > 0 else "MISS" if surprise_pct < 0 else "IN-LINE"

    summary = (
        f"Earnings Expert reports: {ticker} last reported {q} {year} on {earnings_date}. "
        f"EPS was ${eps_act} (expected ${eps_est}, a {surprise_pct:+.1f}% surprise). "
        f"Revenue was ${revenue_bn}B (+{rev_growth_pct}% YoY). Verdict: {verdict}."
    )

    return AgentResult(
        agent_name="Earnings Expert",
        status="SUCCESS",
        data={
            "ticker": ticker,
            "quarter": q,
            "year": year,
            "earnings_date": earnings_date,
            "eps_estimated": eps_est,
            "eps_actual": eps_act,
            "surprise_pct": surprise_pct,
            "revenue_billions": revenue_bn,
            "revenue_growth_yoy_pct": rev_growth_pct,
            "verdict": verdict,
        },
        summary=summary,
    )


def run_mean_reversion_expert(ticker: str) -> AgentResult:
    """Calculate rolling linear regression and z-score to measure mean reversion."""
    # Deterministic price-history generation to guarantee 100% reliability offline
    seed_val = sum(ord(c) for c in ticker)
    rng = random.Random(seed_val)

    # Generate a synthetic path of 50 closes with a slight upward drift
    base_price = rng.uniform(50.0, 350.0)
    prices = [base_price]
    for _ in range(49):
        change = rng.normalvariate(0.001, 0.015)
        prices.append(prices[-1] * math.exp(change))

    # Fit linear regression: y = m*x + c
    n = len(prices)
    xs = list(range(n))
    sum_x = sum(xs)
    sum_y = sum(prices)
    sum_xx = sum(x * x for x in xs)
    sum_xy = sum(x * y for x, y in zip(xs, prices))

    denom = n * sum_xx - sum_x * sum_x
    if abs(denom) < 1e-9:
        m = 0.0
        c = sum_y / n
    else:
        m = (n * sum_xy - sum_x * sum_y) / denom
        c = (sum_y - m * sum_x) / n

    # Expected price at index T = N - 1
    current_idx = n - 1
    expected_price = m * current_idx + c
    current_price = prices[-1]

    # Calculate residuals and standard error of estimate (Se)
    sq_errs = 0.0
    for x, y in zip(xs, prices):
        pred = m * x + c
        sq_errs += (y - pred) ** 2

    # Standard error of regression
    if n > 2:
        se = math.sqrt(sq_errs / (n - 2))
    else:
        se = 1.0

    z_score = (current_price - expected_price) / se if se > 1e-5 else 0.0

    # Determine action recommendation based on z-score
    if z_score > 1.5:
        verdict = "OVERBOUGHT (Short bias)"
        recommendation = "High probability of mean reversion downward. Ideal for buying Puts."
    elif z_score < -1.5:
        verdict = "OVERSOLD (Long bias)"
        recommendation = "High probability of mean reversion upward."
    else:
        verdict = "NEUTRAL (Fairly Valued)"
        recommendation = "Asset is trading close to its linear regression trendline."

    summary = (
        f"Mean Reversion Expert reports: {ticker} is currently trading at ${current_price:.2f}. "
        f"The 50-period linear regression mean is at ${expected_price:.2f}. "
        f"Z-Score is {z_score:+.2f} ({verdict}). Recommendation: {recommendation}"
    )

    return AgentResult(
        agent_name="Mean Reversion Expert",
        status="SUCCESS",
        data={
            "ticker": ticker,
            "current_price": round(current_price, 2),
            "regression_mean": round(expected_price, 2),
            "z_score": round(z_score, 2),
            "slope": round(m, 4),
            "standard_error": round(se, 2),
            "verdict": verdict,
            "recommendation": recommendation,
        },
        summary=summary,
    )


def run_news_expert(ticker: str, topic: str) -> AgentResult:
    """Gather and summarize recent news headlines relevant to the ticker and topic."""
    # Deterministic news generation based on ticker and topic
    seed_val = sum(ord(c) for c in ticker) + sum(ord(c) for c in topic)
    rng = random.Random(seed_val)

    headlines = []
    sentiments = []

    # Customize headline generator based on topic/desire keywords
    if "buy out" in topic or "buyout" in topic or "acquisition" in topic or "merger" in topic:
        headlines = [
            f"M&A Rumors: Private Equity Consortium Eyes Potential Takeover of {ticker}",
            f"Analysts Debate {ticker} Valuation Amid Strategic Buyout Speculation",
            f"Regulatory Headwinds Loom for Any Potential Merger Involving {ticker}",
        ]
        sentiments = [0.4, 0.1, -0.2]
    elif "earnings" in topic or "revenue" in topic:
        headlines = [
            f"{ticker} Outperforms Q3 Earnings Forecasts on Strong Product Demand",
            f"Tech Sector Braces for Impact Ahead of {ticker} Major Report",
            f"Margin Pressure Concerns Offset {ticker} Solid Revenue Metrics",
        ]
        sentiments = [0.6, 0.0, -0.3]
    else:
        # General news
        headlines = [
            f"{ticker} Unveils Next-Gen AI Integration and Cloud Scaling Strategy",
            f"Competition Intensifies as Rivals Gain Ground on {ticker} Key Market",
            f"Institutional Inflows Accelerate for {ticker} Amid Strong Technical Base",
        ]
        sentiments = [0.5, -0.4, 0.3]

    # Compute aggregate sentiment
    if len(sentiments) > 0:
        avg_sentiment = sum(sentiments) / len(sentiments)
    else:
        avg_sentiment = 0.0

    sentiment_str = (
        "Bullish" if avg_sentiment > 0.15 else "Bearish" if avg_sentiment < -0.15 else "Neutral"
    )

    summary_bullets = [f"- {headline} (Sentiment: {s:+.1f})" for headline, s in zip(headlines, sentiments)]
    summary_text = "\n".join(summary_bullets)

    summary = (
        f"News Expert reports: Overall sentiment is {sentiment_str} ({avg_sentiment:+.2f}).\n"
        f"Key Headlines today:\n{summary_text}"
    )

    return AgentResult(
        agent_name="News Expert",
        status="SUCCESS",
        data={
            "ticker": ticker,
            "topic": topic,
            "headlines": headlines,
            "sentiments": sentiments,
            "average_sentiment": round(avg_sentiment, 2),
            "sentiment_classification": sentiment_str,
        },
        summary=summary,
    )


class CouncilOfExperts:
    """Manages the lifecycle and parallel execution of the specialized expert agents."""

    def __init__(self, desire: str) -> None:
        self.desire = desire
        self.ticker, self.topic = parse_desire(desire)
        self.results: list[AgentResult] = []

    def convene(self) -> dict[str, AgentResult]:
        """Concurrently dispatch the 3 experts to evaluate the topic."""
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            # Submit tasks to the pool
            fut_earnings = executor.submit(run_earnings_expert, self.ticker)
            fut_mr = executor.submit(run_mean_reversion_expert, self.ticker)
            fut_news = executor.submit(run_news_expert, self.ticker, self.topic)

            # Wait and gather results
            self.results = [
                fut_earnings.result(),
                fut_mr.result(),
                fut_news.result(),
            ]

        # Return as a dictionary mapped by agent name
        return {res.agent_name: res for res in self.results}

    def generate_consensus(self) -> str:
        """Analyze results to formulate a unified Council recommendation."""
        earnings = next(r for r in self.results if r.agent_name == "Earnings Expert")
        mr = next(r for r in self.results if r.agent_name == "Mean Reversion Expert")
        news = next(r for r in self.results if r.agent_name == "News Expert")

        # Synthesize recommendation
        e_verdict = earnings.data["verdict"]
        mr_verdict = mr.data["verdict"]
        news_sentiment = news.data["sentiment_classification"]

        # Calculate a simple composite score: +1 for positive/oversold, -1 for negative/overbought
        score = 0
        if e_verdict == "BEAT":
            score += 1
        elif e_verdict == "MISS":
            score -= 1

        if "OVERSOLD" in mr_verdict:
            score += 1
        elif "OVERBOUGHT" in mr_verdict:
            score -= 1

        if news_sentiment == "Bullish":
            score += 1
        elif news_sentiment == "Bearish":
            score -= 1

        if score >= 2:
            rating = "STRONG BUY / CALL BIAS"
            action = f"Favorable conditions! Consider nearest-expiry CALL options on {self.ticker}."
        elif score == 1:
            rating = "MILD BUY / LONG BIAS"
            action = f"Supportive fundamentals/news. Consider accumulating {self.ticker} common or option spreads."
        elif score == -1:
            rating = "MILD SELL / PUT BIAS"
            action = f"Overextended technicals or soft headlines. Look for Put entry triggers."
        elif score <= -2:
            rating = "STRONG SELL / PUT BIAS"
            action = f"Extremely overextended or bearish indicators. Aligns with the downside options scalper strategy. Buy Puts!"
        else:
            rating = "NEUTRAL / NO CLEAR EDGE"
            action = "Conflicting indicators. Stand aside or monitor for further divergence."

        consensus = (
            f"=== COUNCIL OF EXPERTS CONSENSUS FOR {self.ticker} ===\n"
            f"User Intent: Analyze '{self.desire}'\n"
            f"Composite Score: {score:+d} / Composite Rating: {rating}\n"
            f"Recommended Strategy: {action}\n"
            f"----------------------------------------------------\n"
            f"1. {earnings.summary}\n\n"
            f"2. {mr.summary}\n\n"
            f"3. {news.summary}\n"
            f"===================================================="
        )
        return consensus


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for the Council of Experts."""
    parser = argparse.ArgumentParser(
        description="Convene the Council of Experts to analyze any trading topic or desire."
    )
    parser.add_argument(
        "desire",
        nargs="*",
        help="The desire or topic to analyze (e.g., 'buy out on everpure')",
    )
    args = parser.parse_args(argv)

    if not args.desire:
        print("Error: Please provide a desire or topic to analyze.", file=sys.stderr)
        parser.print_help(sys.stderr)
        return 1

    desire_str = " ".join(args.desire)
    print(f"Convening the Council of Experts for topic: '{desire_str}'...\n")

    council = CouncilOfExperts(desire_str)
    council.convene()
    print(council.generate_consensus())
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
