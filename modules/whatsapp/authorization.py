"""Separate, document/form/control-bound permission for four fixed Setup fields."""
import asyncio
from dataclasses import dataclass
import json
import sys
from modules.whatsapp.contract import ORDER,SetupStop


@dataclass(frozen=True)
class MutationGrant:
    browser:object
    run_id:str
    document:tuple
    form:tuple
    controls:tuple
    route:str
    risk_binding:tuple|None=None
    scope:tuple=ORDER

    def summary(self):
        scoped=self.route=='OPERATOR_REVIEWED_SCOPED_POC'
        return {'status':'AUTHORIZED','route':self.route,'fields':list(self.scope),
            'fixed_values_only':True,'campaign_commit_authorized':False,
            'complete_negative_coverage':not scoped,'remaining_uncertainty_acknowledged':scoped,
            'independent_authentication_certified':False}


async def confirm_scoped_mutation(payload,seconds=30):
    """Only the actual operator, never a model/tool parameter, can make this grant."""
    if not sys.stdin.isatty():raise SetupStop('MUTATION_REVIEW_UNAVAILABLE')
    print('SETUP MUTATION REVIEW: global negative-state coverage is incomplete. '+
        'Review the protected page and evidence below. Closed-root function/contents may remain unknown. '+
        'A approves only the four fixed fields in this observed form for this POC, accepting the recorded uncertainty; '+
        'Enter declines. This does not certify safety coverage or authentication. Deadline: '+str(seconds)+' seconds.',flush=True)
    print(json.dumps(payload),flush=True)
    try:
        async with asyncio.timeout(seconds):
            while True:
                key=None
                if sys.platform=='win32':
                    import msvcrt
                    if msvcrt.kbhit():key=msvcrt.getwch().lower()
                else:
                    import select
                    if select.select([sys.stdin],[],[],0)[0]:key=sys.stdin.readline().strip().lower() or '\n'
                if key=='a':return True
                if key in ('\n','\r'):return False
                if key=='\x03':raise KeyboardInterrupt
                await asyncio.sleep(.05)
    except TimeoutError:raise SetupStop('MUTATION_REVIEW_TIMEOUT') from None
