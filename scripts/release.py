"""Standard-library source inventory, credential-pattern scan and ZIP validation."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import tomllib
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
ROOT_FILES={'README.md','program.md','pyproject.toml','.gitignore','.gitattributes'}
CONFIG_FILES={'settings.toml','evaluation.toml','catalog.demo.json'}
SOURCE_ROOTS={'alpharesearch','docs','examples','tests','scripts','.github'}
EXTENSIONS={'.py','.md','.toml','.json','.txt','.yaml','.yml'}
TOKEN_PATTERNS={
    'openai_like_key':re.compile(r'\bsk-(?:proj-)?[A-Za-z0-9_-]{24,}'),
    'github_like_token':re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})'),
    'aws_access_key':re.compile(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'),
    'private_key':re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'literal_credential_assignment':re.compile(r'''(?im)^\s*(?:BRAIN_PASSWORD|BRAIN_EMAIL|LLM_API_KEY|OPENAI_API_KEY|GITHUB_TOKEN|password)\s*=\s*["'][^"'\r\n]+["']'''),
}


def scan_text(name,text):
    return [{'path':str(name),'line':text.count('\n',0,m.start())+1,'pattern':kind}
            for kind,pattern in TOKEN_PATTERNS.items() for m in pattern.finditer(text)]


def inventory(root=ROOT):
    files={Path(p) for p in ROOT_FILES if (root/p).is_file()}
    files.update(Path('config')/p for p in CONFIG_FILES if (root/'config'/p).is_file())
    for directory in SOURCE_ROOTS:
        for path in (root/directory).rglob('*'):
            if '__pycache__' in path.parts:continue
            if path.is_symlink():raise ValueError('发布源码不允许符号链接：'+str(path.relative_to(root)))
            if path.is_file() and path.suffix in EXTENSIONS:files.add(path.relative_to(root))
    return sorted(files,key=lambda p:p.as_posix())


def scan_sources(root=ROOT):
    findings=[]
    for path in inventory(root):findings.extend(scan_text(path,(root/path).read_text(encoding='utf-8')))
    if findings:raise ValueError('发现疑似凭据模式（不输出值）：'+json.dumps(findings,ensure_ascii=False))
    return len(inventory(root))


def refresh_manifest(root=ROOT):
    scan_sources(root)
    (root/'MANIFEST.sha256').write_text(''.join(hashlib.sha256((root/p).read_bytes()).hexdigest()+'  '+p.as_posix()+'\n'
        for p in inventory(root)),encoding='utf-8')


def parse_manifest(text):
    expected={}
    for line in text.splitlines():
        if not re.fullmatch(r'[0-9a-f]{64}  .+',line):raise ValueError('清单格式错误')
        sha,name=line.split('  ',1);path=Path(name)
        if path.is_absolute() or '..' in path.parts or '\\' in name or name in expected:raise ValueError('清单路径无效或重复')
        expected[name]=sha
    if not expected:raise ValueError('清单为空')
    return expected


def verify_sources(root=ROOT):
    count=scan_sources(root)
    expected=parse_manifest((root/'MANIFEST.sha256').read_text(encoding='utf-8'))
    if set(expected)!={p.as_posix() for p in inventory(root)}:raise ValueError('清单与发布源码范围不一致')
    settings=root/'config/settings.toml'
    if settings.is_file():
        config=tomllib.loads(settings.read_text(encoding='utf-8'))
        for field in ('program','catalog','evaluation'):
            if field in config and config[field] not in expected:
                raise ValueError('发布包缺少配置依赖：'+field)
    for name,sha in expected.items():
        if hashlib.sha256((root/name).read_bytes()).hexdigest()!=sha:raise ValueError('源码哈希不匹配：'+name)
    return count


def verify_archive(path):
    with zipfile.ZipFile(path) as archive:
        names=archive.namelist()
        if len(names)!=len(set(names)) or archive.testzip() is not None:raise ValueError('ZIP 重复成员或内容损坏')
        prefix='alpha-research/'
        expected=parse_manifest(archive.read(prefix+'MANIFEST.sha256').decode('utf-8'))
        if set(names)!={prefix+n for n in expected}|{prefix+'MANIFEST.sha256'}:raise ValueError('ZIP 含未登记文件或缺少源码')
        for name,sha in expected.items():
            raw=archive.read(prefix+name)
            if hashlib.sha256(raw).hexdigest()!=sha:raise ValueError('ZIP 哈希不匹配：'+name)
            findings=scan_text(name,raw.decode('utf-8'))
            if findings:raise ValueError('ZIP 含疑似凭据模式：'+json.dumps(findings))
    return len(expected)


def create_archive(output,root=ROOT):
    verify_sources(root)
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(inventory(root)+[Path('MANIFEST.sha256')],key=lambda p:p.as_posix()):
            info=zipfile.ZipInfo('alpha-research/'+path.as_posix(),date_time=(2026,1,1,0,0,0))
            info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o100644<<16
            archive.writestr(info,(root/path).read_bytes())
    verify_archive(output)
    return hashlib.sha256(output.read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--refresh-manifest',action='store_true')
    parser.add_argument('--check',action='store_true')
    parser.add_argument('--output')
    parser.add_argument('--verify-archive')
    args=parser.parse_args()
    if not any((args.refresh_manifest,args.check,args.output,args.verify_archive)):parser.error('选择一个检查或打包操作')
    if args.refresh_manifest:refresh_manifest()
    result={}
    if args.check:result['source_files_verified']=verify_sources()
    if args.output:result.update(archive=str(args.output),sha256=create_archive(args.output))
    if args.verify_archive:result['archive_files_verified']=verify_archive(args.verify_archive)
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
