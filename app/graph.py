from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt
from app.state import State, Observation, Result
from modules.login.verifier import verify

class LoginWorkflow:
    def __init__(self, browser, reasoner, strategy=False):
        self.browser, self.reasoner, self.strategy = browser, reasoner, strategy
        self.trace = browser.trace
        self.trace.reasoner_mode = reasoner.mode
        builder = StateGraph(State)
        for name in ("initialize", "observe", "reason", "act", "handoff", "finish"):
            builder.add_node(name, getattr(self, name))
        builder.add_edge(START, "initialize")
        builder.add_edge("initialize", "observe")
        builder.add_conditional_edges("observe", self.route_observation,
            {"reason": "reason", "finish": "finish"})
        builder.add_conditional_edges("reason", lambda s: "handoff" if s["action"] == "handoff" else ("finish" if s["action"] == "stop" else "act"),
            {"handoff": "handoff", "finish": "finish", "act": "act"})
        builder.add_edge("act", "observe")
        builder.add_conditional_edges("handoff", lambda s: "finish" if s.get("cancelled") else "observe",
            {"finish": "finish", "observe": "observe"})
        builder.add_edge("finish", END)
        self.graph = builder.compile(checkpointer=InMemorySaver())

    async def initialize(self, state):
        await self.browser.start()
        await self.browser.navigate(self.url)
        return {"handoffs": 0}

    async def observe(self, state):
        o = await self.browser.observe()
        result = verify(o)
        self.trace.add("observe", o, result)
        return {"observation": o.model_dump(), "verification": str(result)}

    def route_observation(self, state):
        o = Observation(**state["observation"])
        if state["verification"] not in {str(Result.HUMAN_ACTION_REQUIRED), str(Result.UNEXPECTED_PAGE)}:
            return "finish"
        if not o.allowed or (not (o.standard_entry or o.alternate_entry or o.credential_form or o.login_entry or o.challenge)):
            return "finish"
        # A remaining password form after handoff is unverified/rejected: do not resubmit.
        if state.get("handoffs", 0) and (o.credential_form or o.login_entry) and not o.challenge:
            return "finish"
        if state.get("handoffs", 0) >= self.browser.config.max_handoffs:
            return "finish"
        return "reason"

    async def reason(self, state):
        if self.trace.reasoning_turns >= self.browser.config.max_model_turns:
            self.trace.add("error", error="BUDGET")
            return {"action": "stop"}
        self.trace.reasoning_turns += 1
        if not self.reasoner.is_mock:
            self.trace.model_calls += 1
            if self.reasoner.mode == "codex_cli":
                self.trace.codex_calls += 1
            else:
                self.trace.openai_api_calls += 1
        try:
            action = await self.reasoner.decide(Observation(**state["observation"]), self.strategy)
        except Exception:
            self.trace.add("error", error="MODEL")
            return {"action": "stop"}
        o = Observation(**state["observation"])
        permitted = {"handoff", "stop"}
        if o.standard_entry:
            permitted.add("standard_entry")
        if o.alternate_entry and self.strategy:
            permitted.add("alternate_entry")
        if action not in permitted:
            action = "stop"
        self.trace.add("reason", o, state["verification"])
        return {"action": action}

    async def act(self, state):
        await self.browser.entry(state["action"])
        self.trace.add("entry")
        return {}

    def handoff(self, state):
        # No side effects before interrupt: LangGraph reruns this node on resume.
        response = interrupt({"kind": "authentication", "instruction": "Complete login or verification directly in Chromium. Return only completed or cancel; never credentials."})
        cancelled = response != "completed"
        self.trace.add("handoff", result=Result.HUMAN_ACTION_REQUIRED,
            error="CANCELLED" if cancelled else None)
        return {"handoffs": state.get("handoffs", 0)+1, "cancelled": cancelled,
            **({"verification": str(Result.HUMAN_ACTION_REQUIRED)} if cancelled else {})}

    def finish(self, state):
        self.trace.add("verify", Observation(**state["observation"]), state["verification"])
        return {}
