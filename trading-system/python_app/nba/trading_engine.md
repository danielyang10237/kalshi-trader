1. Every trade quotes a two-sided market around the Kalshi-anchored posterior (theo). Fixed 1¢ offset: bid at `theo - 1` and ask at `theo + 1`. Whichever side crosses the current spread executes immediately (edge capture); the other side rests passively on the book (no Kalshi fees for resting liquidity-adding orders). The aggressive side pays fees: `roundup(0.07 * C * P * (1-P))` where C is contract count and P is price.
	- **Inventory skew**: when holding a position, skew the 1¢ offset toward the side that reduces exposure. E.g. if long and theo=80, quote 79@80 (ask AT theo to encourage sells). If short, quote 80@81 (bid AT theo to encourage buys).
	- **Snapshot lifecycle** (order of operations on every game state update):
		1. New snapshot arrives → model computes new theo
		2. **Cancel all resting orders immediately** — before the market reprices to the same event. This prevents stale orders from being picked off (e.g. old bid at 79 when theo just dropped to 75).
		3. Check if `edge > fee` on the aggressive side of the new quote. Fee = `ceil(0.07 * size * P * (1-P) * 100) / 100` where P = aggressive price / 100.
		4. If edge > fee → compute size (per points 3-6), send new bid/ask pair with inventory skew.
		5. If edge ≤ fee → stay flat, no new quote. All old orders already cancelled in step 2.
2. Game variance is derived directly from the GAM posterior: `game_variance = p * (1 - p)` where p = P(home_win) from the model. No separate variance model is needed — for a binary outcome, this IS the conditional variance. Empirically validated against 3.75M training snapshots bucketed by (score_diff, time_remaining): the GAM's calibrated output matches the observed win-rate variance in every bucket (ECE ~0.02-0.05). `max_variance = 0.25` (at p=0.5). `certainty = 1 - game_variance / 0.25`.
3. We run the SAME GAM posterior model twice with different prior inputs. The prior enters the GAM as a single linear term `l(pregame_logit)` — a calibrated probability is a calibrated probability regardless of source. No need for two separate trained models.
	- **Kalshi-anchored posterior**: feed `prior = market_price / 100` (the current Kalshi best mid or last trade). Since the market price already incorporates in-game info from other participants, this posterior stays closer to the market — giving a conservative, latency-aware trading signal.
	- **Computed-prior posterior**: feed `prior = xgboost_prediction` (pre-game only, no in-game market info). This posterior drifts further from the market since the XGBoost prior doesn't know the current score — giving an independent confirmation layer.
	- We always trade on the Kalshi-anchored posterior, and use the computed-prior posterior only for sizing / confidence.
	1.	Example strong-confirmation case: market trading at 80¢, Kalshi posterior computes 85¢, prior posterior computes 90¢.
	2.	Entry edge is still driven only by the Kalshi posterior:
	•	kalshi_edge = p_kalshi_post - p_market
	•	e.g. 85 - 80 = +5¢ → directional signal is buy YES
	3.	We separately measure whether the independent prior-posterior confirms the trade direction:
	•	prior_edge = p_prior_post - p_market
	•	e.g. 90 - 80 = +10¢
	•	same_direction = sign(kalshi_edge) == sign(prior_edge)
	4.	We compute raw agreement by posterior distance:
	•	disagreement = |p_kalshi_post - p_prior_post|
	•	agreement = 1 - disagreement / 100  # 0 to 1
	•	e.g. |85 - 90| = 5¢, so agreement = 0.95
	5.	But raw agreement alone is not enough, because a small disagreement where both models are on the same side of the market is much stronger than a small disagreement where they straddle the market. So we introduce a directional agreement bonus / penalty:
	•	If both models point the same way:
	•	confirmation = min(|prior_edge| / |kalshi_edge|, 2.0)
	•	agreement_score = agreement * confirmation
	•	If they point in opposite directions:
	•	agreement_score = agreement * 0.5
	6.	Position sizing is then determined by certainty from the volatility model together with the agreement score, while always respecting hard risk bounds:
	•	min_size = minimum order size worth placing (e.g. 5 contracts)
	•	base_size = normal trade size (e.g. 50 contracts)
	•	max_size = absolute hard cap, never exceeded (e.g. 100 contracts)
	•	certainty = 1 - game_variance / max_variance
	•	raw_scale = certainty * clip(agreement_score, 0, 2.0) / 2.0
	•	size = lerp(min_size, max_size, clip(raw_scale, 0, 1))
	7.	This gives us three key behaviors:
	•	Edge magnitude decides whether we trade at all
	•	Directional agreement boosts size when both models agree on buy/sell, and penalizes size when they disagree
	•	Certainty / game state resolution scales exposure up in lower-variance states and down in noisier states
	8.	Concrete example with strong confirmation: let min_size = 5, base_size = 50, max_size = 100, and certainty = 0.88 (say Q4, 2 min left, +8).
	•	Market = 80¢
	•	Kalshi posterior = 85¢
	•	Prior posterior = 90¢
	•	kalshi_edge = 85 - 80 = +5¢ → buy YES
	•	prior_edge = 90 - 80 = +10¢
	•	same_direction = true
	•	disagreement = |85 - 90| = 5¢
	•	agreement = 1 - 5/100 = 0.95
	•	confirmation = min(10/5, 2.0) = 2.0
	•	agreement_score = 0.95 * 2.0 = 1.90
	•	raw_scale = 0.88 * 1.90 / 2.0 = 0.836
	•	size = lerp(5, 100, 0.836) ≈ 84 contracts
	•	Interpretation: both models say the market is underpriced, and the independent prior-posterior is even more bullish, so this earns near-max sizing.
	9.	Compare that with a disagreement / straddle case: market = 65¢, Kalshi posterior = 70¢, prior posterior = 60¢.
	•	kalshi_edge = 70 - 65 = +5¢ → buy YES
	•	prior_edge = 60 - 65 = -5¢ → sell / fade
	•	same_direction = false
	•	disagreement = |70 - 60| = 10¢
	•	agreement = 0.90
	•	agreement_score = 0.90 * 0.5 = 0.45
	•	If certainty = 0.28, then raw_scale = 0.28 * 0.45 / 2.0 = 0.063
	•	size = lerp(5, 100, 0.063) ≈ 11 contracts
	•	Interpretation: the Kalshi model still determines direction, but the independent model disagrees on side, so we size way down.
	10.	The computed-prior posterior never determines trade direction — that always comes from the Kalshi posterior because it preserves the latency / market-conditioning edge. The computed-prior posterior only acts as an independent confirmation layer for sizing.
	11.	Hard limits always apply:
	•	never place below min_size if trading is not worth the fees
	•	never exceed max_size under any circumstance
	•	base_size is just the typical size for an ordinary, moderately strong setup
	13.	Inventory reduction is handled by the quote skew in point 1: every re-quote skews the 1¢ offset toward reducing exposure. Since all old orders are cancelled on each snapshot, there is always exactly one bid and one ask resting. Net position should never exceed max_size in either direction.
	14.	Residual inventory is managed within the same max_size hard cap and should never be allowed to accumulate beyond absolute exposure limits.