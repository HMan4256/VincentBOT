"""Parse a single JSON object, optionally wrapped in a Markdown fence."""
import json
import re


def parse_object(text):
    if not isinstance(text,str):raise ValueError('Model output must be text')
    text=text.strip()
    if text.startswith('```'):
        match=re.fullmatch(r'```(?:json)?\s*([\s\S]*?)\s*```',text,re.I)
        if not match:raise ValueError('Malformed fenced JSON')
        text=match.group(1)
    value=json.loads(text)
    if not isinstance(value,dict):raise ValueError('Expected a JSON object')
    return value
