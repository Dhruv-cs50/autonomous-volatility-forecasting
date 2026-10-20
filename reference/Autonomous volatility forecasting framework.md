# Build the vol shootout into a self-running research lab

Dhruv's shootout can become a credible autonomous framework if one rule holds: **a deterministic pipeline owns the data, the splits and the scoring, and the LangGraph agent only proposes experiments through typed, validated tools**. The closest blueprint is Microsoft's RD-Agent(Q). It runs a five-unit loop (specify, synthesize, implement, validate, analyze) with a bandit scheduler and a knowledge base. Recent papers show why the harness must own evaluation: an LLM reviewer approved a leaky feature, and a deliberately leaky strategy passed standard statistical corrections. I found no open-source agentic framework built specifically for volatility forecasting, so the project fills a real gap. The search was not exhaustive. The largest accuracy gains come from inputs, not model complexity. In rough order of value: intraday realized variance, negative semivariance and signed jumps, horizon-matched implied vol (which his own options library can supply), and regime switching at weekly and monthly horizons. ML and foundation models beat HAR by only about 1 to 2%, and much of that edge is calibration. The whole system can run for **about $1 per month**: GitHub Actions cron, Cloudflare R2, Neon Postgres, a Next.js dashboard on Vercel and a cheap LLM (OpenRouter's free tier for development, a low-cost paid model for scheduled runs). A $29 per month data plan is the only cost worth adding, and only if he needs a longer intraday history.

## RD-Agent(Q) is the template; trading-agent repos are not

Most popular "finance agent" repos make trading decisions. They do not run research loops, so they offer patterns rather than architecture. The table below sorts the projects worth studying by what each contributes.

| Project | What it is | Pattern worth copying |
|---|---|---|
| RD-Agent / RD-Agent(Q) (Microsoft, ~15k stars, MIT, NeurIPS 2025) | Closed-loop factor and model research on Qlib | Specification, Synthesis, Implementation, Validation and Analysis units; a linear Thompson-sampling bandit that picks "refine features" vs "change model"; a code knowledge base; dropping candidates with 0.99 or higher correlation to the SOTA set ([arXiv 2505.15155](https://arxiv.org/html/2505.15155v2); [GitHub](https://github.com/microsoft/RD-Agent)) |
| AQuA (Princeton/Ant/Stanford, Aug 2026, no code) | Manager-routed research team plus a config-diff loop | Agents never call each other, because a manager routes all handoffs; each hypothesis is one config diff over a sealed sandbox; the test window is never returned to the agent ([arXiv 2608.12841](https://arxiv.org/pdf/2608.12841)) |
| AlphaAgent (paper, no code) | Idea, Factor and Eval agents on Qlib | A critic that scores whether the implementation matches the hypothesis; originality and complexity penalties ([arXiv 2502.16789](https://arxiv.org/html/2502.16789v1)) |
| QuantEvolve (Qraft, paper) | Quality-diversity evolutionary search | An insight archive plus a rejected-strategy archive; the authors admit exposure to data snooping ([arXiv 2510.18569](https://arxiv.org/pdf/2510.18569)) |
| TradingAgents (~98k stars, Apache-2.0, LangGraph) | Analyst, debate, trader and risk roles | SQLite checkpoint resume, a reflection memory that scores past decisions against realized outcomes, structured outputs, look-ahead filtering ([GitHub](https://github.com/TauricResearch/TradingAgents)) |
| FinRobot Desktop (Apache-2.0) | Lead agent plus five sub-agents | Deterministic Python does all computation; the LLM only plans and narrates ([GitHub](https://github.com/AI4Finance-Foundation/FinRobot)) |
| `arch` 8.0.0 (Oct 2025) | GARCH family, HAR mean, bootstraps | Built-in SPA, StepM and Model Confidence Set ([PyPI](https://pypi.org/project/arch/); [GitHub](https://github.com/bashtage/arch)) |
| HARNet (Univ. of Vienna, TensorFlow) | Dilated-CNN generalization of HAR | About 11.7% lower median MAE than HAR-OLS under MAE loss and almost no gain under MSE; QLIKE training was unstable unless initialized from the HAR fit ([arXiv 2205.07719](https://arxiv.org/pdf/2205.07719)) |
| VOLARE (2026) | Open realized-measure archive that replaces the Oxford-Man library, which closed in 2022 | Free RV, semivariance, bipower and realized-kernel targets for stocks (from 2015), FX and futures ([arXiv 2602.19732](https://arxiv.org/html/2602.19732v1)) |

RD-Agent itself is too heavy to adopt wholesale for this project. It needs Linux and Docker, it targets equity cross-sections, and Qlib's last release dates from 2022 and its official dataset is disabled ([GitHub Qlib](https://github.com/microsoft/qlib)). Its decomposition still maps directly onto a vol shootout. Specification becomes the target, the loss and the data interface. Synthesis proposes a model or feature such as HAR-RV-J or IV-augmented HAR. Validation is the walk-forward run. Analysis compares the candidate against the current confidence set.

Reported gains across these systems are modest, come from single markets and periods, and are author-run. RD-Agent(Q) also performs noticeably worse with weaker LLM backends ([arXiv 2505.15155](https://arxiv.org/html/2505.15155v2)). The most useful negative result comes from "What survives honest evaluation?". Its protocol restricts the agent to registry-validated tools and logs every trial. Under that protocol, **every LLM-discovered strategy was rejected**, and a deliberately leaky strategy with a Sharpe of 35 passed both the Deflated Sharpe and PBO checks ([arXiv 2608.27734](https://arxiv.org/pdf/2608.27734)). AQuA reports a reviewing agent that approved a feature built from future intraday bars ([arXiv 2608.12841](https://arxiv.org/pdf/2608.12841)). Taken together, these results argue for deterministic leakage tests as a hard gate, with an LLM critic as a secondary check only.

## Inputs beat architecture: semivariance, horizon-matched IV and regimes

HAR is a hard benchmark. Across 1,078 S&P 500 firms, it had the lowest RMSE, MAE and QLIKE against LASSO, PCA-factor and ensemble rivals ([arXiv 2506.07928](https://arxiv.org/pdf/2506.07928)). A 2026 study used VOLARE data on 50 assets with QLIKE, DM tests and the MCS. Only a sub-1M-parameter foundation model (TTM) beat Log-HAR at every horizon, and only by **1.3 to 1.8%**. After Mincer-Zarnowitz recalibration, most of its edge at 1 and 5 days disappeared. An equal-weight TTM plus Log-HAR combination stayed in the 10% MCS for 98 to 100% of assets ([arXiv 2607.05291](https://arxiv.org/pdf/2607.05291)). The shootout should therefore include a recalibrated HAR and a simple HAR-plus-ML combination as contestants, because both are cheap and strong. The table ranks inputs by documented incremental value.

| Input | Evidence | Best horizon | Free source |
|---|---|---|---|
| Intraday realized variance instead of squared daily returns | Realized models beat daily-return GARCH and add economic value ([Bauwens & Xu 2025](https://ideas.repec.org/a/eee/intfor/v41y2025i3p1165-1183.html)) | 1 to 5 days | Massive minute bars (2 years free, 5 years at $29/mo) or VOLARE |
| Negative semivariance and signed jumps | Keeping only negative semivariance in HAR gives SPY DM statistics of 2.69 / 4.29 / 4.41 / 5.23 at 1 / 5 / 22 / 66 days; the leverage dummy adds about 0.001 R2 once semivariance is in the model ([Patton & Sheppard](https://public.econ.duke.edu/~ap172/Patton_Sheppard_Realized_Semivariance_7oct11.pdf)) | 5 to 66 days | Computed from intraday bars |
| Horizon-matched implied vol | IV adds information beyond HAR jump and continuous components ([Busch et al.](https://repec.econ.au.dk/repec/creates/rp/07/rp07_09.pdf)); VIX1D best predicts 1-day RV, and the best predictor generally matches the forecast maturity ([Li et al. 2025](https://ourarchive.otago.ac.nz/esploro/outputs/journalArticle/VIX-term-structure-and-future-realized/9926827081601891)) | All, strongest at 22 days | Cboe CSVs (VIX, VIX9D, VVIX); his own options library |
| Markov-switching HAR | No gain at 1 day; at 22 days FTSE MSE falls about 38% (0.086 vs 0.138), and the model is the only one to survive the MCS on all 8 indices at 5 days ([Ding et al. 2025](https://storre.stir.ac.uk/retrieve/2df2464a-1aa8-4cba-b60e-4bda18a768eb/Forecasting%20realised%20volatility%20using%20regime-switching%20models.pdf)) | 5 to 22 days | Derived |
| News sentiment and attention | Sentiment-augmented combination regression beat HAR for 98.8% of 404 stocks, with a 12.7% mean MSE gain at 1 day ([arXiv 2503.19767](https://arxiv.org/pdf/2503.19767)) | 1 day | Google Trends, Wikipedia views; MSE-only evidence, so re-test with QLIKE |
| Credit spreads and macro | Default spread is the strongest equity-vol predictor, with out-of-sample R2 of about 2 to 7% concentrated in 2007 to 2009 ([BIS WP 374](https://www.bis.org/publ/work374.pdf)) | 22 days | FRED Moody's spreads (the ICE HY OAS on FRED is capped at 3 years and carries reproduction limits, per [FRED](https://fred.stlouisfed.org/series/BAMLH0A0HYM2)) |
| Earnings dates (single stocks) | Option-implied earnings uncertainty predicts future vol, and ignoring it distorts option pricing ([Dubinsky et al.](https://ideas.repec.org/a/oup/rfinst/v32y2019i2p646-687..html)) | Event windows | Options library plus an earnings calendar |
| Volume | Weak or no incremental value in most studies | Not recommended | |

Two caveats apply. First, a 2-year free minute window gives only about 500 daily observations, which is thin for 22-day walk-forward and DM tests. The $29 tier or VOLARE fixes this, although VOLARE's index coverage and license are unconfirmed ([Massive pricing](https://massive.com/pricing); [arXiv 2602.19732](https://arxiv.org/html/2602.19732v1)). Second, QLIKE should stay the primary loss because it ranks models robustly when the volatility proxy is noisy ([Patton 2011](https://scholars.duke.edu/publication/792433)).

### Downstream consumers turn forecast accuracy into economic loss functions

Adding downstream evaluators makes the project "run better" in two ways. They test whether statistical gains matter, and they give the agent an economic objective in addition to QLIKE. Evidence for value is strongest in allocation: intraday-RV covariance forecasts are worth **50 to 200 bp per year** to a risk-averse investor ([Fleming, Kirby & Ostdiek](https://academicnewsletter.sufe.edu.cn/info/357738)). Volatility targeting raised US equity Sharpe from 0.40 to about 0.50 and cut vol-of-vol from 4.6% to 1.8%, but the gain was not very sensitive to the estimator ([Alpha Architect summary of Harvey et al.](https://alphaarchitect.com/volatility-targeting-improves-risk-adjusted-returns/)). That makes it a weak test of forecast quality. Regression-weighted "vol-managed" portfolios mostly fail out of sample: they beat the original strategy on Sharpe in only 45 of 103 cases ([Cederburg et al.](https://www.lehigh.edu/~xuy219/research/COWY.pdf)). Simple scaling is the safer choice.

Options are where a better engine pays off most. Sorting on realized minus implied vol produces significant long/short option returns ([Goyal & Saretto](https://academicnewsletter.sufe.edu.cn/info/357238)), and small RV gains can matter economically in delta-neutral straddles ([arXiv 2506.07928](https://arxiv.org/pdf/2506.07928)). VaR and ES are the cautionary case. Hybrid models that improved MSE still failed Christoffersen and ES backtests at 1%, while APARCH with skew-t innovations passed every test ([arXiv 2310.01063](https://arxiv.org/pdf/2310.01063)). The innovation distribution matters as much as the vol forecast.

This produces the project's signature feedback loop, which is a design inference: no paper evaluates it directly. The options library computes IV or model-free variance per maturity. Those values become HAR-X regressors and also the "implied" contestant. Forecast RV then flows back into the pricer as a fair-value input. The gap between forecast and implied drives a straddle or VRP strategy, whose P&L serves as an economic loss. VRP work needs model-free implied variance rather than Black-Scholes IV ([BTZ](https://papers.ssrn.com/abstract=948309)). IV must be timestamped at the close before the forecast window, and the same option prices must not both calibrate IV and score P&L on one bar.

## A fifteen-component framework where the harness owns truth

The design has three tiers. A deterministic scheduler runs everything on a timetable. A sealed evaluation harness produces the only numbers that count. A bounded LangGraph research layer proposes work and never sits on the daily forecast path. Prefect 3 or Dagster suits the backbone; Airflow is overkill for one person ([comparison](https://tech-insider.org/airflow-vs-dagster-vs-prefect-2026/), a single secondary source). LangGraph 1.x provides stable core APIs, Postgres checkpointing and `interrupt` for human-in-the-loop. `create_react_agent` is deprecated in favor of LangChain's `create_agent` ([LangGraph v1 notes](https://docs.langchain.com/oss/python/releases/langgraph-v1); [persistence docs](https://docs.langchain.com/oss/python/langgraph/durable-execution)).

### Component responsibilities

| # | Component | Designated task | Inputs | Outputs | Recommended tool |
|---|---|---|---|---|---|
| 1 | Scheduler | Run ingest, features, scoring, reports and monitors on a timetable; fire the weekly agent sprint | Cron schedule, configs | Flow runs, alerts | Prefect 3 (lighter) or Dagster (asset lineage, date partitions) |
| 2 | Ingestion | Pull bars, VIX family, macro series and option chains; write immutable raw files | Vendor APIs | `data/raw/{source}/{symbol}/date=.../*.parquet` stamped with `as_of` and `ingested_at` | Python clients: Massive, Cboe CSVs, FRED |
| 3 | Point-in-time store | Single source of truth; refuse holdout reads in dev | Raw and derived Parquet | DuckDB views; a `load_panel()` that asserts `end <= DEV_END` | Parquet plus DuckDB; Postgres for metadata |
| 4 | Realized-measure and feature engine | Compute RV, semivariances, bipower and jumps, HAR lags, regime probabilities, event dummies, all from trailing windows only | Curated bars, calendars | Feature tables keyed by date | Python plus DuckDB SQL |
| 5 | Options/IV engine (his library) | Build IV surfaces, model-free variance, earnings-implied variance; flag arbitrage violations | Option chains, rates | IV features; the "implied" contestant; fair values from forecast RV | His pricing library |
| 6 | Model zoo | Fit each family against one interface: `fit(train) -> forecast(h)` | Features, config | Forecast series per model, horizon and fold | `arch` (GARCH, GJR, EGARCH, skew-t), statsmodels (HAR, MS-HAR), LightGBM, optional TTM |
| 7 | Walk-forward harness | Run expanding or rolling refits on a fixed cadence, purging and embargoing overlapping multi-day targets | Config, model, dev panel | Out-of-sample loss series (QLIKE, MSE) | Custom harness; skfolio `WalkForward` / `CombinatorialPurgedCV` ([skfolio](https://skfolio.org/generated/skfolio.model_selection.CombinatorialPurgedCV.html)) |
| 8 | Statistical comparison | Pairwise DM tests, then MCS and SPA over every candidate; count trials | Loss series, trials table | MCS membership at 10% and 25%, DM p-values | `arch.bootstrap.MCS/SPA/StepM` ([docs](https://arch.readthedocs.io/en/latest/multiple-comparison/multiple-comparisons.html)), `dieboldmariano` with Harvey correction ([GitHub](https://github.com/edoannunziata/dieboldmariano)) |
| 9 | Downstream evaluators | Turn forecasts into economic metrics | Forecasts, prices, IV | VaR/ES breaches with Kupiec and Christoffersen p-values; vol-target Sharpe, drawdown and turnover; straddle/VRP P&L | Custom modules |
| 10 | Tracker and registry | Log params, data hash, git SHA, losses and artifacts; hold `@champion` and `@challenger` aliases; trace agent LLM calls | Runs, models | Lineage, leaderboard | Self-hosted MLflow 3.x ([MLflow 3](https://mlflow.org/releases/3)) |
| 11 | Research agent | Propose, validate, submit, critique and report experiments (roles below) | Leaderboard, trials, dev metrics only | Config diffs, run requests, draft write-ups | LangGraph `StateGraph` plus PostgresSaver |
| 12 | Guardrail gate | Hard-block leakage before any score counts | Candidate feature or config | Pass or fail | pytest point-in-time tests, a shuffled-future canary, holdout guard |
| 13 | Reporting and serving | Publish the daily forecast, leaderboard and research log | Registry, forecasts | Static report, dashboard | Jinja or Quarto report, Next.js dashboard on Vercel |
| 14 | Monitoring | Detect performance and data drift | Live losses, features | Alerts | Rolling 21/63-day QLIKE vs HAR, rolling MCS membership, Mincer-Zarnowitz slope, missing bars; Evidently ([arXiv 2404.18673](https://arxiv.org/html/2404.18673v2)) |
| 15 | CI | Keep the harness honest on every push | Code | Green or red build | GitHub Actions, uv, ruff, mypy, pytest |

The agent layer splits into roles connected through a supervisor, following AQuA's hub-and-spoke rule that agents never call each other directly.

| Agent role | Task | Allowed tools | Model tier |
|---|---|---|---|
| Supervisor | Pick the next action: add features, change model family, or stop. A bandit in the RD-Agent(Q) style can replace the LLM here | `list_models`, `get_mcs_result` | Cheap, or no LLM |
| Researcher | Write a hypothesis, informed by the SOTA set, the trial log and rejected ideas | Read-only leaderboard and memory | Stronger model |
| Configurator | Turn the hypothesis into a schema-validated config diff (allowed families, features, ranges) | `propose_config` | Cheap |
| Runner | Submit the job to the scheduler and wait. Contains no LLM | `submit_walkforward -> run_id` | None |
| Critic | Check alignment between hypothesis and config, the trial count and dev-only metrics, then write feedback | `get_dev_metrics` | Cheap |
| Reporter | Draft the research log and leaderboard narrative | `write_report` | Cheap |
| Human gate | Approve promotion to `@champion` and the one-time holdout scoring | `interrupt()` | Dhruv |

The agent gets no `read_parquet`, no shell and no holdout tool. Every evaluation it triggers is appended to a `trials` table, and each headline result reports "N trials evaluated." Promotion requires dev-window MCS membership, a passing guardrail gate, human approval and then a single logged holdout check.

### Data and control flow

```
 [1 Scheduler: Prefect/Dagster]
      | daily                                   | weekly "research sprint"
      v                                         v
 [2 Ingestion] -> raw Parquet (as_of, ingested_at)
      v
 [3 Point-in-time store: Parquet + DuckDB]  <-- holdout rows locked (HOLDOUT_UNLOCK only)
      v                         v
 [4 Realized/feature engine] [5 Options/IV engine] --IV, model-free var--+
      \__________ features ________/                                     |
                     v                                                   |
              [6 Model zoo] --> [7 Walk-forward] --> loss series         |
                                                       v                 |
                         [8 DM / MCS / SPA] <-- trials table             |
                                  v                                      |
                      [9 Downstream evaluators: VaR/ES, vol target, straddle/VRP]
                                  v                                      |
                      [10 MLflow tracker + registry @champion] ----------+--> forecast RV
                           |            ^                                     back to pricer
                           v            | run_id
     [13 Report/Vercel]    [14 Monitor] |
                                        |
 [11 LangGraph: Supervisor -> Researcher -> Configurator -> [12 Guardrail gate]
                -> Runner (submits to 7) -> Critic -> Reporter -> interrupt() -> human]
                     state + memory in Postgres checkpointer
 [15 CI] tests 3, 4, 5, 7, 12 on every push
```

### Phased build order for one student

| Phase | Build | Exit criterion |
|---|---|---|
| 0. Skeleton | Repo, uv, ruff, pytest, CI; Parquet layout; `load_panel()` with the holdout guard | CI green; guard test raises on holdout dates |
| 1. Data and targets | Ingestion for bars, VIX family and FRED; RV, semivariance and jump features; point-in-time tests | Features unchanged when post-t data is perturbed |
| 2. Baseline shootout | GARCH(1,1), GJR/EGARCH with skew-t, HAR, recalibrated HAR; walk-forward; QLIKE/MSE; DM plus MCS | Reproducible leaderboard per horizon, logged in MLflow |
| 3. Implied contestant and HAR-X | Options library feeds IV and model-free variance; HAR-RV-IV; MS-HAR; LightGBM and a HAR-plus-ML combination | IV features pass timestamp tests; MCS rerun |
| 4. Scheduler and downstream | Prefect/Dagster flows; VaR/ES, vol-target and straddle evaluators; daily report and monitoring | Unattended daily runs with alerts |
| 5. Agent layer | LangGraph roles, typed tools, Postgres checkpointer, trials table, canary feature, `interrupt` before promotion | One full weekly sprint runs end to end without touching the holdout |
| 6. Polish | Bandit supervisor, reflection memory (TradingAgents style), MLflow tracing of tokens, one-time holdout reveal, write-up | Headline results reported with trial counts and MCS sets |

The order matters. Phases 0 to 4 make a complete project without any LLM. The agent arrives only after the harness it depends on is trustworthy.

## About $1 a month with GitHub Actions, R2, Neon and Vercel

The assumed workload is 1 to 3 pipeline runs a day of about 10 minutes, four 1-hour backtests a month, and an agent making about 5 calls of 8k input and 1k output tokens per run. It fits inside several free tiers. The recommended setup runs the Prefect flows and the agent as **GitHub Actions cron jobs**. Public repos get unlimited standard minutes, and private repos get 2,000 minutes a month, against an estimated 1,140 minutes of usage ([GitHub billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions)). Parquet goes to **Cloudflare R2**, with 10 GB free and zero egress ([R2 pricing](https://developers.cloudflare.com/r2/pricing/)). MLflow's backend store and the LangGraph PostgresSaver share one **Neon free Postgres**. Neon scales to zero after 5 minutes but never deletes data ([Neon pricing](https://neon.com/pricing)). A **Next.js dashboard on Vercel** reads those results (see the Vercel subsection below). Routine agent calls go to **Claude Haiku 5.5** at $0.10 / $0.50 per million tokens, DeepSeek Flash or Gemini 3.1 Flash-Lite, with **OpenRouter's free models** used during development (see the LLM subsection below). The weekly deep review goes to Sonnet 5.5 through the 50%-off Batch API ([Claude pricing](https://platform.claude.com/docs/en/about-claude/pricing); [Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing)). These figures come from pricing pages fetched on 2026-10-19 and should be rechecked before relying on them.

| Line item | Monthly cost |
|---|---|
| Compute (GitHub Actions) | $0 |
| Storage (R2), Postgres (Neon), dashboard (Vercel Hobby) | $0 |
| LLM: OpenRouter free tier in development; Haiku 5.5 or DeepSeek Flash for scheduled runs (about $0.60 to $0.80) plus weekly Sonnet batch review | about $0 to $3 |
| Data: free tiers (Massive Basic, Cboe, FRED) | $0 |
| Optional: Massive Stocks Starter for 5 years of minute history, or Options Starter for IV/greeks | $29 each ([Massive](https://massive.com/pricing?product=options)) |
| **Total** | **about $0 to $3, or about $29 to $32 with one data plan** |

The alternatives trade ops work against reliability. **GCP Cloud Run Jobs** with Cloud Scheduler and GCS is the most "production-shaped" option. The workload uses about 35% of the monthly 240,000 vCPU-second job allowance and would cost only about $1.82 at list price. It needs a billing account, and a budget alert should be set ([Cloud Run pricing](https://cloud.google.com/run/pricing)). **Modal**'s $30 monthly Starter credit covers the workload about 30 times over and allows parallel backtest fan-out, but it is limited to 5 crons ([Modal pricing](https://modal.com/pricing)). An **Oracle A1 VM** gives an always-on MLflow server and Postgres for free. However, Oracle halved the allowance to 2 OCPU / 12 GB in June 2026, and it reclaims VMs whose 95th-percentile CPU, network and memory stay under 20% for 7 days ([OCI Always Free](https://docs.oracle.com/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm); [InfoQ](https://infoq.com/news/2026/07/oracle-cloud-free-tier-limits/)). **Azure for Students** offers $100 a year with no card, plus 12 months of a free burstable VM and Postgres. It is renewable while he is enrolled, so it ends around graduation ([Azure for Students](https://azure.microsoft.com/en-us/free/students)).

### Cutting LLM cost further: free and low-cost providers

The LLM is already a small line item, so switching providers saves cents, not dollars. The choice matters more for reliability. Estimates use the same workload of about 90 agent runs a month at roughly 40k input and 5k output tokens per run.

| Provider | Price (per 1M tokens) | Est. monthly cost | Catch |
|---|---|---|---|
| OpenRouter free models | $0 | $0, or a one-time $10 | 50 requests/day free; a one-time $10 credit purchase raises this to 1,000/day. 20 requests/minute cap. The free lineup rotates, so a model can disappear ([PricePerToken](https://www.pricepertoken.com/endpoints/openrouter/free)). |
| Claude Haiku 5.5 | $0.10 in / $0.50 out | about $0.60 | Cheapest paid option found. |
| DeepSeek Flash | $0.15 in / $0.60 out off-peak, double at peak; cache hits near zero | about $0.80 | China-based provider; low risk for public market analysis, but worth noting ([MorphLLM](https://www.morphllm.com/deepseek-api)). |
| Grok 4.3 | $1.25 in / $2.50 out | about $5.60 | Roughly 10x Haiku; no confirmed current free credit program ([Mem0](https://mem0.ai/blog/xai-grok-api-pricing)). |

Recommended split: use OpenRouter's free tier while developing and testing, where breakage is harmless, and a cheap paid model (Haiku 5.5 or DeepSeek Flash) for scheduled runs, so a rotated-out free model cannot silently break the daily pipeline. Write the agent against an OpenAI-compatible client so the provider is a single config value, and log the provider and model per run in MLflow so results stay comparable when it changes. Prices come from third-party pages checked in September and October 2026 and should be confirmed on each provider's own pricing page.

### Hosting the dashboard on Vercel

Vercel's Hobby plan fits the serving layer but not the compute. Functions run for at most 5 minutes with 2 GB of memory and 1 vCPU, and runtime logs are kept for only 1 hour ([Vercel limits](https://vercel.com/docs/limits)). Cron jobs run at most once a day, and a job scheduled for 1:00 can fire any time before 2:00 ([Vercel cron pricing](https://vercel.com/docs/cron-jobs/usage-and-pricing)). That rules Vercel out for model fitting, 1-hour backtests and the agent loop. Streamlit also cannot run there, since it needs a persistent server.

The resulting division of labor:

| Layer | Service | Designated task | Cost |
|---|---|---|---|
| Compute | GitHub Actions | Scheduled pipeline, model fits, backtests, agent runs | $0 |
| Storage | Cloudflare R2, Neon | Forecasts, leaderboards, MLflow and checkpoint metadata | $0 |
| Serving | Vercel (Next.js) | Read-only dashboard over published results | $0 |

The pipeline writes small derived outputs (forecasts, leaderboard, research log) to R2 or Neon after each run, and the Next.js app reads only those. Raw vendor data never reaches the dashboard, which keeps it within data terms of use. A React/Tailwind dashboard is also a stronger portfolio piece than a default Streamlit app.

Several gotchas apply. GitHub disables scheduled workflows in public repos after 60 days without activity; a daily results commit probably prevents this, though whether bot commits count is unverified ([GitHub docs](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/disable-and-enable-workflows)). Supabase pauses free projects after a week of inactivity, and Render deletes free Postgres after 30 days plus a 14-day grace period ([Supabase](https://supabase.com/pricing); [Render](https://render.com/docs/free)). Fly.io has no free tier for new orgs ([Fly.io](https://docs.fly.io/about/pricing)). AWS's new Free plan accounts close after 6 months unless upgraded ([AWS](https://aws.amazon.com/blogs/aws/aws-free-tier-update-new-customers-can-get-started-and-explore-aws-with-up-to-200-in-credits)). Haiku 5.5 charges 5 times as much for prompts over 100k tokens, so agent context should stay small. A provider spend cap matters because an autonomous loop can run away. yfinance data is for personal use, so publish code, download scripts and derived metrics rather than raw data in a public repo ([yfinance](https://pypi.org/project/yfinance/)). For storage, Parquet plus DuckDB avoids license questions entirely. ArcticDB is free for personal, academic and non-commercial use under its Business Source License, but business use requires a commercial license ([ArcticDB FAQ](https://arcticdb.io/faqs/)).

## Conclusion

The research points to a reframing. The project's distinctive contribution is not a model that beats HAR, since the literature says such wins are small, loss-dependent and often just calibration. It is an **honest autonomous harness**: one that counts its trials, reports confidence sets instead of a single winner, and closes the loop between his own options pricer and the forecaster. That makes it stand out among agent projects whose headline numbers are unreplicated or, as the honest-evaluation paper showed, do not survive scrutiny.

There are two practical implications. First, money is better spent on a longer intraday history than on compute or bigger LLMs. Infrastructure is effectively free, and short samples are the binding constraint on DM and MCS power. Second, the agent's best measurable value is search efficiency rather than discovery: fewer wasted trials to reach the same confidence set, with token cost traced in MLflow. That is a claim the framework can test about itself.
