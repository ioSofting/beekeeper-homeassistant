"""Constants of the integration Beekeeper."""

from datetime import timedelta

DOMAIN = "beekeeper"

# bee-ha next to Home Assistant in Docker (container name bee-ha); the add-on is found by discovery
DEFAULT_URL = "http://bee-ha:8099"

# how often the integration asks the agent
SCAN_INTERVAL = timedelta(seconds=15)
# how often it tells the agent version, installation and name of Home Assistant again
INFO_INTERVAL = timedelta(hours=1)

# fired on start and end of a remote access, for automations
EVENT_REMOTE_ACCESS = "beekeeper_remote_access"
# fired on start and end of an AI session
EVENT_AI_ACCESS = "beekeeper_ai_access"

# options: what an AI may do in Home Assistant during an AI session of the portal; all off by default
CONF_AI_READ = "ai_read"
CONF_AI_WRITE = "ai_write"
CONF_AI_USERS = "ai_users"

# system_info installation_type -> HomeAssistantInfo.installation of the protocol
INSTALLATION = {
    "Home Assistant OS": "os",
    "Home Assistant Supervised": "supervised",
    "Home Assistant Container": "container",
    "Home Assistant Core": "core",
}
