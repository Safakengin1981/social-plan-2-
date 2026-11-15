"""Fail-closed release gate: checks configuration without contacting or modifying services."""
import json
import os
from pathlib import Path
from preflight import check


def evaluate(env=None, root=None):
    env = dict(os.environ if env is None else env)
    root = Path(root or Path(__file__).parent)
    findings = check(env)
    yaml = root / 'render.yaml'
    if not yaml.is_file():
        findings.append({'check':'Render Blueprint','ok':False,'note':'render.yaml eksik'})
    else:
        content = yaml.read_text(encoding='utf-8')
        findings.append({'check':'Render kalıcı disk','ok':'mountPath: /var/data' in content and 'plan: starter' in content,'note':'Ücretli kalıcı disk tanımı bulunmalı; gerçek bağlantı ayrıca test edilmeli'})
    findings.append({'check':'Instagram canlı yayın izni','ok':False,'note':'Gerçek Meta yetkilendirmesi ve yayın testi yapılmadı; canlı yayın kapalı tutulmalı'})
    return {'ready':all(f['ok'] for f in findings), 'checks':findings}


if __name__ == '__main__':
    result = evaluate()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result['ready'] else 1)
