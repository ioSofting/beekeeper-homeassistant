# Add-on Beekeeper: the agent bee-ha, one bundled file on Node.js.
# bee-ha.cjs is built in the repository of the server: npm run build:ha-addon
FROM node:22-alpine
# set by the Supervisor when it builds the add-on; empty in a docker build of its own
ARG BUILD_ARCH
# the CLI ha of Home Assistant for the console of the add-on (it exists for amd64 and aarch64 only)
ARG HA_CLI_VERSION=5.6.0
# the console (option console): a pseudo terminal with script of util-linux, a shell and a few tools
RUN apk add --no-cache bash util-linux-misc nano less curl jq \
 && case "${BUILD_ARCH}" in \
      amd64|aarch64) \
        curl -fsSL -o /usr/bin/ha "https://github.com/home-assistant/cli/releases/download/${HA_CLI_VERSION}/ha_${BUILD_ARCH}" \
        && chmod +x /usr/bin/ha ;; \
    esac
WORKDIR /app
ENV NODE_ENV=production \
    BEE_DATA_DIR=/data
COPY bee-ha.cjs ./
CMD ["node", "bee-ha.cjs"]
