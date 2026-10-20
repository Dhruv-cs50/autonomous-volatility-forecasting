"""
LangGraph Starter Project: A Volatility Model "Shootout" Agent
==============================================================
This is a self-contained tutorial. It runs with NO API key, because
LangGraph nodes are just Python functions — an LLM is only one possible
kind of node. At the bottom, you'll see exactly where an LLM would slot in.

THE MENTAL MODEL
----------------
LangGraph apps have 3 parts:
  1. STATE   — a shared dict-like object every node reads from and writes to
  2. NODES   — plain functions: take state, return a partial update to state
  3. EDGES   — wiring that decides which node runs next (can be conditional,
               which is how you get LOOPS — the thing chains can't do)

Our agent's job: try several volatility forecasting models on return data,
evaluate each, LOOP until all are tried, then report the winner.
That loop (forecast -> evaluate -> forecast -> ...) is the core LangGraph idea.

Run it:  python3 vol_agent.py
"""

from typing import TypedDict
import numpy as np
from langgraph.graph import StateGraph, START, END


# ----------------------------------------------------------------------
# 1. STATE — the single source of truth passed between nodes.
#    Each node returns a dict with only the keys it wants to update;
#    LangGraph merges that into the state for you.
# ----------------------------------------------------------------------
class ShootoutState(TypedDict):
    returns: list[float]        # daily returns (train portion)
    realized_vol: float         # "ground truth" vol on the test window
    models_to_try: list[str]    # work queue
    current_model: str          # which model this loop iteration uses
    results: dict[str, float]   # model name -> forecast error
    winner: str                 # filled in at the end


# ----------------------------------------------------------------------
# 2. NODES — plain functions. Signature: (state) -> partial state update
# ----------------------------------------------------------------------

def fetch_data(state: ShootoutState) -> dict:
    """Simulate ~2 years of daily returns with volatility clustering
    (GARCH-like), so the models have something realistic to chew on.
    In your real project this node would pull data from an API."""
    rng = np.random.default_rng(42)
    n = 500
    vol = np.zeros(n)
    r = np.zeros(n)
    vol[0] = 0.01
    for t in range(1, n):
        # simple GARCH(1,1)-style variance recursion
        vol[t] = np.sqrt(1e-6 + 0.1 * r[t-1]**2 + 0.85 * vol[t-1]**2)
        r[t] = vol[t] * rng.standard_normal()

    train, test = r[:450], r[450:]
    realized = float(np.std(test) * np.sqrt(252))   # annualized test vol
    print(f"[fetch_data] {len(train)} train days, realized test vol = {realized:.2%}")
    return {"returns": train.tolist(), "realized_vol": realized}


def pick_next_model(state: ShootoutState) -> dict:
    """Pop the next model off the work queue. This node exists so the
    graph has a clean place to loop back to."""
    queue = state["models_to_try"]
    current, rest = queue[0], queue[1:]
    print(f"[pick_next_model] trying: {current}")
    return {"current_model": current, "models_to_try": rest}


def forecast(state: ShootoutState) -> dict:
    """Forecast annualized vol using whichever model is 'current'.
    (Toy implementations — your real library replaces these.)"""
    r = np.array(state["returns"])
    model = state["current_model"]

    if model == "rolling_std":
        f = np.std(r[-30:])                       # 30-day rolling std
    elif model == "ewma":
        lam, s2 = 0.94, r[0]**2                    # RiskMetrics-style EWMA
        for x in r[1:]:
            s2 = lam * s2 + (1 - lam) * x**2
        f = np.sqrt(s2)
    else:  # "long_run_mean" — naive baseline
        f = np.std(r)

    forecast_ann = float(f * np.sqrt(252))
    error = abs(forecast_ann - state["realized_vol"])
    print(f"[forecast]  {model}: {forecast_ann:.2%} (error {error:.2%})")
    return {"results": {**state["results"], model: error}}


def report(state: ShootoutState) -> dict:
    """Terminal node: crown the winner."""
    winner = min(state["results"], key=state["results"].get)
    print(f"[report] winner: {winner}")
    return {"winner": winner}


# ----------------------------------------------------------------------
# 3. EDGES — including the CONDITIONAL edge that creates the loop.
#    A conditional edge is a function that looks at state and returns
#    the NAME of the next node. This is your control flow.
# ----------------------------------------------------------------------
def more_models_left(state: ShootoutState) -> str:
    return "pick_next_model" if state["models_to_try"] else "report"


# ----------------------------------------------------------------------
# 4. BUILD THE GRAPH
# ----------------------------------------------------------------------
graph = StateGraph(ShootoutState)

graph.add_node("fetch_data", fetch_data)
graph.add_node("pick_next_model", pick_next_model)
graph.add_node("forecast", forecast)
graph.add_node("report", report)

graph.add_edge(START, "fetch_data")
graph.add_edge("fetch_data", "pick_next_model")
graph.add_edge("pick_next_model", "forecast")
# After each forecast, DECIDE: loop again, or finish?  <-- the key line
graph.add_conditional_edges("forecast", more_models_left)
graph.add_edge("report", END)

app = graph.compile()   # compile() turns the blueprint into a runnable


# ----------------------------------------------------------------------
# 5. RUN IT — invoke() takes the initial state, returns the final state
# ----------------------------------------------------------------------
if __name__ == "__main__":
    final = app.invoke({
        "returns": [],
        "realized_vol": 0.0,
        "models_to_try": ["rolling_std", "ewma", "long_run_mean"],
        "current_model": "",
        "results": {},
        "winner": "",
    })
    print("\nFinal results:", {k: f"{v:.2%}" for k, v in final["results"].items()})
    print("Winner:", final["winner"])


# ----------------------------------------------------------------------
# WHERE THE LLM PLUGS IN (the "agentic" upgrade)
# ----------------------------------------------------------------------
# Everything above is deterministic. To make this a true AGENT, you replace
# the hard-coded decision logic with an LLM node. Two classic upgrades:
#
#   a) LLM CRITIC: replace `more_models_left` with a node that shows the
#      LLM the results so far and asks: "should we try another model,
#      tune parameters, or stop?" The LLM's answer routes the graph.
#
#      from langchain_anthropic import ChatAnthropic
#      llm = ChatAnthropic(model="claude-sonnet-4-6")
#
#      def critic(state):
#          msg = llm.invoke(
#              f"Results so far: {state['results']}. "
#              "Reply with exactly one word: CONTINUE or STOP."
#          )
#          return {"decision": msg.content.strip()}
#
#   b) TOOL-USING RESEARCHER: give the LLM tools (fetch_prices,
#      run_garch, search_web) and let LangGraph's prebuilt ReAct agent
#      loop plan->act->observe until it has an answer:
#
#      from langgraph.prebuilt import create_react_agent
#      agent = create_react_agent(llm, tools=[fetch_prices, run_garch])
#
# Same graph machinery either way — the LLM is just a node whose output
# steers the edges. That's the whole trick.
