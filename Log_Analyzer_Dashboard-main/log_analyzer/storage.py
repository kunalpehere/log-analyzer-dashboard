"""Versioned disk-backed imports, revision snapshots, and bounded investigation queries."""
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid

from .filtering import FilterSpec
from .grouping import signature_details, SIGNATURE_VERSION
from .ingestion import iter_records
from .limits import DEFAULT_LIMITS, ResourceLimitError
from .models import BlankLine, DEFAULT_OPTIONS, Event, ScanState, Severity
from .streams import decoded_lines, fingerprint

SCHEMA_VERSION=1
_ORDER='e.ts_key,e.source,e.start_line,e.end_line,e.level,e.message,e.raw_text,e.original_timestamp,e.timestamp_assumed,e.id'
_SCHEMA="""
CREATE TABLE IF NOT EXISTS legacy_log_entries_archive AS SELECT * FROM log_entries;
CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS analysis_runs(id TEXT PRIMARY KEY, status TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS source_revisions(
 id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES analysis_runs(id),identity TEXT NOT NULL,source TEXT NOT NULL,
 sha256 TEXT NOT NULL,byte_count INTEGER NOT NULL,options_json TEXT NOT NULL,policy_json TEXT NOT NULL,
 stats_json TEXT NOT NULL,active INTEGER NOT NULL CHECK(active IN (0,1)));
CREATE UNIQUE INDEX IF NOT EXISTS active_source_identity ON source_revisions(run_id,identity) WHERE active=1;
CREATE TABLE IF NOT EXISTS stored_events(
 id INTEGER PRIMARY KEY,source_id TEXT NOT NULL REFERENCES source_revisions(id),source TEXT NOT NULL,
 ts_key TEXT NOT NULL,timestamp_utc TEXT NOT NULL,level TEXT NOT NULL,message TEXT NOT NULL,raw_text TEXT NOT NULL,
 original_timestamp TEXT NOT NULL,timestamp_assumed INTEGER NOT NULL,start_line INTEGER NOT NULL,end_line INTEGER NOT NULL,
 group_key TEXT,group_id TEXT,signature TEXT);
CREATE INDEX IF NOT EXISTS events_source_time ON stored_events(source_id,ts_key,id);
CREATE INDEX IF NOT EXISTS events_group ON stored_events(source_id,group_key);
CREATE TABLE IF NOT EXISTS stored_issues(
 id INTEGER PRIMARY KEY,source_id TEXT NOT NULL REFERENCES source_revisions(id),source TEXT NOT NULL,
 line_number INTEGER NOT NULL,end_line INTEGER NOT NULL,reason TEXT NOT NULL,raw_text TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS issues_source_line ON stored_issues(source_id,line_number,id);
CREATE TABLE IF NOT EXISTS selections(id TEXT PRIMARY KEY,source_ids_json TEXT NOT NULL,filters_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS selected_events(
 selection_id TEXT NOT NULL REFERENCES selections(id),position INTEGER NOT NULL,event_id INTEGER NOT NULL REFERENCES stored_events(id),
 group_key TEXT,PRIMARY KEY(selection_id,position));
CREATE INDEX IF NOT EXISTS selected_group ON selected_events(selection_id,group_key,position);
CREATE TABLE IF NOT EXISTS selection_groups(
 selection_id TEXT NOT NULL REFERENCES selections(id),group_key TEXT NOT NULL,group_id TEXT NOT NULL,level TEXT NOT NULL,
 signature TEXT NOT NULL,count INTEGER NOT NULL,first_seen TEXT NOT NULL,last_seen TEXT NOT NULL,
 PRIMARY KEY(selection_id,group_key));
CREATE TABLE IF NOT EXISTS store_settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
"""
_EVENT_INSERT='INSERT INTO stored_events(source_id,source,ts_key,timestamp_utc,level,message,raw_text,original_timestamp,timestamp_assumed,start_line,end_line,group_key,group_id,signature) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)'
_ISSUE_INSERT='INSERT INTO stored_issues(source_id,source,line_number,end_line,reason,raw_text) VALUES (?,?,?,?,?,?)'

class ImportFailure(RuntimeError):
    def __init__(self,source,error):
        self.source=source
        self.reason=f'{type(error).__name__}: {error}'
        super().__init__(f'{source}: {self.reason}')

@dataclass(frozen=True)
class ImportResult:
    run_id: str
    source_ids: tuple[str,...]
    reused: int
    published: bool
    peak_event_batch: int
    peak_issue_batch: int

@dataclass(frozen=True)
class SourceSummary:
    id: str
    run_id: str
    source: str
    identity: str
    sha256: str
    byte_count: int
    options: dict
    stats: dict
    @property
    def state(self): return ScanState(self.stats['state'])
    @property
    def parsed_count(self): return self.stats['parsed_count']
    @property
    def rejected_count(self): return self.stats['rejected_count']
    @property
    def counts(self): return {Severity(key):value for key,value in self.stats['counts'].items()}
    @property
    def error_count(self): return self.counts[Severity.ERROR]+self.counts[Severity.CRITICAL]
    @property
    def warning_count(self): return self.counts[Severity.WARNING]

class Store:
    def __init__(self,path,limits=DEFAULT_LIMITS,*,timeout=2.0):
        self.path=Path(path).resolve(); self.limits=limits; self.timeout=timeout
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self._migrate()

    @contextmanager
    def connection(self):
        conn=sqlite3.connect(self.path,timeout=self.timeout)
        conn.row_factory=sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('PRAGMA temp_store=FILE')
        conn.execute('PRAGMA cache_size=-4096')
        conn.create_function('unicode_casefold',1,lambda value:value.casefold(),deterministic=True)
        try: yield conn
        finally: conn.close()

    def _migrate(self):
        with self.connection() as conn:
            version=conn.execute('PRAGMA user_version').fetchone()[0]
            if version>SCHEMA_VERSION: raise ValueError(f'Unsupported future schema version: {version}')
            if version==SCHEMA_VERSION:
                names={row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('schema_migrations','analysis_runs','source_revisions','stored_events','stored_issues','selections','selected_events','selection_groups','store_settings','log_entries','legacy_log_entries_archive')")}
                if len(names)!=11: raise ValueError('Schema version is set but required storage tables are missing')
                return
            try:
                conn.execute('BEGIN IMMEDIATE')
                # Preserve legacy rows and their unknown original timezone.
                from database import _ensure_schema
                _ensure_schema(conn)
                for statement in _SCHEMA.split(';'):
                    if statement.strip(): conn.execute(statement)
                conn.execute('INSERT OR IGNORE INTO schema_migrations VALUES (?,?)',(SCHEMA_VERSION,datetime.now(timezone.utc).isoformat()))
                conn.execute(f'PRAGMA user_version={SCHEMA_VERSION}')
                conn.commit()
            except BaseException:
                conn.rollback(); raise

    def _flush(self,conn,events,issues):
        if events: conn.executemany(_EVENT_INSERT,events); events.clear()
        if issues: conn.executemany(_ISSUE_INSERT,issues); issues.clear()

    def import_sources(self,inputs,options=DEFAULT_OPTIONS,*,run_id=None):
        from itertools import islice
        inputs=tuple(islice(inputs,self.limits.max_sources+1))
        if not inputs or len(inputs)>self.limits.max_sources: raise ResourceLimitError('Source count must be within configured limit')
        if options.max_event_chars>self.limits.max_event_chars or options.max_event_lines>self.limits.max_event_lines:
            raise ResourceLimitError('Traceback options exceed import hard limits')
        if len({source.identity for source in inputs})!=len(inputs): raise ValueError('Duplicate source identities in one request')
        from .paths import validate_output_path
        for suffix in ('','-journal','-wal','-shm'):
            validate_output_path(str(self.path)+suffix,[source.source for source in inputs])
        run_id=run_id or uuid.uuid4().hex
        if not isinstance(run_id,str) or len(run_id)>128: raise ValueError("Invalid run ID")
        encoded_options=json.dumps(dict(asdict(options),pipeline_version='text-jsonl-v1',signature_version=SIGNATURE_VERSION),sort_keys=True)
        encoded_policy=json.dumps(self.limits.import_policy(),sort_keys=True)
        source_ids=[]; reused=0; peak_events=0; peak_issues=0
        current=inputs[0].source
        with self.connection() as conn:
            try:
                conn.execute('BEGIN IMMEDIATE')
                existing=conn.execute('SELECT id FROM analysis_runs WHERE id=?',(run_id,)).fetchone()
                if existing is None:
                    conn.execute('INSERT INTO analysis_runs VALUES (?,?,?)',(run_id,'importing',datetime.now(timezone.utc).isoformat()))
                active_total=conn.execute('SELECT COUNT(*) FROM source_revisions WHERE run_id=? AND active=1',(run_id,)).fetchone()[0]
                if active_total>self.limits.max_sources: raise ResourceLimitError('Existing run exceeds current source count limit')
                for source in inputs:
                    current=source.source
                    digest,size=fingerprint(source,self.limits)
                    previous=conn.execute('SELECT * FROM source_revisions WHERE run_id=? AND identity=? AND active=1',(run_id,source.identity)).fetchone()
                    if previous and previous['source']==source.source and previous['sha256']==digest and previous['options_json']==encoded_options and previous['policy_json']==encoded_policy:
                        source_ids.append(previous['id']); reused+=1; continue
                    active_count=conn.execute('SELECT COUNT(*) FROM source_revisions WHERE run_id=? AND active=1',(run_id,)).fetchone()[0]
                    if not previous and active_count>=self.limits.max_sources: raise ResourceLimitError('Run source count limit exceeded')
                    revision=uuid.uuid4().hex
                    conn.execute('INSERT INTO source_revisions VALUES (?,?,?,?,?,?,?,?,?,0)',(revision,run_id,source.identity,source.source,digest,size,encoded_options,encoded_policy,'{}'))
                    stats={'state':'empty','physical_lines':0,'total_lines':0,'parsed_count':0,'rejected_count':0,'issue_count':0,'continuation_lines':0,'counts':{level.value:0 for level in Severity}}
                    events=[]; issues=[]; records=0
                    with decoded_lines(source,self.limits) as (lines,reader):
                        for item in iter_records(lines,source.source,options):
                            if isinstance(item,BlankLine): stats['physical_lines']+=1; continue
                            records+=1
                            if records>self.limits.max_records: raise ResourceLimitError('Event/issue record limit exceeded')
                            stats['physical_lines']+=item.nonblank_lines
                            stats['total_lines']+=item.nonblank_lines
                            if isinstance(item,Event):
                                stats['parsed_count']+=1; stats['counts'][item.level.value]+=1
                                stats['continuation_lines']+=item.nonblank_lines-1
                                key=gid=signature=None
                                if item.level in (Severity.ERROR,Severity.CRITICAL):
                                    tokens,gid,signature=signature_details(item.level,item.message)
                                    key=json.dumps([SIGNATURE_VERSION,item.level.value,tokens],ensure_ascii=False,separators=(',',':'))
                                events.append((revision,source.source,item.timestamp.isoformat(timespec='microseconds'),item.timestamp.isoformat(),item.level.value,item.message,item.raw_text,item.original_timestamp,int(item.timestamp_assumed),item.start_line,item.end_line,key,gid,signature))
                                peak_events=max(peak_events,len(events))
                            else:
                                stats['issue_count']+=1; stats['rejected_count']+=item.nonblank_lines
                                issues.append((revision,source.source,item.line_number,item.end_line,item.reason,item.raw_text))
                                peak_issues=max(peak_issues,len(issues))
                            if len(events)>=self.limits.batch_size or len(issues)>=self.limits.batch_size:
                                self._flush(conn,events,issues)
                        if reader.digest.hexdigest()!=digest or reader.count!=size:
                            raise OSError('Source changed between fingerprint and parsing passes')
                    self._flush(conn,events,issues)
                    stats['state']='success' if stats['total_lines'] else 'empty'
                    assert stats['total_lines']==stats['parsed_count']+stats['continuation_lines']+stats['rejected_count']
                    if previous: conn.execute('UPDATE source_revisions SET active=0 WHERE id=?',(previous['id'],))
                    conn.execute('UPDATE source_revisions SET stats_json=?,active=1 WHERE id=?',(json.dumps(stats,sort_keys=True),revision))
                    source_ids.append(revision)
                conn.execute("UPDATE analysis_runs SET status='completed' WHERE id=?",(run_id,))
                published=all(json.loads(row[0])['parsed_count']>0 for row in conn.execute('SELECT stats_json FROM source_revisions WHERE run_id=? AND active=1',(run_id,)))
                if published:
                    conn.execute('DELETE FROM log_entries')
                    conn.execute("""INSERT INTO log_entries(file,date,time,level,message,timestamp_utc,original_timestamp,timestamp_assumed,start_line,end_line,raw_text)
                    SELECT e.source,substr(e.ts_key,1,10),substr(e.timestamp_utc,12,length(e.timestamp_utc)-17),e.level,e.message,e.timestamp_utc,e.original_timestamp,e.timestamp_assumed,e.start_line,e.end_line,e.raw_text
                    FROM stored_events e JOIN source_revisions s ON s.id=e.source_id WHERE s.run_id=? AND s.active=1 AND e.level IN ('ERROR','CRITICAL','WARNING')""",(run_id,))
                    conn.execute("INSERT OR REPLACE INTO store_settings VALUES ('active_run',?)",(run_id,))
                    published_ids=[row[0] for row in conn.execute('SELECT id FROM source_revisions WHERE run_id=? AND active=1 ORDER BY source,identity',(run_id,))]
                    conn.execute("INSERT OR REPLACE INTO store_settings VALUES ('active_source_revision_ids',?)",(json.dumps(published_ids),))
                conn.commit()
            except (OSError,UnicodeError,sqlite3.Error,ValueError) as error:
                conn.rollback(); raise ImportFailure(current,error) from error
            except BaseException:
                conn.rollback(); raise
        return ImportResult(run_id,tuple(source_ids),reused,published,peak_events,peak_issues)

    def source(self,source_id):
        with self.connection() as conn:
            row=conn.execute('SELECT * FROM source_revisions WHERE id=?',(source_id,)).fetchone()
            if row is None: raise ValueError('Unknown source revision')
            return SourceSummary(row['id'],row['run_id'],row['source'],row['identity'],row['sha256'],row['byte_count'],json.loads(row['options_json']),json.loads(row['stats_json']))

    def run_sources(self,run_id):
        with self.connection() as conn:
            values=tuple(row[0] for row in conn.execute('SELECT id FROM source_revisions WHERE run_id=? AND active=1 ORDER BY source,identity LIMIT ?',(run_id,self.limits.max_sources+1)))
            if len(values)>self.limits.max_sources: raise ResourceLimitError('Run exceeds current source count limit')
            return values

    def source_history(self,run_id,*,size=None,offset=0):
        size=self.limits.preview_size if size is None else size
        if type(size) is not int or not 1<=size<=self.limits.max_page_size or type(offset) is not int or offset<0:
            raise ValueError('Invalid revision history page size/offset')
        with self.connection() as conn:
            return [dict(row) for row in conn.execute('SELECT id,run_id,identity,source,sha256,byte_count,active,stats_json FROM source_revisions WHERE run_id=? ORDER BY rowid DESC LIMIT ? OFFSET ?',(run_id,size,offset))]

    def published_sources(self):
        with self.connection() as conn:
            row=conn.execute("SELECT value FROM store_settings WHERE key='active_source_revision_ids'").fetchone()
            return tuple(json.loads(row[0])) if row else ()

    def page_legacy_archive(self,size=None,offset=0):
        size=self.limits.preview_size if size is None else size
        if type(size) is not int or not 1<=size<=self.limits.max_page_size or type(offset) is not int or offset<0:
            raise ValueError('Invalid legacy archive page size/offset')
        with self.connection() as conn:
            return [dict(row) for row in conn.execute("SELECT id,substr(file,1,1024) AS file,substr(date,1,64) AS date,substr(time,1,64) AS time,substr(level,1,64) AS level,substr(message,1,1024) AS message_preview FROM legacy_log_entries_archive ORDER BY id LIMIT ? OFFSET ?",(size,offset))]

    def list_runs(self,size=None,offset=0):
        size=self.limits.preview_size if size is None else size
        if type(size) is not int or not 1<=size<=self.limits.max_page_size or type(offset) is not int or offset<0:
            raise ValueError('Invalid run page size/offset')
        with self.connection() as conn:
            return [dict(row) for row in conn.execute('SELECT id,status,created_at FROM analysis_runs ORDER BY created_at DESC,id LIMIT ? OFFSET ?',(size,offset))]

    def view(self,source_ids,filters=FilterSpec()):
        from itertools import islice
        candidates=tuple(islice(source_ids,self.limits.max_sources+1))
        if len(candidates)>self.limits.max_sources: raise ValueError('Source revision count limit exceeded')
        ids=tuple(sorted(set(candidates)))
        if not ids or len(ids)>self.limits.max_sources: raise ValueError('Invalid source revision selection')
        sources=tuple(self.source(value) for value in ids)
        encoded=json.dumps([ids,filters.as_dict()],sort_keys=True,ensure_ascii=False)
        selection=hashlib.sha256(encoded.encode('utf-8')).hexdigest()
        with self.connection() as conn:
            if not conn.execute('SELECT 1 FROM selections WHERE id=?',(selection,)).fetchone():
                where=['e.source_id IN ('+','.join('?' for _ in ids)+')']; args=list(ids)
                if filters.start: where.append('e.ts_key>=?'); args.append(filters.start.isoformat(timespec='microseconds'))
                if filters.end: where.append('e.ts_key<=?'); args.append(filters.end.isoformat(timespec='microseconds'))
                if filters.severities is not None:
                    if filters.severities:
                        levels=sorted(level.value for level in filters.severities)
                        where.append('e.level IN ('+','.join('?' for _ in levels)+')'); args.extend(levels)
                    else: where.append('0')
                if filters.text:
                    where.append('instr(e.message,?)>0' if filters.case_sensitive else 'instr(unicode_casefold(e.message),?)>0')
                    args.append(filters.text if filters.case_sensitive else filters.text.casefold())
                try:
                    conn.execute('BEGIN IMMEDIATE')
                    conn.execute('INSERT OR IGNORE INTO selections VALUES (?,?,?)',(selection,json.dumps(ids),json.dumps(filters.as_dict(),sort_keys=True)))
                    conn.execute('INSERT OR IGNORE INTO selected_events SELECT ?,ROW_NUMBER() OVER (ORDER BY '+_ORDER+'),e.id,e.group_key FROM stored_events e WHERE '+' AND '.join(where),[selection,*args])
                    conn.execute("""INSERT OR IGNORE INTO selection_groups SELECT ?,s.group_key,e.group_id,e.level,e.signature,COUNT(*),MIN(e.ts_key),MAX(e.ts_key)
                    FROM selected_events s JOIN stored_events e ON e.id=s.event_id WHERE s.selection_id=? AND s.group_key IS NOT NULL GROUP BY s.group_key""",(selection,selection))
                    conn.commit()
                except BaseException: conn.rollback(); raise
        return StoredView(self,selection,sources,filters)

class StoredView:
    def __init__(self,store,selection_id,sources,filters):
        self.store=store; self.id=selection_id; self.sources=sources; self.filters=filters
        with store.connection() as conn:
            self.cache_number=conn.execute('SELECT rowid FROM selections WHERE id=?',(self.id,)).fetchone()[0]
            counts={Severity(row[0]):row[1] for row in conn.execute('SELECT e.level,COUNT(*) FROM selected_events s JOIN stored_events e ON e.id=s.event_id WHERE s.selection_id=? GROUP BY e.level',(self.id,))}
            self.counts={level:counts.get(level,0) for level in Severity}
            self.event_count=sum(self.counts.values())
            self.continuation_lines=conn.execute('SELECT COALESCE(SUM(e.end_line-e.start_line),0) FROM selected_events s JOIN stored_events e ON e.id=s.event_id WHERE s.selection_id=?',(self.id,)).fetchone()[0]
            self.group_count=conn.execute('SELECT COUNT(*) FROM selection_groups WHERE selection_id=?',(self.id,)).fetchone()[0]
    @property
    def error_count(self): return self.counts[Severity.ERROR]+self.counts[Severity.CRITICAL]
    @property
    def warning_count(self): return self.counts[Severity.WARNING]

    def _page(self,size,offset):
        size=self.store.limits.preview_size if size is None else size
        if type(size) is not int or not 1<=size<=self.store.limits.max_page_size or type(offset) is not int or offset<0:
            raise ValueError('Invalid page size or offset')
        return size,offset

    def _rows(self,sql,args):
        with self.store.connection() as conn:
            cursor=conn.execute(sql,args)
            while True:
                rows=cursor.fetchmany(self.store.limits.batch_size)
                if not rows: break
                for row in rows: yield dict(row)

    @staticmethod
    def event_row(row):
        stamp=datetime.fromisoformat(row['timestamp_utc'])
        return {'event_id':f"event-{row['position']:06d}",'source':row['source'],'line_number':row['start_line'],
                'start_line':row['start_line'],'end_line':row['end_line'],'timestamp_utc':row['timestamp_utc'],
                'original_timestamp':row['original_timestamp'],'timestamp_assumed':bool(row['timestamp_assumed']),
                'date':stamp.date().isoformat(),'time':stamp.time().isoformat(),'level':row['level'],
                'message':row['message'],'raw_text':row['raw_text'],'group_id':row['group_id']}

    def iter_events(self,*,group_key=None,source_id=None,size=None,offset=0):
        sql='SELECT e.*,s.position FROM selected_events s JOIN stored_events e ON e.id=s.event_id WHERE s.selection_id=?'; args=[self.id]
        if group_key is not None: sql+=' AND s.group_key=?'; args.append(group_key)
        if source_id is not None:
            if source_id not in {source.id for source in self.sources}: raise ValueError('Source is outside investigation')
            sql+=' AND e.source_id=?'; args.append(source_id)
        sql+=' ORDER BY s.position'
        if size is not None:
            size,offset=self._page(size,offset); sql+=' LIMIT ? OFFSET ?'; args.extend([size,offset])
        for row in self._rows(sql,args): yield self.event_row(row)

    def page_events(self,size=None,offset=0,*,group_key=None,source_id=None):
        size,offset=self._page(size,offset)
        return list(self.iter_events(group_key=group_key,source_id=source_id,size=size,offset=offset))

    def iter_groups(self,size=None,offset=0):
        sql='SELECT * FROM selection_groups WHERE selection_id=? ORDER BY count DESC,first_seen,level,signature,group_key'; args=[self.id]
        if size is not None:
            size,offset=self._page(size,offset); sql+=' LIMIT ? OFFSET ?'; args.extend([size,offset])
        for row in self._rows(sql,args):
            row['first_seen_utc']=datetime.fromisoformat(row.pop('first_seen')).isoformat()
            row['last_seen_utc']=datetime.fromisoformat(row.pop('last_seen')).isoformat()
            row.pop('selection_id'); yield row

    def page_groups(self,size=None,offset=0):
        size,offset=self._page(size,offset)
        return list(self.iter_groups(size,offset))

    def iter_issues(self,source_id,*,size=None,offset=0):
        if source_id not in {source.id for source in self.sources}: raise ValueError('Source is outside investigation')
        sql='SELECT source,line_number,end_line,reason,raw_text FROM stored_issues WHERE source_id=? ORDER BY line_number,id'; args=[source_id]
        if size is not None:
            size,offset=self._page(size,offset); sql+=' LIMIT ? OFFSET ?'; args.extend([size,offset])
        yield from self._rows(sql,args)

    def examples(self,group_key):
        sql="""SELECT e.*,s.position FROM selected_events s JOIN stored_events e ON e.id=s.event_id
        WHERE s.selection_id=? AND s.group_key=? AND s.position IN
        (SELECT MIN(s2.position) FROM selected_events s2 JOIN stored_events e2 ON e2.id=s2.event_id
        WHERE s2.selection_id=? AND s2.group_key=? GROUP BY e2.message)
        ORDER BY s.position LIMIT 3"""
        return [self.event_row(row) for row in self._rows(sql,[self.id,group_key,self.id,group_key])]

    def iter_member_ids(self,group_key):
        for row in self._rows('SELECT position FROM selected_events WHERE selection_id=? AND group_key=? ORDER BY position',[self.id,group_key]):
            yield f"event-{row['position']:06d}"

    def iter_csv_events(self):
        sql="""SELECT e.*,s.position,g.signature AS group_signature,g.count AS group_count,
        g.first_seen AS group_first_seen,g.last_seen AS group_last_seen
        FROM selected_events s JOIN stored_events e ON e.id=s.event_id
        LEFT JOIN selection_groups g ON g.selection_id=s.selection_id AND g.group_key=s.group_key
        WHERE s.selection_id=? ORDER BY s.position"""
        for row in self._rows(sql,[self.id]):
            result=self.event_row(row)
            result.update(group_signature=row['group_signature'] or '',group_count=row['group_count'] or '',
                group_first_seen_utc=datetime.fromisoformat(row['group_first_seen']).isoformat() if row['group_first_seen'] else '',
                group_last_seen_utc=datetime.fromisoformat(row['group_last_seen']).isoformat() if row['group_last_seen'] else '')
            yield result
