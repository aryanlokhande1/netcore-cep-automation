import asyncio
from langgraph.types import Command
from app.config import ROOT
from app.state import Result
from app.graph import LoginWorkflow

async def attempt(browser, reasoner, url, handoff, strategy=False):
    workflow = LoginWorkflow(browser, reasoner, strategy)
    workflow.url = url
    thread = {"configurable": {"thread_id": browser.trace.run_id}, "recursion_limit": 50}
    result = {"verification": str(Result.NOT_TESTED)}
    try:
        async with asyncio.timeout(browser.config.timeout_seconds):
            result = await workflow.graph.ainvoke({}, thread)
            while result.get('__interrupt__'):
                browser.trace.add('handoff', result=Result.HUMAN_ACTION_REQUIRED)
                browser.trace.write(ROOT / 'outputs')
                answer = await handoff.complete(browser)
                result = await workflow.graph.ainvoke(Command(resume=answer), thread)
    except (TimeoutError, PermissionError):
        browser.trace.add('error', error='BUDGET' if not browser.guard or not browser.guard.blocked_hosts else 'POLICY')
        result = {"verification": str(Result.UNEXPECTED_PAGE)}
    except Exception:
        # Never persist exception text; browser/model exceptions may contain page content or secrets.
        browser.trace.add('error', error='BROWSER')
        result = {"verification": str(Result.NOT_TESTED)}
    finally:
        try:
            await browser.close()
        finally:
            browser.trace.add('verify', result=result.get('verification', Result.NOT_TESTED))
            browser.trace.write(ROOT / 'outputs')
    return result
