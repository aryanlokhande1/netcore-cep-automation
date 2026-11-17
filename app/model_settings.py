"""Model-only secure configuration; no browser or credential configuration loading."""
import os
import re
from io import StringIO
from dataclasses import dataclass,field
from dotenv import dotenv_values
from app.config import ROOT,ConfigurationError

NAMES={'OPENAI_API_KEY','OPENAI_MODEL','DOER_MAX_OUTPUT_TOKENS','LOGIN_MAX_OUTPUT_TOKENS','TEACHER_MAX_OUTPUT_TOKENS',
    'TEACHER_REASONING_EFFORT','TEACHER_MAX_INPUT_TOKENS','TEACHER_REQUEST_BUDGET',
    'LEARNING_MAX_REQUESTS','LEARNING_MAX_INPUT_TOKENS','LEARNING_MAX_OUTPUT_TOKENS'}

def selected_model_environment(path):
    # Filter keys before dotenv value parsing. Credential keys are never loaded,
    # interpolated, returned or inserted into os.environ by this loader.
    lines=[]
    if path.is_file():
        with path.open(encoding='utf-8-sig') as stream:
            for line in stream:
                name=line.split('=',1)[0].strip().removeprefix('export ').strip()
                if name in NAMES:lines.append(line)
    data=dotenv_values(stream=StringIO(''.join(lines)),interpolate=False)
    return {name:os.environ.get(name,data.get(name)) for name in NAMES}

@dataclass(frozen=True)
class ModelSettings:
    api_key:str=field(repr=False,default='')
    model:str='gpt-6-luna'
    doer_output:int=500
    login_output:int=1536
    teacher_output:int=2000
    teacher_reasoning:str='low'
    teacher_input:int=8000
    teacher_requests:int=3
    cycle_requests:int=24
    cycle_input:int=240000
    cycle_output:int=21000
    def __post_init__(self):
        if self.model!='gpt-6-luna':raise ConfigurationError('Only gpt-6-luna is permitted; no fallback')
        if not 1024<=self.login_output<=4096:raise ConfigurationError('Login output allowance must be bounded from 1024 to 4096')
        if self.teacher_reasoning not in {'none','low','medium','high'}:raise ConfigurationError('Invalid Teacher reasoning effort')
        if not 1<=self.doer_output<=4096 or not 1<=self.teacher_output<=2000:raise ConfigurationError('Invalid separate output limits')
        if not 1000<=self.teacher_input<=8000 or not 1<=self.teacher_requests<=5:raise ConfigurationError('Invalid Teacher input/request limits')
        if not 1<=self.cycle_requests<=24 or min(self.cycle_input,self.cycle_output)<1:raise ConfigurationError('Invalid cycle token budget')
    @classmethod
    def load(cls):
        data=selected_model_environment(ROOT/'.env')
        mapping={'doer_output':('DOER_MAX_OUTPUT_TOKENS',500),'login_output':('LOGIN_MAX_OUTPUT_TOKENS',1536),'teacher_output':('TEACHER_MAX_OUTPUT_TOKENS',2000),
            'teacher_input':('TEACHER_MAX_INPUT_TOKENS',8000),'teacher_requests':('TEACHER_REQUEST_BUDGET',3),
            'cycle_requests':('LEARNING_MAX_REQUESTS',24),'cycle_input':('LEARNING_MAX_INPUT_TOKENS',240000),
            'cycle_output':('LEARNING_MAX_OUTPUT_TOKENS',21000)}
        try:values={k:int(data.get(name) or default) for k,(name,default) in mapping.items()}
        except (TypeError,ValueError):raise ConfigurationError('Invalid model token/request configuration') from None
        return cls(api_key=data.get('OPENAI_API_KEY') or '',model=data.get('OPENAI_MODEL') or 'gpt-6-luna',
            teacher_reasoning=data.get('TEACHER_REASONING_EFFORT') or 'low',**values)
