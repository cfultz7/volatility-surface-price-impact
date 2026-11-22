# Plain-English summary

**Question.** When S&P 500 options make downside protection relatively expensive, do S&P futures prices
behave differently in the minutes after a burst of selling pressure?

**What I did.**
- Rebuilt a 30-day volatility smile from SPXW option quotes every five minutes and measured how much
  more downside puts cost than comparable calls (the 25-delta skew).
- Measured buying and selling pressure in E-mini futures from changes in the best bid and offer each
  minute, sampled every second.
- Tested whether the next five minutes of futures returns depend on the skew measured *before* the
  pressure.

**Data.** Every regular trading session of 2025 (250 scheduled, 250 passing data-quality
checks), from Databento (about $25 of data). The model was frozen before the last four months
were examined, and those months were used for the final test.

**Result.** In the final period (85 sessions) I estimate a sell-pressure × skew coefficient of 0.11 bps per SD (95% block-bootstrap interval -0.57 to 0.61). The interval includes zero, so the data do not establish that downside skew changes the response to selling pressure. Adding skew
made out-of-sample squared forecast error 0.34% larger than the
flow-only model.

**What this does not show.** It is not a trading strategy (midquote returns ignore costs). It does not
identify who traded or why, and does not prove causation. Order flow was measured from 1-second
snapshots rather than every order-book event, to stay within budget.
