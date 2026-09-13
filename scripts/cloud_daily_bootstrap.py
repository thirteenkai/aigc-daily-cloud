"""Configure a bot-only CLI without printing or passing a secret in argv."""
import os
import subprocess
import sys

app_id = os.environ.pop('LARKSUITE_CLI_APP_ID')
app_secret = os.environ.pop('LARKSUITE_CLI_APP_SECRET')
result = subprocess.run(['lark-cli', 'config', 'init', '--app-id', app_id,
                         '--app-secret-stdin', '--brand', 'feishu'],
                        input=app_secret, text=True, capture_output=True)
if result.returncode:
    raise SystemExit('Bot configuration failed; credentials were not logged')
subprocess.run([sys.executable, 'scripts/cloud_daily.py'], check=True)
