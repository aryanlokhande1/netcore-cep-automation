"""Private CDP ownership state; only numeric aliases and canonical facts are logged."""
import asyncio
from dataclasses import dataclass,field

class SessionDetached(RuntimeError):
    """A command belongs to a generation which is no longer attached."""

@dataclass
class GuardSession:
    session:str
    target:str|None
    kind:str
    generation:int
    parent:str|None=None
    parent_generation:int|None=None
    parent_verified:bool=False
    attached:bool=True
    intercepting:bool=False
    protected:bool=False
    destroyed:bool=False
    completed:bool=False
    frame_id:str|None=None
    loader_id:str|None=None
    document_generation:int=0
    primary_document_generation:int=0
    frame_retired:bool=False
    crashed:bool=False
    root_session:str|None=None
    gone:asyncio.Event=field(default_factory=asyncio.Event)
    settling:asyncio.Task|None=None
