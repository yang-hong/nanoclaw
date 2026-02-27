#!/bin/bash
# Wrapper to start nanoclaw with docker group active
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
export PATH="$HOME/.nvm/versions/node/v22.22.0/bin:$PATH"

cd /home/radxa/nanoclaw
exec sg docker -c "node /home/radxa/nanoclaw/dist/index.js"
