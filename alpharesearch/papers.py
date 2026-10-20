"""Public arXiv metadata search, bounded cached requests, no arbitrary URL fetching."""
import json
import sqlite3
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlencode

from .common import ResearchError, digest, dumps, now
from .http import NoRedirect


def parse_atom(raw):
    if len(raw)>2_000_000 or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper(): raise ResearchError('拒绝过大/未知 XML')
    try: root=ET.fromstring(raw)
    except ET.ParseError as error: raise ResearchError('arXiv Atom 解析失败') from error
    ns={'a':'http://www.w3.org/2005/Atom'}
    results=[]
    for entry in root.findall('a:entry',ns):
        def value(name):return ' '.join((entry.findtext('a:'+name,'',ns) or '').split())
        identifier=value('id').replace('http://','https://')
        if not identifier.startswith('https://arxiv.org/abs/'): raise ResearchError('返回的 arXiv ID 未知')
        results.append({'paper_id':identifier.rsplit('/',1)[1],'url':identifier,'title':value('title'),
            'abstract':value('summary')[:6000],'authors':[n.text or '' for n in entry.findall('a:author/a:name',ns)],
            'published':value('published'),'updated':value('updated'),'provider':'arxiv',
            'retrieved_at':now(),'financial_claims_verified':False})
    return results


class PaperSearch:
    def __init__(self,cache_path,fetch=None,clock=time.time,sleep=time.sleep):
        self.path=Path(cache_path);self.path.parent.mkdir(parents=True,exist_ok=True)
        self.fetch=fetch or self._fetch;self.clock=clock;self.sleep=sleep

    @staticmethod
    def _fetch(url):
        request=urllib.request.Request(url,headers={'User-Agent':'AlphaResearch/0.3 metadata-research','Accept':'application/atom+xml'})
        try:
            with urllib.request.build_opener(NoRedirect()).open(request,timeout=30) as response:return response.read(2_000_001)
        except (urllib.error.URLError,OSError) as error:raise ResearchError('arXiv 查询失败；请稍后再试，不切换到未经允许的数据源') from error

    def search(self,query,limit=5):
        if not isinstance(query,str) or not 1<=len(query.strip())<=300 or type(limit)is not int or not 1<=limit<=10: raise ResearchError('论文查询需短文本，limit 为 1..10')
        key=digest([query.strip(),limit])
        db=sqlite3.connect(self.path,timeout=10)
        try:
            db.execute('CREATE TABLE IF NOT EXISTS cache(id TEXT PRIMARY KEY,payload TEXT,created REAL)')
            db.execute('CREATE TABLE IF NOT EXISTS pacing(id INTEGER PRIMARY KEY,next_at REAL)')
            db.commit();db.execute('BEGIN IMMEDIATE')
            cached=db.execute('SELECT payload,created FROM cache WHERE id=?',(key,)).fetchone()
            if cached and self.clock()-cached[1]<86400:
                db.commit();return {'papers':json.loads(cached[0]),'cache_hit':True}
            row=db.execute('SELECT next_at FROM pacing WHERE id=1').fetchone()
            wait=max(0,(row[0] if row else 0)-self.clock())
            if wait>6: raise ResearchError('论文检索正在排队，请稍后重试')
            db.execute('INSERT OR REPLACE INTO pacing VALUES(1,?)',(self.clock()+wait+3.1,));db.commit()
            if wait:self.sleep(wait)
            url='https://export.arxiv.org/api/query?'+urlencode({'search_query':query,'start':0,'max_results':limit,'sortBy':'relevance','sortOrder':'descending'})
            papers=parse_atom(self.fetch(url))[:limit]
            db.execute('INSERT OR REPLACE INTO cache VALUES(?,?,?)',(key,dumps(papers),self.clock()));db.commit()
            return {'papers':papers,'cache_hit':False}
        finally:db.close()


def agent_search(engine,query,limit=5):
    settings=engine.config.get('research',{})
    if not settings.get('paper_search_enabled',False):raise ResearchError('paper_search_enabled=false；本机负责人开启后才能外发查询')
    if type(limit)is not int or not 1<=limit<=settings.get('max_papers_per_search',5):raise ResearchError('论文数量超过配置上限')
    def reserve():
        count=engine.memory.db.execute("SELECT count(*) FROM events WHERE run_id=? AND kind='paper_search_requested'",(engine.run_id,)).fetchone()[0]
        if count>=settings.get('max_paper_requests_per_run',5):raise ResearchError('论文检索预算耗尽')
        engine.memory.event(engine.run_id,'paper_search_requested',{'query':query,'limit':limit})
    engine.memory.transaction(reserve)
    started=time.monotonic()
    try: result=PaperSearch(Path(engine.config['database']).parent/'papers.sqlite3').search(query,limit)
    finally:engine.memory.cost(engine.run_id,'paper_search',time.monotonic()-started)
    engine.memory.transaction(lambda:engine.memory.event(engine.run_id,'paper_search_result',result))
    return result
