"""Module-owned safety policy for eligibility of the next fresh task execution."""
from typing import Protocol

class EligibilityPolicy(Protocol):
    def __call__(self,result:dict,live:bool=False)->str|None: ...
