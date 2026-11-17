"""Reusable confirmed cleanup for an application-owned local browser session."""
import asyncio
import shutil
from pathlib import Path
from app.config import ROOT

PROFILE_ROOT=ROOT/'profiles'

class CleanupUnconfirmed(RuntimeError):
    pass

async def close_owned_session(session):
    trace=session.trace
    diagnostics={'state':'CLOSING','process_exit_confirmed':None,'profile_removed':False,'transport_closed':False,'warnings':[]}
    trace.cleanup_diagnostic=diagnostics
    async def step(name,callback,timeout):
        if name=='STAGEHAND_CLOSE':session.stagehand_close_started=True
        if name=='BROWSER_CLOSE':session.browser_close_started=True
        trace.lifecycle('BROWSER',{'state':'CLOSE_STAGE_STARTED','stage':name})
        try:
            await asyncio.wait_for(callback(),timeout)
            return True
        except Exception as error:
            diagnostics['warnings'].append({'stage':name,'category':'TIMEOUT' if isinstance(error,TimeoutError) else 'CLOSE_EXCEPTION'})
            return False
    session.close_started=True
    if session.guard:session.guard.closing=True
    discovery=getattr(session,'discovery',None)
    if discovery:await step('DISCOVERY_CLOSE',discovery.close,2)
    if session.stagehand:await step('STAGEHAND_CLOSE',session.stagehand.close,3)
    exited=None
    if session.process:
        try:exited=await asyncio.wait_for(asyncio.to_thread(session.process.exited),1.5)
        except Exception:diagnostics['warnings'].append({'stage':'PROCESS_CHECK','category':'CONFIRMATION_UNAVAILABLE'})
    if session.browser and exited is not True:
        await step('BROWSER_CLOSE',session.browser.close,6)
    if session.process:
        try:
            exited=await asyncio.wait_for(asyncio.to_thread(session.process.exited),1.5)
            if not exited and hasattr(session.process,'terminate'):
                await asyncio.wait_for(asyncio.to_thread(session.process.terminate),4)
                exited=await asyncio.wait_for(asyncio.to_thread(session.process.exited),1.5)
        except Exception:diagnostics['warnings'].append({'stage':'OWNED_TERMINATION','category':'CONFIRMATION_UNAVAILABLE'})
    if session.guard:diagnostics['transport_closed']=await step('GUARD_CLOSE',session.guard.close,2)
    else:diagnostics['transport_closed']=True
    diagnostics['process_exit_confirmed']=exited
    profile=Path(session.profile)
    if exited is True and profile.exists():
        target=profile.resolve();root=PROFILE_ROOT.resolve()
        if not target.is_relative_to(root) or target.parent!=root or target.name!=trace.run_id:
            diagnostics['warnings'].append({'stage':'PROFILE_REMOVE','category':'PROFILE_PATH_NOT_OWNED'})
        else:
            for attempt in range(3):
                try:
                    await asyncio.wait_for(asyncio.to_thread(shutil.rmtree,target),2)
                    break
                except FileNotFoundError:break
                except Exception:
                    if attempt==2:diagnostics['warnings'].append({'stage':'PROFILE_REMOVE','category':'PROFILE_REMOVE_FAILED'})
                    else:await asyncio.sleep(.1*(attempt+1))
    diagnostics['profile_removed']=not profile.exists()
    never_created=session.browser is None and session.process is None and diagnostics['profile_removed']
    if exited is True and diagnostics['profile_removed'] and diagnostics['transport_closed']:
        trace.cleanup_result='CLOSED_AND_PROFILE_REMOVED';diagnostics['state']='CONFIRMED'
    elif never_created:
        trace.cleanup_result='NOT_STARTED_NO_RESOURCES';diagnostics['state']='NO_RESOURCES_CREATED'
    else:
        trace.cleanup_result='CLEANUP_FAILED' if exited is False or (exited is True and not diagnostics['profile_removed']) else 'CLEANUP_UNCONFIRMED'
        diagnostics['state']='FAILED' if trace.cleanup_result=='CLEANUP_FAILED' else 'UNCONFIRMED'
    if session.process:session.process.close()
    trace.add('close')
    trace.events[-1]['cleanup']=dict(diagnostics)
    trace.write(ROOT/'outputs')
    if trace.cleanup_result not in {'CLOSED_AND_PROFILE_REMOVED','NOT_STARTED_NO_RESOURCES'}:
        raise CleanupUnconfirmed('Owned profile cleanup could not be confirmed')
    return diagnostics
