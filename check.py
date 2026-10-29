import tests.test_core as t1, tests.test_speed_routing as t2
from core.agent import Agent
orig = Agent.ask
def ask(self, *a, **k):
    r = orig(self, *a, **k)
    print("ASK ->", r.kind, "|", r.text)
    return r
Agent.ask = ask
for f in (t1.test_clarify_and_reject_come_from_the_sql_step,
          t2.test_creating_a_dashboard_fills_the_cache_for_its_first_display):
    print("==", f.__name__)
    try: f()
    except Exception as e: print("упало:", type(e).__name__)

