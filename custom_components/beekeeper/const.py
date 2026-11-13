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

# system_info installation_type -> HomeAssistantInfo.installation of the protocol
INSTALLATION = {
    "Home Assistant OS": "os",
    "Home Assistant Supervised": "supervised",
    "Home Assistant Container": "container",
    "Home Assistant Core": "core",
}
