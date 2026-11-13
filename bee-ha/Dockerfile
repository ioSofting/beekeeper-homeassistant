# Add-on Beekeeper: the agent bee-ha, one bundled file on Node.js.
# bee-ha.cjs is built in the repository of the server: npm run build:ha-addon
FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production \
    BEE_DATA_DIR=/data
COPY bee-ha.cjs ./
CMD ["node", "bee-ha.cjs"]
