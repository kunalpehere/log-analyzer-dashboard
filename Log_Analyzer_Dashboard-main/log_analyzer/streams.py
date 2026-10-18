"""Chunked hashing and decoding without whole-file/upload copies."""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import io
import os
from pathlib import Path
from typing import Callable
from .limits import ResourceLimits, ResourceLimitError

@dataclass(frozen=True)
class SourceInput:
    source: str
    identity: str
    open_binary: Callable

    def __post_init__(self):
        if not self.source or not self.identity or len(self.source)>4096 or len(self.identity)>8192:
            raise ValueError("Source label/identity is empty or exceeds its limit")

    @classmethod
    def file(cls,path):
        path=Path(path).resolve()
        return cls(str(path),os.path.normcase(str(path)),lambda: path.open('rb'))

    @classmethod
    def upload(cls,stream,name):
        @contextmanager
        def opened():
            stream.seek(0)
            yield stream
        return cls(name,'upload:'+name,opened)

class LimitedReader(io.RawIOBase):
    def __init__(self,raw,limit):
        super().__init__()
        self.raw=raw
        self.limit=limit
        self.count=0
        self.digest=hashlib.sha256()
    def readable(self): return True
    def readinto(self,buffer):
        data=self.raw.read(min(len(buffer),65536,self.limit-self.count+1))
        if not isinstance(data,bytes): raise TypeError("Source must yield bytes")
        self.count+=len(data)
        if self.count>self.limit: raise ResourceLimitError("Source byte limit exceeded")
        self.digest.update(data)
        buffer[:len(data)]=data
        return len(data)

def fingerprint(source,limits):
    digest=hashlib.sha256(); size=0
    with source.open_binary() as raw:
        while True:
            chunk=raw.read(min(65536,limits.max_source_bytes-size+1))
            if not chunk: break
            size+=len(chunk)
            if size>limits.max_source_bytes: raise ResourceLimitError("Source byte limit exceeded")
            digest.update(chunk)
    return digest.hexdigest(),size

@contextmanager
def decoded_lines(source,limits):
    with source.open_binary() as raw:
        reader=LimitedReader(raw,limits.max_source_bytes)
        text=io.TextIOWrapper(io.BufferedReader(reader,buffer_size=8192),encoding='utf-8-sig',newline='')
        def lines():
            number=0
            while True:
                line=text.readline(limits.max_line_chars+1)
                if not line: break
                number+=1
                if number>limits.max_physical_lines: raise ResourceLimitError("Physical line limit exceeded")
                if len(line)>limits.max_line_chars: raise ResourceLimitError("Physical line character limit exceeded")
                yield line
        try:
            yield lines(),reader
        finally:
            text.close()
