"""Frozen, read-only virtual skill backend. Never reads host paths."""
from dataclasses import dataclass
from deepagents.backends.protocol import BackendProtocol, ReadResult, WriteResult, EditResult, LsResult, FileDownloadResponse, FileUploadResponse

SKILL_SOURCE='/approved-skills'
SKILL_PATH=SKILL_SOURCE+'/netcore-login/SKILL.md'

@dataclass(frozen=True)
class SkillSnapshot:
    content: str
    digest: str
    metadata: dict
    approved: bool=True

class ApprovedSkillBackend(BackendProtocol):
    def __init__(self,snapshot,*,skill_path=SKILL_PATH):
        self.snapshot=snapshot
        self.skill_path=skill_path
        self.directory=skill_path.rsplit('/',1)[0]
        self.source=self.directory.rsplit('/',1)[0]
    def ls(self,path):
        if path.rstrip('/')==self.source:
            return LsResult(entries=[{'path':self.directory,'is_dir':True}])
        if path.rstrip('/')==self.directory:
            return LsResult(entries=[{'path':self.skill_path,'is_dir':False}])
        return LsResult(error='permission_denied',entries=[])
    def read(self,file_path,offset=0,limit=2000):
        if file_path!=self.skill_path:
            return ReadResult(error='permission_denied')
        lines=self.snapshot.content.splitlines()
        text='\n'.join(lines[max(0,offset):max(0,offset)+max(0,limit)])
        return ReadResult(file_data={'content':text,'encoding':'utf-8'})
    def download_files(self,paths):
        return [FileDownloadResponse(path=path,content=self.snapshot.content.encode() if path==self.skill_path else None,
            error=None if path==self.skill_path else 'permission_denied') for path in paths]
    def upload_files(self,files):
        return [FileUploadResponse(path=path,error='permission_denied') for path,_ in files]
    def write(self,*args,**kwargs):
        return WriteResult(error='permission_denied')
    def edit(self,*args,**kwargs):
        return EditResult(error='permission_denied')
