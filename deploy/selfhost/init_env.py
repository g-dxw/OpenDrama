"""Create a private installation environment without printing credentials."""
from pathlib import Path
import base64
import os
import secrets

root = Path(__file__).resolve().parent
target = root / ".env"
if target.exists():
    raise SystemExit(".env already exists; existing credentials were preserved")
content = (root / ".env.example").read_text(encoding="utf-8")
for key in ("POSTGRES_PASSWORD", "SETTINGS_ENCRYPTION_KEY", "MODERATION_KEY_SECRET"):
    value = secrets.token_urlsafe(32) if key == "POSTGRES_PASSWORD" else base64.urlsafe_b64encode(os.urandom(32)).decode()
    content = content.replace(key + "=generated-by-init-env", key + "=" + value)
with target.open("x", encoding="utf-8") as output:
    output.write(content)
try: target.chmod(0o600)
except OSError: pass
print("Created private .env. Set your company, domain, media and email configuration before starting.")
