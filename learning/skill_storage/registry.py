import hashlib
import json
import re
import shutil
import os
from contextlib import contextmanager
from pathlib import Path
import yaml
from app.config import ROOT
from learning.policies import Strategy


def module_baseline(module,*,root=None):
    """Resolve the explicit WhatsApp baseline, never an approved Login skill.

    Baseline guidance is unverified and grants no execution permission. Approval,
    candidate loading, promotion and rollback remain the separate SkillStore path.
    """
    from app.config import ConfigurationError
    from learning.skill_storage.backend import SkillSnapshot
    if module!='whatsapp':raise ValueError('Unsupported module baseline')
    directory=Path(root or ROOT)/'skills'/'whatsapp'
    try:
        content=(directory/'SKILL.md').read_bytes()
        metadata=json.loads((directory/'version.json').read_text(encoding='utf-8'))
        digest=hashlib.sha256(content).hexdigest()
        text=content.decode('utf-8')
        header=yaml.safe_load(text.split('---',2)[1]) if text.startswith('---\n') else None
        v2=metadata.get('source_version')==2
        if (not isinstance(header,dict) or header.get('name')!=('netcore-whatsapp-v2' if v2 else 'netcore-whatsapp-page1')
            or not isinstance(header.get('description'),str)
            or metadata.get('identity')!=header['name'] or metadata.get('source_version') not in (1,2)
            or metadata.get('content_sha256')!=digest or metadata.get('approved') is not False
            or metadata.get('status')!=('UNAPPROVED_V2_CANDIDATE' if v2 else 'UNVERIFIED_BASELINE')
            or metadata.get('provenance',{}).get('graduated') is not False
            or metadata.get('provenance',{}).get('live_skill_load_provenance')!='NOT_ESTABLISHED'):
            raise ValueError('Invalid module baseline')
    except (OSError,ValueError,TypeError,IndexError,AttributeError,yaml.YAMLError):
        raise ConfigurationError('Invalid WhatsApp baseline skill or metadata') from None
    return SkillSnapshot(text,digest,metadata,approved=False)


def validate_skill(text):
    if len(text)>16000 or not text.startswith('---\n'):
        raise ValueError('Invalid skill structure')
    parts=text.split('---',2)
    meta=yaml.safe_load(parts[1])
    if not isinstance(meta,dict) or meta.get('name')!='netcore-login' or not isinstance(meta.get('description'),str):
        raise ValueError('Invalid skill frontmatter')
    required=['Purpose and scope','Login recognition','Authentication boundary','Destination verification','Recovery','Stop conditions']
    if any(f'## {title}' not in text for title in required):
        raise ValueError('Incomplete skill')
    if re.search(r'sk-|(?:password|token|secret)\s*[:=]\s*\S+|NETCORE_PASSWORD\s*=\s*\S+|(?<![A-Za-z0-9])[A-Za-z]:[/\\]',text,re.I):
        raise ValueError('Unsafe skill content')
    Strategy.model_validate(meta.get('strategy',{}))
    return meta


def validate_skill_directory(directory):
    directory=Path(directory)
    metadata=validate_skill((directory/'SKILL.md').read_text(encoding='utf-8'))
    if directory.name!=metadata['name']:
        raise ValueError('Skill name must match its parent directory')
    return metadata

def history_root():
    path=Path(os.getenv('NETCORE_SKILL_HISTORY_DIR') or ROOT/'outputs'/'skill_history'/'login')
    return (path if path.is_absolute() else ROOT/path).resolve()

class SkillStore:
    def __init__(self,root=None):
        self.root=Path(root or history_root()).resolve()

    def version_path(self,category,version):
        if category not in {'candidates','versions'} or not re.fullmatch(r'[a-f0-9]{32,64}',version):
            raise ValueError('Invalid skill version')
        path=(self.root/category/version).resolve()
        if not path.is_relative_to(self.root) or path.parent.parent!=self.root:
            raise PermissionError('Skill path escaped store')
        return self.bundle_directory(path)

    def bundle_directory(self,path):
        path=Path(path)
        nested=path/'netcore-login'
        return nested if nested.is_dir() else path

    def approved_directory(self):
        return self.bundle_directory(self.root/'approved')

    def create_directory(self,version):
        container=self.version_path('candidates',version)
        container.mkdir(parents=True,exist_ok=False)
        directory=container/'netcore-login'
        directory.mkdir()
        return directory

    def normalize(self,category,version):
        """Explicit layout-only migration. File bytes and bundle digest are unchanged."""
        directory=self.version_path(category,version)
        before=self.digest(directory)
        if directory.name=='netcore-login':
            validate_skill_directory(directory)
            return directory
        target=directory/'netcore-login'
        if directory.is_symlink() or not target.resolve().is_relative_to(self.root):
            raise PermissionError('Skill layout escaped store')
        with self.lock():
            target.mkdir(exist_ok=False)
            moved=[]
            try:
                for name in self.bundle_names(directory):
                    source=directory/name
                    if source.is_symlink() or not source.resolve().is_relative_to(self.root):
                        raise PermissionError('Skill artifact escaped store')
                    source.rename(target/name)
                    moved.append(name)
                validate_skill_directory(target)
                if self.digest(target)!=before:
                    raise ValueError('Layout migration changed bundle integrity')
            except BaseException:
                for name in reversed(moved):(target/name).rename(directory/name)
                target.rmdir()
                raise
        return target

    @contextmanager
    def lock(self):
        self.root.mkdir(parents=True,exist_ok=True)
        path=self.root/'.approval.lock'
        with path.open('x',encoding='utf-8') as handle:
            handle.write('locked')
        try:
            yield
        finally:
            path.unlink()

    def digest(self,directory):
        directory=self.bundle_directory(directory)
        h=hashlib.sha256()
        for name in self.bundle_names(directory):
            h.update(name.encode());h.update((directory/name).read_bytes())
        return h.hexdigest()

    def bundle_names(self,directory):
        return ('SKILL.md','evaluation.json','provenance.json')+tuple(n for n in ('execution-history.md','graduation.json') if (directory/n).is_file())

    def draft(self,version,text,evaluation,provenance,history):
        validate_skill(text)
        path=self.create_directory(version)
        for name,value in (('SKILL.md',text),('execution-history.md',history)):
            (path/name).write_text(value,encoding='utf-8')
        for name,value in (('evaluation.json',evaluation),('provenance.json',provenance)):
            (path/name).write_text(json.dumps(value,indent=2),encoding='utf-8')
        validate_skill_directory(path)
        return {'version':version,'path':str(path),'digest':self.digest(path)}

    def candidate(self,version,text,evaluation,provenance):
        validate_skill(text)
        if evaluation.get('passed') is not True or not provenance.get('verified_run_ids'):
            raise ValueError('Candidate needs repeatable verified evidence')
        path=self.create_directory(version)
        (path/'SKILL.md').write_text(text,encoding='utf-8')
        (path/'evaluation.json').write_text(json.dumps(evaluation,indent=2),encoding='utf-8')
        (path/'provenance.json').write_text(json.dumps(provenance,indent=2),encoding='utf-8')
        validate_skill_directory(path)
        return {'version':version,'digest':self.digest(path),'path':str(path.relative_to(self.root))}

    def snapshot(self,version=None,evaluation=False):
        from learning.skill_storage.backend import SkillSnapshot
        if version is None and self.root==history_root():
            active=ROOT/'skills'/'login'
            if not (active/'version.json').is_file():return None
            active_metadata=json.loads((active/'version.json').read_text())
            if active_metadata.get('approved') is not True:return None
            if hashlib.sha256((active/'SKILL.md').read_bytes()).hexdigest()!=active_metadata['content_sha256']:raise PermissionError('Active skill changed')
        directory=self.version_path('candidates',version) if version else self.approved_directory()
        if not (directory/'SKILL.md').exists():
            return None
        if version and not evaluation:
            raise PermissionError('Candidates require explicit controlled evaluation')
        text=(directory/'SKILL.md').read_text(encoding='utf-8')
        metadata=validate_skill(text)
        if version is None and self.root==history_root() and (ROOT/'skills/login/SKILL.md').read_text(encoding='utf-8')!=text:raise PermissionError('Canonical skill differs from approved bundle')
        provenance=json.loads((directory/'provenance.json').read_text())
        if 'strategy' not in metadata:
            metadata['strategy']=Strategy.model_validate(provenance.get('strategy',{})).model_dump()
        metadata.setdefault('evidence_scope',provenance.get('evidence_scope','synthetic_only'))
        digest=self.digest(directory)
        if not version:
            manifest=json.loads((directory/'approval.json').read_text())
            if manifest.get('digest')!=digest:
                raise PermissionError('Approved skill content changed after review')
        return SkillSnapshot(text,digest,metadata,approved=not bool(version))

    def _activate(self,source,expected_digest,approved):
        if approved is not True or self.digest(source)!=expected_digest:
            raise PermissionError('Exact reviewed approval digest is required')
        validate_skill((source/'SKILL.md').read_text(encoding='utf-8'))
        evidence=json.loads((source/'evaluation.json').read_text())
        if evidence.get('passed') is not True or evidence.get('skill_loading_verified') is not True:
            raise PermissionError('Skill evaluation did not pass')
        if (source/'graduation.json').exists():
            graduation=json.loads((source/'graduation.json').read_text())
            if graduation.get('state')!='GRADUATION_READY':
                raise PermissionError('Three-run graduation gates did not pass')
        with self.lock():
            target=self.approved_directory()
            if target.exists():
                old=self.digest(target)
                archive=self.version_path('versions',old)
                if not archive.exists():
                    shutil.copytree(target,archive/'netcore-login')
            else:
                target=target/'netcore-login'
            target.mkdir(parents=True,exist_ok=True)
            # Approval is written last. Concurrent readers fail closed if a bundle is mid-update.
            for name in ('execution-history.md','graduation.json'):
                if (target/name).exists() and not (source/name).exists():
                    (target/name).unlink()
            for name in self.bundle_names(source):
                temporary=target/(name+'.tmp')
                temporary.write_bytes((source/name).read_bytes())
                temporary.replace(target/name)
            (target/'approval.json').write_text(json.dumps({'digest':expected_digest,'reviewed_approval':True,'state':'APPROVED'}),encoding='utf-8')
        if self.root==history_root():
            active=ROOT/'skills'/'login';active.mkdir(parents=True,exist_ok=True)
            content=(target/'SKILL.md').read_bytes()
            metadata={'identity':'netcore-login','approved':True,'status':'APPROVED','source_version':expected_digest,'bundle_digest':expected_digest,'content_sha256':hashlib.sha256(content).hexdigest(),'provenance':str(target.relative_to(self.root))}
            for name,data in [('SKILL.md',content),('version.json',json.dumps(metadata,indent=2).encode())]:
                temporary=active/(name+'.tmp');temporary.write_bytes(data);temporary.replace(active/name)
        return expected_digest

    def promote(self,version,expected_digest,approved=False):
        return self._activate(self.version_path('candidates',version),expected_digest,approved)

    def rollback(self,version,expected_digest,approved=False):
        return self._activate(self.version_path('versions',version),expected_digest,approved)


# WhatsApp uses this existing storage module and immutable backend, not Login's
# netcore-login bundle namespace. No model receives filesystem tools.
def whatsapp_history():
    return ROOT/'outputs'/'skill_history'/'whatsapp'


def whatsapp_procedure_text(steps,ui):
    from modules.whatsapp.contract import V2_CAPABILITIES,validate_v2_capability,V2Stop
    for step in steps:
        if step.action in V2_CAPABILITIES:
            # Typed goal/action/field scope is checked on load. Current widget kind
            # may be discovered later; the same executor matrix still checks it
            # before any interaction. Missing future selectors cannot gate Login.
            if ui is None or step.field not in ui.fields:continue
            kind=ui.fields[step.field].kind
            part='search' if step.action=='search' else 'option' if step.action=='select' and kind=='combobox' else 'control'
            validate_v2_capability(step.action,kind,part)
    return '\n'.join('- When '+step.when.replace('_',' ')+' during '+step.goal+', use browser_action(action='+step.action+
        (', field='+step.field if step.field else '')+'), using a fresh observed reference when required. Never retry uncertain writes or relax permissions.' for step in steps)


def whatsapp_skill(*,candidate=None,evaluate=False,task_digest=None,ui=None):
    from learning.skill_storage.backend import SkillSnapshot
    from modules.whatsapp.contract import V2Stop
    base=module_baseline('whatsapp')
    directory=whatsapp_history()/'approved'
    if candidate:
        if not evaluate or not re.fullmatch('[a-f0-9]{32}',candidate):raise V2Stop('CANDIDATE_EVALUATION_REQUIRED',owner='authorization')
        directory=whatsapp_history()/'candidates'/candidate
    elif not (directory/'approval.json').is_file():
        if not evaluate:raise V2Stop('WHATSAPP_SKILL_NOT_APPROVED',owner='authorization')
        return base
    content=(directory/'SKILL.md').read_bytes()
    meta=json.loads((directory/'provenance.json').read_text())
    digest=hashlib.sha256(content).hexdigest()
    if digest!=meta['content_sha256'] or meta['base_sha256']!=base.digest:raise V2Stop('CANDIDATE_HASH_OR_BASE_CHANGED',owner='authorization')
    if task_digest is not None and meta['task_sha256']!=task_digest:raise V2Stop('CANDIDATE_TASK_SCOPE_CHANGED',owner='authorization')
    from modules.whatsapp.contract import ProcedureStep
    steps=[ProcedureStep.model_validate(step) for step in meta['procedures']]
    expected=base.content+'\n\n## Evidence-grounded candidate guidance\n\n'+whatsapp_procedure_text(steps,ui)+'\n'
    if content.decode('utf-8')!=expected:raise V2Stop('CANDIDATE_PROCEDURE_CONTENT_MISMATCH',owner='authorization')
    approved=not bool(candidate)
    if approved and json.loads((directory/'approval.json').read_text())['content_sha256']!=digest:raise V2Stop('APPROVAL_HASH_MISMATCH',owner='authorization')
    return SkillSnapshot(content.decode('utf-8'),digest,meta,approved=approved)


def save_whatsapp_candidate(lesson,*,run_id,parent,task_digest,evidence_refs,payload,ui):
    from uuid import uuid4
    from modules.whatsapp.contract import V2Stop
    if not re.fullmatch('[a-f0-9]{32}',run_id):raise V2Stop('INVALID_SOURCE_ITERATION')
    if lesson.code_fix_required or lesson.owner in {'tool','controller','authentication','authorization','browser','environment'}:
        return None
    if not lesson.safe_next_iteration or not lesson.instructions:return None
    if not set(lesson.evidence_refs)<=set(evidence_refs):raise V2Stop('TEACHER_CITATIONS_INVALID')
    text=whatsapp_procedure_text(lesson.instructions,ui)
    if re.search(r'https?://|querySelector|document\.|locator\(|```',text):raise V2Stop('UNSUPPORTED_CANDIDATE_CODE_OR_SELECTORS')
    if any(isinstance(v,str) and len(v)>=8 and v in text+'\n'+lesson.change_summary for v in payload.model_dump().values()):
        raise V2Stop('TASK_VALUES_MUST_NOT_ENTER_SKILL')
    base=module_baseline('whatsapp');version=uuid4().hex
    content=base.content+'\n\n## Evidence-grounded candidate guidance\n\n'+text+'\n'
    digest=hashlib.sha256(content.encode()).hexdigest()
    if digest==parent.digest:return None
    for previous in (whatsapp_history()/'candidates').glob('*/provenance.json'):
        prior=json.loads(previous.read_text())
        if prior.get('content_sha256')==digest and prior.get('task_sha256')==task_digest:return None
    directory=whatsapp_history()/'candidates'/version;directory.mkdir(parents=True,exist_ok=False)
    metadata={'version':version,'content_sha256':digest,'base_sha256':base.digest,'parent_sha256':parent.digest,
        'task_sha256':task_digest,'source_iteration_id':run_id,'evidence_refs':lesson.evidence_refs,
        'change_summary':lesson.change_summary,'status':'UNAPPROVED_PENDING_EXECUTION','approved':False,
        'live_verified':False,'loaded_by':[],'procedures':[step.model_dump() for step in lesson.instructions]}
    (directory/'SKILL.md').write_text(content,encoding='utf-8')
    (directory/'provenance.json').write_text(json.dumps(metadata,indent=2),encoding='utf-8')
    return {'version':version,'content_sha256':digest,'status':metadata['status']}


def approve_whatsapp_candidate(version,digest,*,approved=False,iteration_records=()):
    """Explicit operator operation only; never called by the workflow or Teacher."""
    from modules.whatsapp.contract import V2Stop
    if not approved or not re.fullmatch('[a-f0-9]{32}',version):raise V2Stop('EXACT_REVIEWED_PROMOTION_REQUIRED',owner='authorization')
    source=whatsapp_history()/'candidates'/version
    data=(source/'SKILL.md').read_bytes();meta=json.loads((source/'provenance.json').read_text())
    if hashlib.sha256(data).hexdigest()!=digest or meta['content_sha256']!=digest:raise V2Stop('PROMOTION_DIGEST_MISMATCH')
    records=list(iteration_records)
    required=('real_netcore','authentication_verified','observer_draft_verified','teacher_reviewed','cleanup_confirmed','equivalent_start','task_skill_read')
    if len(records)<3 or len({r['session_id'] for r in records})!=len(records):raise V2Stop('THREE_FRESH_REAL_ITERATIONS_REQUIRED')
    for record in records:
        if not re.fullmatch('[a-f0-9]{32}',record.get('session_id','')):raise V2Stop('INVALID_ITERATION_RECORD_ID')
        path=ROOT/'outputs'/'whatsapp-v2'/record['session_id']/'result.json'
        if not path.is_file() or json.loads(path.read_text())!=record:raise V2Stop('ORIGINAL_ITERATION_RECORD_REQUIRED')
        if record.get('loaded_skill_sha256')!=digest or record.get('task_sha256')!=meta['task_sha256'] or not all(record.get(k) is True for k in required):raise V2Stop('LIVE_GRADUATION_EVIDENCE_INCOMPLETE')
    # Preserve every approved version; never overwrite an old approved snapshot.
    destination=whatsapp_history()/'approved'
    if destination.exists():raise V2Stop('EXISTING_APPROVAL_REQUIRES_REVIEWED_ROLLBACK')
    destination.mkdir(parents=True)
    (destination/'SKILL.md').write_bytes(data)
    (destination/'provenance.json').write_text(json.dumps(meta,indent=2))
    (destination/'approval.json').write_text(json.dumps({'content_sha256':digest,'explicit_approval':True,
        'source_iterations':[r['session_id'] for r in records]}))
