"""Shared open-root predicates plus protected closed-root metadata; counts only."""
import asyncio
import json
from modules.login.negative_scan import NEGATIVE_SNAPSHOT

NEGATIVES=('credential_form','challenge','rejected','expired','account_restricted')
COUNT_KEYS=('documents_inspected','nodes_inspected','permitted_frames_unavailable','external_frames_skipped',
    'shadow_roots_uninspected','open_shadow_roots_inspected','inaccessible_shadow_roots','user_agent_shadow_roots',
    'unknown_shadow_roots','inventory_open_shadow_roots','inventory_nodes_inspected','inventory_documents_inspected',
    'inventory_commands','inventory_nodes_processed','inventory_passes','inventory_batch_depth','inventory_duration_ms',
    'inventory_response_bytes','inventory_transport_commands','remaining_coverage_gaps')
LIMIT_KEYS=('document_limit_reached','node_limit_reached','shadow_root_limit_reached','shadow_depth_limit_reached',
    'tree_depth_limit_reached','text_limit_reached','inventory_limit_reached','inventory_unavailable','inventory_changed',
    'inventory_incomplete','inventory_root_count_mismatch','inventory_command_limit_reached','inventory_time_limit_reached',
    'inventory_node_limit_reached','inventory_depth_limit_reached','inventory_document_limit_reached',
    'inventory_payload_limit_reached','inventory_shadow_root_limit_reached','snapshot_invalid')
STATUS_KEYS=('inventory_complete','inventory_root_counts_comparable','inventory_requested')

SAFETY_SNAPSHOT = r'''(settings => {
 const snapshot=''' + NEGATIVE_SNAPSHOT.replace('SETTINGS','settings') + r''';
 return {...snapshot.flags,_scope_diagnostics:snapshot.coverage};
})(SETTINGS)'''


async def read_safety_snapshot(browser,*,include_inventory=True):
    """Never certify unknown closed scope; bind metadata to the protected document."""
    from browser.protected_tabs import bound_tab_observable
    from browser.shadow_inventory import shadow_inventory
    from browser.metadata_channel import metadata_channel
    guard=browser.guard;binding=getattr(browser,'setup_binding',None)
    session=binding.session if binding is not None else guard.primary_session
    def identity():
        owner=guard.session_states.get(session)
        if not owner or guard.failed or guard.blocking_hosts or not guard.session_observable(session):return None
        if binding is not None and not bound_tab_observable(guard,binding):return None
        return (owner.target,owner.session,owner.generation,owner.document_generation,owner.frame_id,owner.loader_id)
    await guard.ensure_observable();before=identity()
    if before is None:return {'negative_scope_complete':False,'_scope_diagnostics':{'inventory_unavailable':True,'snapshot_invalid':True}}
    expression=SAFETY_SNAPSHOT.replace('SETTINGS',json.dumps({'origins':sorted(browser.policy.origins)}))
    first=await browser.page.evaluate(expression)
    if safety_diagnostic(first)['code']=='SAFETY_OBSERVATION_INVALID':return first
    if not isinstance(first,dict) or not all(type(first.get(k)) is bool for k in (*NEGATIVES,'negative_scope_complete')):return first
    if not include_inventory:
        # Reuse the same bounded negative scanner. Read admission never waits for
        # metadata inventory and never turns its absence into a full certificate.
        first['negative_scope_complete']=False
        coverage=dict(first.get('_scope_diagnostics') or {})
        coverage.update(inventory_complete=False,inventory_incomplete=True,inventory_requested=False)
        if before!=identity():coverage['snapshot_invalid']=True
        first['_scope_diagnostics']=coverage
        return first
    # A fresh negative scan sits BETWEEN the two complete metadata passes.
    # Neither raw metadata nor its private comparison digest leaves the inventory.
    latest=first;observed=False
    async def read_latest():
        nonlocal latest,observed
        if before!=identity():return
        observed=True;latest=await browser.page.evaluate(expression)
    try:
        async with asyncio.timeout(5):
            async with metadata_channel(browser,session) as reader:
                inventory=await shadow_inventory(reader,session,browser.policy.origins,observe=read_latest)
    except Exception as error:
        limited=getattr(error,'code',None)=='METADATA_BYTE_LIMIT_REACHED'
        timed_out=isinstance(error,TimeoutError) or getattr(error,'code',None)=='METADATA_CHANNEL_TIMEOUT'
        inventory={'inventory_unavailable':not limited and not timed_out,'inventory_limit_reached':limited or timed_out,
            'inventory_payload_limit_reached':limited,'inventory_time_limit_reached':timed_out,'inventory_complete':False}
    changed=before!=identity() or inventory.get('inventory_changed',False)
    malformed=not isinstance(latest,dict) or not all(type(latest.get(k)) is bool for k in (*NEGATIVES,'negative_scope_complete'))
    if malformed:
        latest={**{k:first[k] or (isinstance(latest,dict) and latest.get(k) is True) for k in NEGATIVES},
            'negative_scope_complete':False,'_scope_diagnostics':{'snapshot_invalid':True}}
    coverage=latest.get('_scope_diagnostics')
    coverage=dict(coverage) if isinstance(coverage,dict) else {'snapshot_invalid':True}
    first_coverage=first.get('_scope_diagnostics')
    if isinstance(first_coverage,dict):
        for key in COUNT_KEYS:
            value=first_coverage.get(key)
            if key!='open_shadow_roots_inspected' and type(value) is int:
                current=coverage.get(key,0)
                coverage[key]=max(current if type(current) is int else 0,value)
        for key in LIMIT_KEYS:
            if first_coverage.get(key) is True:coverage[key]=True
    for key in NEGATIVES:
        if first.get(key) is True:latest[key]=True
    coverage.update(inventory)
    coverage['inventory_changed']=changed
    coverage['inventory_incomplete']=inventory.get('inventory_complete') is not True or not observed
    open_count=coverage.get('open_shadow_roots_inspected')
    comparable=inventory.get('inventory_complete') is True and type(open_count) is int and latest.get('negative_scope_complete') is True
    coverage['inventory_root_counts_comparable']=comparable
    coverage['inventory_root_count_mismatch']=comparable and open_count!=inventory.get('inventory_open_shadow_roots')
    if observed and isinstance(first_coverage,dict) and first_coverage.get('open_shadow_roots_inspected')!=open_count:
        coverage['inventory_changed']=True
    gaps=sum(coverage.get(k,0) for k in ('shadow_roots_uninspected','inaccessible_shadow_roots','unknown_shadow_roots','permitted_frames_unavailable'))
    gaps+=sum(coverage.get(k) is True for k in LIMIT_KEYS)
    if not first['negative_scope_complete'] or not latest['negative_scope_complete']:gaps=max(1,gaps)
    coverage['remaining_coverage_gaps']=gaps
    latest['_scope_diagnostics']=coverage
    latest['negative_scope_complete']=latest.get('negative_scope_complete') is True and first.get('negative_scope_complete') is True and gaps==0
    return latest


def safety_diagnostic(snapshot):
    """A coverage gap is not proof of rejected authentication. Both remain blocked."""
    valid=isinstance(snapshot,dict) and all(type(snapshot.get(k)) is bool for k in NEGATIVES)
    complete=snapshot.get('negative_scope_complete') if isinstance(snapshot,dict) else None
    valid=valid and type(complete) is bool
    valid=valid and not (isinstance(snapshot,dict) and isinstance(snapshot.get('_scope_diagnostics'),dict) and snapshot['_scope_diagnostics'].get('snapshot_invalid') is True)
    if not valid:
        detected={k:v for k in NEGATIVES if isinstance(snapshot,dict) and type(v:=snapshot.get(k)) is bool}
        failed=[k+'_present' for k,v in detected.items() if v]
        return {'code':'AUTHENTICATION_OR_SECURITY_STOP' if failed else 'SAFETY_OBSERVATION_INVALID',
            'failed_predicates':failed+['safety_snapshot_schema'],'gates':{'negative_state_observation':'NOT_CHECKED'},
            'negative_flags':detected,'scope_reason_counts':{}}
    flags={key:snapshot[key] for key in NEGATIVES}
    failed=[key+'_present' for key,value in flags.items() if value]
    if not complete:failed.append('negative_scope_complete')
    coverage=snapshot.get('_scope_diagnostics') or {}
    if not isinstance(coverage,dict):coverage={}
    counts={key:value for key,value in coverage.items() if key in COUNT_KEYS and type(value) is int and 0<=value<=(4*1024*1024 if key=='inventory_response_bytes' else 100000)}
    counts.update({key:value for key,value in coverage.items() if key in (*LIMIT_KEYS,*STATUS_KEYS) and type(value) is bool})
    gates={key+'_absent':'FAIL' if value else 'PASS' for key,value in flags.items()}
    gates['negative_scope_complete']='PASS' if complete else 'FAIL'
    return {'code':'AUTHENTICATION_OR_SECURITY_STOP' if any(flags.values()) else 'SAFETY_OBSERVATION_INCOMPLETE' if not complete else None,
        'failed_predicates':failed,'gates':gates,'negative_flags':flags,'scope_reason_counts':counts,
        'negative_scope_complete':complete,'strict_independent_authentication_reapplied':False,
        'coverage_status':('DEFERRED_INVENTORY_AT_ACTION_BOUNDARY' if coverage.get('inventory_requested') is False
            else 'COMPLETE' if complete else 'INCOMPLETE_OR_UNAVAILABLE'),
        'read_only_observation_permitted':not any(flags.values())}
