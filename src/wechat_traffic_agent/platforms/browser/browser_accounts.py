"""Map a registered X identity to a context in the shared browser."""
from pathlib import Path
from wechat_traffic_agent.account_registry import AccountRegistry

def x_account_binding(state_root: Path, account_id: str):
    record = AccountRegistry(state_root).get(account_id)
    if record.provider_id != "x":
        raise ValueError("account is not an X identity")
    # The migrated first account retains Chrome's persistent default context.
    # Every other stable registry ID owns its own isolated context.
    context = "default" if record.browser_profile_id == "default" else record.account_id
    return record, context
