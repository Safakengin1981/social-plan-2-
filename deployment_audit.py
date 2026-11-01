"""Read-only deployment readiness audit. Does not deploy anything."""
import json, os, pathlib, sys
root=pathlib.Path(__file__).resolve().parent
checks={
 "backend":(root/'app.py').is_file(),
 "frontend":(root/'static/index.html').is_file(),
 "pwa_manifest":(root/'static/manifest.webmanifest').is_file(),
 "service_worker":(root/'static/sw.js').is_file(),
 "dockerfile":(root/'Dockerfile').is_file(),
 "compose":(root/'compose.yaml').is_file(),
 "tests":(root/'tests/test_api.py').is_file(),
 "https_url_configured":os.environ.get('SOCIALPLAN_PUBLIC_URL','').startswith('https://'),
 "meta_app_configured":bool(os.environ.get('META_APP_ID') and os.environ.get('META_APP_SECRET')),
}
print(json.dumps({'checks':checks,'ready_for_public_launch':False,'note':'Static checks only. Security review, HTTPS deployment and Android device tests required.'},ensure_ascii=False,indent=2))
sys.exit(0 if all(list(checks.values())[:7]) else 1)
