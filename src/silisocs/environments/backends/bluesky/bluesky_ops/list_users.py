import subprocess
from pathlib import Path

def list_users() -> list[dict]:
    """Lists existing users on the PDS."""
    script = Path(__file__).parent / "pdsadmin.sh"

    result = subprocess.run(
        [str(script), "account", "list"],
        capture_output=True,
        text=True,
        check=True,
    )

    users = []
    for line in result.stdout.splitlines()[1:]:  # skip header
        line = line.strip()
        if not line:
            continue
        handle, email, did = line.split()
        users.append({
            "did": did.strip(),
            "handle": handle.strip(),
            "email": email.strip(),
        })

    return users