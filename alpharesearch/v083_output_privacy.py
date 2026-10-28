"""Context-aware, credential-first screening for structured research replies.

Do not confuse a public paper URL with an API token. Never log literal
matches. The raw model tool arguments retain their SHA-256 provenance; the
archived/returned JSON is deterministically sanitized. No external I/O.
"""
from __future__ import annotations

import os
import re
from urllib.parse import urlsplit, urlunsplit

from .common import ResearchError

# These *credential* patterns do not include a general URL or mailbox regex.
CREDENTIAL_PATTERNS = (
    ('bearer_token', re.compile(r'(?i)\bBearer\s+[a-z0-9._~+/-]{8,}')),
    ('key_prefix', re.compile(r'(?i)\bsk-[a-z0-9_-]{12,}')),
    ('secret_assignment', re.compile(
        r'(?i)\b(?:api[_-]?key|access[_-]?token|password|secret|authorization|token)\s*[:=]\s*[^\s,;"\']{8,}')),
    ('url_authentication', re.compile(r'(?i)https?://[^\s/"\'@]+:[^\s/"\'@]+@')),
)
EMAIL = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b')
URL = re.compile(r'(?i)https?://[^\s"\'<>]+')
ENV_SECRET_KEYS = ('LLM_API_KEY','OPENAI_API_KEY','ANTHROPIC_API_KEY',
                   'QINIU_API_KEY','BRAIN_PASSWORD','BRAIN_API_KEY')


def _secret_categories(text):
    kinds={kind for kind, regex in CREDENTIAL_PATTERNS if regex.search(text)}
    for key in ENV_SECRET_KEYS:
        secret=os.environ.get(key)
        # Match only plausible secrets, not short coincidental substrings.
        if isinstance(secret,str) and len(secret)>=12 and secret in text:
            kinds.add('environment_secret_match')
    return kinds


def _public_url(match):
    original=match.group(0)
    suffix=''
    while original and original[-1] in '.,;)]}':
        suffix=original[-1]+suffix
        original=original[:-1]
    try:
        parts=urlsplit(original)
        # Preserve citable domain/path, discard userinfo, query parameters and
        # fragments. Auth-containing URLs are already rejected by preflight.
        if not parts.hostname or parts.username or parts.password:
            return '[REDACTED_URL]'+suffix
        clean=urlunsplit((parts.scheme,parts.netloc,parts.path,'',''))
        return clean+suffix
    except ValueError:
        return '[REDACTED_URL]'+suffix


def screen_and_sanitize(payload):
    """Return (safe JSON, safe decision metadata); reject high-confidence secrets.

    This is NOT a guarantee against every possible confidential string. All
    diagnostics use hashes and category labels, never the original matches.
    """
    counters={'emails_redacted':0,'urls_normalized':0,'text_fields_examined':0}
    rejected=set()

    def visit(value):
        if isinstance(value,str):
            counters['text_fields_examined']+=1
            rejected.update(_secret_categories(value))
            counters['emails_redacted']+=len(EMAIL.findall(value))
            safe=EMAIL.sub('[REDACTED_CONTACT]',value)
            normalized=URL.sub(_public_url,safe)
            if safe!=normalized:
                counters['urls_normalized']+=1
            return normalized
        if isinstance(value,list):
            return [visit(item) for item in value]
        if isinstance(value,dict):
            return {k:visit(v) for k,v in value.items()}
        return value

    safe_payload=visit(payload)
    metadata={**counters,'credential_signal_categories':sorted(rejected),
              'decision':'blocked_credentials' if rejected else 'accepted_sanitized'}
    if rejected:
        return None,metadata
    return safe_payload,metadata
